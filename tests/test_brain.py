"""TDD tests for brain/gemini_client.py — written BEFORE implementation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
        prompt = client.build_prompt_sync(market_data)
        assert "005930" in prompt

    def test_prompt_contains_price(self, settings):
        client = GeminiClient(settings)
        market_data = {
            "stock_code": "005930",
            "current_price": 72000,
            "orderbook": {"asks": [], "bids": []},
            "foreigner_net": -50000,
        }
        prompt = client.build_prompt_sync(market_data)
        assert "72000" in prompt

    def test_prompt_enforces_json_output_format(self, settings):
        client = GeminiClient(settings)
        market_data = {
            "stock_code": "005930",
            "current_price": 72000,
            "orderbook": {"asks": [], "bids": []},
            "foreigner_net": 0,
        }
        prompt = client.build_prompt_sync(market_data)
        assert "JSON" in prompt
        assert "action" in prompt
        assert "confidence" in prompt


# ---------------------------------------------------------------------------
# Batch Decision Making
# ---------------------------------------------------------------------------


class TestBatchDecisionParsing:
    """Batch response parser must handle JSON arrays correctly."""

    def test_parse_valid_batch_response(self, settings):
        client = GeminiClient(settings)
        stocks_data = [
            {"stock_code": "AAPL", "current_price": 185.5},
            {"stock_code": "MSFT", "current_price": 420.0},
        ]
        raw = """[
            {"code": "AAPL", "action": "BUY", "confidence": 85, "rationale": "Strong momentum"},
            {"code": "MSFT", "action": "HOLD", "confidence": 50, "rationale": "Wait for earnings"}
        ]"""

        decisions = client._parse_batch_response(raw, stocks_data, token_count=100)

        assert len(decisions) == 2
        assert decisions["AAPL"].action == "BUY"
        assert decisions["AAPL"].confidence == 85
        assert decisions["MSFT"].action == "HOLD"
        assert decisions["MSFT"].confidence == 50

    def test_parse_batch_with_markdown_wrapper(self, settings):
        client = GeminiClient(settings)
        stocks_data = [{"stock_code": "AAPL", "current_price": 185.5}]
        raw = """```json
[{"code": "AAPL", "action": "BUY", "confidence": 90, "rationale": "Good"}]
```"""

        decisions = client._parse_batch_response(raw, stocks_data, token_count=100)

        assert decisions["AAPL"].action == "BUY"
        assert decisions["AAPL"].confidence == 90

    def test_parse_batch_empty_response_returns_hold_for_all(self, settings):
        client = GeminiClient(settings)
        stocks_data = [
            {"stock_code": "AAPL", "current_price": 185.5},
            {"stock_code": "MSFT", "current_price": 420.0},
        ]

        decisions = client._parse_batch_response("", stocks_data, token_count=100)

        assert len(decisions) == 2
        assert decisions["AAPL"].action == "HOLD"
        assert decisions["AAPL"].confidence == 0
        assert decisions["MSFT"].action == "HOLD"

    def test_parse_batch_malformed_json_returns_hold_for_all(self, settings):
        client = GeminiClient(settings)
        stocks_data = [{"stock_code": "AAPL", "current_price": 185.5}]
        raw = "This is not JSON"

        decisions = client._parse_batch_response(raw, stocks_data, token_count=100)

        assert decisions["AAPL"].action == "HOLD"
        assert decisions["AAPL"].confidence == 0

    def test_parse_batch_not_array_returns_hold_for_all(self, settings):
        client = GeminiClient(settings)
        stocks_data = [{"stock_code": "AAPL", "current_price": 185.5}]
        raw = '{"code": "AAPL", "action": "BUY", "confidence": 90, "rationale": "Good"}'

        decisions = client._parse_batch_response(raw, stocks_data, token_count=100)

        assert decisions["AAPL"].action == "HOLD"
        assert decisions["AAPL"].confidence == 0

    def test_parse_batch_missing_stock_gets_hold(self, settings):
        client = GeminiClient(settings)
        stocks_data = [
            {"stock_code": "AAPL", "current_price": 185.5},
            {"stock_code": "MSFT", "current_price": 420.0},
        ]
        # Response only has AAPL, MSFT is missing
        raw = '[{"code": "AAPL", "action": "BUY", "confidence": 85, "rationale": "Good"}]'

        decisions = client._parse_batch_response(raw, stocks_data, token_count=100)

        assert decisions["AAPL"].action == "BUY"
        assert decisions["MSFT"].action == "HOLD"
        assert decisions["MSFT"].confidence == 0

    def test_parse_batch_invalid_action_becomes_hold(self, settings):
        client = GeminiClient(settings)
        stocks_data = [{"stock_code": "AAPL", "current_price": 185.5}]
        raw = '[{"code": "AAPL", "action": "YOLO", "confidence": 90, "rationale": "Moon"}]'

        decisions = client._parse_batch_response(raw, stocks_data, token_count=100)

        assert decisions["AAPL"].action == "HOLD"

    def test_parse_batch_low_confidence_becomes_hold(self, settings):
        client = GeminiClient(settings)
        stocks_data = [{"stock_code": "AAPL", "current_price": 185.5}]
        raw = '[{"code": "AAPL", "action": "BUY", "confidence": 65, "rationale": "Weak"}]'

        decisions = client._parse_batch_response(raw, stocks_data, token_count=100)

        assert decisions["AAPL"].action == "HOLD"
        assert decisions["AAPL"].confidence == 65

    def test_parse_batch_missing_fields_gets_hold(self, settings):
        client = GeminiClient(settings)
        stocks_data = [{"stock_code": "AAPL", "current_price": 185.5}]
        raw = '[{"code": "AAPL", "action": "BUY"}]'  # Missing confidence and rationale

        decisions = client._parse_batch_response(raw, stocks_data, token_count=100)

        assert decisions["AAPL"].action == "HOLD"
        assert decisions["AAPL"].confidence == 0


# ---------------------------------------------------------------------------
# Prompt Override (used by pre_market_planner)
# ---------------------------------------------------------------------------


class TestPromptOverride:
    """decide() must use prompt_override when present in market_data."""

    @pytest.mark.asyncio
    async def test_prompt_override_is_sent_to_gemini(self, settings):
        """When prompt_override is in market_data, it should be used as the prompt."""
        client = GeminiClient(settings)

        custom_prompt = "You are a playbook generator. Return JSON with scenarios."

        mock_response = MagicMock()
        mock_response.text = '{"action": "HOLD", "confidence": 50, "rationale": "test"}'

        with patch.object(
            client._client.aio.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_generate:
            market_data = {
                "stock_code": "PLANNER",
                "current_price": 0,
                "prompt_override": custom_prompt,
            }
            await client.decide(market_data)

            # Verify the custom prompt was sent, not a built prompt
            mock_generate.assert_called_once()
            actual_prompt = mock_generate.call_args[1].get(
                "contents", mock_generate.call_args[0][1] if len(mock_generate.call_args[0]) > 1 else None
            )
            assert actual_prompt == custom_prompt

    @pytest.mark.asyncio
    async def test_prompt_override_skips_optimization(self, settings):
        """prompt_override should bypass prompt optimization."""
        client = GeminiClient(settings)
        client._enable_optimization = True

        custom_prompt = "Custom playbook prompt"

        mock_response = MagicMock()
        mock_response.text = '{"action": "HOLD", "confidence": 50, "rationale": "ok"}'

        with patch.object(
            client._client.aio.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_generate:
            market_data = {
                "stock_code": "PLANNER",
                "current_price": 0,
                "prompt_override": custom_prompt,
            }
            await client.decide(market_data)

            actual_prompt = mock_generate.call_args[1].get(
                "contents", mock_generate.call_args[0][1] if len(mock_generate.call_args[0]) > 1 else None
            )
            assert actual_prompt == custom_prompt

    @pytest.mark.asyncio
    async def test_without_prompt_override_uses_build_prompt(self, settings):
        """Without prompt_override, decide() should use build_prompt as before."""
        client = GeminiClient(settings)

        mock_response = MagicMock()
        mock_response.text = '{"action": "HOLD", "confidence": 50, "rationale": "ok"}'

        with patch.object(
            client._client.aio.models,
            "generate_content",
            new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_generate:
            market_data = {
                "stock_code": "005930",
                "current_price": 72000,
            }
            await client.decide(market_data)

            actual_prompt = mock_generate.call_args[1].get(
                "contents", mock_generate.call_args[0][1] if len(mock_generate.call_args[0]) > 1 else None
            )
            # Should contain stock code from build_prompt, not be a custom override
            assert "005930" in actual_prompt
