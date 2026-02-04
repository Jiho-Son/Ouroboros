"""TDD tests for brain/gemini_client.py — written BEFORE implementation."""

from __future__ import annotations

from src.brain.gemini_client import GeminiClient

# ---------------------------------------------------------------------------
# Response Parsing
# ---------------------------------------------------------------------------


class TestResponseParsing:
    """Gemini responses must be parsed into validated TradeDecision objects."""

    def test_valid_buy_response(self, settings):
        client = GeminiClient(settings)
        raw = '{"action": "BUY", "confidence": 90, "rationale": "Strong momentum"}'
        decision = client.parse_response(raw)
        assert decision.action == "BUY"
        assert decision.confidence == 90
        assert decision.rationale == "Strong momentum"

    def test_valid_sell_response(self, settings):
        client = GeminiClient(settings)
        raw = '{"action": "SELL", "confidence": 85, "rationale": "Overbought RSI"}'
        decision = client.parse_response(raw)
        assert decision.action == "SELL"

    def test_valid_hold_response(self, settings):
        client = GeminiClient(settings)
        raw = '{"action": "HOLD", "confidence": 95, "rationale": "Sideways market"}'
        decision = client.parse_response(raw)
        assert decision.action == "HOLD"


# ---------------------------------------------------------------------------
# Confidence Threshold Enforcement
# ---------------------------------------------------------------------------


class TestConfidenceThreshold:
    """If confidence < 80, the action MUST be forced to HOLD."""

    def test_low_confidence_buy_becomes_hold(self, settings):
        client = GeminiClient(settings)
        raw = '{"action": "BUY", "confidence": 65, "rationale": "Weak signal"}'
        decision = client.parse_response(raw)
        assert decision.action == "HOLD"
        assert decision.confidence == 65

    def test_low_confidence_sell_becomes_hold(self, settings):
        client = GeminiClient(settings)
        raw = '{"action": "SELL", "confidence": 79, "rationale": "Uncertain"}'
        decision = client.parse_response(raw)
        assert decision.action == "HOLD"

    def test_exactly_threshold_is_allowed(self, settings):
        client = GeminiClient(settings)
        raw = '{"action": "BUY", "confidence": 80, "rationale": "Just enough"}'
        decision = client.parse_response(raw)
        assert decision.action == "BUY"


# ---------------------------------------------------------------------------
# Malformed JSON Handling
# ---------------------------------------------------------------------------


class TestMalformedJsonHandling:
    """Gemini may return garbage — the parser must not crash."""

    def test_empty_string_returns_hold(self, settings):
        client = GeminiClient(settings)
        decision = client.parse_response("")
        assert decision.action == "HOLD"
        assert decision.confidence == 0

    def test_plain_text_returns_hold(self, settings):
        client = GeminiClient(settings)
        decision = client.parse_response("I think you should buy Samsung stock")
        assert decision.action == "HOLD"
        assert decision.confidence == 0

    def test_partial_json_returns_hold(self, settings):
        client = GeminiClient(settings)
        decision = client.parse_response('{"action": "BUY", "confidence":')
        assert decision.action == "HOLD"
        assert decision.confidence == 0

    def test_json_with_missing_fields_returns_hold(self, settings):
        client = GeminiClient(settings)
        decision = client.parse_response('{"action": "BUY"}')
        assert decision.action == "HOLD"
        assert decision.confidence == 0

    def test_json_with_invalid_action_returns_hold(self, settings):
        client = GeminiClient(settings)
        decision = client.parse_response(
            '{"action": "YOLO", "confidence": 99, "rationale": "moon"}'
        )
        assert decision.action == "HOLD"
        assert decision.confidence == 0

    def test_json_wrapped_in_markdown_code_block(self, settings):
        """Gemini often wraps JSON in ```json ... ``` blocks."""
        client = GeminiClient(settings)
        raw = '```json\n{"action": "BUY", "confidence": 92, "rationale": "Good"}\n```'
        decision = client.parse_response(raw)
        assert decision.action == "BUY"
        assert decision.confidence == 92


# ---------------------------------------------------------------------------
# Prompt Construction
# ---------------------------------------------------------------------------


class TestPromptConstruction:
    """The prompt sent to Gemini must include all required market data."""

    def test_prompt_contains_stock_code(self, settings):
        client = GeminiClient(settings)
        market_data = {
            "stock_code": "005930",
            "current_price": 72000,
            "orderbook": {"asks": [], "bids": []},
            "foreigner_net": -50000,
        }
        prompt = client.build_prompt(market_data)
        assert "005930" in prompt

    def test_prompt_contains_price(self, settings):
        client = GeminiClient(settings)
        market_data = {
            "stock_code": "005930",
            "current_price": 72000,
            "orderbook": {"asks": [], "bids": []},
            "foreigner_net": -50000,
        }
        prompt = client.build_prompt(market_data)
        assert "72000" in prompt

    def test_prompt_enforces_json_output_format(self, settings):
        client = GeminiClient(settings)
        market_data = {
            "stock_code": "005930",
            "current_price": 72000,
            "orderbook": {"asks": [], "bids": []},
            "foreigner_net": 0,
        }
        prompt = client.build_prompt(market_data)
        assert "JSON" in prompt
        assert "action" in prompt
        assert "confidence" in prompt
