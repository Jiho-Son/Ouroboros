# Mid-Session Playbook Refresh Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** US/KR 정규장 12:00 PM(현지)에 플레이북을 자동 갱신하고, `slot` 컬럼으로 open/mid 플레이북을 별도 저장한다.

**Architecture:** `playbooks` 테이블에 `slot` 컬럼 추가 + `UNIQUE(date, market, slot)`으로 변경. `PlaybookStore`에 slot 파라미터 추가. 메인 루프에서 `_should_mid_session_refresh()` 판단 후 메모리 캐시 무효화 → 다음 스캔 사이클에서 자동 `mid` 플레이북 생성. Telegram/Dashboard도 slot 인식하도록 수정.

**Tech Stack:** Python, SQLite (sqlite3), pydantic, pytest, FastAPI (dashboard)

---

### Task 1: DB 스키마 — `slot` 컬럼 추가 및 마이그레이션

**Files:**
- Modify: `src/db.py:160-176`
- Test: `tests/test_playbook_store.py` (기존 파일에 추가)

**배경:**
현재 `playbooks` 테이블은 `UNIQUE(date, market)`. `slot TEXT NOT NULL DEFAULT 'open'` 컬럼을 추가하고 unique constraint를 `(date, market, slot)`으로 교체. 실제 DB는 `ALTER TABLE`로 마이그레이션.

**Step 1: 실패하는 테스트 작성**

`tests/test_playbook_store.py`의 `class TestSchema` 안에 추가:

```python
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
```

**Step 2: 테스트 실행 — 실패 확인**

```bash
cd /home/agentson/repos/The-Ouroboros && uv run pytest tests/test_playbook_store.py::TestSchema::test_playbooks_table_has_slot_column -v
```
Expected: FAILED — `slot` 컬럼 없음

**Step 3: `src/db.py` 수정**

`CREATE TABLE IF NOT EXISTS playbooks` 블록 (line 160-171)을 아래로 교체:

```python
        CREATE TABLE IF NOT EXISTS playbooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            market TEXT NOT NULL,
            slot TEXT NOT NULL DEFAULT 'open',
            status TEXT NOT NULL DEFAULT 'pending',
            playbook_json TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            token_count INTEGER DEFAULT 0,
            scenario_count INTEGER DEFAULT 0,
            match_count INTEGER DEFAULT 0,
            UNIQUE(date, market, slot)
        )
```

그리고 기존 DB 마이그레이션을 위해 `init_db()` 함수 끝 부분(인덱스 생성 이후)에 아래 추가:

```python
    # Migration: add slot column if not exists (issue #433)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(playbooks)").fetchall()}
    if "slot" not in cols:
        conn.execute("ALTER TABLE playbooks ADD COLUMN slot TEXT NOT NULL DEFAULT 'open'")
        conn.commit()
        logger.info("DB migration: added slot column to playbooks table")
```

`src/db.py` 상단에 `import logging`과 `logger = logging.getLogger(__name__)`이 없으면 추가.

**Step 4: 테스트 실행 — 통과 확인**

```bash
cd /home/agentson/repos/The-Ouroboros && uv run pytest tests/test_playbook_store.py::TestSchema -v
```
Expected: PASSED

**Step 5: 커밋**

```bash
cd /home/agentson/repos/The-Ouroboros && git add src/db.py tests/test_playbook_store.py && git commit -m "feat: add slot column to playbooks table for mid-session refresh (#433)"
```

---

### Task 2: PlaybookStore — slot 파라미터 지원

**Files:**
- Modify: `src/strategy/playbook_store.py`
- Test: `tests/test_playbook_store.py`

**배경:**
`save()`, `load()`, `get_status()`, `update_status()`, `increment_match_count()`, `get_stats()`, `delete()` 모두 `slot` 파라미터를 추가. 새로운 `load_latest()` 메서드 추가 (재시작 시 mid 우선 로드).

**Step 1: 실패하는 테스트 작성**

`tests/test_playbook_store.py`에 새 클래스 추가:

```python
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
```

**Step 2: 테스트 실행 — 실패 확인**

```bash
cd /home/agentson/repos/The-Ouroboros && uv run pytest tests/test_playbook_store.py::TestPlaybookStoreSlot -v
```
Expected: FAILED — `save()`/`load()`에 slot 파라미터 없음

**Step 3: `src/strategy/playbook_store.py` 수정**

