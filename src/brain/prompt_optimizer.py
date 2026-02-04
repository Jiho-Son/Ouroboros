"""Prompt optimization utilities for reducing token usage.

This module provides tools to compress prompts while maintaining decision quality:
- Token counting
- Text compression and abbreviation
- Template-based prompts with variable slots
- Priority-based context truncation
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

# Abbreviation mapping for common terms
ABBREVIATIONS = {
    "price": "P",
    "volume": "V",
    "current": "cur",
    "previous": "prev",
    "change": "chg",
    "percentage": "pct",
    "market": "mkt",
    "orderbook": "ob",
    "foreigner": "fgn",
    "buy": "B",
    "sell": "S",
    "hold": "H",
    "confidence": "conf",
    "rationale": "reason",
    "action": "act",
    "net": "net",
}

# Reverse mapping for decompression
REVERSE_ABBREVIATIONS = {v: k for k, v in ABBREVIATIONS.items()}


@dataclass(frozen=True)
class TokenMetrics:
    """Metrics about token usage in a prompt."""

    char_count: int
    word_count: int
    estimated_tokens: int  # Rough estimate: ~4 chars per token
    compression_ratio: float = 1.0  # Original / Compressed


class PromptOptimizer:
    """Optimizes prompts to reduce token usage while maintaining quality."""

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estimate token count for text.

        Uses a simple heuristic: ~4 characters per token for English.
        This is approximate but sufficient for optimization purposes.

        Args:
            text: Input text to estimate tokens for

        Returns:
            Estimated token count
        """
        if not text:
            return 0
        # Simple estimate: 1 token ≈ 4 characters
        return max(1, len(text) // 4)

    @staticmethod
    def count_tokens(text: str) -> TokenMetrics:
        """Count various metrics for a text.

        Args:
            text: Input text to analyze

        Returns:
            TokenMetrics with character, word, and estimated token counts
        """
        char_count = len(text)
        word_count = len(text.split())
        estimated_tokens = PromptOptimizer.estimate_tokens(text)

        return TokenMetrics(
            char_count=char_count,
            word_count=word_count,
            estimated_tokens=estimated_tokens,
        )

    @staticmethod
    def compress_json(data: dict[str, Any]) -> str:
        """Compress JSON by removing whitespace.

        Args:
            data: Dictionary to serialize

        Returns:
            Compact JSON string without whitespace
        """
        return json.dumps(data, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def abbreviate_text(text: str, aggressive: bool = False) -> str:
        """Apply abbreviations to reduce text length.

        Args:
            text: Input text to abbreviate
            aggressive: If True, apply more aggressive compression

        Returns:
            Abbreviated text
        """
        result = text

        # Apply word-level abbreviations (case-insensitive)
        for full, abbr in ABBREVIATIONS.items():
            # Word boundaries to avoid partial replacements
            pattern = r"\b" + re.escape(full) + r"\b"
            result = re.sub(pattern, abbr, result, flags=re.IGNORECASE)

        if aggressive:
            # Remove articles and filler words
            result = re.sub(r"\b(a|an|the)\b", "", result, flags=re.IGNORECASE)
            result = re.sub(r"\b(is|are|was|were)\b", "", result, flags=re.IGNORECASE)
            # Collapse multiple spaces
            result = re.sub(r"\s+", " ", result)

        return result.strip()

    @staticmethod
    def build_compressed_prompt(
        market_data: dict[str, Any],
        include_instructions: bool = True,
        max_length: int | None = None,
    ) -> str:
        """Build a compressed prompt from market data.

        Args:
            market_data: Market data dictionary with stock info
            include_instructions: Whether to include full instructions
            max_length: Maximum character length (truncates if needed)

        Returns:
            Compressed prompt string
        """
        # Abbreviated market name
        market_name = market_data.get("market_name", "KR")
        if "Korea" in market_name:
            market_name = "KR"
        elif "United States" in market_name or "US" in market_name:
            market_name = "US"

        # Core data - always included
        core_info = {
            "mkt": market_name,
            "code": market_data["stock_code"],
            "P": market_data["current_price"],
        }

        # Optional fields
        if "orderbook" in market_data and market_data["orderbook"]:
            ob = market_data["orderbook"]
            # Compress orderbook: keep only top 3 levels
            compressed_ob = {
                "bid": ob.get("bid", [])[:3],
                "ask": ob.get("ask", [])[:3],
            }
            core_info["ob"] = compressed_ob

        if market_data.get("foreigner_net", 0) != 0:
            core_info["fgn_net"] = market_data["foreigner_net"]

        # Compress to JSON
        data_str = PromptOptimizer.compress_json(core_info)

        if include_instructions:
            # Minimal instructions
            prompt = (
                f"{market_name} trader. Analyze:\n{data_str}\n\n"
                'Return JSON: {"act":"BUY"|"SELL"|"HOLD","conf":<0-100>,"reason":"<text>"}\n'
                "Rules: act=BUY/SELL/HOLD, conf=0-100, reason=concise. No markdown."
            )
        else:
            # Data only (for cached contexts where instructions are known)
            prompt = data_str

        # Truncate if needed
        if max_length and len(prompt) > max_length:
            prompt = prompt[:max_length] + "..."

        return prompt

    @staticmethod
    def truncate_context(
        context: dict[str, Any],
        max_tokens: int,
        priority_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        """Truncate context data to fit within token budget.

        Keeps high-priority keys first, then truncates less important data.

        Args:
            context: Context dictionary to truncate
            max_tokens: Maximum token budget
            priority_keys: List of keys to keep (in order of priority)

        Returns:
            Truncated context dictionary
        """
        if not context:
            return {}

        if priority_keys is None:
            priority_keys = []

        result: dict[str, Any] = {}
        current_tokens = 0

        # Add priority keys first
        for key in priority_keys:
            if key in context:
                value_str = json.dumps(context[key])
                tokens = PromptOptimizer.estimate_tokens(value_str)

                if current_tokens + tokens <= max_tokens:
                    result[key] = context[key]
                    current_tokens += tokens
                else:
                    break

        # Add remaining keys if space available
        for key, value in context.items():
            if key in result:
                continue

            value_str = json.dumps(value)
            tokens = PromptOptimizer.estimate_tokens(value_str)

            if current_tokens + tokens <= max_tokens:
                result[key] = value
                current_tokens += tokens
            else:
                break

        return result

    @staticmethod
    def calculate_compression_ratio(original: str, compressed: str) -> float:
        """Calculate compression ratio between original and compressed text.

        Args:
            original: Original text
            compressed: Compressed text

        Returns:
            Compression ratio (original_tokens / compressed_tokens)
        """
        original_tokens = PromptOptimizer.estimate_tokens(original)
        compressed_tokens = PromptOptimizer.estimate_tokens(compressed)

        if compressed_tokens == 0:
            return 1.0

        return original_tokens / compressed_tokens
