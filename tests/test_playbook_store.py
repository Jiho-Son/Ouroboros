"""Tests for playbook persistence (PlaybookStore + DB schema)."""

from __future__ import annotations

from datetime import date

import pytest

from src.db import init_db
from src.strategy.models import (
    DayPlaybook,
    GlobalRule,
    MarketOutlook,
    PlaybookStatus,
    ScenarioAction,
    StockCondition,
    StockPlaybook,
    StockScenario,
)
from src.strategy.playbook_store import PlaybookStore


@pytest.fixture
def conn():
    """Create an in-memory DB with schema."""
    connection = init_db(":memory:")
    yield connection
    connection.close()


@pytest.fixture
def store(conn) -> PlaybookStore:
    return PlaybookStore(conn)


def _make_playbook(
    target_date: date = date(2026, 2, 8),
    market: str = "KR",
    outlook: MarketOutlook = MarketOutlook.NEUTRAL,
    stock_codes: list[str] | None = None,
) -> DayPlaybook:
    """Create a test playbook with sensible defaults."""
    if stock_codes is None:
        stock_codes = ["005930"]
    return DayPlaybook(
        date=target_date,
        market=market,
        market_outlook=outlook,
        token_count=150,
        stock_playbooks=[
            StockPlaybook(
                stock_code=code,
                scenarios=[
                    StockScenario(
                        condition=StockCondition(rsi_below=30.0),
                        action=ScenarioAction.BUY,
                        confidence=85,
                        rationale=f"Oversold bounce for {code}",
                    ),
                ],
            )
            for code in stock_codes
        ],
        global_rules=[
            GlobalRule(
                condition="portfolio_pnl_pct < -2.0",
                action=ScenarioAction.REDUCE_ALL,
                rationale="Near circuit breaker",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_playbooks_table_exists(self, conn) -> None:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='playbooks'"
        ).fetchone()
        assert row is not None

    def test_playbooks_table_has_slot_column(self, conn) -> None:
        """playbooks 테이블에 slot 컬럼이 존재해야 한다."""
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='playbooks'"
        ).fetchone()
        assert row is not None
        assert "slot" in row[0]

    def test_playbooks_unique_by_date_market_slot(self, conn, store) -> None:
        """같은 (date, market, slot)은 upsert되어야 한다."""
        pb = _make_playbook()
        store.save(pb, slot="open")
        store.save(pb, slot="open")  # 두 번 저장해도 1건
        rows = conn.execute(
            "SELECT COUNT(*) FROM playbooks WHERE date=? AND market=? AND slot=?",
            (pb.date.isoformat(), pb.market, "open"),
        ).fetchone()
        assert rows[0] == 1

    def test_playbooks_open_and_mid_coexist(self, conn, store) -> None:
        """같은 date/market이라도 open과 mid는 별개 행으로 저장된다."""
        pb = _make_playbook()
        store.save(pb, slot="open")
        store.save(pb, slot="mid")
        rows = conn.execute(
            "SELECT slot FROM playbooks WHERE date=? AND market=? ORDER BY slot",
            (pb.date.isoformat(), pb.market),
        ).fetchall()
        slots = [r[0] for r in rows]
        assert slots == ["mid", "open"]

    def test_unique_constraint(self, store: PlaybookStore) -> None:
        pb = _make_playbook()
        store.save(pb)
        # Saving again for same date+market should replace, not error
        pb2 = _make_playbook(stock_codes=["005930", "000660"])
        store.save(pb2)
        loaded = store.load(date(2026, 2, 8), "KR")
        assert loaded is not None
        assert loaded.stock_count == 2

    def test_legacy_schema_migration_rebuilds_unique_constraint(self) -> None:
        """구 UNIQUE(date, market) → UNIQUE(date, market, slot) 마이그레이션 검증."""
        import os
        import sqlite3
        import tempfile

        # 파일 기반 DB 필요 — in-memory DB는 재연결 시 초기화됨
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            # 구 스키마 DB 생성 (slot 컬럼 없음, UNIQUE(date, market))
            conn_old = sqlite3.connect(db_path)
            conn_old.execute(
                """
                CREATE TABLE playbooks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    market TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    playbook_json TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    token_count INTEGER DEFAULT 0,
                    scenario_count INTEGER DEFAULT 0,
                    match_count INTEGER DEFAULT 0,
                    UNIQUE(date, market)
                )
                """
            )
            conn_old.execute(
                "INSERT INTO playbooks"
                " (date, market, status, playbook_json, generated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                ("2026-01-01", "KR", "ready", '{"v":1}', "2026-01-01T09:00:00"),
            )
            conn_old.commit()
            conn_old.close()

            # init_db 재실행 → 마이그레이션 수행
            conn_new = init_db(db_path)

            # UNIQUE 제약이 (date, market, slot)으로 변경됐는지 확인
            ddl_row = conn_new.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='playbooks'"
            ).fetchone()
            assert ddl_row is not None
            ddl = ddl_row[0].replace(" ", "").replace("\n", "")
            assert "UNIQUE(date,market,slot)" in ddl, (
                f"Expected UNIQUE(date,market,slot), got: {ddl_row[0]}"
            )

            # open + mid 동시 저장 가능한지 확인
            from src.strategy.playbook_store import PlaybookStore  # noqa: PLC0415
            store_new = PlaybookStore(conn_new)
            pb = _make_playbook()
            store_new.save(pb, slot="open")
            store_new.save(pb, slot="mid")  # 구 스키마라면 여기서 IntegrityError

            rows = conn_new.execute(
                "SELECT slot FROM playbooks WHERE date=? AND market=? ORDER BY slot",
                (pb.date.isoformat(), pb.market),
            ).fetchall()
            slots = [r[0] for r in rows]
            assert "open" in slots
            assert "mid" in slots

            # Step 6: 보조 인덱스가 재생성됐는지 확인 (issue #435 Medium)
            index_names = {
                r[1]
                for r in conn_new.execute("PRAGMA index_list('playbooks')").fetchall()
            }
            assert "idx_playbooks_date" in index_names, (
                f"idx_playbooks_date missing after rebuild; found: {index_names}"
            )
            assert "idx_playbooks_market" in index_names, (
                f"idx_playbooks_market missing after rebuild; found: {index_names}"
            )
            conn_new.close()
        finally:
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# Save / Load
# ---------------------------------------------------------------------------