`save()` 교체:
```python
def save(self, playbook: DayPlaybook, slot: str = "open") -> int:
    """Save or replace a playbook for a given date+market+slot."""
    playbook_json = playbook.model_dump_json()
    cursor = self._conn.execute(
        """
        INSERT OR REPLACE INTO playbooks
            (date, market, slot, status, playbook_json, generated_at,
             token_count, scenario_count, match_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            playbook.date.isoformat(),
            playbook.market,
            slot,
            PlaybookStatus.READY.value,
            playbook_json,
            playbook.generated_at,
            playbook.token_count,
            playbook.scenario_count,
            0,
        ),
    )
    self._conn.commit()
    row_id = cursor.lastrowid or 0
    logger.info(
        "Saved playbook for %s/%s slot=%s (%d stocks, %d scenarios)",
        playbook.date,
        playbook.market,
        slot,
        playbook.stock_count,
        playbook.scenario_count,
    )
    return row_id
```

`load()` 교체:
```python
def load(self, target_date: date, market: str, slot: str = "open") -> DayPlaybook | None:
    """Load a playbook for a specific date, market, and slot."""
    row = self._conn.execute(
        "SELECT playbook_json FROM playbooks WHERE date = ? AND market = ? AND slot = ?",
        (target_date.isoformat(), market, slot),
    ).fetchone()
    if row is None:
        return None
    return DayPlaybook.model_validate_json(row[0])
```

`load_latest()` 추가 (클래스 내 `load()` 바로 다음):
```python
def load_latest(self, target_date: date, market: str) -> DayPlaybook | None:
    """Load the most recent playbook: mid if exists, otherwise open.

    Used on restart to resume from the most up-to-date playbook.
    """
    row = self._conn.execute(
        """
        SELECT playbook_json FROM playbooks
        WHERE date = ? AND market = ?
        ORDER BY CASE slot WHEN 'mid' THEN 0 ELSE 1 END, generated_at DESC
        LIMIT 1
        """,
        (target_date.isoformat(), market),
    ).fetchone()
    if row is None:
        return None
    return DayPlaybook.model_validate_json(row[0])
```

나머지 메서드(`get_status`, `update_status`, `increment_match_count`, `get_stats`, `delete`)에도 `slot: str = "open"` 파라미터 추가 및 WHERE 절에 `AND slot = ?` 추가.

**Step 4: 테스트 실행**

```bash
cd /home/agentson/repos/The-Ouroboros && uv run pytest tests/test_playbook_store.py -v
```
Expected: 전체 PASSED

**Step 5: 커밋**

```bash
cd /home/agentson/repos/The-Ouroboros && git add src/strategy/playbook_store.py tests/test_playbook_store.py && git commit -m "feat: add slot parameter to PlaybookStore (load/save/load_latest) (#433)"
```

---

### Task 3: main.py — 갱신 트리거 로직

**Files:**
- Modify: `src/main.py`
- Test: `tests/test_main.py`

**배경:**
`_should_mid_session_refresh()` 함수 추가. 메인 루프 내 각 마켓 처리 시 체크 후 메모리 캐시 무효화. 플레이북 저장 시 `slot='mid'` 전달. 재시작 시 `load_latest()` 사용으로 전환.

**Step 1: 실패하는 테스트 작성**

`tests/test_main.py`에 추가 (imports 확인 후 기존 패턴 따라):

```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from src.main import _should_mid_session_refresh


class TestMidSessionRefresh:
    def _make_dt(self, hour: int, minute: int, tz: str) -> datetime:
        return datetime(2026, 3, 5, hour, minute, 0, tzinfo=ZoneInfo(tz))

    def test_triggers_at_noon_us_reg(self) -> None:
        """US_REG 12:00 ET에 True."""
        now = self._make_dt(12, 0, "America/New_York")
        assert _should_mid_session_refresh(
            market_code="US_NASDAQ", session_id="US_REG",
            now=now, mid_refreshed=set()
        ) is True

    def test_triggers_at_noon_kr_reg(self) -> None:
        """KRX_REG 12:00 KST에 True."""
        now = self._make_dt(12, 0, "Asia/Seoul")
        assert _should_mid_session_refresh(
            market_code="KR", session_id="KRX_REG",
            now=now, mid_refreshed=set()
        ) is True

    def test_does_not_trigger_before_noon(self) -> None:
        """11:59에는 False."""
        now = self._make_dt(11, 59, "America/New_York")
        assert _should_mid_session_refresh(
            market_code="US_NYSE", session_id="US_REG",
            now=now, mid_refreshed=set()
        ) is False

    def test_does_not_trigger_after_noon(self) -> None:
        """12:01에는 False."""
        now = self._make_dt(12, 1, "America/New_York")
        assert _should_mid_session_refresh(
            market_code="US_AMEX", session_id="US_REG",
            now=now, mid_refreshed=set()
        ) is False

    def test_does_not_trigger_wrong_session(self) -> None:
        """US_PRE 세션에는 False."""
        now = self._make_dt(12, 0, "America/New_York")
        assert _should_mid_session_refresh(
            market_code="US_NASDAQ", session_id="US_PRE",
            now=now, mid_refreshed=set()
        ) is False

    def test_does_not_trigger_if_already_refreshed(self) -> None:
        """이미 오늘 갱신된 마켓은 False."""
        now = self._make_dt(12, 0, "America/New_York")
        assert _should_mid_session_refresh(
            market_code="US_NASDAQ", session_id="US_REG",
            now=now, mid_refreshed={"US_NASDAQ"}
        ) is False
```

