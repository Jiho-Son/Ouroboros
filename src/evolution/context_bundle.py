"""Helpers for assembling market-scoped evolution prompt context."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any

from src.context.layer import ContextLayer
from src.context.store import ContextStore

_SNAPSHOT_CLUE_LIMIT = 12


def build_evolution_context_bundle(
    context_store: ContextStore,
    failures: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build deterministic market/date-scoped prompt context from failures."""
    failures_by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    failure_dates_by_market: dict[str, set[str]] = defaultdict(set)

    for failure in failures:
        market = str(failure.get("market") or "UNKNOWN")
        failures_by_market[market].append(failure)
        failure_date = _extract_failure_date(failure.get("timestamp"))
        if failure_date is not None:
            failure_dates_by_market[market].add(failure_date)

    bundles: list[dict[str, Any]] = []
    for market in sorted(failures_by_market):
        failure_dates = sorted(failure_dates_by_market.get(market, set()), reverse=True)
        bundle: dict[str, Any] = {"market": market}

        if failure_dates:
            bundle["failure_dates"] = failure_dates

        daily_context = _load_daily_context(context_store, market, failure_dates)
        if daily_context:
            bundle["daily_context"] = daily_context

        recent_evolution_report = _load_latest_evolution_report(context_store, market)
        if recent_evolution_report is not None:
            bundle["recent_evolution_report"] = recent_evolution_report

        weekly_context = _load_weekly_context(context_store, market, failure_dates)
        if weekly_context:
            bundle["weekly_context"] = weekly_context

        snapshot_summary = summarize_context_snapshots(failures_by_market[market])
        if snapshot_summary is not None:
            bundle["context_snapshot_summary"] = snapshot_summary

        bundles.append(bundle)

    return bundles


def render_evolution_context_section(bundle: list[dict[str, Any]]) -> str:
    """Render the prompt section for the evolution context bundle."""
    return f"## Evolution Context\n{json.dumps(bundle, indent=2, ensure_ascii=True)}\n\n"


def summarize_context_snapshots(failures: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Extract repeated and representative clues from failure snapshots."""
    clue_counter: Counter[str] = Counter()
    sample_count = 0

    for failure in failures:
        snapshot = failure.get("context_snapshot")
        if not isinstance(snapshot, dict):
            continue
        sample_count += 1
        clue_counter.update(set(_collect_snapshot_clues(snapshot)))

    if sample_count == 0:
        return None

    ordered_clues = sorted(clue_counter.items(), key=lambda item: (-item[1], item[0]))
    repeated_clues = [f"{clue} ({count}x)" for clue, count in ordered_clues if count > 1][:5]
    repeated_keys = {clue for clue, count in ordered_clues if count > 1}
    representative_clues = [
        clue for clue, _count in ordered_clues if clue not in repeated_keys
    ][:3]

    summary: dict[str, Any] = {"sample_count": sample_count}
    if repeated_clues:
        summary["repeated_clues"] = repeated_clues
    if representative_clues:
        summary["representative_clues"] = representative_clues
    return summary


def _load_daily_context(
    context_store: ContextStore,
    market: str,
    failure_dates: list[str],
) -> list[dict[str, Any]]:
    scorecard_key = f"scorecard_{market}"
    daily_context: list[dict[str, Any]] = []
    for failure_date in failure_dates:
        scorecard = context_store.get_context(ContextLayer.L6_DAILY, failure_date, scorecard_key)
        if scorecard is None:
            continue
        daily_context.append(
            {
                "date": failure_date,
                "key": scorecard_key,
                "data": scorecard,
            }
        )
    return daily_context


def _load_latest_evolution_report(
    context_store: ContextStore,
    market: str,
) -> dict[str, Any] | None:
    evolution_key = f"evolution_{market}"
    row = context_store.conn.execute(
        """
        SELECT timeframe, value
        FROM contexts
        WHERE layer = ? AND key = ?
        ORDER BY timeframe DESC, updated_at DESC
        LIMIT 1
        """,
        (ContextLayer.L6_DAILY.value, evolution_key),
    ).fetchone()
    if row is None:
        return None

    value = json.loads(row[1])
    if isinstance(value, dict):
        report: dict[str, Any] = {
            "date": row[0],
            "key": evolution_key,
        }
        for field in ("summary", "adjustments", "risk_notes"):
            if field in value:
                report[field] = value[field]
        if len(report) > 2:
            return report
    return {
        "date": row[0],
        "key": evolution_key,
        "value": value,
    }


def _load_weekly_context(
    context_store: ContextStore,
    market: str,
    failure_dates: list[str],
) -> list[dict[str, Any]]:
    weekly_context: list[dict[str, Any]] = []
    for timeframe in _failure_weeks(failure_dates):
        weekly_values = context_store.get_all_contexts(ContextLayer.L5_WEEKLY, timeframe)
        market_scoped_metrics = {
            key: value
            for key, value in weekly_values.items()
            if key.endswith(f"_{market}")
        }
        if market_scoped_metrics:
            weekly_context.append(
                {
                    "timeframe": timeframe,
                    "metrics": market_scoped_metrics,
                }
            )
    return weekly_context


def _failure_weeks(failure_dates: list[str]) -> list[str]:
    weeks = {
        f"{iso.year}-W{iso.week:02d}"
        for failure_date in failure_dates
        for iso in [date.fromisoformat(failure_date).isocalendar()]
    }
    return sorted(weeks, reverse=True)


def _extract_failure_date(timestamp: Any) -> str | None:
    if not isinstance(timestamp, str) or not timestamp:
        return None
    normalized = timestamp.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).date().isoformat()
    except ValueError:
        return None


def _collect_snapshot_clues(snapshot: dict[str, Any]) -> list[str]:
    clues: list[str] = []
    _walk_snapshot(snapshot, prefix="", clues=clues)
    return clues


def _walk_snapshot(value: Any, *, prefix: str, clues: list[str]) -> None:
    if len(clues) >= _SNAPSHOT_CLUE_LIMIT:
        return

    if isinstance(value, dict):
        for key in sorted(value):
            next_prefix = f"{prefix}.{key}" if prefix else key
            _walk_snapshot(value[key], prefix=next_prefix, clues=clues)
        return

    if isinstance(value, list):
        if not value:
            return
        if all(_is_scalar(item) for item in value):
            rendered = ",".join(_render_scalar(item) for item in value[:3])
            if len(value) > 3:
                rendered += ",..."
            clues.append(f"{prefix}=[{rendered}]")
            return
        for index, item in enumerate(value[:2]):
            _walk_snapshot(item, prefix=f"{prefix}[{index}]", clues=clues)
        return

    if _is_scalar(value):
        clues.append(f"{prefix}={_render_scalar(value)}")


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


def _render_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)
