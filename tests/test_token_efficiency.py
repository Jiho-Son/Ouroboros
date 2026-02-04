"""Tests for token efficiency optimization components.

Tests cover:
- Prompt compression and optimization
- Context selection logic
- Summarization
- Caching
- Token reduction metrics
"""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime, timedelta

import pytest

from src.brain.cache import CacheMetrics, DecisionCache
from src.brain.context_selector import ContextSelector, DecisionType
from src.brain.gemini_client import TradeDecision
from src.brain.prompt_optimizer import PromptOptimizer, TokenMetrics
from src.context.layer import ContextLayer
from src.context.store import ContextStore
from src.context.summarizer import ContextSummarizer, SummaryStats


# ============================================================================
# Prompt Optimizer Tests
# ============================================================================


class TestPromptOptimizer:
    """Tests for PromptOptimizer."""

    def test_estimate_tokens(self):
        """Test token estimation."""
        optimizer = PromptOptimizer()

        # Empty text
        assert optimizer.estimate_tokens("") == 0

        # Short text (4 chars = 1 token estimate)
        assert optimizer.estimate_tokens("test") == 1

        # Longer text
        text = "This is a longer piece of text for testing token estimation."
        tokens = optimizer.estimate_tokens(text)
        assert tokens > 0
        assert tokens == len(text) // 4

    def test_count_tokens(self):
        """Test token counting metrics."""
        optimizer = PromptOptimizer()

        text = "Hello world, this is a test."
        metrics = optimizer.count_tokens(text)

        assert isinstance(metrics, TokenMetrics)
        assert metrics.char_count == len(text)
        assert metrics.word_count == 6
        assert metrics.estimated_tokens > 0

    def test_compress_json(self):
        """Test JSON compression."""
        optimizer = PromptOptimizer()

        data = {
            "action": "BUY",
            "confidence": 85,
            "rationale": "Strong uptrend",
        }

        compressed = optimizer.compress_json(data)

        # Should have no newlines and minimal whitespace
        assert "\n" not in compressed
        # Note: JSON values may contain spaces (e.g., "Strong uptrend")
        # but there should be no spaces around separators
        assert ": " not in compressed
        assert ", " not in compressed

        # Should be valid JSON
        import json

        parsed = json.loads(compressed)
        assert parsed == data

    def test_abbreviate_text(self):
        """Test text abbreviation."""
        optimizer = PromptOptimizer()

        text = "The current price is high and volume is increasing."
        abbreviated = optimizer.abbreviate_text(text)

        # Should contain abbreviations
        assert "cur" in abbreviated or "P" in abbreviated
        assert len(abbreviated) <= len(text)

    def test_abbreviate_text_aggressive(self):
        """Test aggressive text abbreviation."""
        optimizer = PromptOptimizer()

        text = "The price is increasing and the volume is high."
        abbreviated = optimizer.abbreviate_text(text, aggressive=True)

        # Should be shorter
        assert len(abbreviated) < len(text)

        # Should have removed articles
        assert "the" not in abbreviated.lower()

    def test_build_compressed_prompt(self):
        """Test compressed prompt building."""
        optimizer = PromptOptimizer()

        market_data = {
            "stock_code": "005930",
            "current_price": 75000,
            "market_name": "Korean stock market",
        }

        prompt = optimizer.build_compressed_prompt(market_data)

        # Should be much shorter than original
        assert len(prompt) < 300
        assert "005930" in prompt
        assert "75000" in prompt

    def test_build_compressed_prompt_no_instructions(self):
        """Test compressed prompt without instructions."""
        optimizer = PromptOptimizer()

        market_data = {
            "stock_code": "AAPL",
            "current_price": 150.5,
            "market_name": "United States",
        }

        prompt = optimizer.build_compressed_prompt(market_data, include_instructions=False)

        # Should be very short (data only)
        assert len(prompt) < 100
        assert "AAPL" in prompt

    def test_truncate_context(self):
        """Test context truncation."""
        optimizer = PromptOptimizer()

        context = {
            "price": 100.5,
            "volume": 1000000,
            "sentiment": 0.8,
            "extra_data": "Some long text that should be truncated",
        }

        # Truncate to small budget
        truncated = optimizer.truncate_context(context, max_tokens=10)

        # Should have fewer keys
        assert len(truncated) <= len(context)

    def test_truncate_context_with_priority(self):
        """Test context truncation with priority keys."""
        optimizer = PromptOptimizer()

        context = {
            "price": 100.5,
            "volume": 1000000,
            "sentiment": 0.8,
            "extra_data": "Some data",
        }

        priority_keys = ["price", "sentiment"]
        truncated = optimizer.truncate_context(context, max_tokens=20, priority_keys=priority_keys)

        # Priority keys should be included
        assert "price" in truncated
        assert "sentiment" in truncated

    def test_calculate_compression_ratio(self):
        """Test compression ratio calculation."""
        optimizer = PromptOptimizer()

        original = "This is a very long piece of text that should be compressed significantly."
        compressed = "Short text"

        ratio = optimizer.calculate_compression_ratio(original, compressed)

        # Ratio should be > 1 (original is longer)
        assert ratio > 1.0


