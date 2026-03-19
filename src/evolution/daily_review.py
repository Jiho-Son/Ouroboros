"""Daily review generator for market-scoped end-of-day scorecards."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import asdict

from src.brain.decision_engine import DecisionEngine
from src.context.layer import ContextLayer
from src.context.store import ContextStore
from src.evolution.scorecard import DailyScorecard

logger = logging.getLogger(__name__)


class DailyReviewer:
    """Builds daily scorecards and optional AI-generated lessons."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        context_store: ContextStore,
        decision_engine: DecisionEngine | None = None,
        gemini_client: DecisionEngine | None = None,
    ) -> None:
        if decision_engine is not None and gemini_client is not None:
            raise ValueError("Pass only one of decision_engine or gemini_client")

        self._conn = conn
        self._context_store = context_store
        self._decision_engine = decision_engine or gemini_client
        self._gemini = self._decision_engine

    def generate_scorecard(self, date: str, market: str) -> DailyScorecard:
        """Generate a market-scoped scorecard from decision logs and trades."""
        decision_rows = self._conn.execute(
            """
            SELECT action, confidence, context_snapshot
            FROM decision_logs
            WHERE DATE(timestamp) = ? AND market = ?
            """,
            (date, market),
        ).fetchall()

        total_decisions = len(decision_rows)
        buys = sum(1 for row in decision_rows if row[0] == "BUY")
        sells = sum(1 for row in decision_rows if row[0] == "SELL")
        holds = sum(1 for row in decision_rows if row[0] == "HOLD")
        avg_confidence = (
            round(sum(int(row[1]) for row in decision_rows) / total_decisions, 2)
            if total_decisions > 0
            else 0.0
        )

        matched = 0
        for row in decision_rows:
            try:
                snapshot = json.loads(row[2]) if row[2] else {}
            except json.JSONDecodeError:
                snapshot = {}
            scenario_match = snapshot.get("scenario_match", {})
            if isinstance(scenario_match, dict) and scenario_match:
                matched += 1
        scenario_match_rate = (
            round((matched / total_decisions) * 100, 2) if total_decisions else 0.0
        )

        trade_stats = self._conn.execute(
            """
            SELECT
                COALESCE(SUM(pnl), 0.0),
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),
                SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END)
            FROM trades
            WHERE DATE(timestamp) = ? AND market = ?
            """,
            (date, market),
        ).fetchone()
        total_pnl = round(float(trade_stats[0] or 0.0), 2) if trade_stats else 0.0
        wins = int(trade_stats[1] or 0) if trade_stats else 0
        losses = int(trade_stats[2] or 0) if trade_stats else 0
        win_rate = round((wins / (wins + losses)) * 100, 2) if (wins + losses) > 0 else 0.0

        top_winners = [
            row[0]
            for row in self._conn.execute(
                """
                SELECT stock_code, SUM(pnl) AS stock_pnl
                FROM trades
                WHERE DATE(timestamp) = ? AND market = ?
                GROUP BY stock_code
                HAVING stock_pnl > 0
                ORDER BY stock_pnl DESC
                LIMIT 3
                """,
                (date, market),
            ).fetchall()
        ]

        top_losers = [
            row[0]
            for row in self._conn.execute(
                """
                SELECT stock_code, SUM(pnl) AS stock_pnl
                FROM trades
                WHERE DATE(timestamp) = ? AND market = ?
                GROUP BY stock_code
                HAVING stock_pnl < 0
                ORDER BY stock_pnl ASC
                LIMIT 3
                """,
                (date, market),
            ).fetchall()
        ]

        return DailyScorecard(
            date=date,
            market=market,
            total_decisions=total_decisions,
            buys=buys,
            sells=sells,
            holds=holds,
            total_pnl=total_pnl,
            win_rate=win_rate,
            avg_confidence=avg_confidence,
            scenario_match_rate=scenario_match_rate,
            top_winners=top_winners,
            top_losers=top_losers,
            lessons=[],
            cross_market_note="",
        )

    async def generate_lessons(self, scorecard: DailyScorecard) -> list[str]:
        """Generate concise lessons from scorecard metrics using the decision engine."""
        if self._decision_engine is None:
            return []

        prompt = (
            "You are a trading performance reviewer.\n"
            "Return ONLY a JSON array of 1-3 short lessons in English.\n"
            f"Market: {scorecard.market}\n"
            f"Date: {scorecard.date}\n"
            f"Total decisions: {scorecard.total_decisions}\n"
            f"Buys/Sells/Holds: {scorecard.buys}/{scorecard.sells}/{scorecard.holds}\n"
            f"Total PnL: {scorecard.total_pnl}\n"
            f"Win rate: {scorecard.win_rate}%\n"
            f"Average confidence: {scorecard.avg_confidence}\n"
            f"Scenario match rate: {scorecard.scenario_match_rate}%\n"
            f"Top winners: {', '.join(scorecard.top_winners) or 'N/A'}\n"
            f"Top losers: {', '.join(scorecard.top_losers) or 'N/A'}\n"
        )

        try:
            decision = await self._decision_engine.decide(
                {
                    "stock_code": "REVIEW",
                    "market_name": scorecard.market,
                    "current_price": 0,
                    "prompt_override": prompt,
                }
            )
            return self._parse_lessons(decision.rationale)
        except Exception as exc:
            logger.warning("Failed to generate daily lessons: %s", exc)
            return []

    def store_scorecard_in_context(self, scorecard: DailyScorecard) -> None:
        """Store scorecard in L6 using market-scoped key."""
        self._context_store.set_context(
            ContextLayer.L6_DAILY,
            scorecard.date,
            f"scorecard_{scorecard.market}",
            asdict(scorecard),
        )

    def _parse_lessons(self, raw_text: str) -> list[str]:
        """Parse lessons from JSON array response or fallback text."""
        raw_text = raw_text.strip()
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()][:3]
        except json.JSONDecodeError:
            pass

        match = re.search(r"\[.*\]", raw_text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()][:3]
            except json.JSONDecodeError:
                pass

        lines = [line.strip("-* \t") for line in raw_text.splitlines() if line.strip()]
        return lines[:3]
