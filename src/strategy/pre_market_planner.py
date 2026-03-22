"""Pre-market planner — generates DayPlaybook via the decision engine before market open.

One decision-engine call per market per day. Candidates come from SmartVolatilityScanner.
On failure, returns a smart rule-based fallback playbook that uses scanner signals
(momentum/oversold) to generate BUY conditions, avoiding the all-HOLD problem.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from src.analysis.smart_scanner import ScanCandidate
from src.brain.context_selector import ContextSelector, DecisionType
from src.brain.decision_engine import DecisionEngine
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

_RAW_PNL_UNIT_BY_MARKET: dict[str, str] = {
    "KR": "KRW",
    "US": "USD",
}


def _raw_pnl_unit_for_market(market: str) -> str:
    """Return the display unit for raw realized PnL values in scorecards."""
    return _RAW_PNL_UNIT_BY_MARKET.get(market, market)


@dataclass(frozen=True)
class RecentSelfMarketGuard:
    """Deterministic BUY guard derived from recent self-market scorecards."""

    lookback_days: int
    scorecards: list[dict[str, Any]]
    cumulative_pnl: float
    average_win_rate: float
    consecutive_loss_days: int
    action: str
    reasons: tuple[str, ...]

    @property
    def active(self) -> bool:
        return bool(self.reasons)


class PreMarketPlanner:
    """Generates a DayPlaybook by calling the decision engine once before market open.

    Flow:
    1. Collect strategic context (L5-L7) + cross-market context
    2. Build a structured prompt with scan candidates
    3. Call the decision engine for JSON scenario generation
    4. Parse and validate response into DayPlaybook
    5. On failure → defensive playbook (HOLD everything)
    """

    def __init__(
        self,
        decision_engine: DecisionEngine,
        context_store: ContextStore,
        context_selector: ContextSelector,
        settings: Settings,
    ) -> None:
        self._decision_engine = decision_engine
        self._gemini = decision_engine
        self._context_store = context_store
        self._context_selector = context_selector
        self._settings = settings

    async def generate_playbook(
        self,
        market: str,
        candidates: list[ScanCandidate],
        today: date | None = None,
        current_holdings: list[dict] | None = None,
    ) -> DayPlaybook:
        """Generate a DayPlaybook for a market using the decision engine.

        Args:
            market: Market code ("KR" or "US")
            candidates: Stock candidates from SmartVolatilityScanner
            today: Override date (defaults to date.today()). Use market-local date.
            current_holdings: Currently held positions with entry_price and unrealized_pnl_pct.
                Each dict: {"stock_code": str, "name": str, "qty": int,
                            "entry_price": float, "unrealized_pnl_pct": float,
                            "holding_days": int}

        Returns:
            DayPlaybook with scenarios. Empty/defensive if no candidates or failure.
        """
        if today is None:
            today = date.today()

        if not candidates:
            logger.info("No candidates for %s — returning empty playbook", market)
            return self._empty_playbook(today, market)

        recent_self_market_guard = self._build_recent_self_market_guard(market, today)

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
                recent_self_market_guard=recent_self_market_guard,
                current_holdings=current_holdings,
            )

            # 3. Call decision engine
            market_data = {
                "stock_code": "PLANNER",
                "current_price": 0,
                "prompt_override": prompt,
            }
            decision = await self._decision_engine.decide(market_data)

            # 4. Parse response
            playbook = self._parse_response(
                decision.rationale,
                today,
                market,
                candidates,
                cross_market,
                current_holdings=current_holdings,
            )
            playbook = self._apply_recent_self_market_guard(playbook, recent_self_market_guard)
            playbook_with_tokens = playbook.model_copy(update={"token_count": decision.token_count})
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
                fallback_playbook = self._smart_fallback_playbook(
                    today,
                    market,
                    candidates,
                    self._settings,
                )
                return self._apply_recent_self_market_guard(
                    fallback_playbook,
                    recent_self_market_guard,
                )
            return self._empty_playbook(today, market)

    def build_cross_market_context(
        self,
        target_market: str,
        today: date | None = None,
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
        self,
        market: str,
        today: date | None = None,
    ) -> dict[str, Any] | None:
        """Build previous-day scorecard for the same market."""
        if today is None:
            today = date.today()
        timeframe = (today - timedelta(days=1)).isoformat()
        scorecard_key = f"scorecard_{market}"
        scorecard_data = self._context_store.get_context(
            ContextLayer.L6_DAILY, timeframe, scorecard_key
        )
        return self._normalize_scorecard(scorecard_data, timeframe=timeframe)

    def _normalize_scorecard(
        self,
        scorecard_data: Any,
        *,
        timeframe: str,
    ) -> dict[str, Any] | None:
        """Parse scorecard payload from context storage into a normalized dict."""
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

    def _load_recent_self_market_scorecards(
        self,
        market: str,
        today: date,
    ) -> list[dict[str, Any]]:
        """Load up to N recent same-market scorecards, skipping non-trading days."""
        lookback_days = self._settings.SCORECARD_BUY_GUARD_LOOKBACK_DAYS
        if lookback_days <= 0:
            return []

        scorecard_key = f"scorecard_{market}"
        max_calendar_days = max(lookback_days * 4, lookback_days)
        recent_scorecards: list[dict[str, Any]] = []

        for days_back in range(1, max_calendar_days + 1):
            if len(recent_scorecards) >= lookback_days:
                break
            timeframe = (today - timedelta(days=days_back)).isoformat()
            scorecard_data = self._context_store.get_context(
                ContextLayer.L6_DAILY,
                timeframe,
                scorecard_key,
            )
            normalized = self._normalize_scorecard(scorecard_data, timeframe=timeframe)
            if normalized is not None:
                recent_scorecards.append(normalized)

        return recent_scorecards

    def _build_recent_self_market_guard(
        self,
        market: str,
        today: date,
    ) -> RecentSelfMarketGuard | None:
        """Build the deterministic recent-loss BUY guard for the current market."""
        lookback_days = self._settings.SCORECARD_BUY_GUARD_LOOKBACK_DAYS
        if lookback_days <= 0:
            return None

        scorecards = self._load_recent_self_market_scorecards(market, today)
        if not scorecards:
            return None

        cumulative_pnl = sum(float(scorecard["total_pnl"]) for scorecard in scorecards)
        average_win_rate = sum(float(scorecard["win_rate"]) for scorecard in scorecards) / len(
            scorecards
        )
        consecutive_loss_days = 0
        for scorecard in scorecards:
            if float(scorecard["total_pnl"]) < 0:
                consecutive_loss_days += 1
                continue
            break

        reasons: list[str] = []
        max_cumulative_pnl = self._settings.SCORECARD_BUY_GUARD_MAX_CUMULATIVE_PNL
        if max_cumulative_pnl is not None and cumulative_pnl <= max_cumulative_pnl:
            reasons.append(
                f"cumulative_pnl {cumulative_pnl:+.2f} <= {max_cumulative_pnl:+.2f}"
            )
        min_win_rate = self._settings.SCORECARD_BUY_GUARD_MIN_WIN_RATE
        if min_win_rate is not None and average_win_rate < min_win_rate:
            reasons.append(f"avg_win_rate {average_win_rate:.1f}% < {min_win_rate:.1f}%")
        max_consecutive_losses = self._settings.SCORECARD_BUY_GUARD_MAX_CONSECUTIVE_LOSS_DAYS
        if (
            max_consecutive_losses is not None
            and consecutive_loss_days >= max_consecutive_losses
        ):
            reasons.append(
                f"consecutive_loss_days {consecutive_loss_days} >= {max_consecutive_losses}"
            )

        return RecentSelfMarketGuard(
            lookback_days=lookback_days,
            scorecards=scorecards,
            cumulative_pnl=cumulative_pnl,
            average_win_rate=average_win_rate,
            consecutive_loss_days=consecutive_loss_days,
            action=self._settings.SCORECARD_BUY_GUARD_ACTION,
            reasons=tuple(reasons),
        )

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
        recent_self_market_guard: RecentSelfMarketGuard | None = None,
        current_holdings: list[dict] | None = None,
    ) -> str:
        """Build a structured prompt for Gemini to generate scenario JSON."""
        max_scenarios = self._settings.MAX_SCENARIOS_PER_STOCK

        candidates_text = "\n".join(
            f"  - {c.stock_code} ({c.name}): price={c.price}, "
            f"RSI={c.rsi:.1f}, volume_ratio={c.volume_ratio:.1f}, "
            f"signal={c.signal}, score={c.score:.1f}"
            for c in candidates
        )

        holdings_text = ""
        if current_holdings:
            lines = []
            for h in current_holdings:
                code = h.get("stock_code", "")
                name = h.get("name", "")
                qty = h.get("qty", 0)
                entry_price = h.get("entry_price", 0.0)
                pnl_pct = h.get("unrealized_pnl_pct", 0.0)
                holding_days = h.get("holding_days", 0)
                lines.append(
                    f"  - {code} ({name}): {qty}주 @ {entry_price:,.0f}, "
                    f"미실현손익 {pnl_pct:+.2f}%, 보유 {holding_days}일"
                )
            holdings_text = (
                "\n## Current Holdings (보유 중 — SELL/HOLD 전략 고려 필요)\n"
                + "\n".join(lines)
                + "\n"
            )

        cross_market_text = ""
        if cross_market:
            cross_market_pnl_unit = _raw_pnl_unit_for_market(cross_market.market)
            cross_market_text = (
                f"\n## Other Market ({cross_market.market}) Summary\n"
                f"- Realized PnL ({cross_market_pnl_unit}, raw): {cross_market.total_pnl:+.2f}\n"
                f"- Win Rate: {cross_market.win_rate:.0f}%\n"
                f"- Index Change: {cross_market.index_change_pct:+.2f}%\n"
            )
            if cross_market.lessons:
                cross_market_text += f"- Lessons: {'; '.join(cross_market.lessons[:3])}\n"

        self_market_text = ""
        if self_market_scorecard:
            self_market_pnl_unit = _raw_pnl_unit_for_market(market)
            self_market_text = (
                f"\n## My Market Previous Day ({market})\n"
                f"- Date: {self_market_scorecard['date']}\n"
                f"- Realized PnL ({self_market_pnl_unit}, raw): "
                f"{self_market_scorecard['total_pnl']:+.2f}\n"
                f"- Win Rate: {self_market_scorecard['win_rate']:.0f}%\n"
            )
            lessons = self_market_scorecard.get("lessons", [])
            if lessons:
                self_market_text += f"- Lessons: {'; '.join(lessons[:3])}\n"

        recent_guard_text = ""
        guard_instruction = ""
        if recent_self_market_guard:
            recent_guard_pnl_unit = _raw_pnl_unit_for_market(market)
            dates = ", ".join(
                scorecard["date"] for scorecard in recent_self_market_guard.scorecards
            )
            recent_guard_text = (
                f"\n## Recent Self-Market Guard\n"
                f"- Window: last {recent_self_market_guard.lookback_days} scorecards "
                f"({len(recent_self_market_guard.scorecards)} loaded)\n"
                f"- Dates: {dates}\n"
                f"- Cumulative Realized PnL ({recent_guard_pnl_unit}, raw): "
                f"{recent_self_market_guard.cumulative_pnl:+.2f}\n"
                f"- Average Win Rate: {recent_self_market_guard.average_win_rate:.0f}%\n"
                f"- Consecutive Loss Days: {recent_self_market_guard.consecutive_loss_days}\n"
                f"- Guard Status: {'ACTIVE' if recent_self_market_guard.active else 'INACTIVE'}\n"
            )
            if recent_self_market_guard.reasons:
                recent_guard_text += (
                    f"- Guard Reasons: {'; '.join(recent_self_market_guard.reasons)}\n"
                )
                guard_instruction = (
                    "- Recent self-market performance guard is ACTIVE: "
                    "do not emit new BUY scenarios; produce only SELL/HOLD or "
                    "defensive risk reduction.\n"
                )

        context_text = ""
        if context_data:
            context_text = "\n## Strategic Context\n"
            for layer_name, layer_data in context_data.items():
                if layer_data:
                    context_text += f"### {layer_name}\n"
                    for key, value in list(layer_data.items())[:5]:
                        context_text += f"  - {key}: {value}\n"

        holdings_instruction = ""
        if current_holdings:
            holding_codes = [h.get("stock_code", "") for h in current_holdings]
            holdings_instruction = (
                f"- Also include SELL/HOLD scenarios for held stocks: "
                f"{', '.join(holding_codes)} "
                f"(even if not in candidates list)\n"
            )

        return (
            f"You are a pre-market trading strategist for the {market} market.\n"
            f"Generate structured trading scenarios for today.\n\n"
            f"## Candidates (from volatility scanner)\n{candidates_text}\n"
            f"{holdings_text}"
            f"{self_market_text}"
            f"{cross_market_text}"
            f"{recent_guard_text}"
            f"{context_text}\n"
            f"## Instructions\n"
            f"Return a JSON object with this exact structure:\n"
            f"{{\n"
            f'  "market_outlook": "bullish|neutral_to_bullish|neutral'
            f'|neutral_to_bearish|bearish",\n'
            f'  "global_rules": [\n'
            f'    {{"condition": "portfolio_pnl_pct < -2.0",'
            f' "action": "REDUCE_ALL", "rationale": "..."}}\n'
            f"  ],\n"
            f'  "stocks": [\n'
            f"    {{\n"
            f'      "stock_code": "...",\n'
            f'      "scenarios": [\n'
            f"        {{\n"
            f'          "condition": {{"rsi_below": 30, "volume_ratio_above": 2.0,'
            f' "unrealized_pnl_pct_above": 3.0, "holding_days_above": 5}},\n'
            f'          "action": "BUY|SELL|HOLD",\n'
            f'          "confidence": 85,\n'
            f'          "allocation_pct": 10.0,\n'
            f'          "stop_loss_pct": -2.0,\n'
            f'          "take_profit_pct": 3.0,\n'
            f'          "rationale": "..."\n'
            f"        }}\n"
            f"      ]\n"
            f"    }}\n"
            f"  ]\n"
            f"}}\n\n"
            f"Rules:\n"
            f"- Max {max_scenarios} scenarios per stock\n"
            f"- Candidates list is the primary source for BUY candidates\n"
            f"{holdings_instruction}"
            f"{guard_instruction}"
            f"- Confidence 0-100 (80+ for actionable trades)\n"
            f"- stop_loss_pct must be <= 0, take_profit_pct must be >= 0\n"
            f"- Return ONLY the JSON, no markdown fences or explanation\n"
        )

    def _apply_recent_self_market_guard(
        self,
        playbook: DayPlaybook,
        guard: RecentSelfMarketGuard | None,
    ) -> DayPlaybook:
        """Deterministically remove new BUY scenarios when recent loss guard is active."""
        if guard is None or not guard.active:
            return playbook

        logger.warning(
            "Recent self-market BUY guard active for %s: %s",
            playbook.market,
            "; ".join(guard.reasons),
        )

        guarded_stock_playbooks: list[StockPlaybook] = []
        for stock_playbook in playbook.stock_playbooks:
            scenarios = [
                scenario
                for scenario in stock_playbook.scenarios
                if scenario.action != ScenarioAction.BUY
            ]
            if scenarios:
                guarded_stock_playbooks.append(
                    stock_playbook.model_copy(update={"scenarios": scenarios})
                )

        updates: dict[str, Any] = {"stock_playbooks": guarded_stock_playbooks}

        if guard.action == "defensive":
            if playbook.market_outlook not in (
                MarketOutlook.NEUTRAL_TO_BEARISH,
                MarketOutlook.BEARISH,
            ):
                updates["market_outlook"] = MarketOutlook.NEUTRAL_TO_BEARISH

            global_rules = list(playbook.global_rules)
            if not any(rule.action == ScenarioAction.REDUCE_ALL for rule in global_rules):
                global_rules.append(
                    GlobalRule(
                        condition="portfolio_pnl_pct < -1.5",
                        action=ScenarioAction.REDUCE_ALL,
                        rationale=(
                            "Defensive: recent self-market performance guard active"
                        ),
                    )
                )
            updates["global_rules"] = global_rules

        return playbook.model_copy(update=updates)

    def _parse_response(
        self,
        response_text: str,
        today: date,
        market: str,
        candidates: list[ScanCandidate],
        cross_market: CrossMarketContext | None,
        current_holdings: list[dict] | None = None,
    ) -> DayPlaybook:
        """Parse Gemini's JSON response into a validated DayPlaybook."""
        cleaned = self._extract_json(response_text)
        data = json.loads(cleaned)

        valid_codes = {c.stock_code for c in candidates}
        # Holdings are also valid — AI may generate SELL/HOLD scenarios for them
        if current_holdings:
            for h in current_holdings:
                code = h.get("stock_code", "")
                if code:
                    valid_codes.add(code)

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
                unrealized_pnl_pct_above=cond_data.get("unrealized_pnl_pct_above"),
                unrealized_pnl_pct_below=cond_data.get("unrealized_pnl_pct_below"),
                holding_days_above=cond_data.get("holding_days_above"),
                holding_days_below=cond_data.get("holding_days_below"),
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

    @staticmethod
    def _smart_fallback_playbook(
        today: date,
        market: str,
        candidates: list[ScanCandidate],
        settings: Settings,
    ) -> DayPlaybook:
        """Rule-based fallback playbook when Gemini is unavailable.

        Uses scanner signals (RSI, volume_ratio) to generate meaningful BUY
        conditions instead of the all-SELL defensive playbook.  Candidates are
        already pre-qualified by SmartVolatilityScanner, so we trust their
        signals and build actionable scenarios from them.

        Scenario logic per candidate:
        - momentum signal: BUY when volume_ratio exceeds scanner threshold
        - oversold signal: BUY when RSI is below oversold threshold
        - always: SELL stop-loss at -3.0% as guard
        """
        stock_playbooks = []
        for c in candidates:
            scenarios: list[StockScenario] = []

            if c.signal == "momentum":
                scenarios.append(
                    StockScenario(
                        condition=StockCondition(
                            volume_ratio_above=settings.VOL_MULTIPLIER,
                        ),
                        action=ScenarioAction.BUY,
                        confidence=80,
                        allocation_pct=10.0,
                        stop_loss_pct=-3.0,
                        take_profit_pct=5.0,
                        rationale=(
                            f"Rule-based BUY: momentum signal, "
                            f"volume={c.volume_ratio:.1f}x (fallback planner)"
                        ),
                    )
                )
            elif c.signal == "oversold":
                scenarios.append(
                    StockScenario(
                        condition=StockCondition(
                            rsi_below=settings.RSI_OVERSOLD_THRESHOLD,
                        ),
                        action=ScenarioAction.BUY,
                        confidence=80,
                        allocation_pct=10.0,
                        stop_loss_pct=-3.0,
                        take_profit_pct=5.0,
                        rationale=(
                            f"Rule-based BUY: oversold signal, RSI={c.rsi:.0f} (fallback planner)"
                        ),
                    )
                )

            # Always add stop-loss guard
            scenarios.append(
                StockScenario(
                    condition=StockCondition(price_change_pct_below=-3.0),
                    action=ScenarioAction.SELL,
                    confidence=90,
                    stop_loss_pct=-3.0,
                    rationale="Rule-based stop-loss (fallback planner)",
                )
            )

            stock_playbooks.append(
                StockPlaybook(
                    stock_code=c.stock_code,
                    scenarios=scenarios,
                )
            )

        logger.info(
            "Smart fallback playbook for %s: %d stocks with rule-based BUY/SELL conditions",
            market,
            len(stock_playbooks),
        )
        return DayPlaybook(
            date=today,
            market=market,
            market_outlook=MarketOutlook.NEUTRAL,
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
