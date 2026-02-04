"""Decision engine powered by Google Gemini.

Constructs prompts from market data, calls Gemini, and parses structured
JSON responses into validated TradeDecision objects.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from google import genai

from src.config import Settings

logger = logging.getLogger(__name__)

VALID_ACTIONS = {"BUY", "SELL", "HOLD"}


@dataclass(frozen=True)
class TradeDecision:
    """Validated decision from the AI brain."""

    action: str  # "BUY" | "SELL" | "HOLD"
    confidence: int  # 0-100
    rationale: str


class GeminiClient:
    """Wraps the Gemini API for trade decision-making."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._confidence_threshold = settings.CONFIDENCE_THRESHOLD
        self._client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self._model_name = settings.GEMINI_MODEL

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
        prompt = self.build_prompt(market_data)
        logger.info("Requesting trade decision from Gemini")

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model_name, contents=prompt,
            )
            raw = response.text
        except Exception as exc:
            logger.error("Gemini API error: %s", exc)
            return TradeDecision(
                action="HOLD", confidence=0, rationale=f"API error: {exc}"
            )

        decision = self.parse_response(raw)
        logger.info(
            "Gemini decision",
            extra={
                "action": decision.action,
                "confidence": decision.confidence,
            },
        )
        return decision
