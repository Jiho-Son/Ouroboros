"""Decision engine backed by the configured LLM provider.

Constructs prompts from market data, calls the selected provider, and parses structured
JSON responses into validated TradeDecision objects.

Includes token efficiency optimizations:
- Prompt compression and abbreviation
- Response caching for common scenarios
- Smart context selection
- Token usage tracking and metrics

Includes external data integration:
- News sentiment analysis
- Economic calendar events
- Market indicators
"""

from __future__ import annotations

import json
import logging
import re
import warnings
from dataclasses import dataclass
from typing import Any

from src.brain.cache import DecisionCache
from src.brain.llm_client import LLMProvider, build_llm_provider
from src.brain.prompt_optimizer import PromptOptimizer
from src.config import Settings
from src.data.economic_calendar import EconomicCalendar
from src.data.market_data import MarketData
from src.data.news_api import NewsAPI, NewsSentiment

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


class DecisionEngine:
    """Provider-agnostic trade decision engine."""

    def __init__(
        self,
        settings: Settings,
        llm_provider: LLMProvider | None = None,
        llm_client: LLMProvider | None = None,
        news_api: NewsAPI | None = None,
        economic_calendar: EconomicCalendar | None = None,
        market_data: MarketData | None = None,
        enable_cache: bool = True,
        enable_optimization: bool = True,
    ) -> None:
        if llm_provider is not None and llm_client is not None:
            raise ValueError("Pass only one of llm_provider or llm_client")
        if llm_client is not None:
            warnings.warn(
                "llm_client is deprecated, use llm_provider instead",
                DeprecationWarning,
                stacklevel=2,
            )

        self._settings = settings
        self._confidence_threshold = settings.CONFIDENCE_THRESHOLD
        self._provider = llm_provider or llm_client or build_llm_provider(settings)
        self._client = self._provider
        self._model_name = settings.llm_model

        # External data sources (optional)
        self._news_api = news_api
        self._economic_calendar = economic_calendar
        self._market_data = market_data

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
    # External Data Integration
    # ------------------------------------------------------------------

    async def _build_external_context(
        self, stock_code: str, news_sentiment: NewsSentiment | None = None
    ) -> str:
        """Build external data context for the prompt.

        Args:
            stock_code: Stock ticker symbol
            news_sentiment: Optional pre-fetched news sentiment

        Returns:
            Formatted string with external data context
        """
        context_parts: list[str] = []

        # News sentiment
        if news_sentiment is not None:
            sentiment_str = self._format_news_sentiment(news_sentiment)
            if sentiment_str:
                context_parts.append(sentiment_str)
        elif self._news_api is not None:
            # Fetch news sentiment if not provided
            try:
                sentiment = await self._news_api.get_news_sentiment(stock_code)
                if sentiment is not None:
                    sentiment_str = self._format_news_sentiment(sentiment)
                    if sentiment_str:
                        context_parts.append(sentiment_str)
            except Exception as exc:
                logger.warning("Failed to fetch news sentiment: %s", exc)

        # Economic events
        if self._economic_calendar is not None:
            events_str = self._format_economic_events(stock_code)
            if events_str:
                context_parts.append(events_str)

        # Market indicators
        if self._market_data is not None:
            indicators_str = self._format_market_indicators()
            if indicators_str:
                context_parts.append(indicators_str)

        if not context_parts:
            return ""

        return "EXTERNAL DATA:\n" + "\n\n".join(context_parts)

    def _format_news_sentiment(self, sentiment: NewsSentiment) -> str:
        """Format news sentiment for prompt."""
        if sentiment.article_count == 0:
            return ""

        # Select top 3 most relevant articles
        top_articles = sentiment.articles[:3]

        lines = [
            f"News Sentiment: {sentiment.avg_sentiment:.2f} "
            f"(from {sentiment.article_count} articles)",
        ]

        for i, article in enumerate(top_articles, 1):
            lines.append(
                f"  {i}. [{article.source}] {article.title} "
                f"(sentiment: {article.sentiment_score:.2f})"
            )

        return "\n".join(lines)

    def _format_economic_events(self, stock_code: str) -> str:
        """Format upcoming economic events for prompt."""
        if self._economic_calendar is None:
            return ""

        # Check for upcoming high-impact events
        upcoming = self._economic_calendar.get_upcoming_events(days_ahead=7, min_impact="HIGH")

        if upcoming.high_impact_count == 0:
            return ""

        lines = [f"Upcoming High-Impact Events: {upcoming.high_impact_count} in next 7 days"]

        if upcoming.next_major_event is not None:
            event = upcoming.next_major_event
            lines.append(
                f"  Next: {event.name} ({event.event_type}) "
                f"on {event.datetime.strftime('%Y-%m-%d')}"
            )

        # Check for earnings
        earnings_date = self._economic_calendar.get_earnings_date(stock_code)
        if earnings_date is not None:
            lines.append(f"  Earnings: {stock_code} on {earnings_date.strftime('%Y-%m-%d')}")

        return "\n".join(lines)

    def _format_market_indicators(self) -> str:
        """Format market indicators for prompt."""
        if self._market_data is None:
            return ""

        try:
            indicators = self._market_data.get_market_indicators()
            lines = [f"Market Sentiment: {indicators.sentiment.name}"]

            # Add breadth if meaningful
            if indicators.breadth.advance_decline_ratio != 1.0:
                lines.append(
                    f"Advance/Decline Ratio: {indicators.breadth.advance_decline_ratio:.2f}"
                )

            return "\n".join(lines)
        except Exception as exc:
            logger.warning("Failed to get market indicators: %s", exc)
            return ""

    # ------------------------------------------------------------------
    # Prompt Construction
    # ------------------------------------------------------------------

    async def build_prompt(
        self, market_data: dict[str, Any], news_sentiment: NewsSentiment | None = None
    ) -> str:
        """Build a structured prompt from market data and external sources.

        The prompt instructs the LLM provider to return valid JSON with action,
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
            market_info_lines.append(f"Foreigner Net Buy/Sell: {market_data['foreigner_net']}")

        market_info = "\n".join(market_info_lines)

        # Add external data context if available
        external_context = await self._build_external_context(
            market_data["stock_code"], news_sentiment
        )
        if external_context:
            market_info += f"\n\n{external_context}"

        json_format = (
            '{"action": "BUY"|"SELL"|"HOLD", "confidence": <int 0-100>, "rationale": "<string>"}'
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

    def build_prompt_sync(self, market_data: dict[str, Any]) -> str:
        """Synchronous version of build_prompt (for backward compatibility).

        This version does NOT include external data integration.
        Use async build_prompt() for full functionality.
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
            market_info_lines.append(f"Foreigner Net Buy/Sell: {market_data['foreigner_net']}")

        market_info = "\n".join(market_info_lines)

        json_format = (
            '{"action": "BUY"|"SELL"|"HOLD", "confidence": <int 0-100>, "rationale": "<string>"}'
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
        """Parse a raw provider response into a TradeDecision.

        Handles: valid JSON, JSON wrapped in markdown code blocks,
        malformed JSON, missing fields, and invalid action values.

        On any failure, returns a safe HOLD with confidence 0.
        """
        if not raw or not raw.strip():
            logger.warning("Empty response from LLM provider — defaulting to HOLD")
            return TradeDecision(action="HOLD", confidence=0, rationale="Empty response")

        # Strip markdown code fences if present
        cleaned = raw.strip()
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1).strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Malformed JSON from LLM provider — defaulting to HOLD")
            return TradeDecision(action="HOLD", confidence=0, rationale="Malformed JSON response")

        # Validate required fields
        if not all(k in data for k in ("action", "confidence", "rationale")):
            logger.warning("Missing fields in LLM response — defaulting to HOLD")
            # Preserve raw text in rationale so prompt_override callers (e.g. pre_market_planner)
            # can extract their own JSON format from decision.rationale (#245)
            return TradeDecision(action="HOLD", confidence=0, rationale=raw)

        action = str(data["action"]).upper()
        if action not in VALID_ACTIONS:
            logger.warning("Invalid action '%s' from LLM response — defaulting to HOLD", action)
            return TradeDecision(action="HOLD", confidence=0, rationale=f"Invalid action: {action}")

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

    async def _generate_text(self, prompt: str) -> str:
        """Call the configured provider, tolerating legacy injected test doubles."""
        if hasattr(self._provider, "generate_text"):
            return await self._provider.generate_text(model=self._model_name, prompt=prompt)

        warnings.warn(
            "legacy LLM client fallback is deprecated; implement generate_text instead",
            DeprecationWarning,
            stacklevel=2,
        )
        response = await self._client.aio.models.generate_content(
            model=self._model_name,
            contents=prompt,
        )
        return response.text

    async def decide(
        self, market_data: dict[str, Any], news_sentiment: NewsSentiment | None = None
    ) -> TradeDecision:
        """Build prompt, call the configured provider, and return a parsed decision.

        Args:
            market_data: Market data dictionary with price, orderbook, etc.
            news_sentiment: Optional pre-fetched news sentiment

        Returns:
            Parsed TradeDecision
        """
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

        # Build prompt (prompt_override takes priority for callers like pre_market_planner)
        if "prompt_override" in market_data:
            prompt = market_data["prompt_override"]
        elif self._enable_optimization:
            prompt = self._optimizer.build_compressed_prompt(market_data)
        else:
            prompt = await self.build_prompt(market_data, news_sentiment)

        # Estimate tokens
        token_count = self._optimizer.estimate_tokens(prompt)
        self._total_tokens_used += token_count

        logger.info(
            "Requesting trade decision from configured LLM provider",
            extra={"estimated_tokens": token_count, "optimized": self._enable_optimization},
        )

        try:
            raw = await self._generate_text(prompt)
        except Exception as exc:
            logger.error("LLM provider error: %s", exc)
            return TradeDecision(
                action="HOLD", confidence=0, rationale=f"API error: {exc}", token_count=token_count
            )

        # prompt_override callers (e.g. pre_market_planner) expect raw text back,
        # not a parsed TradeDecision. Skip parse_response to avoid spurious
        # "Missing fields" warnings and return the raw response directly. (#247)
        if "prompt_override" in market_data:
            logger.info("LLM raw response received (prompt_override, tokens=%d)", token_count)
            # Not a trade decision — don't inflate _total_decisions metrics
            return TradeDecision(
                action="HOLD", confidence=0, rationale=raw, token_count=token_count
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
            "LLM decision",
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

    # ------------------------------------------------------------------
    # Batch Decision Making (for daily trading mode)
    # ------------------------------------------------------------------

    async def decide_batch(self, stocks_data: list[dict[str, Any]]) -> dict[str, TradeDecision]:
        """Make decisions for multiple stocks in a single API call.

        This is designed for daily trading mode to minimize API usage
        when working with cost-limited LLM providers.

        Args:
            stocks_data: List of market data dictionaries, each with:
                - stock_code: Stock ticker
                - current_price: Current price
                - market_name: Market name (optional)
                - foreigner_net: Foreigner net buy/sell (optional)

        Returns:
            Dictionary mapping stock_code to TradeDecision

        Example:
            >>> stocks_data = [
            ...     {"stock_code": "AAPL", "current_price": 185.5},
            ...     {"stock_code": "MSFT", "current_price": 420.0},
            ... ]
            >>> decisions = await client.decide_batch(stocks_data)
            >>> decisions["AAPL"].action
            'BUY'
        """
        if not stocks_data:
            return {}

        # Build compressed batch prompt
        market_name = stocks_data[0].get("market_name", "stock market")

        # Format stock data as compact JSON array
        compact_stocks = []
        for stock in stocks_data:
            compact = {
                "code": stock["stock_code"],
                "price": stock["current_price"],
            }
            if stock.get("foreigner_net", 0) != 0:
                compact["frgn"] = stock["foreigner_net"]
            compact_stocks.append(compact)

        data_str = json.dumps(compact_stocks, ensure_ascii=False)

        prompt = (
            f"You are a professional {market_name} trading analyst.\n"
            "Analyze the following stocks and decide whether to BUY, SELL, or HOLD each one.\n\n"
            f"Stock Data: {data_str}\n\n"
            "You MUST respond with ONLY a valid JSON array in this format:\n"
            '[{"code": "AAPL", "action": "BUY", "confidence": 85, "rationale": "..."},\n'
            ' {"code": "MSFT", "action": "HOLD", "confidence": 50, "rationale": "..."}, ...]\n\n'
            "Rules:\n"
            "- Return one decision object per stock\n"
            "- action must be exactly: BUY, SELL, or HOLD\n"
            "- confidence must be 0-100\n"
            "- rationale should be concise (1-2 sentences)\n"
            "- Do NOT wrap JSON in markdown code blocks\n"
        )

        # Estimate tokens
        token_count = self._optimizer.estimate_tokens(prompt)
        self._total_tokens_used += token_count

        logger.info(
            "Requesting batch decision for %d stocks from configured LLM provider",
            len(stocks_data),
            extra={"estimated_tokens": token_count},
        )

        try:
            raw = await self._generate_text(prompt)
        except Exception as exc:
            logger.error("LLM provider error in batch decision: %s", exc)
            # Return HOLD for all stocks on API error
            return {
                stock["stock_code"]: TradeDecision(
                    action="HOLD",
                    confidence=0,
                    rationale=f"API error: {exc}",
                    token_count=token_count,
                    cached=False,
                )
                for stock in stocks_data
            }

        # Parse batch response
        return self._parse_batch_response(raw, stocks_data, token_count)

    def _parse_batch_response(
        self, raw: str, stocks_data: list[dict[str, Any]], token_count: int
    ) -> dict[str, TradeDecision]:
        """Parse batch response into a dictionary of decisions.

        Args:
            raw: Raw response from the configured LLM provider
            stocks_data: Original stock data list
            token_count: Token count for the request

        Returns:
            Dictionary mapping stock_code to TradeDecision
        """
        if not raw or not raw.strip():
            logger.warning("Empty batch response from LLM provider — defaulting all to HOLD")
            return {
                stock["stock_code"]: TradeDecision(
                    action="HOLD",
                    confidence=0,
                    rationale="Empty response",
                    token_count=0,
                    cached=False,
                )
                for stock in stocks_data
            }

        # Strip markdown code fences if present
        cleaned = raw.strip()
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1).strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Malformed JSON in batch response — defaulting all to HOLD")
            return {
                stock["stock_code"]: TradeDecision(
                    action="HOLD",
                    confidence=0,
                    rationale="Malformed JSON response",
                    token_count=0,
                    cached=False,
                )
                for stock in stocks_data
            }

        if not isinstance(data, list):
            logger.warning("Batch response is not a JSON array — defaulting all to HOLD")
            return {
                stock["stock_code"]: TradeDecision(
                    action="HOLD",
                    confidence=0,
                    rationale="Invalid response format",
                    token_count=0,
                    cached=False,
                )
                for stock in stocks_data
            }

        # Build decision map
        decisions: dict[str, TradeDecision] = {}
        stock_codes = {stock["stock_code"] for stock in stocks_data}

        for item in data:
            if not isinstance(item, dict):
                continue

            code = item.get("code")
            if not code or code not in stock_codes:
                continue

            # Validate required fields
            if not all(k in item for k in ("action", "confidence", "rationale")):
                logger.warning("Missing fields for %s — using HOLD", code)
                decisions[code] = TradeDecision(
                    action="HOLD",
                    confidence=0,
                    rationale="Missing required fields",
                    token_count=0,
                    cached=False,
                )
                continue

            action = str(item["action"]).upper()
            if action not in VALID_ACTIONS:
                logger.warning("Invalid action '%s' for %s — forcing HOLD", action, code)
                action = "HOLD"

            confidence = int(item["confidence"])
            rationale = str(item["rationale"])

            # Enforce confidence threshold
            if confidence < self._confidence_threshold:
                logger.info(
                    "Confidence %d < threshold %d for %s — forcing HOLD",
                    confidence,
                    self._confidence_threshold,
                    code,
                )
                action = "HOLD"

            decisions[code] = TradeDecision(
                action=action,
                confidence=confidence,
                rationale=rationale,
                token_count=token_count // len(stocks_data),  # Split token cost
                cached=False,
            )
            self._total_decisions += 1

        # Fill in missing stocks with HOLD
        for stock in stocks_data:
            code = stock["stock_code"]
            if code not in decisions:
                logger.warning("No decision for %s in batch response — using HOLD", code)
                decisions[code] = TradeDecision(
                    action="HOLD",
                    confidence=0,
                    rationale="Not found in batch response",
                    token_count=0,
                    cached=False,
                )

        logger.info(
            "Batch decision completed for %d stocks",
            len(decisions),
            extra={"tokens": token_count},
        )

        return decisions
