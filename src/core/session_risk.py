"""Session-aware risk profile helpers.

Functions for parsing ``SESSION_RISK_PROFILES_JSON``, resolving per-market
overrides, and computing dynamic stop-loss thresholds.  Extracted from
``src/main.py``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.config import Settings
from src.core.order_policy import get_session_info
from src.markets.schedule import MarketInfo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Local copy of ``safe_float`` to avoid circular import (main -> session_risk).
# ---------------------------------------------------------------------------

def _safe_float(value: str | float | None, default: float = 0.0) -> float:
    """Convert to float, handling empty strings and None.

    Local copy of ``src.main.safe_float`` to avoid a circular import
    (main -> session_risk -> main).
    """
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Module-level globals (previously in src/main.py)
# ---------------------------------------------------------------------------

_SESSION_RISK_PROFILES_RAW = "{}"
_SESSION_RISK_PROFILES_MAP: dict[str, dict[str, Any]] = {}
_SESSION_RISK_LAST_BY_MARKET: dict[str, str] = {}
_SESSION_RISK_OVERRIDES_BY_MARKET: dict[str, dict[str, Any]] = {}
_STOPLOSS_REENTRY_COOLDOWN_UNTIL: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Functions (previously in src/main.py)
# ---------------------------------------------------------------------------


def _compute_kr_dynamic_stop_loss_pct(
    *,
    market: MarketInfo | None = None,
    entry_price: float,
    atr_value: float,
    fallback_stop_loss_pct: float,
    settings: Settings | None,
) -> float:
    """Compute KR dynamic hard-stop threshold in percent."""
    if entry_price <= 0 or atr_value <= 0:
        return fallback_stop_loss_pct

    k = _resolve_market_setting(
        market=market,
        settings=settings,
        key="KR_ATR_STOP_MULTIPLIER_K",
        default=2.0,
    )
    min_pct = float(
        _resolve_market_setting(
        market=market,
        settings=settings,
        key="KR_ATR_STOP_MIN_PCT",
        default=-2.0,
        )
    )
    max_pct = float(
        _resolve_market_setting(
        market=market,
        settings=settings,
        key="KR_ATR_STOP_MAX_PCT",
        default=-7.0,
        )
    )
    if max_pct > min_pct:
        min_pct, max_pct = max_pct, min_pct

    dynamic_stop_pct = -((k * atr_value) / entry_price) * 100.0
    return float(max(max_pct, min(min_pct, dynamic_stop_pct)))


def _stoploss_cooldown_key(*, market: MarketInfo, stock_code: str) -> str:
    return f"{market.code}:{stock_code}"


def _parse_session_risk_profiles(settings: Settings | None) -> dict[str, dict[str, Any]]:
    if settings is None:
        return {}
    global _SESSION_RISK_PROFILES_RAW, _SESSION_RISK_PROFILES_MAP
    raw = str(getattr(settings, "SESSION_RISK_PROFILES_JSON", "{}") or "{}")
    if raw == _SESSION_RISK_PROFILES_RAW:
        return _SESSION_RISK_PROFILES_MAP

    parsed_map: dict[str, dict[str, Any]] = {}
    try:
        decoded = json.loads(raw)
        if isinstance(decoded, dict):
            for session_id, session_values in decoded.items():
                if isinstance(session_id, str) and isinstance(session_values, dict):
                    parsed_map[session_id] = session_values
    except (ValueError, TypeError) as exc:
        logger.warning("Invalid SESSION_RISK_PROFILES_JSON; using defaults: %s", exc)
        parsed_map = {}

    _SESSION_RISK_PROFILES_RAW = raw
    _SESSION_RISK_PROFILES_MAP = parsed_map
    return _SESSION_RISK_PROFILES_MAP


def _coerce_setting_value(*, value: Any, default: Any) -> Any:
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        if isinstance(value, (int, float)):
            return value != 0
        return default
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(value)
        except (ValueError, TypeError):
            return default
    if isinstance(default, float):
        return _safe_float(value, float(default))
    if isinstance(default, str):
        return str(value)
    return value


def _session_risk_overrides(
    *,
    market: MarketInfo | None,
    settings: Settings | None,
) -> dict[str, Any]:
    if market is None or settings is None:
        return {}
    if not bool(getattr(settings, "SESSION_RISK_RELOAD_ENABLED", True)):
        return {}

    session_id = get_session_info(market).session_id
    previous_session = _SESSION_RISK_LAST_BY_MARKET.get(market.code)
    if previous_session == session_id:
        return _SESSION_RISK_OVERRIDES_BY_MARKET.get(market.code, {})

    profile_map = _parse_session_risk_profiles(settings)
    merged: dict[str, Any] = {}
    default_profile = profile_map.get("default")
    if isinstance(default_profile, dict):
        merged.update(default_profile)
    session_profile = profile_map.get(session_id)
    if isinstance(session_profile, dict):
        merged.update(session_profile)

    _SESSION_RISK_LAST_BY_MARKET[market.code] = session_id
    _SESSION_RISK_OVERRIDES_BY_MARKET[market.code] = merged
    if previous_session is None:
        logger.info(
            "Session risk profile initialized for %s: %s (overrides=%s)",
            market.code,
            session_id,
            ",".join(sorted(merged.keys())) if merged else "none",
        )
    else:
        logger.info(
            "Session risk profile reloaded for %s: %s -> %s (overrides=%s)",
            market.code,
            previous_session,
            session_id,
            ",".join(sorted(merged.keys())) if merged else "none",
        )
    return merged


def _resolve_market_setting(
    *,
    market: MarketInfo | None,
    settings: Settings | None,
    key: str,
    default: Any,
) -> Any:
    if settings is None:
        return default

    fallback = getattr(settings, key, default)
    overrides = _session_risk_overrides(market=market, settings=settings)
    if key not in overrides:
        return fallback
    return _coerce_setting_value(value=overrides[key], default=fallback)


def _stoploss_cooldown_minutes(
    settings: Settings | None,
    market: MarketInfo | None = None,
) -> int:
    minutes = _resolve_market_setting(
        market=market,
        settings=settings,
        key="STOPLOSS_REENTRY_COOLDOWN_MINUTES",
        default=120,
    )
    return max(1, int(minutes))