# ============================================================================
# Context Selector Tests
# ============================================================================


class TestContextSelector:
    """Tests for ContextSelector."""

    @pytest.fixture
    def store(self):
        """Create in-memory ContextStore."""
        conn = sqlite3.connect(":memory:")
        # Create tables
        conn.execute(
            """
            CREATE TABLE context_metadata (
                layer TEXT PRIMARY KEY,
                description TEXT,
                retention_days INTEGER,
                aggregation_source TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE contexts (
                layer TEXT,
                timeframe TEXT,
                key TEXT,
                value TEXT,
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (layer, timeframe, key)
            )
            """
        )
        conn.commit()
        return ContextStore(conn)

    def test_select_layers_normal(self, store):
        """Test layer selection for normal decisions."""
        selector = ContextSelector(store)

        layers = selector.select_layers(DecisionType.NORMAL)

        # Should only select L7 (real-time)
        assert layers == [ContextLayer.L7_REALTIME]

    def test_select_layers_strategic(self, store):
        """Test layer selection for strategic decisions."""
        selector = ContextSelector(store)

        layers = selector.select_layers(DecisionType.STRATEGIC)

        # Should select L7 + L6 + L5
        assert ContextLayer.L7_REALTIME in layers
        assert ContextLayer.L6_DAILY in layers
        assert ContextLayer.L5_WEEKLY in layers
        assert len(layers) == 3

    def test_select_layers_major_event(self, store):
        """Test layer selection for major events."""
        selector = ContextSelector(store)

        layers = selector.select_layers(DecisionType.MAJOR_EVENT)

        # Should select all layers
        assert len(layers) == 7
        assert ContextLayer.L1_LEGACY in layers
        assert ContextLayer.L7_REALTIME in layers

    def test_score_layer_relevance(self, store):
        """Test layer relevance scoring."""
        selector = ContextSelector(store)

        # Add some data first so scores aren't penalized
        store.set_context(ContextLayer.L7_REALTIME, "2026-02-04", "price", 100.5)
        store.set_context(ContextLayer.L1_LEGACY, "legacy", "lesson", "test")

        # L7 should have high score for normal decisions
        score = selector.score_layer_relevance(ContextLayer.L7_REALTIME, DecisionType.NORMAL)
        assert score == 1.0

        # L1 should have low score for normal decisions
        score = selector.score_layer_relevance(ContextLayer.L1_LEGACY, DecisionType.NORMAL)
        assert score == 0.0

        # L1 should have high score for major events
        score = selector.score_layer_relevance(ContextLayer.L1_LEGACY, DecisionType.MAJOR_EVENT)
        assert score == 1.0

    def test_select_with_scoring(self, store):
        """Test selection with relevance scoring."""
        selector = ContextSelector(store)

        # Add data so layers aren't penalized
        store.set_context(ContextLayer.L7_REALTIME, "2026-02-04", "price", 100.5)

        selection = selector.select_with_scoring(DecisionType.NORMAL, min_score=0.5)

        # Should only select high-relevance layers
        assert len(selection.layers) >= 1
        assert ContextLayer.L7_REALTIME in selection.layers
        assert all(selection.relevance_scores[l] >= 0.5 for l in selection.layers)

    def test_get_context_data(self, store):
        """Test context data retrieval."""
        selector = ContextSelector(store)

        # Add some test data
        store.set_context(ContextLayer.L7_REALTIME, "2026-02-04", "price", 100.5)
        store.set_context(ContextLayer.L7_REALTIME, "2026-02-04", "volume", 1000000)

        context_data = selector.get_context_data([ContextLayer.L7_REALTIME])

        # Should retrieve data
        assert "L7_REALTIME" in context_data
        assert "price" in context_data["L7_REALTIME"]
        assert context_data["L7_REALTIME"]["price"] == 100.5

    def test_estimate_context_tokens(self, store):
        """Test context token estimation."""
        selector = ContextSelector(store)

        context_data = {
            "L7_REALTIME": {"price": 100.5, "volume": 1000000},
            "L6_DAILY": {"avg_price": 99.8, "avg_volume": 950000},
        }

        tokens = selector.estimate_context_tokens(context_data)

        # Should estimate tokens
        assert tokens > 0

    def test_optimize_context_for_budget(self, store):
        """Test context optimization for token budget."""
        selector = ContextSelector(store)

        # Add test data
        store.set_context(ContextLayer.L7_REALTIME, "2026-02-04", "price", 100.5)

        # Get optimized context within budget
        context = selector.optimize_context_for_budget(DecisionType.NORMAL, max_tokens=50)

        # Should return data within budget
        tokens = selector.estimate_context_tokens(context)
        assert tokens <= 50


