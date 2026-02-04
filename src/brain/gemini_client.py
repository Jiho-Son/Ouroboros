"""Decision engine powered by Google Gemini.

Constructs prompts from market data, calls Gemini, and parses structured
JSON responses into validated TradeDecision objects.

Includes token efficiency optimizations:
- Prompt compression and abbreviation
- Response caching for common scenarios
- Token usage tracking and metrics
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from google import genai

from src.brain.cache import DecisionCache
from src.brain.prompt_optimizer import PromptOptimizer
from src.config import Settings

logger = logging.getLogger(__name__)

VALID_ACTIONS = {"BUY", "SELL", "HOLD"}


@dataclass(frozen=True)
class TradeDecision:
    """Validated decision from the AI brain."""

    action: str  # "BUY" | "SELL" | "HOLD"
    confidence: int  # 0-100
    rationale: str
    token_count: int = 0  # Estimated tokens used
    cached: bool = False  # Whether decision came from cache


class GeminiClient:
    """Wraps the Gemini API for trade decision-making."""

    def __init__(
        self,
        settings: Settings,
        enable_cache: bool = True,
        enable_optimization: bool = True,
    ) -> None:
        self._settings = settings
        self._confidence_threshold = settings.CONFIDENCE_THRESHOLD
        self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self._model_name = settings.GEMINI_MODEL

        # Token efficiency features
        self._enable_cache = enable_cache
        self._enable_optimization = enable_optimization
        self._cache = DecisionCache(ttl_seconds=300) if enable_cache else None
        self._optimizer = PromptOptimizer()

        # Token usage metrics
        self._total_tokens_used = 0
        self._total_decisions = 0
        self._total_cached_decisions = 0

    # ------------------------------------------------------------------
    # Prompt Construction
    # ------------------------------------------------------------------

    def build_prompt(self, market_data: dict[str, Any]) -> str:
        """Build a structured prompt from market data.

        The prompt instructs Gemini to return valid JSON with action,
        confidence, and rationale fields.
        """
        market_name = market_data.get("market_name", "Korean stock market")

        # Build market data section dynamically based on available fields
        market_info_lines = [
            f"Market: {market_name}",
            f"Stock Code: {market_data['stock_code']}",
            f"Current Price: {market_data['current_price']}",
        ]

        # Add orderbook if available (domestic markets)
        if "orderbook" in market_data:
            market_info_lines.append(
                f"Orderbook: {json.dumps(market_data['orderbook'], ensure_ascii=False)}"
            )

        # Add foreigner net if non-zero
        if market_data.get("foreigner_net", 0) != 0:
            market_info_lines.append(
                f"Foreigner Net Buy/Sell: {market_data['foreigner_net']}"
            )

        market_info = "\n".join(market_info_lines)

        json_format = (
            '{"action": "BUY"|"SELL"|"HOLD", '
            '"confidence": <int 0-100>, "rationale": "<string>"}'
        )
        return (
            f"You are a professional {market_name} trading analyst.\n"
            "Analyze the following market data and decide whether to "
            "BUY, SELL, or HOLD.\n\n"
            f"{market_info}\n\n"
            "You MUST respond with ONLY valid JSON in the following format:\n"
            f"{json_format}\n\n"
            "Rules:\n"
            "- action must be exactly one of: BUY, SELL, HOLD\n"
            "- confidence must be an integer from 0 to 100\n"
            "- rationale must explain your reasoning concisely\n"
            "- Do NOT wrap the JSON in markdown code blocks\n"
        )

    # ------------------------------------------------------------------
    # Response Parsing
    # ------------------------------------------------------------------

    def parse_response(self, raw: str) -> TradeDecision:
        """Parse a raw Gemini response into a TradeDecision.

        Handles: valid JSON, JSON wrapped in markdown code blocks,
        malformed JSON, missing fields, and invalid action values.

        On any failure, returns a safe HOLD with confidence 0.
        """
        if not raw or not raw.strip():
            logger.warning("Empty response from Gemini — defaulting to HOLD")
            return TradeDecision(action="HOLD", confidence=0, rationale="Empty response")

        # Strip markdown code fences if present
        cleaned = raw.strip()
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1).strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Malformed JSON from Gemini — defaulting to HOLD")
            return TradeDecision(
                action="HOLD", confidence=0, rationale="Malformed JSON response"
            )

        # Validate required fields
        if not all(k in data for k in ("action", "confidence", "rationale")):
            logger.warning("Missing fields in Gemini response — defaulting to HOLD")
            return TradeDecision(
                action="HOLD", confidence=0, rationale="Missing required fields"
            )

        action = str(data["action"]).upper()
        if action not in VALID_ACTIONS:
            logger.warning("Invalid action '%s' from Gemini — defaulting to HOLD", action)
            return TradeDecision(
                action="HOLD", confidence=0, rationale=f"Invalid action: {action}"
            )

        confidence = int(data["confidence"])
        rationale = str(data["rationale"])

        # Enforce confidence threshold
        if confidence < self._confidence_threshold:
            logger.info(
                "Confidence %d < threshold %d — forcing HOLD",
                confidence,
                self._confidence_threshold,
            )
            action = "HOLD"

        return TradeDecision(action=action, confidence=confidence, rationale=rationale)

    # ------------------------------------------------------------------
    # API Call
    # ------------------------------------------------------------------

    async def decide(self, market_data: dict[str, Any]) -> TradeDecision:
        """Build prompt, call Gemini, and return a parsed decision."""
        # Check cache first
        if self._cache:
            cached_decision = self._cache.get(market_data)
            if cached_decision:
                self._total_cached_decisions += 1
                self._total_decisions += 1
                logger.info(
                    "Cache hit for decision",
                    extra={
                        "action": cached_decision.action,
                        "confidence": cached_decision.confidence,
                        "cache_hit_rate": self.get_cache_hit_rate(),
                    },
                )
                # Return cached decision with cached flag
                return TradeDecision(
                    action=cached_decision.action,
                    confidence=cached_decision.confidence,
                    rationale=cached_decision.rationale,
                    token_count=0,
                    cached=True,
                )

        # Build optimized prompt
        if self._enable_optimization:
            prompt = self._optimizer.build_compressed_prompt(market_data)
        else:
            prompt = self.build_prompt(market_data)

        # Estimate tokens
        token_count = self._optimizer.estimate_tokens(prompt)
        self._total_tokens_used += token_count

        logger.info(
            "Requesting trade decision from Gemini",
            extra={"estimated_tokens": token_count, "optimized": self._enable_optimization},
        )

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model_name,
                contents=prompt,
            )
            raw = response.text
        except Exception as exc:
            logger.error("Gemini API error: %s", exc)
            return TradeDecision(
                action="HOLD", confidence=0, rationale=f"API error: {exc}", token_count=token_count
            )

        decision = self.parse_response(raw)
        self._total_decisions += 1

        # Add token count to decision
        decision_with_tokens = TradeDecision(
            action=decision.action,
            confidence=decision.confidence,
            rationale=decision.rationale,
            token_count=token_count,
            cached=False,
        )

        # Cache if appropriate
        if self._cache and self._cache.should_cache_decision(decision):
            self._cache.set(market_data, decision)

        logger.info(
            "Gemini decision",
            extra={
                "action": decision.action,
                "confidence": decision.confidence,
                "tokens": token_count,
                "avg_tokens": self.get_avg_tokens_per_decision(),
            },
        )

        return decision_with_tokens

    # ------------------------------------------------------------------
    # Token Efficiency Metrics
    # ------------------------------------------------------------------

    def get_token_metrics(self) -> dict[str, Any]:
        """Get token usage metrics.

        Returns:
            Dictionary with token usage statistics
        """
        metrics = {
            "total_tokens_used": self._total_tokens_used,
            "total_decisions": self._total_decisions,
            "total_cached_decisions": self._total_cached_decisions,
            "avg_tokens_per_decision": self.get_avg_tokens_per_decision(),
            "cache_hit_rate": self.get_cache_hit_rate(),
        }

        if self._cache:
            cache_metrics = self._cache.get_metrics()
            metrics["cache_metrics"] = cache_metrics.to_dict()

        return metrics

    def get_avg_tokens_per_decision(self) -> float:
        """Calculate average tokens per decision.

        Returns:
            Average tokens per decision
        """
        if self._total_decisions == 0:
            return 0.0
        return self._total_tokens_used / self._total_decisions

    def get_cache_hit_rate(self) -> float:
        """Calculate cache hit rate.

        Returns:
            Cache hit rate (0.0 to 1.0)
        """
        if self._total_decisions == 0:
            return 0.0
        return self._total_cached_decisions / self._total_decisions

    def reset_metrics(self) -> None:
        """Reset token usage metrics."""
        self._total_tokens_used = 0
        self._total_decisions = 0
        self._total_cached_decisions = 0
        if self._cache:
            self._cache.reset_metrics()
        logger.info("Token metrics reset")

    def get_cache(self) -> DecisionCache | None:
        """Get the decision cache instance.

        Returns:
            DecisionCache instance or None if caching disabled
        """
        return self._cache
