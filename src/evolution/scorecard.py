"""Daily scorecard model for end-of-day performance review."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DailyScorecard:
    """Structured daily performance snapshot for a single market."""

    date: str
    market: str
    total_decisions: int
    buys: int
    sells: int
    holds: int
    total_pnl: float
    win_rate: float
    avg_confidence: float
    scenario_match_rate: float
    top_winners: list[str] = field(default_factory=list)
    top_losers: list[str] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)
    cross_market_note: str = ""