# ============================================================================
# Context Summarizer Tests
# ============================================================================


class TestContextSummarizer:
    """Tests for ContextSummarizer."""

    @pytest.fixture
    def store(self):
        """Create in-memory ContextStore."""
        conn = sqlite3.connect(":memory:")
        conn.execute(
            """
            CREATE TABLE context_metadata (
                layer TEXT PRIMARY KEY,
                description TEXT,
                retention_days INTEGER,
                aggregation_source TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE contexts (
                layer TEXT,
                timeframe TEXT,
                key TEXT,
                value TEXT,
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (layer, timeframe, key)
            )
            """
        )
        conn.commit()
        return ContextStore(conn)

    def test_summarize_numeric_values(self, store):
        """Test numeric value summarization."""
        summarizer = ContextSummarizer(store)

        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        stats = summarizer.summarize_numeric_values(values)

        assert isinstance(stats, SummaryStats)
        assert stats.count == 5
        assert stats.mean == 30.0
        assert stats.min == 10.0
        assert stats.max == 50.0
        assert stats.std is not None

    def test_summarize_numeric_values_trend(self, store):
        """Test trend detection in numeric values."""
        summarizer = ContextSummarizer(store)

        # Uptrend
        values_up = [10.0, 15.0, 20.0, 25.0, 30.0, 35.0]
        stats_up = summarizer.summarize_numeric_values(values_up)
        assert stats_up.trend == "up"

        # Downtrend
        values_down = [35.0, 30.0, 25.0, 20.0, 15.0, 10.0]
        stats_down = summarizer.summarize_numeric_values(values_down)
        assert stats_down.trend == "down"

        # Flat
        values_flat = [20.0, 20.1, 19.9, 20.0, 20.1, 19.9]
        stats_flat = summarizer.summarize_numeric_values(values_flat)
        assert stats_flat.trend == "flat"

    def test_summarize_layer(self, store):
        """Test layer summarization."""
        summarizer = ContextSummarizer(store)

        # Add test data
        store.set_context(ContextLayer.L6_DAILY, "2026-02-04", "price", 100.5)
        store.set_context(ContextLayer.L6_DAILY, "2026-02-04", "volume", 1000000)

        summary = summarizer.summarize_layer(ContextLayer.L6_DAILY)

        # Should have summary
        assert "total_entries" in summary
        assert summary["total_entries"] > 0

    def test_create_compact_summary(self, store):
        """Test compact summary creation."""
        summarizer = ContextSummarizer(store)

        # Add test data
        store.set_context(ContextLayer.L7_REALTIME, "2026-02-04", "price", 100.5)

        layers = [ContextLayer.L7_REALTIME, ContextLayer.L6_DAILY]
        summary = summarizer.create_compact_summary(layers, top_n_metrics=3)

        # Should have summaries for layers
        assert "L7_REALTIME" in summary

    def test_format_summary_for_prompt(self, store):
        """Test summary formatting for prompt."""
        summarizer = ContextSummarizer(store)

        summary = {
            "L7_REALTIME": {
                "price": {"avg": 100.5, "trend": "up"},
                "volume": {"avg": 1000000, "trend": "flat"},
            }
        }

        formatted = summarizer.format_summary_for_prompt(summary)

        # Should be formatted string
        assert isinstance(formatted, str)
        assert "L7_REALTIME" in formatted
        assert "100.5" in formatted or "100.50" in formatted


# ============================================================================
# Decision Cache Tests
# ============================================================================