class TestSaveLoad:
    def test_save_and_load(self, store: PlaybookStore) -> None:
        pb = _make_playbook()
        row_id = store.save(pb)
        assert row_id > 0

        loaded = store.load(date(2026, 2, 8), "KR")
        assert loaded is not None
        assert loaded.date == date(2026, 2, 8)
        assert loaded.market == "KR"
        assert loaded.stock_count == 1
        assert loaded.scenario_count == 1

    def test_load_not_found(self, store: PlaybookStore) -> None:
        result = store.load(date(2026, 1, 1), "KR")
        assert result is None

    def test_save_preserves_all_fields(self, store: PlaybookStore) -> None:
        pb = _make_playbook(
            outlook=MarketOutlook.BULLISH,
            stock_codes=["005930", "AAPL"],
        )
        store.save(pb)
        loaded = store.load(date(2026, 2, 8), "KR")
        assert loaded is not None
        assert loaded.market_outlook == MarketOutlook.BULLISH
        assert loaded.stock_count == 2
        assert loaded.global_rules[0].action == ScenarioAction.REDUCE_ALL
        assert loaded.token_count == 150

    def test_save_different_markets(self, store: PlaybookStore) -> None:
        kr = _make_playbook(market="KR")
        us = _make_playbook(market="US", stock_codes=["AAPL"])
        store.save(kr)
        store.save(us)

        kr_loaded = store.load(date(2026, 2, 8), "KR")
        us_loaded = store.load(date(2026, 2, 8), "US")
        assert kr_loaded is not None
        assert us_loaded is not None
        assert kr_loaded.market == "KR"
        assert us_loaded.market == "US"
        assert kr_loaded.stock_playbooks[0].stock_code == "005930"
        assert us_loaded.stock_playbooks[0].stock_code == "AAPL"

    def test_save_different_dates(self, store: PlaybookStore) -> None:
        d1 = _make_playbook(target_date=date(2026, 2, 7))
        d2 = _make_playbook(target_date=date(2026, 2, 8))
        store.save(d1)
        store.save(d2)

        assert store.load(date(2026, 2, 7), "KR") is not None
        assert store.load(date(2026, 2, 8), "KR") is not None

    def test_replace_updates_data(self, store: PlaybookStore) -> None:
        pb1 = _make_playbook(outlook=MarketOutlook.BEARISH)
        store.save(pb1)

        pb2 = _make_playbook(outlook=MarketOutlook.BULLISH)
        store.save(pb2)

        loaded = store.load(date(2026, 2, 8), "KR")
        assert loaded is not None
        assert loaded.market_outlook == MarketOutlook.BULLISH


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_get_status(self, store: PlaybookStore) -> None:
        store.save(_make_playbook())
        status = store.get_status(date(2026, 2, 8), "KR")
        assert status == PlaybookStatus.READY

    def test_get_status_not_found(self, store: PlaybookStore) -> None:
        assert store.get_status(date(2026, 1, 1), "KR") is None

    def test_update_status(self, store: PlaybookStore) -> None:
        store.save(_make_playbook())
        updated = store.update_status(date(2026, 2, 8), "KR", PlaybookStatus.EXPIRED)
        assert updated is True

        status = store.get_status(date(2026, 2, 8), "KR")
        assert status == PlaybookStatus.EXPIRED

    def test_update_status_not_found(self, store: PlaybookStore) -> None:
        updated = store.update_status(date(2026, 1, 1), "KR", PlaybookStatus.FAILED)
        assert updated is False


