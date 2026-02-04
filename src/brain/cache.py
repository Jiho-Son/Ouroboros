"""Response caching system for reducing redundant LLM calls.

This module provides caching for common trading scenarios:
- TTL-based cache invalidation
- Cache key based on market conditions
- Cache hit rate monitoring
- Special handling for HOLD decisions in quiet markets
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.brain.gemini_client import TradeDecision

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Cached decision with metadata."""

    decision: TradeDecision
    cached_at: float  # Unix timestamp
    hit_count: int = 0
    market_data_hash: str = ""


@dataclass
class CacheMetrics:
    """Metrics for cache performance monitoring."""

    total_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    evictions: int = 0
    total_entries: int = 0

    @property
    def hit_rate(self) -> float:
        """Calculate cache hit rate."""
        if self.total_requests == 0:
            return 0.0
        return self.cache_hits / self.total_requests

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            "total_requests": self.total_requests,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "hit_rate": self.hit_rate,
            "evictions": self.evictions,
            "total_entries": self.total_entries,
        }


class DecisionCache:
    """TTL-based cache for trade decisions."""

    def __init__(self, ttl_seconds: int = 300, max_size: int = 1000) -> None:
        """Initialize the decision cache.

        Args:
            ttl_seconds: Time-to-live for cache entries in seconds (default: 5 minutes)
            max_size: Maximum number of cache entries
        """
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._cache: dict[str, CacheEntry] = {}
        self._metrics = CacheMetrics()

    def _generate_cache_key(self, market_data: dict[str, Any]) -> str:
        """Generate cache key from market data.

        Key is based on:
        - Stock code
        - Current price (rounded to reduce sensitivity)
        - Market conditions (orderbook snapshot)

        Args:
            market_data: Market data dictionary

        Returns:
            Cache key string
        """
        # Extract key components
        stock_code = market_data.get("stock_code", "UNKNOWN")
        current_price = market_data.get("current_price", 0)

        # Round price to reduce sensitivity (cache hits for similar prices)
        # For prices > 1000, round to nearest 10
        # For prices < 1000, round to nearest 1
        if current_price > 1000:
            price_rounded = round(current_price / 10) * 10
        else:
            price_rounded = round(current_price)

        # Include orderbook snapshot (if available)
        orderbook_key = ""
        if "orderbook" in market_data and market_data["orderbook"]:
            ob = market_data["orderbook"]
            # Just use bid/ask spread as indicator
            if "bid" in ob and "ask" in ob and ob["bid"] and ob["ask"]:
                bid_price = ob["bid"][0].get("price", 0) if ob["bid"] else 0
                ask_price = ob["ask"][0].get("price", 0) if ob["ask"] else 0
                spread = ask_price - bid_price
                orderbook_key = f"_spread{spread}"

        # Generate cache key
        key_str = f"{stock_code}_{price_rounded}{orderbook_key}"

        return key_str

    def _generate_market_hash(self, market_data: dict[str, Any]) -> str:
        """Generate hash of full market data for invalidation checks.

        Args:
            market_data: Market data dictionary

        Returns:
            Hash string
        """
        # Create stable JSON representation
        stable_json = json.dumps(market_data, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(stable_json.encode()).hexdigest()

    def get(self, market_data: dict[str, Any]) -> TradeDecision | None:
        """Retrieve cached decision if valid.

        Args:
            market_data: Market data dictionary

        Returns:
            Cached TradeDecision if valid, None otherwise
        """
        self._metrics.total_requests += 1

        cache_key = self._generate_cache_key(market_data)

        if cache_key not in self._cache:
            self._metrics.cache_misses += 1
            return None

        entry = self._cache[cache_key]
        current_time = time.time()

        # Check TTL
        if current_time - entry.cached_at > self.ttl_seconds:
            # Expired
            del self._cache[cache_key]
            self._metrics.cache_misses += 1
            self._metrics.evictions += 1
            logger.debug("Cache expired for key: %s", cache_key)
            return None

        # Cache hit
        entry.hit_count += 1
        self._metrics.cache_hits += 1
        logger.debug("Cache hit for key: %s (hits: %d)", cache_key, entry.hit_count)

        return entry.decision

    def set(
        self,
        market_data: dict[str, Any],
        decision: TradeDecision,
    ) -> None:
        """Store decision in cache.

        Args:
            market_data: Market data dictionary
            decision: TradeDecision to cache
        """
        cache_key = self._generate_cache_key(market_data)
        market_hash = self._generate_market_hash(market_data)

        # Enforce max size (evict oldest if full)
        if len(self._cache) >= self.max_size:
            # Find oldest entry
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k].cached_at)
            del self._cache[oldest_key]
            self._metrics.evictions += 1
            logger.debug("Cache full, evicted key: %s", oldest_key)

        # Store entry
        entry = CacheEntry(
            decision=decision,
            cached_at=time.time(),
            market_data_hash=market_hash,
        )
        self._cache[cache_key] = entry
        self._metrics.total_entries = len(self._cache)

        logger.debug("Cached decision for key: %s", cache_key)

    def invalidate(self, stock_code: str | None = None) -> int:
        """Invalidate cache entries.

        Args:
            stock_code: Specific stock code to invalidate, or None for all

        Returns:
            Number of entries invalidated
        """
        if stock_code is None:
            # Clear all
            count = len(self._cache)
            self._cache.clear()
            self._metrics.evictions += count
            self._metrics.total_entries = 0
            logger.info("Invalidated all cache entries (%d)", count)
            return count

        # Invalidate specific stock
        keys_to_remove = [k for k in self._cache.keys() if k.startswith(f"{stock_code}_")]
        count = len(keys_to_remove)

        for key in keys_to_remove:
            del self._cache[key]

        self._metrics.evictions += count
        self._metrics.total_entries = len(self._cache)
        logger.info("Invalidated %d cache entries for stock: %s", count, stock_code)

        return count

    def cleanup_expired(self) -> int:
        """Remove expired entries from cache.

        Returns:
            Number of entries removed
        """
        current_time = time.time()
        expired_keys = [
            k
            for k, v in self._cache.items()
            if current_time - v.cached_at > self.ttl_seconds
        ]

        count = len(expired_keys)
        for key in expired_keys:
            del self._cache[key]

        self._metrics.evictions += count
        self._metrics.total_entries = len(self._cache)

        if count > 0:
            logger.debug("Cleaned up %d expired cache entries", count)

        return count

    def get_metrics(self) -> CacheMetrics:
        """Get current cache metrics.

        Returns:
            CacheMetrics object with current statistics
        """
        return self._metrics

    def reset_metrics(self) -> None:
        """Reset cache metrics."""
        self._metrics = CacheMetrics(total_entries=len(self._cache))
        logger.info("Cache metrics reset")

    def should_cache_decision(self, decision: TradeDecision) -> bool:
        """Determine if a decision should be cached.

        HOLD decisions with low confidence are good candidates for caching,
        as they're likely to recur in quiet markets.

        Args:
            decision: TradeDecision to evaluate

        Returns:
            True if decision should be cached
        """
        # Cache HOLD decisions (common in quiet markets)
        if decision.action == "HOLD":
            return True

        # Cache high-confidence decisions (stable signals)
        if decision.confidence >= 90:
            return True

        # Don't cache low-confidence BUY/SELL (volatile signals)
        return False