class TestDecisionCache:
    """Tests for DecisionCache."""

    def test_cache_init(self):
        """Test cache initialization."""
        cache = DecisionCache(ttl_seconds=60, max_size=100)

        assert cache.ttl_seconds == 60
        assert cache.max_size == 100

    def test_cache_miss(self):
        """Test cache miss."""
        cache = DecisionCache()

        market_data = {"stock_code": "005930", "current_price": 75000}

        decision = cache.get(market_data)

        # Should be None (cache miss)
        assert decision is None

        metrics = cache.get_metrics()
        assert metrics.cache_misses == 1
        assert metrics.cache_hits == 0

    def test_cache_hit(self):
        """Test cache hit."""
        cache = DecisionCache()

        market_data = {"stock_code": "005930", "current_price": 75000}
        decision = TradeDecision(action="HOLD", confidence=50, rationale="Test")

        # Set cache
        cache.set(market_data, decision)

        # Get from cache
        cached = cache.get(market_data)

        assert cached is not None
        assert cached.action == "HOLD"
        assert cached.confidence == 50

        metrics = cache.get_metrics()
        assert metrics.cache_hits == 1

    def test_cache_ttl_expiration(self):
        """Test cache TTL expiration."""
        cache = DecisionCache(ttl_seconds=1)  # 1 second TTL

        market_data = {"stock_code": "005930", "current_price": 75000}
        decision = TradeDecision(action="HOLD", confidence=50, rationale="Test")

        # Set cache
        cache.set(market_data, decision)

        # Should hit immediately
        cached = cache.get(market_data)
        assert cached is not None

        # Wait for expiration
        time.sleep(1.1)

        # Should miss after expiration
        cached = cache.get(market_data)
        assert cached is None

    def test_cache_max_size(self):
        """Test cache max size eviction."""
        cache = DecisionCache(max_size=2)

        decision = TradeDecision(action="HOLD", confidence=50, rationale="Test")

        # Add 3 entries (exceeds max_size)
        for i in range(3):
            market_data = {"stock_code": f"00{i}", "current_price": 1000 * i}
            cache.set(market_data, decision)

        metrics = cache.get_metrics()

        # Should have evicted 1 entry
        assert metrics.total_entries == 2
        assert metrics.evictions == 1

    def test_invalidate_all(self):
        """Test invalidate all cache entries."""
        cache = DecisionCache()

        decision = TradeDecision(action="HOLD", confidence=50, rationale="Test")

        # Add entries
        for i in range(3):
            market_data = {"stock_code": f"00{i}", "current_price": 1000}
            cache.set(market_data, decision)

        # Invalidate all
        count = cache.invalidate()

        assert count == 3

        metrics = cache.get_metrics()
        assert metrics.total_entries == 0

    def test_invalidate_by_stock(self):
        """Test invalidate cache by stock code."""
        cache = DecisionCache()

        decision = TradeDecision(action="HOLD", confidence=50, rationale="Test")

        # Add entries for different stocks
        cache.set({"stock_code": "005930", "current_price": 75000}, decision)
        cache.set({"stock_code": "000660", "current_price": 50000}, decision)

        # Invalidate specific stock
        count = cache.invalidate("005930")

        assert count >= 1

        # Other stock should still be cached
        cached = cache.get({"stock_code": "000660", "current_price": 50000})
        assert cached is not None

    def test_cleanup_expired(self):
        """Test cleanup of expired entries."""
        cache = DecisionCache(ttl_seconds=1)

        decision = TradeDecision(action="HOLD", confidence=50, rationale="Test")

        # Add entry
        cache.set({"stock_code": "005930", "current_price": 75000}, decision)

        # Wait for expiration
        time.sleep(1.1)

        # Cleanup
        count = cache.cleanup_expired()

        assert count == 1

        metrics = cache.get_metrics()
        assert metrics.total_entries == 0

    def test_should_cache_decision(self):
        """Test decision caching criteria."""
        cache = DecisionCache()

        # HOLD decisions should be cached
        hold_decision = TradeDecision(action="HOLD", confidence=50, rationale="Test")
        assert cache.should_cache_decision(hold_decision) is True

        # High confidence BUY should be cached
        buy_decision = TradeDecision(action="BUY", confidence=95, rationale="Test")
        assert cache.should_cache_decision(buy_decision) is True

        # Low confidence BUY should not be cached
        low_conf_buy = TradeDecision(action="BUY", confidence=60, rationale="Test")
        assert cache.should_cache_decision(low_conf_buy) is False

    def test_cache_hit_rate(self):
        """Test cache hit rate calculation."""
        cache = DecisionCache()

        decision = TradeDecision(action="HOLD", confidence=50, rationale="Test")
        market_data = {"stock_code": "005930", "current_price": 75000}

        # First request (miss)
        cache.get(market_data)

        # Set cache
        cache.set(market_data, decision)

        # Second request (hit)
        cache.get(market_data)

        # Third request (hit)
        cache.get(market_data)

        metrics = cache.get_metrics()

        # 1 miss, 2 hits out of 3 requests
        assert metrics.total_requests == 3
        assert metrics.cache_hits == 2
        assert metrics.cache_misses == 1
        assert metrics.hit_rate == pytest.approx(2 / 3)

    def test_reset_metrics(self):
        """Test metrics reset."""
        cache = DecisionCache()

        market_data = {"stock_code": "005930", "current_price": 75000}

        # Generate some activity
        cache.get(market_data)
        cache.get(market_data)

        # Reset
        cache.reset_metrics()

        metrics = cache.get_metrics()
        assert metrics.total_requests == 0
        assert metrics.cache_hits == 0
        assert metrics.cache_misses == 0
