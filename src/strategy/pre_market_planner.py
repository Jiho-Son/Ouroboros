"""Pre-market planner — generates DayPlaybook via Gemini before market open.

One Gemini API call per market per day. Candidates come from SmartVolatilityScanner.
On failure, returns a defensive playbook (all HOLD, no trades).
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

from src.analysis.smart_scanner import ScanCandidate
from src.brain.context_selector import ContextSelector, DecisionType
from src.brain.gemini_client import GeminiClient
from src.config import Settings
from src.context.store import ContextLayer, ContextStore
from src.strategy.models import (
    CrossMarketContext,
    DayPlaybook,
    GlobalRule,
    MarketOutlook,
    ScenarioAction,
    StockCondition,
    StockPlaybook,
    StockScenario,
)

logger = logging.getLogger(__name__)

# Mapping from string to MarketOutlook enum
_OUTLOOK_MAP: dict[str, MarketOutlook] = {
    "bullish": MarketOutlook.BULLISH,
    "neutral_to_bullish": MarketOutlook.NEUTRAL_TO_BULLISH,
    "neutral": MarketOutlook.NEUTRAL,
    "neutral_to_bearish": MarketOutlook.NEUTRAL_TO_BEARISH,
    "bearish": MarketOutlook.BEARISH,
}

_ACTION_MAP: dict[str, ScenarioAction] = {
    "BUY": ScenarioAction.BUY,
    "SELL": ScenarioAction.SELL,
    "HOLD": ScenarioAction.HOLD,
    "REDUCE_ALL": ScenarioAction.REDUCE_ALL,
}


class PreMarketPlanner:
    """Generates a DayPlaybook by calling Gemini once before market open.

    Flow:
    1. Collect strategic context (L5-L7) + cross-market context
    2. Build a structured prompt with scan candidates
    3. Call Gemini for JSON scenario generation
    4. Parse and validate response into DayPlaybook
    5. On failure → defensive playbook (HOLD everything)
    """

    def __init__(
        self,
        gemini_client: GeminiClient,
        context_store: ContextStore,
        context_selector: ContextSelector,
        settings: Settings,
    ) -> None:
        self._gemini = gemini_client
        self._context_store = context_store
        self._context_selector = context_selector
        self._settings = settings

    async def generate_playbook(
        self,
        market: str,
        candidates: list[ScanCandidate],
        today: date | None = None,
    ) -> DayPlaybook:
        """Generate a DayPlaybook for a market using Gemini.

        Args:
            market: Market code ("KR" or "US")
            candidates: Stock candidates from SmartVolatilityScanner
            today: Override date (defaults to date.today()). Use market-local date.

        Returns:
            DayPlaybook with scenarios. Empty/defensive if no candidates or failure.
        """
        if today is None:
            today = date.today()

        if not candidates:
            logger.info("No candidates for %s — returning empty playbook", market)
            return self._empty_playbook(today, market)

        try:
            # 1. Gather context
            context_data = self._gather_context()
            self_market_scorecard = self.build_self_market_scorecard(market, today)
            cross_market = self.build_cross_market_context(market, today)

            # 2. Build prompt
            prompt = self._build_prompt(
                market,
                candidates,
                context_data,
                self_market_scorecard,
                cross_market,
            )

            # 3. Call Gemini
            market_data = {
                "stock_code": "PLANNER",
                "current_price": 0,
                "prompt_override": prompt,
            }
            decision = await self._gemini.decide(market_data)

            # 4. Parse response
            playbook = self._parse_response(
                decision.rationale, today, market, candidates, cross_market
            )
            playbook_with_tokens = playbook.model_copy(
                update={"token_count": decision.token_count}
            )
            logger.info(
                "Generated playbook for %s: %d stocks, %d scenarios, %d tokens",
                market,
                playbook_with_tokens.stock_count,
                playbook_with_tokens.scenario_count,
                playbook_with_tokens.token_count,
            )
            return playbook_with_tokens

        except Exception:
            logger.exception("Playbook generation failed for %s", market)
            if self._settings.DEFENSIVE_PLAYBOOK_ON_FAILURE:
                return self._defensive_playbook(today, market, candidates)
            return self._empty_playbook(today, market)

    def build_cross_market_context(
        self, target_market: str, today: date | None = None,
    ) -> CrossMarketContext | None:
        """Build cross-market context from the other market's L6 data.

        KR planner → reads US scorecard from previous night.
        US planner → reads KR scorecard from today.

        Args:
            target_market: The market being planned ("KR" or "US")
            today: Override date (defaults to date.today()). Use market-local date.
        """
        other_market = "US" if target_market == "KR" else "KR"
        if today is None:
            today = date.today()
        timeframe_date = today - timedelta(days=1) if target_market == "KR" else today
        timeframe = timeframe_date.isoformat()

        scorecard_key = f"scorecard_{other_market}"
        scorecard_data = self._context_store.get_context(
            ContextLayer.L6_DAILY, timeframe, scorecard_key
        )

        if scorecard_data is None:
            logger.debug("No cross-market scorecard found for %s", other_market)
            return None

        if isinstance(scorecard_data, str):
            try:
                scorecard_data = json.loads(scorecard_data)
            except (json.JSONDecodeError, TypeError):
                return None

        if not isinstance(scorecard_data, dict):
            return None

        return CrossMarketContext(
            market=other_market,
            date=timeframe,
            total_pnl=float(scorecard_data.get("total_pnl", 0.0)),
            win_rate=float(scorecard_data.get("win_rate", 0.0)),
            index_change_pct=float(scorecard_data.get("index_change_pct", 0.0)),
            key_events=scorecard_data.get("key_events", []),
            lessons=scorecard_data.get("lessons", []),
        )

    def build_self_market_scorecard(
        self, market: str, today: date | None = None,
    ) -> dict[str, Any] | None:
        """Build previous-day scorecard for the same market."""
        if today is None:
            today = date.today()
        timeframe = (today - timedelta(days=1)).isoformat()
        scorecard_key = f"scorecard_{market}"
        scorecard_data = self._context_store.get_context(
            ContextLayer.L6_DAILY, timeframe, scorecard_key
        )

        if scorecard_data is None:
            return None

        if isinstance(scorecard_data, str):
            try:
                scorecard_data = json.loads(scorecard_data)
            except (json.JSONDecodeError, TypeError):
                return None

        if not isinstance(scorecard_data, dict):
            return None

        return {
            "date": timeframe,
            "total_pnl": float(scorecard_data.get("total_pnl", 0.0)),
            "win_rate": float(scorecard_data.get("win_rate", 0.0)),
            "lessons": scorecard_data.get("lessons", []),
        }

    def _gather_context(self) -> dict[str, Any]:
        """Gather strategic context using ContextSelector."""
        layers = self._context_selector.select_layers(
            decision_type=DecisionType.STRATEGIC,
            include_realtime=True,
        )
        return self._context_selector.get_context_data(layers, max_items_per_layer=10)

    def _build_prompt(
        self,
        market: str,
        candidates: list[ScanCandidate],
        context_data: dict[str, Any],
        self_market_scorecard: dict[str, Any] | None,
        cross_market: CrossMarketContext | None,
    ) -> str:
        """Build a structured prompt for Gemini to generate scenario JSON."""
        max_scenarios = self._settings.MAX_SCENARIOS_PER_STOCK

        candidates_text = "\n".join(
            f"  - {c.stock_code} ({c.name}): price={c.price}, "
            f"RSI={c.rsi:.1f}, volume_ratio={c.volume_ratio:.1f}, "
            f"signal={c.signal}, score={c.score:.1f}"
            for c in candidates
        )

        cross_market_text = ""
        if cross_market:
            cross_market_text = (
                f"\n## Other Market ({cross_market.market}) Summary\n"
                f"- P&L: {cross_market.total_pnl:+.2f}%\n"
                f"- Win Rate: {cross_market.win_rate:.0f}%\n"
                f"- Index Change: {cross_market.index_change_pct:+.2f}%\n"
            )
            if cross_market.lessons:
                cross_market_text += f"- Lessons: {'; '.join(cross_market.lessons[:3])}\n"

        self_market_text = ""
        if self_market_scorecard:
            self_market_text = (
                f"\n## My Market Previous Day ({market})\n"
                f"- Date: {self_market_scorecard['date']}\n"
                f"- P&L: {self_market_scorecard['total_pnl']:+.2f}%\n"
                f"- Win Rate: {self_market_scorecard['win_rate']:.0f}%\n"
            )
            lessons = self_market_scorecard.get("lessons", [])
            if lessons:
                self_market_text += f"- Lessons: {'; '.join(lessons[:3])}\n"

        context_text = ""
        if context_data:
            context_text = "\n## Strategic Context\n"
            for layer_name, layer_data in context_data.items():
                if layer_data:
                    context_text += f"### {layer_name}\n"
                    for key, value in list(layer_data.items())[:5]:
                        context_text += f"  - {key}: {value}\n"

        return (
            f"You are a pre-market trading strategist for the {market} market.\n"
            f"Generate structured trading scenarios for today.\n\n"
            f"## Candidates (from volatility scanner)\n{candidates_text}\n"
            f"{self_market_text}"
            f"{cross_market_text}"
            f"{context_text}\n"
            f"## Instructions\n"
            f"Return a JSON object with this exact structure:\n"
            f'{{\n'
            f'  "market_outlook": "bullish|neutral_to_bullish|neutral'
            f'|neutral_to_bearish|bearish",\n'
            f'  "global_rules": [\n'
            f'    {{"condition": "portfolio_pnl_pct < -2.0",'
            f' "action": "REDUCE_ALL", "rationale": "..."}}\n'
            f'  ],\n'
            f'  "stocks": [\n'
            f'    {{\n'
            f'      "stock_code": "...",\n'
            f'      "scenarios": [\n'
            f'        {{\n'
            f'          "condition": {{"rsi_below": 30, "volume_ratio_above": 2.0}},\n'
            f'          "action": "BUY|SELL|HOLD",\n'
            f'          "confidence": 85,\n'
            f'          "allocation_pct": 10.0,\n'
            f'          "stop_loss_pct": -2.0,\n'
            f'          "take_profit_pct": 3.0,\n'
            f'          "rationale": "..."\n'
            f'        }}\n'
            f'      ]\n'
            f'    }}\n'
            f'  ]\n'
            f'}}\n\n'
            f"Rules:\n"
            f"- Max {max_scenarios} scenarios per stock\n"
            f"- Only use stocks from the candidates list\n"
            f"- Confidence 0-100 (80+ for actionable trades)\n"
            f"- stop_loss_pct must be <= 0, take_profit_pct must be >= 0\n"
            f"- Return ONLY the JSON, no markdown fences or explanation\n"
        )

    def _parse_response(
        self,
        response_text: str,
        today: date,
        market: str,
        candidates: list[ScanCandidate],
        cross_market: CrossMarketContext | None,
    ) -> DayPlaybook:
        """Parse Gemini's JSON response into a validated DayPlaybook."""
        cleaned = self._extract_json(response_text)
        data = json.loads(cleaned)

        valid_codes = {c.stock_code for c in candidates}

        # Parse market outlook
        outlook_str = data.get("market_outlook", "neutral")
        market_outlook = _OUTLOOK_MAP.get(outlook_str, MarketOutlook.NEUTRAL)

        # Parse global rules
        global_rules = []
        for rule_data in data.get("global_rules", []):
            action_str = rule_data.get("action", "HOLD")
            action = _ACTION_MAP.get(action_str, ScenarioAction.HOLD)
            global_rules.append(
                GlobalRule(
                    condition=rule_data.get("condition", ""),
                    action=action,
                    rationale=rule_data.get("rationale", ""),
                )
            )

        # Parse stock playbooks
        stock_playbooks = []
        max_scenarios = self._settings.MAX_SCENARIOS_PER_STOCK
        for stock_data in data.get("stocks", []):
            code = stock_data.get("stock_code", "")
            if code not in valid_codes:
                logger.warning("Gemini returned unknown stock %s — skipping", code)
                continue

            scenarios = []
            for sc_data in stock_data.get("scenarios", [])[:max_scenarios]:
                scenario = self._parse_scenario(sc_data)
                if scenario:
                    scenarios.append(scenario)

            if scenarios:
                stock_playbooks.append(
                    StockPlaybook(
                        stock_code=code,
                        scenarios=scenarios,
                    )
                )

        return DayPlaybook(
            date=today,
            market=market,
            market_outlook=market_outlook,
            global_rules=global_rules,
            stock_playbooks=stock_playbooks,
            cross_market=cross_market,
        )

    def _parse_scenario(self, sc_data: dict) -> StockScenario | None:
        """Parse a single scenario from JSON data. Returns None if invalid."""
        try:
            cond_data = sc_data.get("condition", {})
            condition = StockCondition(
                rsi_below=cond_data.get("rsi_below"),
                rsi_above=cond_data.get("rsi_above"),
                volume_ratio_above=cond_data.get("volume_ratio_above"),
                volume_ratio_below=cond_data.get("volume_ratio_below"),
                price_above=cond_data.get("price_above"),
                price_below=cond_data.get("price_below"),
                price_change_pct_above=cond_data.get("price_change_pct_above"),
                price_change_pct_below=cond_data.get("price_change_pct_below"),
            )

            if not condition.has_any_condition():
                logger.warning("Scenario has no conditions — skipping")
                return None

            action_str = sc_data.get("action", "HOLD")
            action = _ACTION_MAP.get(action_str, ScenarioAction.HOLD)

            return StockScenario(
                condition=condition,
                action=action,
                confidence=int(sc_data.get("confidence", 50)),
                allocation_pct=float(sc_data.get("allocation_pct", 10.0)),
                stop_loss_pct=float(sc_data.get("stop_loss_pct", -2.0)),
                take_profit_pct=float(sc_data.get("take_profit_pct", 3.0)),
                rationale=sc_data.get("rationale", ""),
            )
        except (ValueError, TypeError) as e:
            logger.warning("Failed to parse scenario: %s", e)
            return None

    @staticmethod
    def _extract_json(text: str) -> str:
        """Extract JSON from response, stripping markdown fences if present."""
        stripped = text.strip()
        if stripped.startswith("```"):
            # Remove first line (```json or ```) and last line (```)
            lines = stripped.split("\n")
            lines = lines[1:]  # Remove opening fence
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            stripped = "\n".join(lines)
        return stripped.strip()

    @staticmethod
    def _empty_playbook(today: date, market: str) -> DayPlaybook:
        """Return an empty playbook (no stocks, no scenarios)."""
        return DayPlaybook(
            date=today,
            market=market,
            market_outlook=MarketOutlook.NEUTRAL,
            stock_playbooks=[],
        )

    @staticmethod
    def _defensive_playbook(
        today: date,
        market: str,
        candidates: list[ScanCandidate],
    ) -> DayPlaybook:
        """Return a defensive playbook — HOLD everything with stop-loss ready."""
        stock_playbooks = [
            StockPlaybook(
                stock_code=c.stock_code,
                scenarios=[
                    StockScenario(
                        condition=StockCondition(price_change_pct_below=-3.0),
                        action=ScenarioAction.SELL,
                        confidence=90,
                        stop_loss_pct=-3.0,
                        rationale="Defensive stop-loss (planner failure)",
                    ),
                ],
            )
            for c in candidates
        ]
        return DayPlaybook(
            date=today,
            market=market,
            market_outlook=MarketOutlook.NEUTRAL_TO_BEARISH,
            default_action=ScenarioAction.HOLD,
            stock_playbooks=stock_playbooks,
            global_rules=[
                GlobalRule(
                    condition="portfolio_pnl_pct < -2.0",
                    action=ScenarioAction.REDUCE_ALL,
                    rationale="Defensive: reduce on loss threshold",
                ),
            ],
        )
