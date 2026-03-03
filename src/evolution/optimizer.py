"""Evolution Engine — analyzes trade logs and generates new strategies.

This module:
1. Uses DecisionLogger.get_losing_decisions() to identify failing patterns
2. Analyzes failure patterns by time, market conditions, stock characteristics
3. Asks Gemini to generate improved strategy recommendations
4. Generates new strategy classes with enhanced decision-making logic
"""

from __future__ import annotations

import ast
import json
import logging
import sqlite3
import subprocess
import textwrap
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from google import genai

from src.config import Settings
from src.db import init_db
from src.logging.decision_logger import DecisionLogger

logger = logging.getLogger(__name__)

STRATEGIES_DIR = Path("src/strategies")
STRATEGY_TEMPLATE = """\
\"\"\"Auto-generated strategy: {name}

Generated at: {timestamp}
Rationale: {rationale}
\"\"\"

from __future__ import annotations
from typing import Any
from src.strategies.base import BaseStrategy


class {class_name}(BaseStrategy):
    \"\"\"Strategy: {name}\"\"\"

    def evaluate(self, market_data: dict[str, Any]) -> dict[str, Any]:
{body}
"""


class EvolutionOptimizer:
    """Analyzes trade history and evolves trading strategies."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._db_path = settings.DB_PATH
        self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self._model_name = settings.GEMINI_MODEL
        self._conn = init_db(self._db_path)
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
        conn = sqlite3.connect(self._db_path)
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
    # Strategy Generation
    # ------------------------------------------------------------------

    async def generate_strategy(self, failures: list[dict[str, Any]]) -> Path | None:
        """Ask Gemini to generate a new strategy based on failure analysis.

        Integrates failure patterns and market conditions to create improved strategies.
        Returns the path to the generated strategy file, or None on failure.
        """
        # Identify failure patterns first
        patterns = self.identify_failure_patterns(failures)

        prompt = (
            "You are a quantitative trading strategy developer.\n"
            "Analyze these failed trades and their patterns, "
            "then generate an improved strategy.\n\n"
            f"Failure Patterns:\n{json.dumps(patterns, indent=2)}\n\n"
            f"Sample Failed Trades (first 5):\n"
            f"{json.dumps(failures[:5], indent=2, default=str)}\n\n"
            "Based on these patterns, generate an improved trading strategy.\n"
            "The strategy should:\n"
            "1. Avoid the identified failure patterns\n"
            "2. Consider market-specific conditions\n"
            "3. Adjust confidence based on historical performance\n\n"
            "Generate a Python method body that inherits from BaseStrategy.\n"
            "The method signature is: evaluate(self, market_data: dict) -> dict\n"
            "The method must return a dict with keys: action, confidence, rationale.\n"
            "Respond with ONLY the method body (Python code), no class definition.\n"
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model_name,
                contents=prompt,
            )
            body = response.text.strip()
        except Exception as exc:
            logger.error("Failed to generate strategy: %s", exc)
            return None

        # Clean up code fences
        if body.startswith("```"):
            lines = body.split("\n")
            body = "\n".join(lines[1:-1])

        # Create strategy file
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        version = f"v{timestamp}"
        class_name = f"Strategy_{version}"
        file_name = f"{version}_evolved.py"

        STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
        file_path = STRATEGIES_DIR / file_name

        # Indent the body for the class method
        normalized_body = textwrap.dedent(body).strip()
        indented_body = textwrap.indent(normalized_body, "            ")

        # Generate rationale from patterns
        rationale = f"Auto-evolved from {len(failures)} failures. "
        rationale += f"Primary failure markets: {list(patterns.get('markets', {}).keys())}. "
        rationale += f"Average loss: {patterns.get('avg_loss', 0.0)}"

        content = STRATEGY_TEMPLATE.format(
            name=version,
            timestamp=datetime.now(UTC).isoformat(),
            rationale=rationale,
            class_name=class_name,
            body=indented_body.rstrip(),
        )

        try:
            parsed = ast.parse(content, filename=str(file_path))
            compile(parsed, filename=str(file_path), mode="exec")
        except SyntaxError as exc:
            logger.warning("Generated strategy failed syntax validation: %s", exc)
            return None

        file_path.write_text(content)
        logger.info("Generated strategy file: %s", file_path)
        return file_path

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_strategy(self, strategy_path: Path) -> bool:
        """Run pytest on the generated strategy. Returns True if all tests pass."""
        logger.info("Validating strategy: %s", strategy_path)
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/", "-v", "--tb=short"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            logger.info("Strategy validation PASSED")
            return True
        else:
            logger.warning("Strategy validation FAILED:\n%s", result.stdout + result.stderr)
            # Clean up failing strategy
            strategy_path.unlink(missing_ok=True)
            return False

    # ------------------------------------------------------------------
    # PR Simulation
    # ------------------------------------------------------------------

    def create_pr_simulation(self, strategy_path: Path) -> dict[str, str]:
        """Simulate creating a pull request for the new strategy."""
        pr = {
            "title": f"[Evolution] New strategy: {strategy_path.stem}",
            "branch": f"evolution/{strategy_path.stem}",
            "body": (
                f"Auto-generated strategy from evolution engine.\n"
                f"File: {strategy_path}\n"
                f"All tests passed."
            ),
            "status": "ready_for_review",
        }
        logger.info("PR simulation created: %s", pr["title"])
        return pr

    # ------------------------------------------------------------------
    # Full Pipeline
    # ------------------------------------------------------------------

    async def evolve(self) -> dict[str, Any] | None:
        """Run the full evolution pipeline.

        1. Analyze failures
        2. Generate new strategy
        3. Validate with tests
        4. Create PR simulation

        Returns PR info on success, None on failure.
        """
        failures = self.analyze_failures()
        if not failures:
            logger.info("No failure patterns found — skipping evolution")
            return None

        strategy_path = await self.generate_strategy(failures)
        if strategy_path is None:
            return None

        if not self.validate_strategy(strategy_path):
            return None

        return self.create_pr_simulation(strategy_path)