# ---------------------------------------------------------------------------
# Match count
# ---------------------------------------------------------------------------


class TestMatchCount:
    def test_increment_match_count(self, store: PlaybookStore) -> None:
        store.save(_make_playbook())
        store.increment_match_count(date(2026, 2, 8), "KR")
        store.increment_match_count(date(2026, 2, 8), "KR")

        stats = store.get_stats(date(2026, 2, 8), "KR")
        assert stats is not None
        assert stats["match_count"] == 2

    def test_increment_not_found(self, store: PlaybookStore) -> None:
        result = store.increment_match_count(date(2026, 1, 1), "KR")
        assert result is False


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestStats:
    def test_get_stats(self, store: PlaybookStore) -> None:
        store.save(_make_playbook())
        stats = store.get_stats(date(2026, 2, 8), "KR")
        assert stats is not None
        assert stats["status"] == "ready"
        assert stats["token_count"] == 150
        assert stats["scenario_count"] == 1
        assert stats["match_count"] == 0
        assert stats["generated_at"] != ""

    def test_get_stats_not_found(self, store: PlaybookStore) -> None:
        assert store.get_stats(date(2026, 1, 1), "KR") is None


# ---------------------------------------------------------------------------
# List recent
# ---------------------------------------------------------------------------


class TestListRecent:
    def test_list_recent(self, store: PlaybookStore) -> None:
        for day in range(5, 10):
            store.save(_make_playbook(target_date=date(2026, 2, day)))
        results = store.list_recent(market="KR", limit=3)
        assert len(results) == 3
        # Most recent first
        assert results[0]["date"] == "2026-02-09"
        assert results[2]["date"] == "2026-02-07"

    def test_list_recent_all_markets(self, store: PlaybookStore) -> None:
        store.save(_make_playbook(market="KR"))
        store.save(_make_playbook(market="US", stock_codes=["AAPL"]))
        results = store.list_recent(market=None, limit=10)
        assert len(results) == 2

    def test_list_recent_empty(self, store: PlaybookStore) -> None:
        results = store.list_recent(market="KR")
        assert results == []

    def test_list_recent_filter_by_market(self, store: PlaybookStore) -> None:
        store.save(_make_playbook(market="KR"))
        store.save(_make_playbook(market="US", stock_codes=["AAPL"]))
        kr_only = store.list_recent(market="KR")
        assert len(kr_only) == 1
        assert kr_only[0]["market"] == "KR"