**Step 2: 테스트 실행 — 실패 확인**

```bash
cd /home/agentson/repos/The-Ouroboros && uv run pytest tests/test_main.py::TestMidSessionRefresh -v
```
Expected: FAILED — `_should_mid_session_refresh` not found

**Step 3: `src/main.py`에 함수 추가**

`_should_reuse_stored_playbook` (line ~3777) 바로 위에 추가:

```python
_MID_SESSION_REFRESH_SESSIONS: dict[str, str] = {
    "US_NASDAQ": "US_REG",
    "US_NYSE": "US_REG",
    "US_AMEX": "US_REG",
    "KR": "KRX_REG",
}


def _should_mid_session_refresh(
    *,
    market_code: str,
    session_id: str,
    now: datetime,
    mid_refreshed: set[str],
) -> bool:
    """Return True when a mid-session playbook refresh should fire.

    Triggers once per day at 12:00 (local market time) during the regular session.
    """
    expected_session = _MID_SESSION_REFRESH_SESSIONS.get(market_code)
    if expected_session is None or session_id != expected_session:
        return False
    if market_code in mid_refreshed:
        return False
    market_tz = _MID_SESSION_REFRESH_TZ.get(market_code, timezone.utc)
    local_now = now.astimezone(market_tz)
    return local_now.hour == 12 and local_now.minute == 0
```

그리고 TZ 매핑 상수도 같은 위치에 추가:

```python
from zoneinfo import ZoneInfo  # 파일 상단 imports에 추가 (없으면)

_MID_SESSION_REFRESH_TZ: dict[str, ZoneInfo] = {
    "US_NASDAQ": ZoneInfo("America/New_York"),
    "US_NYSE": ZoneInfo("America/New_York"),
    "US_AMEX": ZoneInfo("America/New_York"),
    "KR": ZoneInfo("Asia/Seoul"),
}
```

**Step 4: 테스트 실행**

```bash
cd /home/agentson/repos/The-Ouroboros && uv run pytest tests/test_main.py::TestMidSessionRefresh -v
```
Expected: 6개 PASSED

**Step 5: 커밋**

```bash
cd /home/agentson/repos/The-Ouroboros && git add src/main.py tests/test_main.py && git commit -m "feat: add _should_mid_session_refresh() function (#433)"
```

---

### Task 4: main.py — 메인 루프에 갱신 연결

**Files:**
- Modify: `src/main.py` (메인 루프 내 플레이북 로드/저장 구간)

**배경:**
메인 루프 내에서 `_should_mid_session_refresh()` 체크 → 메모리 캐시 무효화 + `mid_refreshed` 추가. 플레이북 저장 시 slot 전달. 재시작 로드 시 `load_latest()` 사용.

이 태스크는 코드 로직이 복잡하므로 변경 위치를 정확히 기술한다.

**Step 1: `mid_refreshed` 변수 초기화**

메인 루프 시작 부분 (각 마켓 루프 바깥쪽, `playbooks: dict` 선언 근처)에 추가:

```python
mid_refreshed: set[str] = set()  # 당일 mid-session refresh가 완료된 마켓
```

**Step 2: 날짜 변경 시 `mid_refreshed` 초기화**

기존에 날짜가 바뀔 때 초기화하는 로직이 있는 곳을 찾아서(grep: `market_today`가 바뀌는 곳) `mid_refreshed.clear()` 추가.

찾는 방법:
```bash
grep -n "market_today\|date.*changed\|new.*date" src/main.py | head -20
```

**Step 3: 갱신 트리거 삽입**

