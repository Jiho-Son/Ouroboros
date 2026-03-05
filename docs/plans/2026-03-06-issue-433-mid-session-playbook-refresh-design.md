# Design: Mid-Session Playbook Refresh Policy

**Date:** 2026-03-06
**Issue:** #433

## 배경

플레이북이 장 시작 시 한 번 생성되면 당일 내내 고정된다. 프리마켓에서 선정된 종목이 정규장에서 모멘텀을 잃어도 갱신되지 않아 거래 기회를 놓친다. (오늘 사례: IBO/TPET 페니스탁이 플레이북에 고정되어 하루 종일 BUY suppressed)

## 목표

US/KR 정규장 중간(12:00 PM 현지 시각)에 플레이북을 한 번 자동 갱신한다.

## 요구사항

- US 정규장(US_REG): 12:00 PM ET에 갱신
- KR 정규장(KRX_REG): 12:00 PM KST에 갱신
- 포지션 여부 상관없이 항상 갱신
- 기존 플레이북(open 슬롯)은 보존
- 새 플레이북은 `mid` 슬롯으로 별도 저장
- 재시작 시 mid 슬롯 존재 여부로 로드 슬롯 결정
- Telegram 알림에 슬롯 구분 표시
- Dashboard API에 slot 파라미터 지원

## 변경 상세

### 1. DB: playbooks 테이블

`slot TEXT NOT NULL DEFAULT 'open'` 컬럼 추가.

```sql
ALTER TABLE playbooks ADD COLUMN slot TEXT NOT NULL DEFAULT 'open';
```

기존 행은 `slot='open'`으로 자동 설정됨.

고유 키: `(date, market, slot)` — 기존 `(date, market)` unique constraint 교체.

### 2. PlaybookStore (`src/strategy/playbook_store.py`)

```python
def load(self, date, market_code, slot: str = 'open') -> DayPlaybook | None
def save(self, playbook, slot: str = 'open') -> None  # upsert by (date, market, slot)
def load_latest(self, date, market_code) -> DayPlaybook | None
    # mid 슬롯 있으면 mid, 없으면 open 반환 (재시작용)
```

### 3. main.py — 갱신 트리거

```python
MID_SESSION_REFRESH_SESSIONS = {"US_REG", "KRX_REG"}
MID_SESSION_REFRESH_HOUR = 12  # 현지 시각 12:00 (정오)

def _should_mid_session_refresh(
    market: MarketInfo,
    session_id: str,
    now: datetime,
    mid_refreshed: set[str],
) -> bool:
    if session_id not in MID_SESSION_REFRESH_SESSIONS:
        return False
    if market.code in mid_refreshed:
        return False
    local_now = now.astimezone(market.timezone)
    return local_now.hour == MID_SESSION_REFRESH_HOUR and local_now.minute == 0
```

메인 루프에서:
1. `_should_mid_session_refresh()` 체크
2. True이면 `playbooks.pop(market.code)` — 메모리 캐시 무효화
3. `mid_refreshed.add(market.code)` — 당일 중복 방지
4. 다음 스캐너 사이클에서 자동으로 새 플레이북 생성 → `save(pb, slot='mid')`

**재시작 처리:** 스캐너 사이클 내 플레이북 로드 시 `load_latest()` 사용.

**`mid_refreshed` 초기화:** 날짜가 바뀌면 초기화 (기존 `market_today` 변경 감지 패턴 활용).

### 4. Telegram (`src/notifications/telegram_client.py`)

`notify_playbook_generated()`에 `slot` 파라미터 추가:

```python
async def notify_playbook_generated(
    self, market, stock_count, scenario_count, token_count, slot: str = 'open'
) -> None:
    label = "Mid-Session Refresh" if slot == 'mid' else "Playbook Generated"
    message = f"<b>{label}</b>\nMarket: {market}\n..."
```

### 5. Dashboard (`src/dashboard/app.py`)

**`/api/status`:**
```sql
SELECT status FROM playbooks
WHERE date = ? AND market = ?
ORDER BY generated_at DESC LIMIT 1
```
(기존 `LIMIT 1`은 ORDER BY 없이 무작위 — 수정 필요)

**`/api/playbook/{date_str}`:**
```python
@app.get("/api/playbook/{date_str}")
def get_playbook(date_str, market="KR", slot: str | None = None):
    # slot이 None이면 generated_at DESC LIMIT 1 (최신)
    # slot 지정 시 해당 슬롯만 조회
```

응답에 `slot` 필드 추가.

## 테스트 범위

- `tests/test_playbook_store.py`: slot 파라미터 load/save, load_latest() 동작
- `tests/test_main.py`: `_should_mid_session_refresh()` 조건 검증
- `tests/test_dashboard.py`: slot 파라미터 API 테스트
- `tests/test_telegram.py` (있으면): slot별 메시지 구분

## 영향 없는 부분

- KR 플레이북 DB refresh 로직 (`_refresh_cached_playbook_on_session_transition`) — 별개 동작
- PlaybookStore의 기존 `open` 슬롯 동작 — 하위 호환 유지
