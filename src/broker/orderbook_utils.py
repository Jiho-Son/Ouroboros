"""Shared orderbook payload helpers."""

from __future__ import annotations

from typing import Any

_CONTAINER_KEYS = ("output1", "output2", "output")
_ASK_KEYS = ("pask1", "askp1", "stck_askp1", "ask_price_1")
_BID_KEYS = ("pbid1", "bidp1", "stck_bidp1", "bid_price_1")


def extract_orderbook_top_levels(payload: dict[str, Any]) -> tuple[float | None, float | None]:
    """Extract the first positive top-of-book ask/bid across supported payload aliases."""
    output: Any = payload
    for container_key in _CONTAINER_KEYS:
        candidate = payload.get(container_key)
        if candidate not in (None, ""):
            output = candidate
            break
    if isinstance(output, list):
        output = output[0] if output else {}
    if not isinstance(output, dict):
        return None, None

    def _read(keys: tuple[str, ...]) -> float | None:
        for key in keys:
            raw = output.get(key)
            if raw in (None, ""):
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return None

    return _read(_ASK_KEYS), _read(_BID_KEYS)
