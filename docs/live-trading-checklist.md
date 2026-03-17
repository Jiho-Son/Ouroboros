# 실전 전환 체크리스트

모의 거래(paper)에서 실전(live)으로 전환하기 전에 아래 항목을 **순서대로** 모두 확인하세요.

---

## 1. 사전 조건

### 1-1. KIS OpenAPI 실전 계좌 준비
- [ ] 한국투자증권 계좌 개설 완료 (일반 위탁 계좌)
- [ ] OpenAPI 실전 사용 신청 (KIS 홈페이지 → Open API → 서비스 신청)
- [ ] 실전용 APP_KEY / APP_SECRET 발급 완료
- [ ] KIS_ACCOUNT_NO 형식 확인: `XXXXXXXX-XX` (8자리-2자리)

### 1-2. 리스크 파라미터 검토
- [ ] `CIRCUIT_BREAKER_PCT` 확인: 기본값 -3.0% (더 엄격하게 조정 권장)
- [ ] `FAT_FINGER_PCT` 확인: 기본값 30.0% (1회 주문 최대 잔고 대비 %)
- [ ] `CONFIDENCE_THRESHOLD` 확인: BEARISH ≥ 90, NEUTRAL ≥ 80, BULLISH ≥ 75
- [ ] 초기 투자금 결정 및 해외 주식 운용 한도 설정

### 1-3. 시스템 요건
- [ ] 커버리지 80% 이상 유지 확인: `pytest --cov=src`
- [ ] 타입 체크 통과: `mypy src/ --strict`
- [ ] Lint 통과: `ruff check src/ tests/`

---

## 2. 환경 설정

### 2-1. `.env` 파일 수정

```bash
# 1. KIS 실전 URL로 변경 (모의: openapivts 포트 29443)
KIS_BASE_URL=https://openapi.koreainvestment.com:9443

# 1-1. WebSocket URL 확인 (미설정 시 코드가 자동 기본값 선택)
# 실전 기본값: ws://ops.koreainvestment.com:21000
# 모의 기본값: ws://ops.koreainvestment.com:31000
# 필요 시만 직접 override
# KIS_WS_URL=ws://ops.koreainvestment.com:21000

# 2. 실전 APP_KEY / APP_SECRET으로 교체
KIS_APP_KEY=<실전_APP_KEY>
KIS_APP_SECRET=<실전_APP_SECRET>
KIS_ACCOUNT_NO=<실전_계좌번호>

# 3. 모드를 live로 변경
MODE=live

# 4. PAPER_OVERSEAS_CASH 비활성화 (live 모드에선 무시되지만 명시적으로 0 설정)
PAPER_OVERSEAS_CASH=0
```

> ⚠️ `KIS_BASE_URL` 포트 주의:
> - **모의(VTS)**: `https://openapivts.koreainvestment.com:29443`
> - **실전**: `https://openapi.koreainvestment.com:9443`
> - **모의 WebSocket 기본값**: `ws://ops.koreainvestment.com:31000`
> - **실전 WebSocket 기본값**: `ws://ops.koreainvestment.com:21000`

### 2-2. TR_ID 자동 분기 확인

아래 TR_ID는 `MODE` 값에 따라 코드에서 **자동으로 선택**됩니다.
별도 설정 불필요하나, 문제 발생 시 아래 표를 참조하세요.

| 구분 | 모의 TR_ID | 실전 TR_ID |
|------|-----------|-----------|
| 국내 잔고 조회 | `VTTC8434R` | `TTTC8434R` |
| 국내 현금 매수 | `VTTC0012U` | `TTTC0012U` |
| 국내 현금 매도 | `VTTC0011U` | `TTTC0011U` |
| 해외 잔고 조회 | `VTTS3012R` | `TTTS3012R` |
| 해외 매수 | `VTTT1002U` | `TTTT1002U` |
| 해외 매도 | `VTTT1001U` | `TTTT1006U` |

> **출처**: `docs/한국투자증권_오픈API_전체문서_20260221_030000.xlsx` (공식 문서 기준)

---

## 3. 최종 확인

### 3-1. 실전 시작 전 점검
- [ ] DB 백업 완료: `data/trade_logs.db` → `data/backups/`
- [ ] Telegram 알림 설정 확인 (실전에서는 알림이 더욱 중요)
- [ ] 소액으로 첫 거래 진행 후 TR_ID/계좌 정상 동작 확인

### 3-2. 실행 명령

```bash
# 실전 모드로 실행
python -m src.main --mode=live

# 대시보드 함께 실행 (별도 터미널에서 모니터링)
python -m src.main --mode=live --dashboard
```

