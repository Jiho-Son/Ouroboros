"""Tests for external data integration (news, economic calendar, market data)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.brain.gemini_client import GeminiClient
from src.data.economic_calendar import EconomicCalendar, EconomicEvent
from src.data.market_data import MarketBreadth, MarketData, MarketSentiment
from src.data.news_api import NewsAPI, NewsArticle, NewsSentiment

# ---------------------------------------------------------------------------
# NewsAPI Tests
# ---------------------------------------------------------------------------


class TestNewsAPI:
    """Test news API integration with caching."""

    def test_news_api_init_without_key(self):
        """NewsAPI should initialize without API key for testing."""
        api = NewsAPI(api_key=None)
        assert api._api_key is None
        assert api._provider == "alphavantage"
        assert api._cache_ttl == 300

    def test_news_api_init_with_custom_settings(self):
        """NewsAPI should accept custom provider and cache TTL."""
        api = NewsAPI(api_key="test_key", provider="newsapi", cache_ttl=600)
        assert api._api_key == "test_key"
        assert api._provider == "newsapi"
        assert api._cache_ttl == 600

    @pytest.mark.asyncio
    async def test_get_news_sentiment_without_api_key_returns_none(self):
        """Without API key, get_news_sentiment should return None."""
        api = NewsAPI(api_key=None)
        result = await api.get_news_sentiment("AAPL")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_sentiment(self):
        """Cache hit should return cached sentiment without API call."""
        api = NewsAPI(api_key="test_key")

        # Manually populate cache
        cached_sentiment = NewsSentiment(
            stock_code="AAPL",
            articles=[],
            avg_sentiment=0.5,
            article_count=0,
            fetched_at=time.time(),
        )
        api._cache["AAPL"] = cached_sentiment

        result = await api.get_news_sentiment("AAPL")
        assert result is cached_sentiment
        assert result.stock_code == "AAPL"

    @pytest.mark.asyncio
    async def test_cache_expiry_triggers_refetch(self):
        """Expired cache entry should trigger refetch."""
        api = NewsAPI(api_key="test_key", cache_ttl=1)

        # Add expired cache entry
        expired_sentiment = NewsSentiment(
            stock_code="AAPL",
            articles=[],
            avg_sentiment=0.5,
            article_count=0,
            fetched_at=time.time() - 10,  # 10 seconds ago
        )
        api._cache["AAPL"] = expired_sentiment

        # Mock the fetch to avoid real API call
        with patch.object(api, "_fetch_news", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = None
            result = await api.get_news_sentiment("AAPL")

            # Should have attempted refetch since cache expired
            mock_fetch.assert_called_once_with("AAPL")

    def test_clear_cache(self):
        """clear_cache should empty the cache."""
        api = NewsAPI(api_key="test_key")
        api._cache["AAPL"] = NewsSentiment(
            stock_code="AAPL",
            articles=[],
            avg_sentiment=0.0,
            article_count=0,
            fetched_at=time.time(),
        )
        assert len(api._cache) == 1

        api.clear_cache()
        assert len(api._cache) == 0

    def test_parse_alphavantage_response_with_valid_data(self):
        """Should parse Alpha Vantage response correctly."""
        api = NewsAPI(api_key="test_key", provider="alphavantage")

        mock_response = {
            "feed": [
                {
                    "title": "Apple hits new high",
                    "summary": "Apple stock surges to record levels",
                    "source": "Reuters",
                    "time_published": "2026-02-04T10:00:00",
                    "url": "https://example.com/1",
                    "ticker_sentiment": [
                        {"ticker": "AAPL", "ticker_sentiment_score": "0.85"}
                    ],
                    "overall_sentiment_score": "0.75",
                },
                {
                    "title": "Market volatility rises",
                    "summary": "Tech stocks face headwinds",
                    "source": "Bloomberg",
                    "time_published": "2026-02-04T09:00:00",
                    "url": "https://example.com/2",
                    "ticker_sentiment": [
                        {"ticker": "AAPL", "ticker_sentiment_score": "-0.3"}
                    ],
                    "overall_sentiment_score": "-0.2",
                },
            ]
        }

        result = api._parse_alphavantage_response("AAPL", mock_response)

        assert result is not None
        assert result.stock_code == "AAPL"
        assert result.article_count == 2
        assert len(result.articles) == 2
        assert result.articles[0].title == "Apple hits new high"
        assert result.articles[0].sentiment_score == 0.85
        assert result.articles[1].sentiment_score == -0.3
        # Average: (0.85 - 0.3) / 2 = 0.275
        assert abs(result.avg_sentiment - 0.275) < 0.01

    def test_parse_alphavantage_response_without_feed_returns_none(self):
        """Should return None if 'feed' key is missing."""
        api = NewsAPI(api_key="test_key", provider="alphavantage")
        result = api._parse_alphavantage_response("AAPL", {})
        assert result is None

    def test_parse_newsapi_response_with_valid_data(self):
        """Should parse NewsAPI.org response correctly."""
        api = NewsAPI(api_key="test_key", provider="newsapi")

        mock_response = {
            "status": "ok",
            "articles": [
                {
                    "title": "Apple stock surges",
                    "description": "Strong earnings beat expectations",
                    "source": {"name": "TechCrunch"},
                    "publishedAt": "2026-02-04T10:00:00Z",
                    "url": "https://example.com/1",
                },
                {
                    "title": "Tech sector faces risks",
                    "description": "Concerns over market downturn",
                    "source": {"name": "CNBC"},
                    "publishedAt": "2026-02-04T09:00:00Z",
                    "url": "https://example.com/2",
                },
            ],
        }

        result = api._parse_newsapi_response("AAPL", mock_response)

        assert result is not None
        assert result.stock_code == "AAPL"
        assert result.article_count == 2
        assert len(result.articles) == 2
        assert result.articles[0].title == "Apple stock surges"
        assert result.articles[0].source == "TechCrunch"

    def test_estimate_sentiment_from_text_positive(self):
        """Should detect positive sentiment from keywords."""
        api = NewsAPI()
        text = "Stock price surges with strong profit growth and upgrade"
        sentiment = api._estimate_sentiment_from_text(text)
        assert sentiment > 0.5

    def test_estimate_sentiment_from_text_negative(self):
        """Should detect negative sentiment from keywords."""
        api = NewsAPI()
        text = "Stock plunges on weak earnings, downgrade warning"
        sentiment = api._estimate_sentiment_from_text(text)
        assert sentiment < -0.5

    def test_estimate_sentiment_from_text_neutral(self):
        """Should return neutral sentiment without keywords."""
        api = NewsAPI()
        text = "Company announces quarterly report"
        sentiment = api._estimate_sentiment_from_text(text)
        assert abs(sentiment) < 0.1


# ---------------------------------------------------------------------------
# EconomicCalendar Tests
# ---------------------------------------------------------------------------


class TestEconomicCalendar:
    """Test economic calendar functionality."""

    def test_economic_calendar_init(self):
        """EconomicCalendar should initialize correctly."""
        calendar = EconomicCalendar(api_key="test_key")
        assert calendar._api_key == "test_key"
        assert len(calendar._events) == 0

    def test_add_event(self):
        """Should be able to add events to calendar."""
        calendar = EconomicCalendar()
        event = EconomicEvent(
            name="FOMC Meeting",
            event_type="FOMC",
            datetime=datetime(2026, 3, 18),
            impact="HIGH",
            country="US",
            description="Interest rate decision",
        )
        calendar.add_event(event)
        assert len(calendar._events) == 1
        assert calendar._events[0].name == "FOMC Meeting"

    def test_get_upcoming_events_filters_by_timeframe(self):
        """Should only return events within specified timeframe."""
        calendar = EconomicCalendar()

        # Add events at different times
        now = datetime.now()
        calendar.add_event(
            EconomicEvent(
                name="Event Tomorrow",
                event_type="GDP",
                datetime=now + timedelta(days=1),
                impact="HIGH",
                country="US",
                description="Test event",
            )
        )
        calendar.add_event(
            EconomicEvent(
                name="Event Next Month",
                event_type="CPI",
                datetime=now + timedelta(days=30),
                impact="HIGH",
                country="US",
                description="Test event",
            )
        )

        # Get events for next 7 days
        upcoming = calendar.get_upcoming_events(days_ahead=7, min_impact="HIGH")
        assert upcoming.high_impact_count == 1
        assert upcoming.events[0].name == "Event Tomorrow"

    def test_get_upcoming_events_filters_by_impact(self):
        """Should filter events by minimum impact level."""
        calendar = EconomicCalendar()

        now = datetime.now()
        calendar.add_event(
            EconomicEvent(
                name="High Impact Event",
                event_type="FOMC",
                datetime=now + timedelta(days=1),
                impact="HIGH",
                country="US",
                description="Test",
            )
        )
        calendar.add_event(
            EconomicEvent(
                name="Low Impact Event",
                event_type="OTHER",
                datetime=now + timedelta(days=1),
                impact="LOW",
                country="US",
                description="Test",
            )
        )

        # Filter for HIGH impact only
        upcoming = calendar.get_upcoming_events(days_ahead=7, min_impact="HIGH")
        assert upcoming.high_impact_count == 1
        assert upcoming.events[0].name == "High Impact Event"

        # Filter for MEDIUM and above (should still get HIGH)
        upcoming = calendar.get_upcoming_events(days_ahead=7, min_impact="MEDIUM")
        assert len(upcoming.events) == 1

        # Filter for LOW and above (should get both)
        upcoming = calendar.get_upcoming_events(days_ahead=7, min_impact="LOW")
        assert len(upcoming.events) == 2

    def test_get_earnings_date_returns_next_earnings(self):
        """Should return next earnings date for a stock."""
        calendar = EconomicCalendar()

        now = datetime.now()
        earnings_date = now + timedelta(days=5)

        calendar.add_event(
            EconomicEvent(
                name="AAPL Earnings",
                event_type="EARNINGS",
                datetime=earnings_date,
                impact="HIGH",
                country="US",
                description="Apple quarterly earnings",
            )
        )

        result = calendar.get_earnings_date("AAPL")
        assert result == earnings_date

    def test_get_earnings_date_returns_none_if_not_found(self):
        """Should return None if no earnings found for stock."""
        calendar = EconomicCalendar()
        result = calendar.get_earnings_date("UNKNOWN")
        assert result is None

    def test_load_hardcoded_events(self):
        """Should load hardcoded major economic events."""
        calendar = EconomicCalendar()
        calendar.load_hardcoded_events()

        # Should have multiple events (FOMC, GDP, CPI)
        assert len(calendar._events) > 10

        # Check for FOMC events
        fomc_events = [e for e in calendar._events if e.event_type == "FOMC"]
        assert len(fomc_events) > 0

        # Check for GDP events
        gdp_events = [e for e in calendar._events if e.event_type == "GDP"]
        assert len(gdp_events) > 0

        # Check for CPI events
        cpi_events = [e for e in calendar._events if e.event_type == "CPI"]
        assert len(cpi_events) == 12  # Monthly CPI releases

    def test_is_high_volatility_period_returns_true_near_high_impact(self):
        """Should return True if high-impact event is within threshold."""
        calendar = EconomicCalendar()

        now = datetime.now()
        calendar.add_event(
            EconomicEvent(
                name="FOMC Meeting",
                event_type="FOMC",
                datetime=now + timedelta(hours=12),
                impact="HIGH",
                country="US",
                description="Test",
            )
        )

        assert calendar.is_high_volatility_period(hours_ahead=24) is True

    def test_is_high_volatility_period_returns_false_when_no_events(self):
        """Should return False if no high-impact events nearby."""
        calendar = EconomicCalendar()
        assert calendar.is_high_volatility_period(hours_ahead=24) is False

    def test_clear_events(self):
        """Should clear all events."""
        calendar = EconomicCalendar()
        calendar.add_event(
            EconomicEvent(
                name="Test",
                event_type="TEST",
                datetime=datetime.now(),
                impact="LOW",
                country="US",
                description="Test",
            )
        )
        assert len(calendar._events) == 1

        calendar.clear_events()
        assert len(calendar._events) == 0


# ---------------------------------------------------------------------------
# MarketData Tests
# ---------------------------------------------------------------------------


class TestMarketData:
    """Test market data indicators."""

    def test_market_data_init(self):
        """MarketData should initialize correctly."""
        data = MarketData(api_key="test_key")
        assert data._api_key == "test_key"

    def test_get_market_sentiment_without_api_key_returns_neutral(self):
        """Without API key, should return NEUTRAL sentiment."""
        data = MarketData(api_key=None)
        sentiment = data.get_market_sentiment()
        assert sentiment == MarketSentiment.NEUTRAL

    def test_get_market_breadth_without_api_key_returns_none(self):
        """Without API key, should return None for breadth."""
        data = MarketData(api_key=None)
        breadth = data.get_market_breadth()
        assert breadth is None

    def test_get_sector_performance_without_api_key_returns_empty(self):
        """Without API key, should return empty list."""
        data = MarketData(api_key=None)
        sectors = data.get_sector_performance()
        assert sectors == []

    def test_get_market_indicators_returns_defaults_without_api(self):
        """Should return default indicators without API key."""
        data = MarketData(api_key=None)
        indicators = data.get_market_indicators()

        assert indicators.sentiment == MarketSentiment.NEUTRAL
        assert indicators.breadth.advance_decline_ratio == 1.0
        assert indicators.sector_performance == []
        assert indicators.vix_level is None

    def test_calculate_fear_greed_score_neutral_baseline(self):
        """Should return neutral score (50) for balanced market."""
        data = MarketData()
        breadth = MarketBreadth(
            advancing_stocks=500,
            declining_stocks=500,
            unchanged_stocks=100,
            new_highs=50,
            new_lows=50,
            advance_decline_ratio=1.0,
        )

        score = data.calculate_fear_greed_score(breadth)
        assert score == 50

    def test_calculate_fear_greed_score_greedy_market(self):
        """Should return high score for greedy market conditions."""
        data = MarketData()
        breadth = MarketBreadth(
            advancing_stocks=800,
            declining_stocks=200,
            unchanged_stocks=100,
            new_highs=100,
            new_lows=10,
            advance_decline_ratio=4.0,
        )

        score = data.calculate_fear_greed_score(breadth, vix=12.0)
        assert score > 70

    def test_calculate_fear_greed_score_fearful_market(self):
        """Should return low score for fearful market conditions."""
        data = MarketData()
        breadth = MarketBreadth(
            advancing_stocks=200,
            declining_stocks=800,
            unchanged_stocks=100,
            new_highs=10,
            new_lows=100,
            advance_decline_ratio=0.25,
        )

        score = data.calculate_fear_greed_score(breadth, vix=35.0)
        assert score < 30


# ---------------------------------------------------------------------------
# GeminiClient Integration Tests
# ---------------------------------------------------------------------------


class TestGeminiClientWithExternalData:
    """Test GeminiClient integration with external data sources."""

    def test_gemini_client_accepts_optional_data_sources(self, settings):
        """GeminiClient should accept optional external data sources."""
        news_api = NewsAPI(api_key="test_key")
        calendar = EconomicCalendar()
        market_data = MarketData()

        client = GeminiClient(
            settings,
            news_api=news_api,
            economic_calendar=calendar,
            market_data=market_data,
        )

        assert client._news_api is news_api
        assert client._economic_calendar is calendar
        assert client._market_data is market_data

    def test_gemini_client_works_without_external_data(self, settings):
        """GeminiClient should work without external data sources."""
        client = GeminiClient(settings)
        assert client._news_api is None
        assert client._economic_calendar is None
        assert client._market_data is None

    @pytest.mark.asyncio
    async def test_build_prompt_includes_news_sentiment(self, settings):
        """build_prompt should include news sentiment when available."""
        client = GeminiClient(settings)

        market_data = {
            "stock_code": "AAPL",
            "current_price": 180.0,
            "market_name": "US stock market",
        }

        sentiment = NewsSentiment(
            stock_code="AAPL",
            articles=[
                NewsArticle(
                    title="Apple hits record high",
                    summary="Strong earnings",
                    source="Reuters",
                    published_at="2026-02-04",
                    sentiment_score=0.85,
                    url="https://example.com",
                )
            ],
            avg_sentiment=0.85,
            article_count=1,
            fetched_at=time.time(),
        )

        prompt = await client.build_prompt(market_data, news_sentiment=sentiment)

        assert "AAPL" in prompt
        assert "180.0" in prompt
        assert "EXTERNAL DATA" in prompt
        assert "News Sentiment" in prompt
        assert "0.85" in prompt
        assert "Apple hits record high" in prompt

    @pytest.mark.asyncio
    async def test_build_prompt_with_economic_events(self, settings):
        """build_prompt should include upcoming economic events."""
        calendar = EconomicCalendar()
        now = datetime.now()
        calendar.add_event(
            EconomicEvent(
                name="FOMC Meeting",
                event_type="FOMC",
                datetime=now + timedelta(days=2),
                impact="HIGH",
                country="US",
                description="Interest rate decision",
            )
        )

        client = GeminiClient(settings, economic_calendar=calendar)

        market_data = {
            "stock_code": "AAPL",
            "current_price": 180.0,
            "market_name": "US stock market",
        }

        prompt = await client.build_prompt(market_data)

        assert "EXTERNAL DATA" in prompt
        assert "High-Impact Events" in prompt
        assert "FOMC Meeting" in prompt

    @pytest.mark.asyncio
    async def test_build_prompt_with_market_indicators(self, settings):
        """build_prompt should include market sentiment indicators."""
        market_data_provider = MarketData(api_key="test_key")

        # Mock the get_market_indicators to return test data
        with patch.object(market_data_provider, "get_market_indicators") as mock:
            mock.return_value = MagicMock(
                sentiment=MarketSentiment.EXTREME_GREED,
                breadth=MagicMock(advance_decline_ratio=2.5),
            )

            client = GeminiClient(settings, market_data=market_data_provider)

            market_data = {
                "stock_code": "AAPL",
                "current_price": 180.0,
                "market_name": "US stock market",
            }

            prompt = await client.build_prompt(market_data)

            assert "EXTERNAL DATA" in prompt
            assert "Market Sentiment" in prompt
            assert "EXTREME_GREED" in prompt

    @pytest.mark.asyncio
    async def test_build_prompt_graceful_when_no_external_data(self, settings):
        """build_prompt should work gracefully without external data."""
        client = GeminiClient(settings)

        market_data = {
            "stock_code": "AAPL",
            "current_price": 180.0,
            "market_name": "US stock market",
        }

        prompt = await client.build_prompt(market_data)

        assert "AAPL" in prompt
        assert "180.0" in prompt
        # Should NOT have external data section
        assert "EXTERNAL DATA" not in prompt

    def test_build_prompt_sync_backward_compatibility(self, settings):
        """build_prompt_sync should maintain backward compatibility."""
        client = GeminiClient(settings)

        market_data = {
            "stock_code": "005930",
            "current_price": 72000,
            "orderbook": {"asks": [], "bids": []},
            "foreigner_net": -50000,
        }

        prompt = client.build_prompt_sync(market_data)

        assert "005930" in prompt
        assert "72000" in prompt
        assert "JSON" in prompt
        # Sync version should NOT have external data
        assert "EXTERNAL DATA" not in prompt

    @pytest.mark.asyncio
    async def test_decide_with_news_sentiment_parameter(self, settings):
        """decide should accept optional news_sentiment parameter."""
        client = GeminiClient(settings)

        market_data = {
            "stock_code": "AAPL",
            "current_price": 180.0,
            "market_name": "US stock market",
        }

        sentiment = NewsSentiment(
            stock_code="AAPL",
            articles=[],
            avg_sentiment=0.5,
            article_count=1,
            fetched_at=time.time(),
        )

        # Mock the Gemini API call
        with patch.object(client._client.aio.models, "generate_content", new_callable=AsyncMock) as mock_gen:
            mock_response = MagicMock()
            mock_response.text = '{"action": "BUY", "confidence": 85, "rationale": "Good news"}'
            mock_gen.return_value = mock_response

            decision = await client.decide(market_data, news_sentiment=sentiment)

            assert decision.action == "BUY"
            assert decision.confidence == 85
            mock_gen.assert_called_once()
