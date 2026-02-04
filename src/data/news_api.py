"""News API integration with sentiment analysis and caching.

Fetches real-time news for stocks using free-tier APIs (Alpha Vantage or NewsAPI).
Includes 5-minute caching to minimize API quota usage.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# Cache entries expire after 5 minutes
CACHE_TTL_SECONDS = 300


@dataclass
class NewsArticle:
    """Single news article with sentiment."""

    title: str
    summary: str
    source: str
    published_at: str
    sentiment_score: float  # -1.0 (negative) to +1.0 (positive)
    url: str


@dataclass
class NewsSentiment:
    """Aggregated news sentiment for a stock."""

    stock_code: str
    articles: list[NewsArticle]
    avg_sentiment: float  # Average sentiment across all articles
    article_count: int
    fetched_at: float  # Unix timestamp


class NewsAPI:
    """News API client with sentiment analysis and caching."""

    def __init__(
        self,
        api_key: str | None = None,
        provider: str = "alphavantage",
        cache_ttl: int = CACHE_TTL_SECONDS,
    ) -> None:
        """Initialize NewsAPI client.

        Args:
            api_key: API key for the news provider (None for testing)
            provider: News provider ("alphavantage" or "newsapi")
            cache_ttl: Cache time-to-live in seconds
        """
        self._api_key = api_key
        self._provider = provider
        self._cache_ttl = cache_ttl
        self._cache: dict[str, NewsSentiment] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_news_sentiment(self, stock_code: str) -> NewsSentiment | None:
        """Fetch news sentiment for a stock with caching.

        Args:
            stock_code: Stock ticker symbol (e.g., "AAPL", "005930")

        Returns:
            NewsSentiment object or None if fetch fails or API unavailable
        """
        # Check cache first
        cached = self._get_from_cache(stock_code)
        if cached is not None:
            logger.debug("News cache hit for %s", stock_code)
            return cached

        # API key required for real requests
        if self._api_key is None:
            logger.warning("No news API key provided — returning None")
            return None

        # Fetch from API
        try:
            sentiment = await self._fetch_news(stock_code)
            if sentiment is not None:
                self._cache[stock_code] = sentiment
            return sentiment
        except Exception as exc:
            logger.error("Failed to fetch news for %s: %s", stock_code, exc)
            return None

    def clear_cache(self) -> None:
        """Clear the news cache (useful for testing)."""
        self._cache.clear()

    # ------------------------------------------------------------------
    # Cache Management
    # ------------------------------------------------------------------

    def _get_from_cache(self, stock_code: str) -> NewsSentiment | None:
        """Retrieve cached sentiment if not expired."""
        if stock_code not in self._cache:
            return None

        cached = self._cache[stock_code]
        age = time.time() - cached.fetched_at

        if age > self._cache_ttl:
            logger.debug("News cache expired for %s (age: %.1fs)", stock_code, age)
            del self._cache[stock_code]
            return None

        return cached

    # ------------------------------------------------------------------
    # API Fetching
    # ------------------------------------------------------------------

    async def _fetch_news(self, stock_code: str) -> NewsSentiment | None:
        """Fetch news from the provider API."""
        if self._provider == "alphavantage":
            return await self._fetch_alphavantage(stock_code)
        elif self._provider == "newsapi":
            return await self._fetch_newsapi(stock_code)
        else:
            logger.error("Unknown news provider: %s", self._provider)
            return None

    async def _fetch_alphavantage(self, stock_code: str) -> NewsSentiment | None:
        """Fetch news from Alpha Vantage News Sentiment API."""
        url = "https://www.alphavantage.co/query"
        params = {
            "function": "NEWS_SENTIMENT",
            "tickers": stock_code,
            "apikey": self._api_key,
            "limit": 10,  # Fetch top 10 articles
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status != 200:
                        logger.error(
                            "Alpha Vantage API error: HTTP %d", resp.status
                        )
                        return None

                    data = await resp.json()
                    return self._parse_alphavantage_response(stock_code, data)

        except Exception as exc:
            logger.error("Alpha Vantage request failed: %s", exc)
            return None

    async def _fetch_newsapi(self, stock_code: str) -> NewsSentiment | None:
        """Fetch news from NewsAPI.org."""
        url = "https://newsapi.org/v2/everything"
        params = {
            "q": stock_code,
            "apiKey": self._api_key,
            "pageSize": 10,
            "sortBy": "publishedAt",
            "language": "en",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status != 200:
                        logger.error("NewsAPI error: HTTP %d", resp.status)
                        return None

                    data = await resp.json()
                    return self._parse_newsapi_response(stock_code, data)

        except Exception as exc:
            logger.error("NewsAPI request failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Response Parsing
    # ------------------------------------------------------------------

    def _parse_alphavantage_response(
        self, stock_code: str, data: dict[str, Any]
    ) -> NewsSentiment | None:
        """Parse Alpha Vantage API response."""
        if "feed" not in data:
            logger.warning("No 'feed' key in Alpha Vantage response")
            return None

        articles: list[NewsArticle] = []
        for item in data["feed"]:
            # Extract sentiment for this specific ticker
            ticker_sentiment = self._extract_ticker_sentiment(item, stock_code)

            article = NewsArticle(
                title=item.get("title", ""),
                summary=item.get("summary", "")[:200],  # Truncate long summaries
                source=item.get("source", "Unknown"),
                published_at=item.get("time_published", ""),
                sentiment_score=ticker_sentiment,
                url=item.get("url", ""),
            )
            articles.append(article)

        if not articles:
            return None

        avg_sentiment = sum(a.sentiment_score for a in articles) / len(articles)

        return NewsSentiment(
            stock_code=stock_code,
            articles=articles,
            avg_sentiment=avg_sentiment,
            article_count=len(articles),
            fetched_at=time.time(),
        )

    def _extract_ticker_sentiment(
        self, item: dict[str, Any], stock_code: str
    ) -> float:
        """Extract sentiment score for specific ticker from article."""
        ticker_sentiments = item.get("ticker_sentiment", [])
        for ts in ticker_sentiments:
            if ts.get("ticker", "").upper() == stock_code.upper():
                # Alpha Vantage provides sentiment_score as string
                score_str = ts.get("ticker_sentiment_score", "0")
                try:
                    return float(score_str)
                except ValueError:
                    return 0.0

        # Fallback to overall sentiment if ticker-specific not found
        overall_sentiment = item.get("overall_sentiment_score", "0")
        try:
            return float(overall_sentiment)
        except ValueError:
            return 0.0

    def _parse_newsapi_response(
        self, stock_code: str, data: dict[str, Any]
    ) -> NewsSentiment | None:
        """Parse NewsAPI.org response.

        Note: NewsAPI doesn't provide sentiment scores, so we use a
        simple heuristic based on title keywords.
        """
        if data.get("status") != "ok" or "articles" not in data:
            logger.warning("Invalid NewsAPI response")
            return None

        articles: list[NewsArticle] = []
        for item in data["articles"]:
            # Simple sentiment heuristic based on keywords
            sentiment = self._estimate_sentiment_from_text(
                item.get("title", "") + " " + item.get("description", "")
            )

            article = NewsArticle(
                title=item.get("title", ""),
                summary=item.get("description", "")[:200],
                source=item.get("source", {}).get("name", "Unknown"),
                published_at=item.get("publishedAt", ""),
                sentiment_score=sentiment,
                url=item.get("url", ""),
            )
            articles.append(article)

        if not articles:
            return None

        avg_sentiment = sum(a.sentiment_score for a in articles) / len(articles)

        return NewsSentiment(
            stock_code=stock_code,
            articles=articles,
            avg_sentiment=avg_sentiment,
            article_count=len(articles),
            fetched_at=time.time(),
        )

    def _estimate_sentiment_from_text(self, text: str) -> float:
        """Simple keyword-based sentiment estimation.

        This is a fallback for APIs that don't provide sentiment scores.
        Returns a score between -1.0 and +1.0.
        """
        text_lower = text.lower()

        positive_keywords = [
            "surge", "jump", "gain", "rise", "soar", "rally", "profit",
            "growth", "upgrade", "beat", "strong", "bullish", "breakthrough",
        ]
        negative_keywords = [
            "plunge", "fall", "drop", "decline", "crash", "loss", "weak",
            "downgrade", "miss", "bearish", "concern", "risk", "warning",
        ]

        positive_count = sum(1 for kw in positive_keywords if kw in text_lower)
        negative_count = sum(1 for kw in negative_keywords if kw in text_lower)

        total = positive_count + negative_count
        if total == 0:
            return 0.0

        # Normalize to -1.0 to +1.0 range
        return (positive_count - negative_count) / total
