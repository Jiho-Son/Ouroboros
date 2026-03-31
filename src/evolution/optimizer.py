"""Evolution Engine — analyzes trade logs and records daily recommendations.

This module:
1. Uses DecisionLogger.get_losing_decisions() to identify failing patterns
2. Analyzes failure patterns by time, market conditions, stock characteristics
3. Asks the configured LLM provider to generate structured improvement recommendations
4. Stores the resulting recommendation report in the daily context layer
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from src.brain.llm_client import LLMProvider, build_llm_provider
from src.config import Settings
from src.context.layer import ContextLayer
from src.context.store import ContextStore
from src.db import init_db
from src.decision_logging.decision_logger import DecisionLogger
from src.evolution.context_bundle import (
    build_evolution_context_bundle,
    render_evolution_context_section,
)

logger = logging.getLogger(__name__)


class EvolutionOptimizer:
    """Analyzes trade history and evolves trading strategies."""

    def __init__(self, settings: Settings, llm_client: LLMProvider | None = None) -> None:
        self._settings = settings
        self._db_path = settings.DB_PATH
        self._client = llm_client or build_llm_provider(settings)
        self._model_name = settings.llm_model
        self._conn = init_db(self._db_path)
        self._context_store = ContextStore(self._conn)
        self._decision_logger = DecisionLogger(self._conn)

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def analyze_failures(self, limit: int = 50) -> list[dict[str, Any]]:
        """Find high-confidence decisions that resulted in losses.

        Uses DecisionLogger.get_losing_decisions() to retrieve failures.
        """
        losing_decisions = self._decision_logger.get_losing_decisions(
            min_confidence=80, min_loss=-100.0
        )

        # Limit results
        if len(losing_decisions) > limit:
            losing_decisions = losing_decisions[:limit]

        # Convert to dict format for analysis
        failures = []
        for decision in losing_decisions:
            failures.append(
                {
                    "decision_id": decision.decision_id,
                    "timestamp": decision.timestamp,
                    "stock_code": decision.stock_code,
                    "market": decision.market,
                    "exchange_code": decision.exchange_code,
                    "action": decision.action,
                    "confidence": decision.confidence,
                    "rationale": decision.rationale,
                    "outcome_pnl": decision.outcome_pnl,
                    "outcome_accuracy": decision.outcome_accuracy,
                    "context_snapshot": decision.context_snapshot,
                    "input_data": decision.input_data,
                }
            )

        return failures

    def identify_failure_patterns(self, failures: list[dict[str, Any]]) -> dict[str, Any]:
        """Identify patterns in losing decisions.

        Analyzes:
        - Time patterns (hour of day, day of week)
        - Market conditions (volatility, volume)
        - Stock characteristics (price range, market)
        - Common failure modes in rationale
        """
        if not failures:
            return {"pattern_count": 0, "patterns": {}}

        patterns = {
            "markets": Counter(),
            "actions": Counter(),
            "hours": Counter(),
            "avg_confidence": 0.0,
            "avg_loss": 0.0,
            "total_failures": len(failures),
        }

        total_confidence = 0
        total_loss = 0.0

        for failure in failures:
            # Market distribution
            patterns["markets"][failure.get("market", "UNKNOWN")] += 1

            # Action distribution
            patterns["actions"][failure.get("action", "UNKNOWN")] += 1

            # Time pattern (extract hour from ISO timestamp)
            timestamp = failure.get("timestamp", "")
            if timestamp:
                try:
                    dt = datetime.fromisoformat(timestamp)
                    patterns["hours"][dt.hour] += 1
                except (ValueError, AttributeError):
                    pass

            # Aggregate metrics
            total_confidence += failure.get("confidence", 0)
            total_loss += failure.get("outcome_pnl", 0.0)

        patterns["avg_confidence"] = round(total_confidence / len(failures), 2) if failures else 0.0
        patterns["avg_loss"] = round(total_loss / len(failures), 2) if failures else 0.0

        # Convert Counters to regular dicts for JSON serialization
        patterns["markets"] = dict(patterns["markets"])
        patterns["actions"] = dict(patterns["actions"])
        patterns["hours"] = dict(patterns["hours"])

        return patterns

    def get_performance_summary(self) -> dict[str, Any]:
        """Return aggregate performance metrics from trade logs."""
        conn = init_db(self._db_path)
        try:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END) as losses,
                    COALESCE(AVG(pnl), 0) as avg_pnl,
                    COALESCE(SUM(pnl), 0) as total_pnl
                FROM trades
                """
            ).fetchone()
            return {
                "total_trades": row[0],
                "wins": row[1] or 0,
                "losses": row[2] or 0,
                "avg_pnl": round(row[3], 2),
                "total_pnl": round(row[4], 2),
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Recommendation Generation
    # ------------------------------------------------------------------

    async def generate_recommendation(
        self,
        failures: list[dict[str, Any]],
        patterns: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Generate a structured recommendation payload from failure analysis."""
        resolved_patterns = patterns or self.identify_failure_patterns(failures)
        evolution_context = render_evolution_context_section(
            build_evolution_context_bundle(self._context_store, failures)
        )
        prompt = (
            "You are a quantitative trading performance reviewer.\n"
            "Analyze these failed trades and respond with ONLY a JSON object.\n"
            "Required keys:\n"
            '- "summary": string\n'
            '- "adjustments": array of 1-3 short strings\n'
            '- "risk_notes": array of short strings (may be empty)\n'
            "Do not return Python code, markdown, or commentary outside JSON.\n\n"
            f"{evolution_context}"
            f"Failure Patterns:\n{json.dumps(resolved_patterns, indent=2)}\n\n"
            f"Sample Failed Trades (first 5):\n"
            f"{json.dumps(failures[:5], indent=2, default=str)}\n\n"
            "Focus on process or decisioning improvements that a human can review later.\n"
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model_name,
                contents=prompt,
            )
            raw_text = response.text.strip()
        except Exception as exc:
            logger.error("Failed to generate evolution recommendation: %s", exc)
            return None

        cleaned = self._strip_code_fences(raw_text)
        recommendation = self._parse_recommendation(cleaned)
        if recommendation is None:
            logger.warning("Generated evolution recommendation failed schema validation")
            return None
        return recommendation

    def validate_recommendation(self, recommendation: dict[str, Any]) -> bool:
        """Validate the LLM-produced recommendation schema."""
        if not isinstance(recommendation, dict):
            return False

        summary = recommendation.get("summary")
        adjustments = recommendation.get("adjustments")
        risk_notes = recommendation.get("risk_notes")
        if not isinstance(summary, str) or not summary.strip():
            return False
        if not isinstance(adjustments, list) or not adjustments:
            return False
        if not isinstance(risk_notes, list):
            return False

        normalized_adjustments = [
            item.strip() for item in adjustments if isinstance(item, str) and item.strip()
        ]
        normalized_risk_notes = [
            item.strip() for item in risk_notes if isinstance(item, str) and item.strip()
        ]
        return (
            len(normalized_adjustments) == len(adjustments)
            and len(normalized_adjustments) > 0
            and len(normalized_risk_notes) == len(risk_notes)
        )

    # ------------------------------------------------------------------
    # Report Persistence
    # ------------------------------------------------------------------

    def create_evolution_report(
        self,
        *,
        market_code: str,
        market_date: str,
        patterns: dict[str, Any],
        recommendation: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a stored report for later human review."""
        report = {
            "title": f"[Evolution] Daily recommendation: {market_code} {market_date}",
            "status": "recorded",
            "context_key": f"evolution_{market_code}",
            "market": market_code,
            "date": market_date,
            "summary": recommendation["summary"],
            "adjustments": recommendation["adjustments"],
            "risk_notes": recommendation["risk_notes"],
            "failure_patterns": patterns,
        }
        logger.info("Evolution report created: %s", report["title"])
        return report

    def store_evolution_report(self, report: dict[str, Any]) -> None:
        """Persist the report in L6_DAILY context for the market/date."""
        self._context_store.set_context(
            ContextLayer.L6_DAILY,
            report["date"],
            report["context_key"],
            report,
        )

    def _strip_code_fences(self, raw_text: str) -> str:
        cleaned = raw_text.strip()
        if not cleaned.startswith("```"):
            return cleaned

        lines = cleaned.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()

        first_newline = cleaned.find("\n")
        if first_newline == -1:
            without_open = re.sub(r"^```[\w-]*", "", cleaned, count=1)
            return without_open.removesuffix("```").strip()

        return cleaned[first_newline + 1 :].removesuffix("```").strip()

    def _parse_recommendation(self, raw_text: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw_text, re.DOTALL)
            if match is None:
                return None
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                return None

        if not self.validate_recommendation(parsed):
            return None

        return {
            "summary": parsed["summary"].strip(),
            "adjustments": [item.strip() for item in parsed["adjustments"]],
            "risk_notes": [item.strip() for item in parsed["risk_notes"]],
        }

    # ------------------------------------------------------------------
    # Full Pipeline
    # ------------------------------------------------------------------

    async def evolve(
        self,
        *,
        market_code: str | None = None,
        market_date: str | None = None,
    ) -> dict[str, Any] | None:
        """Run the full evolution pipeline.

        1. Analyze failures
        2. Generate structured recommendation
        3. Store it in daily context
        4. Return report metadata

        Returns report info on success, None on failure.
        """
        failures = self.analyze_failures()
        if not failures:
            logger.info("No failure patterns found — skipping evolution")
            return None

        patterns = self.identify_failure_patterns(failures)
        recommendation = await self.generate_recommendation(failures, patterns=patterns)
        if recommendation is None:
            return None

        resolved_market_code = market_code or str(failures[0].get("market", "UNKNOWN"))
        resolved_market_date = market_date or datetime.now(UTC).date().isoformat()
        report = self.create_evolution_report(
            market_code=resolved_market_code,
            market_date=resolved_market_date,
            patterns=patterns,
            recommendation=recommendation,
        )
        self.store_evolution_report(report)

        return report
