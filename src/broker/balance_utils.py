"""Balance-response parsing utilities.

Pure functions that extract stock codes, quantities, prices, and FX rates
from KIS broker balance API responses.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _safe_float(value: str | float | None, default: float = 0.0) -> float:
    """Convert to float, handling empty strings and None.

    Local copy of ``src.main.safe_float`` to avoid a circular import
    (main -> balance_utils -> main).
    """
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _extract_fx_rate_from_sources(*sources: dict[str, Any] | None) -> float | None:
    """Best-effort FX rate extraction from broker payloads."""
    # KIS overseas payloads expose exchange-rate fields with varying key names
    # across endpoints/responses (price, balance, buying power). Keep this list
    # centralised so schema drifts can be patched in one place.
    rate_keys = (
        "frst_bltn_exrt",
        "bass_exrt",
        "ovrs_exrt",
        "aply_xchg_rt",
        "xchg_rt",
        "exchange_rate",
        "fx_rate",
    )
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in rate_keys:
            rate = _safe_float(source.get(key), 0.0)
            if rate > 0:
                return rate
    return None


def _extract_buy_fx_rate(buy_trade: dict[str, Any] | None) -> float | None:
    if not buy_trade:
        return None
    raw_ctx = buy_trade.get("selection_context")
    if not isinstance(raw_ctx, str) or not raw_ctx.strip():
        return None
    try:
        decoded = json.loads(raw_ctx)
    except (TypeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None
    rate = _safe_float(decoded.get("fx_rate"), 0.0)
    return rate if rate > 0 else None


def _extract_symbol_from_holding(item: dict[str, Any]) -> str:
    """Extract symbol from overseas holding payload variants."""
    for key in (
        "ovrs_pdno",
        "pdno",
        "ovrs_item_name",
        "prdt_name",
        "symb",
        "symbol",
        "stock_code",
    ):
        value = item.get(key)
        if isinstance(value, str):
            symbol = value.strip().upper()
            if symbol and symbol.replace(".", "").replace("-", "").isalnum():
                return symbol
    return ""


def _extract_held_codes_from_balance(
    balance_data: dict[str, Any],
    *,
    is_domestic: bool,
    exchange_code: str | None = None,
) -> list[str]:
    """Return stock codes with a positive orderable quantity from a balance response.

    Uses the broker's live output1 as the source of truth so that partial fills
    and manual external trades are always reflected correctly.
    """
    output1 = balance_data.get("output1", [])
    if isinstance(output1, dict):
        output1 = [output1]
    if not isinstance(output1, list):
        return []

    codes: list[str] = []
    expected_exchange = exchange_code.strip().upper() if exchange_code else None
    for holding in output1:
        if not isinstance(holding, dict):
            continue
        code_key = "pdno" if is_domestic else "ovrs_pdno"
        code = str(holding.get(code_key, "")).strip().upper()
        if not code:
            continue
        if not is_domestic and expected_exchange:
            holding_exchange = str(holding.get("ovrs_excg_cd", "")).strip().upper()
            if holding_exchange and holding_exchange != expected_exchange:
                continue
        if is_domestic:
            qty = int(holding.get("ord_psbl_qty") or holding.get("hldg_qty") or 0)
        else:
            # ord_psbl_qty (주문가능수량) is the actual sellable quantity.
            # ovrs_cblc_qty (해외잔고수량) includes unsettled/expired holdings
            # that cannot actually be sold (e.g. expired warrants).
            qty = int(
                holding.get("ord_psbl_qty")
                or holding.get("ovrs_cblc_qty")
                or holding.get("hldg_qty")
                or 0
            )
        if qty > 0:
            codes.append(code)
    return codes


def _extract_held_qty_from_balance(
    balance_data: dict[str, Any],
    stock_code: str,
    *,
    is_domestic: bool,
) -> int:
    """Extract the broker-confirmed orderable quantity for a stock.

    Uses the broker's live balance response (output1) as the source of truth
    rather than the local DB, because DB records reflect order quantity which
    may differ from actual fill quantity due to partial fills.

    Domestic fields (VTTC8434R output1):
        pdno          — 종목코드
        ord_psbl_qty  — 주문가능수량 (preferred: excludes unsettled)
        hldg_qty      — 보유수량 (fallback)

    Overseas fields (VTTS3012R / TTTS3012R output1):
        ovrs_pdno     — 종목코드
        ord_psbl_qty  — 주문가능수량 (preferred: actual sellable qty)
        ovrs_cblc_qty — 해외잔고수량 (fallback: total holding, may include
                        unsettled or expired positions with ord_psbl_qty=0)
        hldg_qty      — 보유수량 (last-resort fallback)
    """
    output1 = balance_data.get("output1", [])
    if isinstance(output1, dict):
        output1 = [output1]
    if not isinstance(output1, list):
        return 0

    for holding in output1:
        if not isinstance(holding, dict):
            continue
        code_key = "pdno" if is_domestic else "ovrs_pdno"
        held_code = str(holding.get(code_key, "")).strip().upper()
        if held_code != stock_code.strip().upper():
            continue
        if is_domestic:
            qty = int(holding.get("ord_psbl_qty") or holding.get("hldg_qty") or 0)
        else:
            qty = int(
                holding.get("ord_psbl_qty")
                or holding.get("ovrs_cblc_qty")
                or holding.get("hldg_qty")
                or 0
            )
        return qty
    return 0


def _extract_avg_price_from_balance(
    balance_data: dict[str, Any],
    stock_code: str,
    *,
    is_domestic: bool,
) -> float:
    """Extract the broker-reported average purchase price for a stock.

    Uses ``pchs_avg_pric`` (매입평균가격) from the balance response (output1).
    Returns 0.0 when absent so callers can use ``if price > 0`` as sentinel.

    Domestic fields (VTTC8434R output1):  pdno, pchs_avg_pric
    Overseas fields (VTTS3012R output1):  ovrs_pdno, pchs_avg_pric
    """
    output1 = balance_data.get("output1", [])
    if isinstance(output1, dict):
        output1 = [output1]
    if not isinstance(output1, list):
        return 0.0

    for holding in output1:
        if not isinstance(holding, dict):
            continue
        code_key = "pdno" if is_domestic else "ovrs_pdno"
        held_code = str(holding.get(code_key, "")).strip().upper()
        if held_code != stock_code.strip().upper():
            continue
        return _safe_float(holding.get("pchs_avg_pric"), 0.0)
    return 0.0