메인 루프 내에서 `session_changed` 처리 직후 (~line 4565), 스캐너 실행 전 위치에 아래 추가:

```python
# Mid-session playbook refresh (12:00 현지 시각)
now_utc = datetime.now(UTC)
if _should_mid_session_refresh(
    market_code=market.code,
    session_id=session_info.session_id,
    now=now_utc,
    mid_refreshed=mid_refreshed,
):
    logger.info(
        "Mid-session refresh triggered for %s (session=%s)",
        market.code,
        session_info.session_id,
    )
    playbooks.pop(market.code, None)
    mid_refreshed.add(market.code)
```

**Step 4: 플레이북 저장 시 slot 전달**

플레이북을 새로 생성·저장하는 위치 (line ~4681):

현재:
```python
playbook_store.save(pb)
```

`mid_refreshed`에 해당 마켓이 포함돼 있으면 `slot='mid'`, 아니면 `slot='open'`:

```python
save_slot = "mid" if market.code in mid_refreshed else "open"
playbook_store.save(pb, slot=save_slot)
```

Telegram 알림에도 slot 전달 (line ~4684):
```python
await telegram.notify_playbook_generated(
    market=market.code,
    stock_count=pb.stock_count,
    scenario_count=pb.scenario_count,
    token_count=pb.token_count,
    slot=save_slot,
)
```

**Step 5: 재시작 시 load_latest() 사용**

플레이북을 DB에서 로드하는 위치 (~line 4654):

현재:
```python
stored_pb = (
    playbook_store.load(market_today, market.code)
    if reuse_stored_pb
    else None
)
```

교체:
```python
stored_pb = (
    playbook_store.load_latest(market_today, market.code)
    if reuse_stored_pb
    else None
)
```

**Step 6: 전체 테스트**

```bash
cd /home/agentson/repos/The-Ouroboros && uv run pytest tests/ -v --tb=short 2>&1 | tail -20
```
Expected: PASSED

**Step 7: 커밋**

```bash
cd /home/agentson/repos/The-Ouroboros && git add src/main.py && git commit -m "feat: wire mid-session refresh trigger into main loop (#433)"
```

---

### Task 5: Telegram — slot 구분 알림

**Files:**
- Modify: `src/notifications/telegram_client.py:372-399`
- Test: (기존 telegram 테스트 있으면 추가, 없으면 스킵)

**Step 1: `notify_playbook_generated()` 수정**

`src/notifications/telegram_client.py`의 `notify_playbook_generated()` (line 372):

```python
async def notify_playbook_generated(
    self,
    market: str,
    stock_count: int,
    scenario_count: int,
    token_count: int,
    slot: str = "open",
) -> None:
    if not self._filter.playbook:
        return
    label = "Playbook Refreshed (mid-session)" if slot == "mid" else "Playbook Generated"
    message = (
        f"<b>{label}</b>\n"
        f"Market: {market}\n"
        f"Stocks: {stock_count}\n"
        f"Scenarios: {scenario_count}\n"
        f"Tokens: {token_count}"
    )
    await self._send_notification(
        NotificationMessage(priority=NotificationPriority.MEDIUM, message=message)
    )
```

**Step 2: 전체 테스트**

```bash
cd /home/agentson/repos/The-Ouroboros && uv run pytest tests/ -v --tb=short 2>&1 | tail -10
```
Expected: PASSED

**Step 3: 커밋**

```bash
cd /home/agentson/repos/The-Ouroboros && git add src/notifications/telegram_client.py && git commit -m "feat: add slot label to playbook generated notification (#433)"
```

---

### Task 6: Dashboard — slot 파라미터 지원

**Files:**
- Modify: `src/dashboard/app.py:65-78, 129-152`
- Test: `tests/test_dashboard.py`

**Step 1: 실패하는 테스트 작성**

`tests/test_dashboard.py`에서 기존 테스트 구조 확인 후 추가:

```python
def test_get_playbook_returns_slot_field(client, db_with_playbook):
    """GET /api/playbook/{date} 응답에 slot 필드가 포함돼야 한다."""
    response = client.get("/api/playbook/2026-02-08?market=KR")
    assert response.status_code == 200
    data = response.json()
    assert "slot" in data

def test_get_playbook_slot_param_mid(client, db_with_open_and_mid_playbook):
    """slot=mid 파라미터로 mid 플레이북을 조회할 수 있다."""
    response = client.get("/api/playbook/2026-02-08?market=KR&slot=mid")
    assert response.status_code == 200
    data = response.json()
    assert data["slot"] == "mid"

def test_get_playbook_default_returns_latest(client, db_with_open_and_mid_playbook):
    """slot 미지정 시 가장 최근(mid) 플레이북을 반환한다."""
    response = client.get("/api/playbook/2026-02-08?market=KR")
    assert response.status_code == 200
    data = response.json()
    assert data["slot"] == "mid"
```

