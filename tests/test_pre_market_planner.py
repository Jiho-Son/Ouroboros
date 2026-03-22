"""Tests for PreMarketPlanner — Gemini prompt builder + response parser."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.analysis.smart_scanner import ScanCandidate
from src.brain.context_selector import DecisionType
from src.brain.gemini_client import TradeDecision
from src.config import Settings
from src.context.store import ContextLayer
from src.strategy.models import (
    CrossMarketContext,
    DayPlaybook,
    MarketOutlook,
    ScenarioAction,
)
from src.strategy.pre_market_planner import PreMarketPlanner, _raw_pnl_unit_for_market

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _candidate(
    code: str = "005930",
    name: str = "Samsung",
    price: float = 71000,
    rsi: float = 28.5,
    volume_ratio: float = 3.2,
    signal: str = "oversold",
    score: float = 82.0,
) -> ScanCandidate:
    return ScanCandidate(
        stock_code=code,
        name=name,
        price=price,
        volume=1_500_000,
        volume_ratio=volume_ratio,
        rsi=rsi,
        signal=signal,
        score=score,
    )


def _gemini_response_json(
    outlook: str = "neutral_to_bullish",
    stocks: list[dict] | None = None,
    global_rules: list[dict] | None = None,
) -> str:
    """Build a valid Gemini JSON response."""
    if stocks is None:
        stocks = [
            {
                "stock_code": "005930",
                "scenarios": [
                    {
                        "condition": {"rsi_below": 30, "volume_ratio_above": 2.5},
                        "action": "BUY",
                        "confidence": 85,
                        "allocation_pct": 15.0,
                        "stop_loss_pct": -2.0,
                        "take_profit_pct": 4.0,
                        "rationale": "Oversold bounce with high volume",
                    }
                ],
            }
        ]
    if global_rules is None:
        global_rules = [
            {
                "condition": "portfolio_pnl_pct < -2.0",
                "action": "REDUCE_ALL",
                "rationale": "Near circuit breaker",
            }
        ]
    return json.dumps({"market_outlook": outlook, "global_rules": global_rules, "stocks": stocks})


def _make_planner(
    gemini_response: str = "",
    token_count: int = 200,
    context_data: dict | None = None,
    scorecard_data: dict | None = None,
    scorecard_map: dict[tuple[str, str, str], dict | None] | None = None,
    settings_overrides: dict | None = None,
) -> PreMarketPlanner:
    """Create a PreMarketPlanner with mocked dependencies."""
    if not gemini_response:
        gemini_response = _gemini_response_json()

    # Mock GeminiClient
    gemini = AsyncMock()
    gemini.decide = AsyncMock(
        return_value=TradeDecision(
            action="HOLD",
            confidence=0,
            rationale=gemini_response,
            token_count=token_count,
        )
    )

    # Mock ContextStore
    store = MagicMock()
    if scorecard_map is not None:
        store.get_context = MagicMock(
            side_effect=lambda layer, timeframe, key: scorecard_map.get(
                (layer.value if hasattr(layer, "value") else layer, timeframe, key)
            )
        )
    else:
        store.get_context = MagicMock(return_value=scorecard_data)

    # Mock ContextSelector
    selector = MagicMock()
    selector.select_layers = MagicMock(
        return_value=[ContextLayer.L7_REALTIME, ContextLayer.L6_DAILY]
    )
    selector.get_context_data = MagicMock(return_value=context_data or {})

    settings_kwargs = {
        "KIS_APP_KEY": "test",
        "KIS_APP_SECRET": "test",
        "KIS_ACCOUNT_NO": "12345678-01",
        "GEMINI_API_KEY": "test",
    }
    if settings_overrides:
        settings_kwargs.update(settings_overrides)

    settings = Settings(
        **settings_kwargs,
    )

    return PreMarketPlanner(gemini, store, selector, settings)


# ---------------------------------------------------------------------------
# generate_playbook
# ---------------------------------------------------------------------------


class TestGeneratePlaybook:
    @pytest.mark.asyncio
    async def test_basic_generation(self) -> None:
        planner = _make_planner()
        candidates = [_candidate()]

        pb = await planner.generate_playbook("KR", candidates, today=date(2026, 2, 8))

        assert isinstance(pb, DayPlaybook)
        assert pb.market == "KR"
        assert pb.stock_count == 1
        assert pb.scenario_count == 1
        assert pb.market_outlook == MarketOutlook.NEUTRAL_TO_BULLISH
        assert pb.token_count == 200

    @pytest.mark.asyncio
    async def test_empty_candidates_returns_empty_playbook(self) -> None:
        planner = _make_planner()

        pb = await planner.generate_playbook("KR", [], today=date(2026, 2, 8))

        assert pb.stock_count == 0
        assert pb.scenario_count == 0
        assert pb.market_outlook == MarketOutlook.NEUTRAL

    @pytest.mark.asyncio
    async def test_gemini_failure_returns_smart_fallback(self) -> None:
        planner = _make_planner()
        planner._gemini.decide = AsyncMock(side_effect=RuntimeError("API timeout"))
        # oversold candidate (signal="oversold", rsi=28.5)
        candidates = [_candidate()]

        pb = await planner.generate_playbook("KR", candidates, today=date(2026, 2, 8))

        assert pb.default_action == ScenarioAction.HOLD
        # Smart fallback uses NEUTRAL outlook (not NEUTRAL_TO_BEARISH)
        assert pb.market_outlook == MarketOutlook.NEUTRAL
        assert pb.stock_count == 1
        # Oversold candidate → first scenario is BUY, second is SELL stop-loss
        scenarios = pb.stock_playbooks[0].scenarios
        assert scenarios[0].action == ScenarioAction.BUY
        assert scenarios[0].condition.rsi_below == 30
        assert scenarios[1].action == ScenarioAction.SELL

    @pytest.mark.asyncio
    async def test_gemini_failure_empty_when_defensive_disabled(self) -> None:
        planner = _make_planner()
        planner._settings.DEFENSIVE_PLAYBOOK_ON_FAILURE = False
        planner._gemini.decide = AsyncMock(side_effect=RuntimeError("fail"))
        candidates = [_candidate()]

        pb = await planner.generate_playbook("KR", candidates, today=date(2026, 2, 8))

        assert pb.stock_count == 0

    @pytest.mark.asyncio
    async def test_multiple_candidates(self) -> None:
        stocks = [
            {
                "stock_code": "005930",
                "scenarios": [
                    {
                        "condition": {"rsi_below": 30},
                        "action": "BUY",
                        "confidence": 85,
                        "rationale": "Oversold",
                    }
                ],
            },
            {
                "stock_code": "AAPL",
                "scenarios": [
                    {
                        "condition": {"rsi_above": 75},
                        "action": "SELL",
                        "confidence": 80,
                        "rationale": "Overbought",
                    }
                ],
            },
        ]
        planner = _make_planner(gemini_response=_gemini_response_json(stocks=stocks))
        candidates = [_candidate(), _candidate(code="AAPL", name="Apple")]

        pb = await planner.generate_playbook("US", candidates, today=date(2026, 2, 8))

        assert pb.stock_count == 2
        codes = [sp.stock_code for sp in pb.stock_playbooks]
        assert "005930" in codes
        assert "AAPL" in codes

    @pytest.mark.asyncio
    async def test_unknown_stock_in_response_skipped(self) -> None:
        stocks = [
            {
                "stock_code": "005930",
                "scenarios": [
                    {
                        "condition": {"rsi_below": 30},
                        "action": "BUY",
                        "confidence": 85,
                        "rationale": "ok",
                    }
                ],
            },
            {
                "stock_code": "UNKNOWN",
                "scenarios": [
                    {
                        "condition": {"rsi_below": 20},
                        "action": "BUY",
                        "confidence": 90,
                        "rationale": "bad",
                    }
                ],
            },
        ]
        planner = _make_planner(gemini_response=_gemini_response_json(stocks=stocks))
        candidates = [_candidate()]  # Only 005930

        pb = await planner.generate_playbook("KR", candidates, today=date(2026, 2, 8))

        assert pb.stock_count == 1
        assert pb.stock_playbooks[0].stock_code == "005930"

    @pytest.mark.asyncio
    async def test_global_rules_parsed(self) -> None:
        planner = _make_planner()
        candidates = [_candidate()]

        pb = await planner.generate_playbook("KR", candidates, today=date(2026, 2, 8))

        assert len(pb.global_rules) == 1
        assert pb.global_rules[0].action == ScenarioAction.REDUCE_ALL

    @pytest.mark.asyncio
    async def test_token_count_from_decision(self) -> None:
        planner = _make_planner(token_count=450)
        candidates = [_candidate()]

        pb = await planner.generate_playbook("KR", candidates, today=date(2026, 2, 8))

        assert pb.token_count == 450

    @pytest.mark.asyncio
    async def test_generate_playbook_uses_strategic_context_selector(self) -> None:
        planner = _make_planner()
        candidates = [_candidate()]

        await planner.generate_playbook("KR", candidates, today=date(2026, 2, 8))

        planner._context_selector.select_layers.assert_called_once_with(
            decision_type=DecisionType.STRATEGIC,
            include_realtime=True,
        )
        planner._context_selector.get_context_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_playbook_injects_self_and_cross_scorecards(self) -> None:
        scorecard_map = {
            (ContextLayer.L6_DAILY.value, "2026-02-07", "scorecard_KR"): {
                "total_pnl": -1.0,
                "win_rate": 40,
                "lessons": ["Tighten entries"],
            },
            (ContextLayer.L6_DAILY.value, "2026-02-07", "scorecard_US"): {
                "total_pnl": 1.5,
                "win_rate": 62,
                "index_change_pct": 0.9,
                "lessons": ["Follow momentum"],
            },
        }
        planner = _make_planner(scorecard_map=scorecard_map)

        await planner.generate_playbook("KR", [_candidate()], today=date(2026, 2, 8))

        call_market_data = planner._gemini.decide.call_args.args[0]
        prompt = call_market_data["prompt_override"]
        assert "My Market Previous Day (KR)" in prompt
        assert "Other Market (US)" in prompt

    @pytest.mark.asyncio
    async def test_generate_playbook_blocks_buy_when_recent_scorecard_guard_is_active(self) -> None:
        stocks = [
            {
                "stock_code": "005930",
                "scenarios": [
                    {
                        "condition": {"rsi_below": 30},
                        "action": "BUY",
                        "confidence": 85,
                        "rationale": "oversold entry",
                    },
                    {
                        "condition": {"price_change_pct_below": -3.0},
                        "action": "SELL",
                        "confidence": 90,
                        "rationale": "stop-loss",
                    },
                ],
            }
        ]
        scorecard_map = {
            (ContextLayer.L6_DAILY.value, "2026-02-07", "scorecard_KR"): {
                "total_pnl": -1500.0,
                "win_rate": 20.0,
            },
            (ContextLayer.L6_DAILY.value, "2026-02-06", "scorecard_KR"): {
                "total_pnl": -900.0,
                "win_rate": 25.0,
            },
            (ContextLayer.L6_DAILY.value, "2026-02-05", "scorecard_KR"): {
                "total_pnl": -700.0,
                "win_rate": 0.0,
            },
            (ContextLayer.L6_DAILY.value, "2026-02-07", "scorecard_US"): {
                "total_pnl": 100.0,
                "win_rate": 55.0,
                "index_change_pct": 0.5,
            },
        }
        planner = _make_planner(
            gemini_response=_gemini_response_json(stocks=stocks),
            scorecard_map=scorecard_map,
            settings_overrides={
                "SCORECARD_BUY_GUARD_LOOKBACK_DAYS": 3,
                "SCORECARD_BUY_GUARD_MAX_CUMULATIVE_PNL": -1000.0,
                "SCORECARD_BUY_GUARD_MIN_WIN_RATE": 30.0,
                "SCORECARD_BUY_GUARD_MAX_CONSECUTIVE_LOSS_DAYS": 2,
                "SCORECARD_BUY_GUARD_ACTION": "block_buy",
            },
        )

        pb = await planner.generate_playbook("KR", [_candidate()], today=date(2026, 2, 8))

        assert pb.stock_count == 1
        scenarios = pb.stock_playbooks[0].scenarios
        assert [scenario.action for scenario in scenarios] == [ScenarioAction.SELL]

    @pytest.mark.asyncio
    async def test_generate_playbook_downgrades_to_defensive_mode_when_guard_requests_it(
        self,
    ) -> None:
        scorecard_map = {
            (ContextLayer.L6_DAILY.value, "2026-02-07", "scorecard_KR"): {
                "total_pnl": -1500.0,
                "win_rate": 20.0,
            },
            (ContextLayer.L6_DAILY.value, "2026-02-06", "scorecard_KR"): {
                "total_pnl": -900.0,
                "win_rate": 25.0,
            },
            (ContextLayer.L6_DAILY.value, "2026-02-05", "scorecard_KR"): {
                "total_pnl": -700.0,
                "win_rate": 0.0,
            },
            (ContextLayer.L6_DAILY.value, "2026-02-07", "scorecard_US"): {
                "total_pnl": 100.0,
                "win_rate": 55.0,
                "index_change_pct": 0.5,
            },
        }
        planner = _make_planner(
            scorecard_map=scorecard_map,
            settings_overrides={
                "SCORECARD_BUY_GUARD_LOOKBACK_DAYS": 3,
                "SCORECARD_BUY_GUARD_MAX_CUMULATIVE_PNL": -1000.0,
                "SCORECARD_BUY_GUARD_MIN_WIN_RATE": 30.0,
                "SCORECARD_BUY_GUARD_MAX_CONSECUTIVE_LOSS_DAYS": 2,
                "SCORECARD_BUY_GUARD_ACTION": "defensive",
            },
        )

        pb = await planner.generate_playbook("KR", [_candidate()], today=date(2026, 2, 8))

        assert pb.market_outlook == MarketOutlook.NEUTRAL_TO_BEARISH
        assert any(rule.action == ScenarioAction.REDUCE_ALL for rule in pb.global_rules)
        assert pb.stock_count == 0

    @pytest.mark.asyncio
    async def test_generate_playbook_injects_recent_scorecard_guard_prompt(self) -> None:
        scorecard_map = {
            (ContextLayer.L6_DAILY.value, "2026-02-07", "scorecard_KR"): {
                "total_pnl": -1500.0,
                "win_rate": 20.0,
            },
            (ContextLayer.L6_DAILY.value, "2026-02-06", "scorecard_KR"): {
                "total_pnl": -900.0,
                "win_rate": 25.0,
            },
            (ContextLayer.L6_DAILY.value, "2026-02-05", "scorecard_KR"): {
                "total_pnl": -700.0,
                "win_rate": 0.0,
            },
            (ContextLayer.L6_DAILY.value, "2026-02-07", "scorecard_US"): {
                "total_pnl": 100.0,
                "win_rate": 55.0,
                "index_change_pct": 0.5,
            },
        }
        planner = _make_planner(
            scorecard_map=scorecard_map,
            settings_overrides={
                "SCORECARD_BUY_GUARD_LOOKBACK_DAYS": 3,
                "SCORECARD_BUY_GUARD_MAX_CUMULATIVE_PNL": -1000.0,
                "SCORECARD_BUY_GUARD_MIN_WIN_RATE": 30.0,
                "SCORECARD_BUY_GUARD_MAX_CONSECUTIVE_LOSS_DAYS": 2,
                "SCORECARD_BUY_GUARD_ACTION": "block_buy",
            },
        )

        captured_prompts: list[str] = []
        original_decide = planner._gemini.decide

        async def capture_and_call(data: dict) -> TradeDecision:
            captured_prompts.append(data.get("prompt_override", ""))
            return await original_decide(data)

        planner._gemini.decide = capture_and_call  # type: ignore[method-assign]

        await planner.generate_playbook("KR", [_candidate()], today=date(2026, 2, 8))

        assert len(captured_prompts) == 1
        assert "Recent Self-Market Guard" in captured_prompts[0]
        assert "Guard Status: ACTIVE" in captured_prompts[0]
        assert "do not emit new BUY scenarios" in captured_prompts[0]

    @pytest.mark.asyncio
    async def test_generate_playbook_preserves_buy_when_recent_scorecard_guard_is_inactive(
        self,
    ) -> None:
        scorecard_map = {
            (ContextLayer.L6_DAILY.value, "2026-02-07", "scorecard_KR"): {
                "total_pnl": 500.0,
                "win_rate": 75.0,
            },
            (ContextLayer.L6_DAILY.value, "2026-02-06", "scorecard_KR"): {
                "total_pnl": 200.0,
                "win_rate": 60.0,
            },
            (ContextLayer.L6_DAILY.value, "2026-02-05", "scorecard_KR"): {
                "total_pnl": -50.0,
                "win_rate": 50.0,
            },
            (ContextLayer.L6_DAILY.value, "2026-02-07", "scorecard_US"): {
                "total_pnl": 100.0,
                "win_rate": 55.0,
                "index_change_pct": 0.5,
            },
        }
        planner = _make_planner(
            scorecard_map=scorecard_map,
            settings_overrides={
                "SCORECARD_BUY_GUARD_LOOKBACK_DAYS": 3,
                "SCORECARD_BUY_GUARD_MAX_CUMULATIVE_PNL": -1000.0,
                "SCORECARD_BUY_GUARD_MIN_WIN_RATE": 30.0,
                "SCORECARD_BUY_GUARD_MAX_CONSECUTIVE_LOSS_DAYS": 2,
                "SCORECARD_BUY_GUARD_ACTION": "block_buy",
            },
        )

        pb = await planner.generate_playbook("KR", [_candidate()], today=date(2026, 2, 8))

        scenarios = pb.stock_playbooks[0].scenarios
        assert [scenario.action for scenario in scenarios] == [ScenarioAction.BUY]

    @pytest.mark.asyncio
    async def test_generate_playbook_applies_recent_scorecard_guard_to_fallback_playbook(
        self,
    ) -> None:
        scorecard_map = {
            (ContextLayer.L6_DAILY.value, "2026-02-07", "scorecard_KR"): {
                "total_pnl": -1500.0,
                "win_rate": 20.0,
            },
            (ContextLayer.L6_DAILY.value, "2026-02-06", "scorecard_KR"): {
                "total_pnl": -900.0,
                "win_rate": 25.0,
            },
            (ContextLayer.L6_DAILY.value, "2026-02-05", "scorecard_KR"): {
                "total_pnl": -700.0,
                "win_rate": 0.0,
            },
        }
        planner = _make_planner(
            scorecard_map=scorecard_map,
            settings_overrides={
                "SCORECARD_BUY_GUARD_LOOKBACK_DAYS": 3,
                "SCORECARD_BUY_GUARD_MAX_CUMULATIVE_PNL": -1000.0,
                "SCORECARD_BUY_GUARD_MIN_WIN_RATE": 30.0,
                "SCORECARD_BUY_GUARD_MAX_CONSECUTIVE_LOSS_DAYS": 2,
                "SCORECARD_BUY_GUARD_ACTION": "block_buy",
            },
        )
        planner._gemini.decide = AsyncMock(side_effect=RuntimeError("API timeout"))

        pb = await planner.generate_playbook("KR", [_candidate()], today=date(2026, 2, 8))

        scenarios = pb.stock_playbooks[0].scenarios
        assert [scenario.action for scenario in scenarios] == [ScenarioAction.SELL]

    @pytest.mark.asyncio
    async def test_generate_playbook_gracefully_skips_guard_when_scorecard_load_fails(
        self,
    ) -> None:
        planner = _make_planner(
            settings_overrides={
                "SCORECARD_BUY_GUARD_LOOKBACK_DAYS": 3,
                "SCORECARD_BUY_GUARD_MAX_CUMULATIVE_PNL": -1000.0,
            }
        )

        planner._build_recent_self_market_guard = MagicMock(
            side_effect=RuntimeError("context store unavailable")
        )

        pb = await planner.generate_playbook("KR", [_candidate()], today=date(2026, 2, 8))

        scenarios = pb.stock_playbooks[0].scenarios
        assert [scenario.action for scenario in scenarios] == [ScenarioAction.BUY]


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------


class TestParseResponse:
    def test_parse_full_response(self) -> None:
        planner = _make_planner()
        response = _gemini_response_json(outlook="bearish")
        candidates = [_candidate()]

        pb = planner._parse_response(response, date(2026, 2, 8), "KR", candidates, None)

        assert pb.market_outlook == MarketOutlook.BEARISH
        assert pb.stock_count == 1
        assert pb.stock_playbooks[0].scenarios[0].confidence == 85

    def test_parse_with_markdown_fences(self) -> None:
        planner = _make_planner()
        response = f"```json\n{_gemini_response_json()}\n```"
        candidates = [_candidate()]

        pb = planner._parse_response(response, date(2026, 2, 8), "KR", candidates, None)

        assert pb.stock_count == 1

    def test_parse_unknown_outlook_defaults_neutral(self) -> None:
        planner = _make_planner()
        response = _gemini_response_json(outlook="super_bullish")
        candidates = [_candidate()]

        pb = planner._parse_response(response, date(2026, 2, 8), "KR", candidates, None)

        assert pb.market_outlook == MarketOutlook.NEUTRAL

    def test_parse_scenario_with_all_condition_fields(self) -> None:
        planner = _make_planner()
        stocks = [
            {
                "stock_code": "005930",
                "scenarios": [
                    {
                        "condition": {
                            "rsi_below": 25,
                            "volume_ratio_above": 3.0,
                            "price_change_pct_below": -2.0,
                        },
                        "action": "BUY",
                        "confidence": 92,
                        "allocation_pct": 20.0,
                        "stop_loss_pct": -3.0,
                        "take_profit_pct": 5.0,
                        "rationale": "Multi-condition entry",
                    }
                ],
            }
        ]
        response = _gemini_response_json(stocks=stocks)
        candidates = [_candidate()]

        pb = planner._parse_response(response, date(2026, 2, 8), "KR", candidates, None)

        sc = pb.stock_playbooks[0].scenarios[0]
        assert sc.condition.rsi_below == 25
        assert sc.condition.volume_ratio_above == 3.0
        assert sc.condition.price_change_pct_below == -2.0
        assert sc.allocation_pct == 20.0
        assert sc.stop_loss_pct == -3.0
        assert sc.take_profit_pct == 5.0

    def test_parse_empty_condition_scenario_skipped(self) -> None:
        planner = _make_planner()
        stocks = [
            {
                "stock_code": "005930",
                "scenarios": [
                    {
                        "condition": {},
                        "action": "BUY",
                        "confidence": 85,
                        "rationale": "No conditions",
                    },
                    {
                        "condition": {"rsi_below": 30},
                        "action": "BUY",
                        "confidence": 80,
                        "rationale": "Valid",
                    },
                ],
            }
        ]
        response = _gemini_response_json(stocks=stocks)
        candidates = [_candidate()]

        pb = planner._parse_response(response, date(2026, 2, 8), "KR", candidates, None)

        # Empty condition scenario skipped, valid one kept
        assert pb.stock_count == 1
        assert pb.stock_playbooks[0].scenarios[0].confidence == 80

    def test_parse_max_scenarios_enforced(self) -> None:
        planner = _make_planner()
        # Settings default MAX_SCENARIOS_PER_STOCK = 5
        scenarios = [
            {
                "condition": {"rsi_below": 20 + i},
                "action": "BUY",
                "confidence": 80 + i,
                "rationale": f"Scenario {i}",
            }
            for i in range(8)  # 8 scenarios, should be capped to 5
        ]
        stocks = [{"stock_code": "005930", "scenarios": scenarios}]
        response = _gemini_response_json(stocks=stocks)
        candidates = [_candidate()]

        pb = planner._parse_response(response, date(2026, 2, 8), "KR", candidates, None)

        assert len(pb.stock_playbooks[0].scenarios) == 5

    def test_parse_invalid_json_raises(self) -> None:
        planner = _make_planner()
        candidates = [_candidate()]

        with pytest.raises(json.JSONDecodeError):
            planner._parse_response("not json at all", date(2026, 2, 8), "KR", candidates, None)

    def test_parse_cross_market_preserved(self) -> None:
        planner = _make_planner()
        response = _gemini_response_json()
        candidates = [_candidate()]
        cross = CrossMarketContext(market="US", date="2026-02-07", total_pnl=1.5, win_rate=60)

        pb = planner._parse_response(response, date(2026, 2, 8), "KR", candidates, cross)

        assert pb.cross_market is not None
        assert pb.cross_market.market == "US"
        assert pb.cross_market.total_pnl == 1.5


# ---------------------------------------------------------------------------
# _raw_pnl_unit_for_market
# ---------------------------------------------------------------------------


class TestRawPnlUnitForMarket:
    def test_unsupported_market_returns_explicit_fallback_and_warns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("WARNING"):
            unit = _raw_pnl_unit_for_market("JP")

        assert unit == "UNKNOWN_CURRENCY"
        assert "Unsupported market JP for raw PnL unit mapping" in caplog.text


# ---------------------------------------------------------------------------
# build_cross_market_context
# ---------------------------------------------------------------------------


class TestBuildCrossMarketContext:
    def test_kr_reads_us_scorecard(self) -> None:
        scorecard = {
            "total_pnl": 2.5,
            "win_rate": 65,
            "index_change_pct": 0.8,
            "lessons": ["Stay patient"],
        }
        planner = _make_planner(scorecard_data=scorecard)

        ctx = planner.build_cross_market_context("KR", today=date(2026, 2, 8))

        assert ctx is not None
        assert ctx.market == "US"
        assert ctx.total_pnl == 2.5
        assert ctx.win_rate == 65
        assert "Stay patient" in ctx.lessons

        # Verify it queried scorecard_US
        planner._context_store.get_context.assert_called_once_with(
            ContextLayer.L6_DAILY, "2026-02-07", "scorecard_US"
        )
        assert ctx.date == "2026-02-07"

    def test_us_reads_kr_scorecard(self) -> None:
        scorecard = {"total_pnl": -1.0, "win_rate": 40, "index_change_pct": -0.5}
        planner = _make_planner(scorecard_data=scorecard)

        ctx = planner.build_cross_market_context("US", today=date(2026, 2, 8))

        assert ctx is not None
        assert ctx.market == "KR"
        assert ctx.total_pnl == -1.0

        planner._context_store.get_context.assert_called_once_with(
            ContextLayer.L6_DAILY, "2026-02-08", "scorecard_KR"
        )

    def test_no_scorecard_returns_none(self) -> None:
        planner = _make_planner(scorecard_data=None)

        ctx = planner.build_cross_market_context("KR", today=date(2026, 2, 8))

        assert ctx is None

    def test_invalid_scorecard_returns_none(self) -> None:
        planner = _make_planner(scorecard_data="not a dict and not json")

        ctx = planner.build_cross_market_context("KR", today=date(2026, 2, 8))

        assert ctx is None


# ---------------------------------------------------------------------------
# build_self_market_scorecard
# ---------------------------------------------------------------------------


class TestBuildSelfMarketScorecard:
    def test_reads_previous_day_scorecard(self) -> None:
        scorecard = {"total_pnl": -1.2, "win_rate": 45, "lessons": ["Reduce overtrading"]}
        planner = _make_planner(scorecard_data=scorecard)

        data = planner.build_self_market_scorecard("KR", today=date(2026, 2, 8))

        assert data is not None
        assert data["date"] == "2026-02-07"
        assert data["total_pnl"] == -1.2
        assert data["win_rate"] == 45
        assert "Reduce overtrading" in data["lessons"]
        planner._context_store.get_context.assert_called_once_with(
            ContextLayer.L6_DAILY, "2026-02-07", "scorecard_KR"
        )

    def test_missing_scorecard_returns_none(self) -> None:
        planner = _make_planner(scorecard_data=None)
        assert planner.build_self_market_scorecard("US", today=date(2026, 2, 8)) is None


# ---------------------------------------------------------------------------
# _build_prompt
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_prompt_contains_candidates(self) -> None:
        planner = _make_planner()
        candidates = [_candidate(code="005930", name="Samsung")]

        prompt = planner._build_prompt("KR", candidates, {}, None, None)

        assert "005930" in prompt
        assert "Samsung" in prompt
        assert "RSI=" in prompt
        assert "volume_ratio=" in prompt

    def test_prompt_contains_cross_market(self) -> None:
        planner = _make_planner()
        cross = CrossMarketContext(
            market="US",
            date="2026-02-07",
            total_pnl=1.5,
            win_rate=60,
            index_change_pct=0.8,
            lessons=["Cut losses early"],
        )

        prompt = planner._build_prompt("KR", [_candidate()], {}, None, cross)

        assert "Other Market (US)" in prompt
        assert "Realized PnL (USD, raw): +1.50" in prompt
        assert "+1.50%" not in prompt
        assert "Cut losses early" in prompt

    def test_prompt_contains_context_data(self) -> None:
        planner = _make_planner()
        context = {"L6_DAILY": {"win_rate": 0.65, "total_pnl": 2.5}}

        prompt = planner._build_prompt("KR", [_candidate()], context, None, None)

        assert "Strategic Context" in prompt
        assert "L6_DAILY" in prompt
        assert "win_rate" in prompt

    def test_prompt_contains_max_scenarios(self) -> None:
        planner = _make_planner()
        prompt = planner._build_prompt("KR", [_candidate()], {}, None, None)

        assert f"Max {planner._settings.MAX_SCENARIOS_PER_STOCK} scenarios" in prompt

    def test_prompt_market_name(self) -> None:
        planner = _make_planner()
        prompt = planner._build_prompt("US", [_candidate()], {}, None, None)
        assert "US market" in prompt

    def test_prompt_contains_self_market_scorecard(self) -> None:
        planner = _make_planner()
        self_scorecard = {
            "date": "2026-02-07",
            "total_pnl": -0.8,
            "win_rate": 45.0,
            "lessons": ["Avoid midday entries"],
        }
        prompt = planner._build_prompt("KR", [_candidate()], {}, self_scorecard, None)

        assert "My Market Previous Day (KR)" in prompt
        assert "2026-02-07" in prompt
        assert "Realized PnL (KRW, raw): -0.80" in prompt
        assert "-0.80%" not in prompt
        assert "Avoid midday entries" in prompt

    def test_prompt_uses_explicit_fallback_for_unsupported_market(self) -> None:
        planner = _make_planner()
        self_scorecard = {
            "date": "2026-02-07",
            "total_pnl": -1250.0,
            "win_rate": 45.0,
            "lessons": ["Expand unit mapping before launch"],
        }

        prompt = planner._build_prompt("JP", [_candidate()], {}, self_scorecard, None)

        assert "My Market Previous Day (JP)" in prompt
        assert "Realized PnL (UNKNOWN_CURRENCY, raw): -1250.00" in prompt
        assert "Realized PnL (JP, raw): -1250.00" not in prompt


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------


class TestExtractJson:
    def test_plain_json(self) -> None:
        assert PreMarketPlanner._extract_json('{"a": 1}') == '{"a": 1}'

    def test_with_json_fence(self) -> None:
        text = '```json\n{"a": 1}\n```'
        assert PreMarketPlanner._extract_json(text) == '{"a": 1}'

    def test_with_plain_fence(self) -> None:
        text = '```\n{"a": 1}\n```'
        assert PreMarketPlanner._extract_json(text) == '{"a": 1}'

    def test_with_whitespace(self) -> None:
        text = '  \n  {"a": 1}  \n  '
        assert PreMarketPlanner._extract_json(text) == '{"a": 1}'


# ---------------------------------------------------------------------------
# Defensive playbook
# ---------------------------------------------------------------------------


class TestDefensivePlaybook:
    def test_defensive_has_stop_loss(self) -> None:
        candidates = [_candidate(code="005930"), _candidate(code="AAPL")]
        pb = PreMarketPlanner._defensive_playbook(date(2026, 2, 8), "KR", candidates)

        assert pb.default_action == ScenarioAction.HOLD
        assert pb.market_outlook == MarketOutlook.NEUTRAL_TO_BEARISH
        assert pb.stock_count == 2
        for sp in pb.stock_playbooks:
            assert sp.scenarios[0].action == ScenarioAction.SELL
            assert sp.scenarios[0].stop_loss_pct == -3.0

    def test_defensive_has_global_rule(self) -> None:
        pb = PreMarketPlanner._defensive_playbook(date(2026, 2, 8), "KR", [_candidate()])

        assert len(pb.global_rules) == 1
        assert pb.global_rules[0].action == ScenarioAction.REDUCE_ALL

    def test_empty_playbook(self) -> None:
        pb = PreMarketPlanner._empty_playbook(date(2026, 2, 8), "US")

        assert pb.stock_count == 0
        assert pb.market == "US"
        assert pb.market_outlook == MarketOutlook.NEUTRAL


# ---------------------------------------------------------------------------
# Smart fallback playbook
# ---------------------------------------------------------------------------


class TestSmartFallbackPlaybook:
    """Tests for _smart_fallback_playbook — rule-based BUY/SELL on Gemini failure."""

    def _make_settings(self) -> Settings:
        return Settings(
            KIS_APP_KEY="test",
            KIS_APP_SECRET="test",
            KIS_ACCOUNT_NO="12345678-01",
            GEMINI_API_KEY="test",
            RSI_OVERSOLD_THRESHOLD=30,
            VOL_MULTIPLIER=2.0,
        )

    def test_momentum_candidate_gets_buy_on_volume(self) -> None:
        candidates = [_candidate(code="CHOW", signal="momentum", volume_ratio=13.64, rsi=100.0)]
        settings = self._make_settings()

        pb = PreMarketPlanner._smart_fallback_playbook(
            date(2026, 2, 17), "US_AMEX", candidates, settings
        )

        assert pb.stock_count == 1
        sp = pb.stock_playbooks[0]
        assert sp.stock_code == "CHOW"
        # First scenario: BUY with volume_ratio_above
        buy_sc = sp.scenarios[0]
        assert buy_sc.action == ScenarioAction.BUY
        assert buy_sc.condition.volume_ratio_above == 2.0
        assert buy_sc.condition.rsi_below is None
        assert buy_sc.confidence == 80
        # Second scenario: stop-loss SELL
        sell_sc = sp.scenarios[1]
        assert sell_sc.action == ScenarioAction.SELL
        assert sell_sc.condition.price_change_pct_below == -3.0

    def test_oversold_candidate_gets_buy_on_rsi(self) -> None:
        candidates = [_candidate(code="005930", signal="oversold", rsi=22.0, volume_ratio=3.5)]
        settings = self._make_settings()

        pb = PreMarketPlanner._smart_fallback_playbook(
            date(2026, 2, 17), "KR", candidates, settings
        )

        sp = pb.stock_playbooks[0]
        buy_sc = sp.scenarios[0]
        assert buy_sc.action == ScenarioAction.BUY
        assert buy_sc.condition.rsi_below == 30
        assert buy_sc.condition.volume_ratio_above is None

    def test_all_candidates_have_stop_loss_sell(self) -> None:
        candidates = [
            _candidate(code="AAA", signal="momentum", volume_ratio=5.0),
            _candidate(code="BBB", signal="oversold", rsi=25.0),
        ]
        settings = self._make_settings()

        pb = PreMarketPlanner._smart_fallback_playbook(
            date(2026, 2, 17), "US_NASDAQ", candidates, settings
        )

        assert pb.stock_count == 2
        for sp in pb.stock_playbooks:
            sell_scenarios = [s for s in sp.scenarios if s.action == ScenarioAction.SELL]
            assert len(sell_scenarios) == 1
            assert sell_scenarios[0].condition.price_change_pct_below == -3.0
            assert sell_scenarios[0].condition.price_change_pct_below == -3.0

    def test_market_outlook_is_neutral(self) -> None:
        candidates = [_candidate(signal="momentum", volume_ratio=5.0)]
        settings = self._make_settings()

        pb = PreMarketPlanner._smart_fallback_playbook(
            date(2026, 2, 17), "US_AMEX", candidates, settings
        )

        assert pb.market_outlook == MarketOutlook.NEUTRAL

    def test_default_action_is_hold(self) -> None:
        candidates = [_candidate(signal="momentum", volume_ratio=5.0)]
        settings = self._make_settings()

        pb = PreMarketPlanner._smart_fallback_playbook(
            date(2026, 2, 17), "US_AMEX", candidates, settings
        )

        assert pb.default_action == ScenarioAction.HOLD

    def test_has_global_reduce_all_rule(self) -> None:
        candidates = [_candidate(signal="momentum", volume_ratio=5.0)]
        settings = self._make_settings()

        pb = PreMarketPlanner._smart_fallback_playbook(
            date(2026, 2, 17), "US_AMEX", candidates, settings
        )

        assert len(pb.global_rules) == 1
        rule = pb.global_rules[0]
        assert rule.action == ScenarioAction.REDUCE_ALL
        assert "portfolio_pnl_pct" in rule.condition

    def test_empty_candidates_returns_empty_playbook(self) -> None:
        settings = self._make_settings()

        pb = PreMarketPlanner._smart_fallback_playbook(date(2026, 2, 17), "US_AMEX", [], settings)

        assert pb.stock_count == 0

    def test_vol_multiplier_applied_from_settings(self) -> None:
        """VOL_MULTIPLIER=3.0 should set volume_ratio_above=3.0 for momentum."""
        candidates = [_candidate(signal="momentum", volume_ratio=5.0)]
        settings = self._make_settings()
        settings = settings.model_copy(update={"VOL_MULTIPLIER": 3.0})

        pb = PreMarketPlanner._smart_fallback_playbook(
            date(2026, 2, 17), "US_AMEX", candidates, settings
        )

        buy_sc = pb.stock_playbooks[0].scenarios[0]
        assert buy_sc.condition.volume_ratio_above == 3.0

    def test_rsi_oversold_threshold_applied_from_settings(self) -> None:
        """RSI_OVERSOLD_THRESHOLD=25 should set rsi_below=25 for oversold."""
        candidates = [_candidate(signal="oversold", rsi=22.0)]
        settings = self._make_settings()
        settings = settings.model_copy(update={"RSI_OVERSOLD_THRESHOLD": 25})

        pb = PreMarketPlanner._smart_fallback_playbook(
            date(2026, 2, 17), "KR", candidates, settings
        )

        buy_sc = pb.stock_playbooks[0].scenarios[0]
        assert buy_sc.condition.rsi_below == 25

    @pytest.mark.asyncio
    async def test_generate_playbook_uses_smart_fallback_on_gemini_error(self) -> None:
        """generate_playbook() should use smart fallback (not defensive) on API failure."""
        planner = _make_planner()
        planner._gemini.decide = AsyncMock(side_effect=ConnectionError("429 quota exceeded"))
        # momentum candidate
        candidates = [_candidate(code="CHOW", signal="momentum", volume_ratio=13.64, rsi=100.0)]

        pb = await planner.generate_playbook("US_AMEX", candidates, today=date(2026, 2, 18))

        # Should NOT be all-SELL defensive; should have BUY for momentum
        assert pb.stock_count == 1
        buy_scenarios = [
            s for s in pb.stock_playbooks[0].scenarios if s.action == ScenarioAction.BUY
        ]
        assert len(buy_scenarios) == 1
        assert buy_scenarios[0].condition.volume_ratio_above == 2.0  # VOL_MULTIPLIER default


# ---------------------------------------------------------------------------
# Holdings in prompt (#170)
# ---------------------------------------------------------------------------


class TestHoldingsInPrompt:
    """Tests for current_holdings parameter in generate_playbook / _build_prompt."""

    def _make_holdings(self) -> list[dict]:
        return [
            {
                "stock_code": "005930",
                "name": "Samsung",
                "qty": 10,
                "entry_price": 71000.0,
                "unrealized_pnl_pct": 2.3,
                "holding_days": 3,
            }
        ]

    def test_build_prompt_includes_holdings_section(self) -> None:
        """Prompt should contain a Current Holdings section when holdings are given."""
        planner = _make_planner()
        candidates = [_candidate()]
        holdings = self._make_holdings()

        prompt = planner._build_prompt(
            "KR",
            candidates,
            context_data={},
            self_market_scorecard=None,
            cross_market=None,
            current_holdings=holdings,
        )

        assert "## Current Holdings" in prompt
        assert "005930" in prompt
        assert "+2.30%" in prompt
        assert "보유 3일" in prompt

    def test_build_prompt_no_holdings_omits_section(self) -> None:
        """Prompt should NOT contain a Current Holdings section when holdings=None."""
        planner = _make_planner()
        candidates = [_candidate()]

        prompt = planner._build_prompt(
            "KR",
            candidates,
            context_data={},
            self_market_scorecard=None,
            cross_market=None,
            current_holdings=None,
        )

        assert "## Current Holdings" not in prompt

    def test_build_prompt_empty_holdings_omits_section(self) -> None:
        """Empty list should also omit the holdings section."""
        planner = _make_planner()
        candidates = [_candidate()]

        prompt = planner._build_prompt(
            "KR",
            candidates,
            context_data={},
            self_market_scorecard=None,
            cross_market=None,
            current_holdings=[],
        )

        assert "## Current Holdings" not in prompt

    def test_build_prompt_holdings_instruction_included(self) -> None:
        """Prompt should include instruction to generate scenarios for held stocks."""
        planner = _make_planner()
        candidates = [_candidate()]
        holdings = self._make_holdings()

        prompt = planner._build_prompt(
            "KR",
            candidates,
            context_data={},
            self_market_scorecard=None,
            cross_market=None,
            current_holdings=holdings,
        )

        assert "005930" in prompt
        assert "SELL/HOLD" in prompt

    @pytest.mark.asyncio
    async def test_generate_playbook_passes_holdings_to_prompt(self) -> None:
        """generate_playbook should pass current_holdings through to the prompt."""
        planner = _make_planner()
        candidates = [_candidate()]
        holdings = self._make_holdings()

        # Capture the actual prompt sent to Gemini
        captured_prompts: list[str] = []
        original_decide = planner._gemini.decide

        async def capture_and_call(data: dict) -> TradeDecision:
            captured_prompts.append(data.get("prompt_override", ""))
            return await original_decide(data)

        planner._gemini.decide = capture_and_call  # type: ignore[method-assign]

        await planner.generate_playbook(
            "KR", candidates, today=date(2026, 2, 8), current_holdings=holdings
        )

        assert len(captured_prompts) == 1
        assert "## Current Holdings" in captured_prompts[0]
        assert "005930" in captured_prompts[0]

    @pytest.mark.asyncio
    async def test_holdings_stock_allowed_in_parse_response(self) -> None:
        """Holdings stocks not in candidates list should be accepted in the response."""
        holding_code = "000660"  # Not in candidates
        stocks = [
            {
                "stock_code": "005930",  # candidate
                "scenarios": [
                    {
                        "condition": {"rsi_below": 30},
                        "action": "BUY",
                        "confidence": 85,
                        "rationale": "oversold",
                    }
                ],
            },
            {
                "stock_code": holding_code,  # holding only
                "scenarios": [
                    {
                        "condition": {"price_change_pct_below": -2.0},
                        "action": "SELL",
                        "confidence": 90,
                        "rationale": "stop-loss",
                    }
                ],
            },
        ]
        planner = _make_planner(gemini_response=_gemini_response_json(stocks=stocks))
        candidates = [_candidate()]  # only 005930
        holdings = [
            {
                "stock_code": holding_code,
                "name": "SK Hynix",
                "qty": 5,
                "entry_price": 180000.0,
                "unrealized_pnl_pct": -1.5,
                "holding_days": 7,
            }
        ]

        pb = await planner.generate_playbook(
            "KR",
            candidates,
            today=date(2026, 2, 8),
            current_holdings=holdings,
        )

        codes = [sp.stock_code for sp in pb.stock_playbooks]
        assert "005930" in codes
        assert holding_code in codes
