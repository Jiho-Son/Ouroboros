# External Data Integration

This module provides objective external data sources to enhance trading decisions beyond just market prices and user input.

## Modules

### `news_api.py` - News Sentiment Analysis

Fetches real-time news for stocks with sentiment scoring.

**Features:**
- Alpha Vantage and NewsAPI.org support
- Sentiment scoring (-1.0 to +1.0)
- 5-minute caching to minimize API quota usage
- Graceful fallback when API unavailable

**Usage:**
```python
from src.data.news_api import NewsAPI

# Initialize with API key
news_api = NewsAPI(api_key="your_key", provider="alphavantage")

# Fetch news sentiment
sentiment = await news_api.get_news_sentiment("AAPL")
if sentiment:
    print(f"Average sentiment: {sentiment.avg_sentiment}")
    for article in sentiment.articles[:3]:
        print(f"{article.title} ({article.sentiment_score})")
```

### `economic_calendar.py` - Major Economic Events

Tracks FOMC meetings, GDP releases, CPI, earnings calendars, and other market-moving events.

**Features:**
- High-impact event tracking (FOMC, GDP, CPI)
- Earnings calendar per stock
- Event proximity checking
- Hardcoded major events for 2026 (no API required)

**Usage:**
```python
from src.data.economic_calendar import EconomicCalendar

calendar = EconomicCalendar()
calendar.load_hardcoded_events()

# Get upcoming high-impact events
upcoming = calendar.get_upcoming_events(days_ahead=7, min_impact="HIGH")
print(f"High-impact events: {upcoming.high_impact_count}")

# Check if near earnings
earnings_date = calendar.get_earnings_date("AAPL")
if earnings_date:
    print(f"Next earnings: {earnings_date}")

# Check for high volatility period
if calendar.is_high_volatility_period(hours_ahead=24):
    print("High-impact event imminent!")
```

### `market_data.py` - Market Indicators

Provides market breadth, sector performance, and sentiment indicators.

**Features:**
- Market sentiment levels (Fear & Greed equivalent)
- Market breadth (advancing/declining stocks)
- Sector performance tracking
- Fear/Greed score calculation

**Usage:**
```python
from src.data.market_data import MarketData

market_data = MarketData(api_key="your_key")

# Get market sentiment
sentiment = market_data.get_market_sentiment()
print(f"Market sentiment: {sentiment.name}")

# Get full indicators
indicators = market_data.get_market_indicators("US")
print(f"Sentiment: {indicators.sentiment.name}")
print(f"A/D Ratio: {indicators.breadth.advance_decline_ratio}")
```

## Integration with GeminiClient

The external data sources are seamlessly integrated into the AI decision engine:

```python
from src.brain.gemini_client import GeminiClient
from src.data.news_api import NewsAPI
from src.data.economic_calendar import EconomicCalendar
from src.data.market_data import MarketData
from src.config import Settings

settings = Settings()

# Initialize data sources
news_api = NewsAPI(api_key=settings.NEWS_API_KEY, provider=settings.NEWS_API_PROVIDER)
calendar = EconomicCalendar()
calendar.load_hardcoded_events()
market_data = MarketData(api_key=settings.MARKET_DATA_API_KEY)

# Create enhanced client
client = GeminiClient(
    settings,
    news_api=news_api,
    economic_calendar=calendar,
    market_data=market_data
)

# Make decision with external context
market_data_dict = {
    "stock_code": "AAPL",
    "current_price": 180.0,
    "market_name": "US stock market"
}

decision = await client.decide(market_data_dict)
```

The external data is automatically included in the prompt sent to Gemini:

```
Market: US stock market
Stock Code: AAPL
Current Price: 180.0

EXTERNAL DATA:
News Sentiment: 0.85 (from 10 articles)
  1. [Reuters] Apple hits record high (sentiment: 0.92)
  2. [Bloomberg] Strong iPhone sales (sentiment: 0.78)
  3. [CNBC] Tech sector rallying (sentiment: 0.85)

Upcoming High-Impact Events: 2 in next 7 days
  Next: FOMC Meeting (FOMC) on 2026-03-18
  Earnings: AAPL on 2026-02-10

Market Sentiment: GREED
Advance/Decline Ratio: 2.35
```

## Configuration

Add these to your `.env` file:

```bash
# External Data APIs (optional)
NEWS_API_KEY=your_alpha_vantage_key
NEWS_API_PROVIDER=alphavantage  # or "newsapi"
MARKET_DATA_API_KEY=your_market_data_key
```

## API Recommendations

### Alpha Vantage (News)
- **Free tier:** 5 calls/min, 500 calls/day
- **Pros:** Provides sentiment scores, no credit card required
- **URL:** https://www.alphavantage.co/

### NewsAPI.org
- **Free tier:** 100 requests/day
- **Pros:** Large news coverage, easy to use
- **Cons:** No sentiment scores (we use keyword heuristics)
- **URL:** https://newsapi.org/

## Caching Strategy

To minimize API quota usage:

1. **News:** 5-minute TTL cache per stock
2. **Economic Calendar:** Loaded once at startup (hardcoded events)
3. **Market Data:** Fetched per decision (lightweight)

## Graceful Degradation

The system works gracefully without external data:

- If no API keys provided → decisions work with just market prices
- If API fails → decision continues without external context
- If cache expired → attempts refetch, falls back to no data
- Errors are logged but never block trading decisions

## Testing

All modules have comprehensive test coverage (81%+):

```bash
pytest tests/test_data_integration.py -v --cov=src/data
```

Tests use mocks to avoid requiring real API keys.

## Future Enhancements

- Twitter/X sentiment analysis
- Reddit WallStreetBets sentiment
- Options flow data
- Insider trading activity
- Analyst upgrades/downgrades
- Real-time economic data APIs
