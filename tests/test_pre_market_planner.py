"""Tests for PreMarketPlanner — Gemini prompt builder + response parser."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.analysis.smart_scanner import ScanCandidate
from src.brain.gemini_client import TradeDecision
from src.config import Settings
from src.context.store import ContextLayer
from src.strategy.models import (
    CrossMarketContext,
    DayPlaybook,
    MarketOutlook,
    PlaybookStatus,
    ScenarioAction,
)
from src.strategy.pre_market_planner import PreMarketPlanner


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
    return json.dumps(
        {"market_outlook": outlook, "global_rules": global_rules, "stocks": stocks}
    )


def _make_planner(
    gemini_response: str = "",
    token_count: int = 200,
    context_data: dict | None = None,
    scorecard_data: dict | None = None,
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
    store.get_context = MagicMock(return_value=scorecard_data)

    # Mock ContextSelector
    selector = MagicMock()
    selector.select_layers = MagicMock(return_value=[ContextLayer.L7_REALTIME, ContextLayer.L6_DAILY])
    selector.get_context_data = MagicMock(return_value=context_data or {})

    settings = Settings(
        KIS_APP_KEY="test",
        KIS_APP_SECRET="test",
        KIS_ACCOUNT_NUMBER="test",
        GEMINI_API_KEY="test",
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

        with patch("src.strategy.pre_market_planner.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 8)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            pb = await planner.generate_playbook("KR", candidates)

        assert isinstance(pb, DayPlaybook)
        assert pb.market == "KR"
        assert pb.stock_count == 1
        assert pb.scenario_count == 1
        assert pb.market_outlook == MarketOutlook.NEUTRAL_TO_BULLISH
        assert pb.token_count == 200

    @pytest.mark.asyncio
    async def test_empty_candidates_returns_empty_playbook(self) -> None:
        planner = _make_planner()

        with patch("src.strategy.pre_market_planner.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 8)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            pb = await planner.generate_playbook("KR", [])

        assert pb.stock_count == 0
        assert pb.scenario_count == 0
        assert pb.market_outlook == MarketOutlook.NEUTRAL

    @pytest.mark.asyncio
    async def test_gemini_failure_returns_defensive(self) -> None:
        planner = _make_planner()
        planner._gemini.decide = AsyncMock(side_effect=RuntimeError("API timeout"))
        candidates = [_candidate()]

        with patch("src.strategy.pre_market_planner.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 8)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            pb = await planner.generate_playbook("KR", candidates)

        assert pb.default_action == ScenarioAction.HOLD
        assert pb.market_outlook == MarketOutlook.NEUTRAL_TO_BEARISH
        assert pb.stock_count == 1
        # Defensive playbook has stop-loss scenarios
        assert pb.stock_playbooks[0].scenarios[0].action == ScenarioAction.SELL

    @pytest.mark.asyncio
    async def test_gemini_failure_empty_when_defensive_disabled(self) -> None:
        planner = _make_planner()
        planner._settings.DEFENSIVE_PLAYBOOK_ON_FAILURE = False
        planner._gemini.decide = AsyncMock(side_effect=RuntimeError("fail"))
        candidates = [_candidate()]

        with patch("src.strategy.pre_market_planner.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 8)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            pb = await planner.generate_playbook("KR", candidates)

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

        with patch("src.strategy.pre_market_planner.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 8)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            pb = await planner.generate_playbook("US", candidates)

        assert pb.stock_count == 2
        codes = [sp.stock_code for sp in pb.stock_playbooks]
        assert "005930" in codes
        assert "AAPL" in codes

    @pytest.mark.asyncio
    async def test_unknown_stock_in_response_skipped(self) -> None:
        stocks = [
            {
                "stock_code": "005930",
                "scenarios": [{"condition": {"rsi_below": 30}, "action": "BUY", "confidence": 85, "rationale": "ok"}],
            },
            {
                "stock_code": "UNKNOWN",
                "scenarios": [{"condition": {"rsi_below": 20}, "action": "BUY", "confidence": 90, "rationale": "bad"}],
            },
        ]
        planner = _make_planner(gemini_response=_gemini_response_json(stocks=stocks))
        candidates = [_candidate()]  # Only 005930

        with patch("src.strategy.pre_market_planner.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 8)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            pb = await planner.generate_playbook("KR", candidates)

        assert pb.stock_count == 1
        assert pb.stock_playbooks[0].stock_code == "005930"

    @pytest.mark.asyncio
    async def test_global_rules_parsed(self) -> None:
        planner = _make_planner()
        candidates = [_candidate()]

        with patch("src.strategy.pre_market_planner.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 8)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            pb = await planner.generate_playbook("KR", candidates)

        assert len(pb.global_rules) == 1
        assert pb.global_rules[0].action == ScenarioAction.REDUCE_ALL

    @pytest.mark.asyncio
    async def test_token_count_from_decision(self) -> None:
        planner = _make_planner(token_count=450)
        candidates = [_candidate()]

        with patch("src.strategy.pre_market_planner.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 8)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            pb = await planner.generate_playbook("KR", candidates)

        assert pb.token_count == 450


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
# build_cross_market_context
# ---------------------------------------------------------------------------


class TestBuildCrossMarketContext:
    def test_kr_reads_us_scorecard(self) -> None:
        scorecard = {"total_pnl": 2.5, "win_rate": 65, "index_change_pct": 0.8, "lessons": ["Stay patient"]}
        planner = _make_planner(scorecard_data=scorecard)

        with patch("src.strategy.pre_market_planner.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 8)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ctx = planner.build_cross_market_context("KR")

        assert ctx is not None
        assert ctx.market == "US"
        assert ctx.total_pnl == 2.5
        assert ctx.win_rate == 65
        assert "Stay patient" in ctx.lessons

        # Verify it queried scorecard_US
        planner._context_store.get_context.assert_called_once_with(
            ContextLayer.L6_DAILY, "2026-02-08", "scorecard_US"
        )

    def test_us_reads_kr_scorecard(self) -> None:
        scorecard = {"total_pnl": -1.0, "win_rate": 40, "index_change_pct": -0.5}
        planner = _make_planner(scorecard_data=scorecard)

        with patch("src.strategy.pre_market_planner.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 8)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ctx = planner.build_cross_market_context("US")

        assert ctx is not None
        assert ctx.market == "KR"
        assert ctx.total_pnl == -1.0

        planner._context_store.get_context.assert_called_once_with(
            ContextLayer.L6_DAILY, "2026-02-08", "scorecard_KR"
        )

    def test_no_scorecard_returns_none(self) -> None:
        planner = _make_planner(scorecard_data=None)

        with patch("src.strategy.pre_market_planner.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 8)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ctx = planner.build_cross_market_context("KR")

        assert ctx is None

    def test_invalid_scorecard_returns_none(self) -> None:
        planner = _make_planner(scorecard_data="not a dict and not json")

        with patch("src.strategy.pre_market_planner.date") as mock_date:
            mock_date.today.return_value = date(2026, 2, 8)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
            ctx = planner.build_cross_market_context("KR")

        assert ctx is None


# ---------------------------------------------------------------------------
# _build_prompt
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_prompt_contains_candidates(self) -> None:
        planner = _make_planner()
        candidates = [_candidate(code="005930", name="Samsung")]

        prompt = planner._build_prompt("KR", candidates, {}, None)

        assert "005930" in prompt
        assert "Samsung" in prompt
        assert "RSI=" in prompt
        assert "volume_ratio=" in prompt

    def test_prompt_contains_cross_market(self) -> None:
        planner = _make_planner()
        cross = CrossMarketContext(
            market="US", date="2026-02-07", total_pnl=1.5,
            win_rate=60, index_change_pct=0.8, lessons=["Cut losses early"],
        )

        prompt = planner._build_prompt("KR", [_candidate()], {}, cross)

        assert "Other Market (US)" in prompt
        assert "+1.50%" in prompt
        assert "Cut losses early" in prompt

    def test_prompt_contains_context_data(self) -> None:
        planner = _make_planner()
        context = {"L6_DAILY": {"win_rate": 0.65, "total_pnl": 2.5}}

        prompt = planner._build_prompt("KR", [_candidate()], context, None)

        assert "Strategic Context" in prompt
        assert "L6_DAILY" in prompt
        assert "win_rate" in prompt

    def test_prompt_contains_max_scenarios(self) -> None:
        planner = _make_planner()
        prompt = planner._build_prompt("KR", [_candidate()], {}, None)

        assert f"Max {planner._settings.MAX_SCENARIOS_PER_STOCK} scenarios" in prompt

    def test_prompt_market_name(self) -> None:
        planner = _make_planner()
        prompt = planner._build_prompt("US", [_candidate()], {}, None)
        assert "US market" in prompt


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