(fixture `db_with_open_and_mid_playbook`는 기존 `db_with_playbook` 패턴을 참고해 작성)

**Step 2: 테스트 실행 — 실패 확인**

```bash
cd /home/agentson/repos/The-Ouroboros && uv run pytest tests/test_dashboard.py -k "slot" -v
```
Expected: FAILED

**Step 3: `src/dashboard/app.py` 수정**

`/api/status`의 playbook 쿼리 (line 65-73) 수정 — ORDER BY 추가:
```python
playbook_row = conn.execute(
    """
    SELECT status
    FROM playbooks
    WHERE date = ? AND market = ?
    ORDER BY generated_at DESC
    LIMIT 1
    """,
    (today, market),
).fetchone()
```

`/api/playbook/{date_str}` 엔드포인트 (line 129-152) 수정:
```python
@app.get("/api/playbook/{date_str}")
def get_playbook(
    date_str: str,
    market: str = Query("KR"),
    slot: str | None = Query(default=None),
) -> dict[str, Any]:
    with _connect(db_path) as conn:
        if slot is not None:
            row = conn.execute(
                """
                SELECT date, market, slot, status, playbook_json, generated_at,
                       token_count, scenario_count, match_count
                FROM playbooks
                WHERE date = ? AND market = ? AND slot = ?
                """,
                (date_str, market, slot),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT date, market, slot, status, playbook_json, generated_at,
                       token_count, scenario_count, match_count
                FROM playbooks
                WHERE date = ? AND market = ?
                ORDER BY generated_at DESC
                LIMIT 1
                """,
                (date_str, market),
            ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="playbook not found")
        return {
            "date": row["date"],
            "market": row["market"],
            "slot": row["slot"],
            "status": row["status"],
            "playbook": json.loads(row["playbook_json"]),
            "generated_at": row["generated_at"],
            "token_count": row["token_count"],
            "scenario_count": row["scenario_count"],
            "match_count": row["match_count"],
        }
```

**Step 4: 전체 테스트**

```bash
cd /home/agentson/repos/The-Ouroboros && uv run pytest tests/ --tb=short 2>&1 | tail -10
```
Expected: PASSED

**Step 5: lint**

```bash
cd /home/agentson/repos/The-Ouroboros && uv run ruff check src/dashboard/app.py src/strategy/playbook_store.py src/main.py src/notifications/telegram_client.py src/db.py
```
Expected: 오류 없음

**Step 6: 커밋**

```bash
cd /home/agentson/repos/The-Ouroboros && git add src/dashboard/app.py tests/test_dashboard.py && git commit -m "feat: add slot param to dashboard playbook API (#433)"
```

---

### Task 7: PR 생성

**Step 1: 전체 테스트 최종 확인**

```bash
cd /home/agentson/repos/The-Ouroboros && uv run pytest tests/ -v --tb=short 2>&1 | tail -30
```

**Step 2: 커버리지 확인**

```bash
cd /home/agentson/repos/The-Ouroboros && uv run pytest tests/test_playbook_store.py tests/test_main.py tests/test_dashboard.py --cov=src/strategy/playbook_store --cov=src/dashboard/app --cov-report=term-missing 2>&1 | tail -20
```
Expected: 80% 이상

**Step 3: PR 생성**

```bash
cd /home/agentson/repos/The-Ouroboros && git push -u origin feature/issue-433-mid-session-playbook-refresh

YES="" ~/bin/tea pulls create \
  --head feature/issue-433-mid-session-playbook-refresh \
  --base main \
  --title "feat: mid-session playbook refresh at 12:00 local time (#433)" \
  --description "## 변경 사항

- playbooks 테이블에 slot 컬럼 추가 (open/mid), UNIQUE(date, market, slot)
- PlaybookStore: slot 파라미터 지원 (load/save/load_latest)
- 메인 루프: US_REG/KRX_REG 12:00 현지에 플레이북 캐시 무효화 → mid 슬롯으로 재생성
- Telegram: slot에 따라 'Playbook Generated' vs 'Playbook Refreshed (mid-session)' 구분
- Dashboard /api/playbook: slot 쿼리 파라미터 지원, 미지정 시 최신 슬롯 반환

Closes #433"
```