# ---------------------------------------------------------------------------
# Slot parameter
# ---------------------------------------------------------------------------


class TestPlaybookStoreSlot:
    def test_save_and_load_open_slot(self, store) -> None:
        """slot='open'으로 저장하고 로드한다."""
        pb = _make_playbook()
        store.save(pb, slot="open")
        loaded = store.load(pb.date, pb.market, slot="open")
        assert loaded is not None
        assert loaded.market == pb.market

    def test_save_and_load_mid_slot(self, store) -> None:
        """slot='mid'로 저장하고 로드한다."""
        pb = _make_playbook()
        store.save(pb, slot="mid")
        loaded = store.load(pb.date, pb.market, slot="mid")
        assert loaded is not None

    def test_load_returns_none_for_missing_slot(self, store) -> None:
        """존재하지 않는 slot은 None을 반환한다."""
        pb = _make_playbook()
        store.save(pb, slot="open")
        assert store.load(pb.date, pb.market, slot="mid") is None

    def test_load_latest_returns_mid_when_both_exist(self, store) -> None:
        """open과 mid 모두 있을 때 load_latest는 mid를 반환한다."""
        pb_open = _make_playbook(stock_codes=["000001"])
        pb_mid = _make_playbook(stock_codes=["000002"])
        store.save(pb_open, slot="open")
        store.save(pb_mid, slot="mid")
        latest = store.load_latest(pb_open.date, pb_open.market)
        assert latest is not None
        assert latest.stock_playbooks[0].stock_code == "000002"

    def test_load_latest_returns_open_when_no_mid(self, store) -> None:
        """mid가 없을 때 load_latest는 open을 반환한다."""
        pb = _make_playbook(stock_codes=["000003"])
        store.save(pb, slot="open")
        latest = store.load_latest(pb.date, pb.market)
        assert latest is not None
        assert latest.stock_playbooks[0].stock_code == "000003"

    def test_load_latest_returns_none_when_empty(self, store) -> None:
        """아무것도 없으면 None을 반환한다."""
        assert store.load_latest(date(2026, 1, 1), "KR") is None

    def test_default_slot_is_open(self, store) -> None:
        """slot 파라미터 없이 save/load하면 open 슬롯을 사용한다."""
        pb = _make_playbook()
        store.save(pb)  # slot 미지정
        loaded = store.load(pb.date, pb.market)  # slot 미지정
        assert loaded is not None


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete(self, store: PlaybookStore) -> None:
        store.save(_make_playbook())
        deleted = store.delete(date(2026, 2, 8), "KR")
        assert deleted is True
        assert store.load(date(2026, 2, 8), "KR") is None

    def test_delete_not_found(self, store: PlaybookStore) -> None:
        deleted = store.delete(date(2026, 1, 1), "KR")
        assert deleted is False

    def test_delete_one_market_keeps_other(self, store: PlaybookStore) -> None:
        store.save(_make_playbook(market="KR"))
        store.save(_make_playbook(market="US", stock_codes=["AAPL"]))
        store.delete(date(2026, 2, 8), "KR")
        assert store.load(date(2026, 2, 8), "KR") is None
        assert store.load(date(2026, 2, 8), "US") is not None