### 3-2-1. 운영 프로세스 규칙
- [ ] 상시 유지하는 canonical 운영 프로세스는 `main` 브랜치 checkout에서만 실행한다.
- [ ] canonical 운영 프로세스 재시작은 Symphony `hooks.before_remove` 가 merged worktree 삭제 직전에 `git rev-parse --show-toplevel` 로 repo root를 해석한 뒤 `bash "$repo_root/scripts/symphony_before_remove_canonical_restart.sh"` 를 실행해 자동 처리하는지 확인한다.
- [ ] canonical 재시작 dedupe marker/log 가 canonical 상태 루트(`data/overnight/canonical_restart.*` 기본값)에만 기록되고, `canonical_restart.log` 에 hook invocation/skip/fail/start 신호가 남는지 확인한다.
- [ ] `scripts/symphony_before_remove_canonical_restart.sh --dry-run` 검증은 canonical `main` checkout 경로와 restart 계획만 stdout 으로 보여주고, `fetch origin` 이나 `canonical_restart.log`/marker 파일 쓰기를 발생시키지 않는지 확인한다.
- [ ] 별도 worktree에서 검증용 런타임을 띄울 때는 동일 명령을 그대로 사용하되, 스크립트가 branch별 `LOG_DIR` / `DASHBOARD_PORT` / `LIVE_RUNTIME_LOCK_PATH` 를 자동 분리한다는 점을 확인한다.
- [ ] `main` checkout에서는 기본 런타임 경로가 `data/overnight`, 기본 dashboard 포트가 `8080` 인지 확인한다.
- [ ] non-`main` worktree에서는 `scripts/run_overnight.sh` 와 `scripts/runtime_verify_monitor.sh` 가 `data/overnight/<branch-slug>` 를 사용하고 `8080` 이외 포트를 자동 선택하는지 확인한다.

### 3-3. 실전 시작 직후 확인 사항
- [ ] 로그에 `MODE=live` 출력 확인
- [ ] 첫 잔고 조회 성공 (ConnectionError 없음)
- [ ] Telegram 알림 수신 확인 ("System started")
- [ ] realtime hard-stop 모드에서 `Realtime hard-stop websocket monitor started enabled_markets=<...> source=websocket_hard_stop` 로그 확인
- [ ] US 보유 포지션이 realtime hard-stop 추적 대상이면 `Realtime websocket action=connect` 와 해당 종목의 `Realtime websocket action=subscribe` 또는 `Realtime websocket action=resubscribe` 로그 확인
- [ ] US 추적 종목에 대해 `Realtime websocket action=parsed_us_event` 또는 `Realtime websocket action=ignore_us_parse_failure` 또는 `Realtime price event action=no_trigger` 또는 `Realtime price event action=dispatch_trigger` 중 최소 1개가 관측되는지 확인
- [ ] 첫 주문 후 KIS 앱에서 체결 내역 확인

### 3-4. 손절/익절 동작 차이 확인
- [ ] KR/US 하드 스탑은 WebSocket 실시간 가격 이벤트 기준으로 감시됨
- [ ] 익절/ATR trailing/모델 보조 청산은 기존 polling loop 기준으로 유지됨
- [ ] WebSocket 장애 시 polling 기반 staged-exit이 fallback으로 남아 있음

### 3-5. US websocket hard-stop close criteria
- [ ] 재시작 검증은 같은 worktree의 최신 `run_*.log` 와 `decision_logs`/`trades` DB 증적만 사용한다.
- [ ] `Realtime websocket action=connect` 와 US 종목별 `action=subscribe` 또는 `action=resubscribe` 가 모두 관측되어야 한다.
- [ ] US 이벤트 경로에서 `parsed_us_event`, `ignore_us_parse_failure`, `no_trigger`, `dispatch_trigger` 중 필요한 단계가 관측되어야 한다.
- [ ] websocket hard-stop SELL 이 발생한 경우 `Realtime hard-stop action=decision_logged`, `Realtime hard-stop action=trade_logged`, `Realtime hard-stop action=persisted ... source=websocket_hard_stop` 가 모두 관측되어야 한다.
- [ ] `decision_logs` 와 `trades` 양쪽에서 `source=websocket_hard_stop` 가 확인되어야 한다.
- [ ] 필요한 단계 중 하나라도 미관측이면 `NOT_OBSERVED` 로 기록하고 close 하지 않는다.

---

## 4. 비상 정지 방법

### 즉각 정지
```bash
# 터미널에서 Ctrl+C (정상 종료 트리거)
# 또는 Telegram 봇 명령:
/stop
```

### Circuit Breaker 발동 시
- CB가 발동되면 자동으로 거래 중단 및 Telegram 알림 전송
- CB 임계값: `CIRCUIT_BREAKER_PCT` (기본 -3.0%)
- **임계값은 엄격하게만 조정 가능** (더 낮은 음수 값으로만 변경)

---

## 5. 롤백 절차

실전 전환 후 문제 발생 시:

```bash
# 1. 즉시 런타임을 중지하고 live 재기동을 금지
# 2. DB에서 최근 거래 확인
# Runtime paper mode is banned (#426)

sqlite3 data/trade_logs.db "SELECT * FROM trades ORDER BY id DESC LIMIT 20;"
```

---

## 관련 문서

- [시스템 아키텍처](architecture.md)
- [워크플로우 가이드](workflow.md)
- [재해 복구](disaster_recovery.md)
- [Agent 제약 조건](agents.md)
