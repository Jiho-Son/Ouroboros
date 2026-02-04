"""Evolution Engine — analyzes trade logs and generates new strategies.

This module:
1. Reads trade_logs.db to identify failing patterns
2. Asks Gemini to generate a new strategy class
3. Runs pytest on the generated file
4. Creates a simulated PR if tests pass
"""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from google import genai

from src.config import Settings

logger = logging.getLogger(__name__)

STRATEGIES_DIR = Path("src/strategies")
STRATEGY_TEMPLATE = textwrap.dedent("""\
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
""")


class EvolutionOptimizer:
    """Analyzes trade history and evolves trading strategies."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._db_path = settings.DB_PATH
        self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self._model_name = settings.GEMINI_MODEL

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def analyze_failures(self, limit: int = 50) -> list[dict[str, Any]]:
        """Find trades where high confidence led to losses."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT stock_code, action, confidence, pnl, rationale, timestamp
                FROM trades
                WHERE confidence >= 80 AND pnl < 0
                ORDER BY pnl ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

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

        Returns the path to the generated strategy file, or None on failure.
        """
        prompt = (
            "You are a quantitative trading strategy developer.\n"
            "Analyze these failed trades and generate an improved strategy.\n\n"
            f"Failed trades:\n{json.dumps(failures, indent=2, default=str)}\n\n"
            "Generate a Python class that inherits from BaseStrategy.\n"
            "The class must have an `evaluate(self, market_data: dict) -> dict` method.\n"
            "The method must return a dict with keys: action, confidence, rationale.\n"
            "Respond with ONLY the method body (Python code), no class definition.\n"
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model_name, contents=prompt,
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
        indented_body = textwrap.indent(body, "            ")

        content = STRATEGY_TEMPLATE.format(
            name=version,
            timestamp=datetime.now(UTC).isoformat(),
            rationale="Auto-evolved from failure analysis",
            class_name=class_name,
            body=indented_body.strip(),
        )

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
            logger.warning(
                "Strategy validation FAILED:\n%s", result.stdout + result.stderr
            )
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
