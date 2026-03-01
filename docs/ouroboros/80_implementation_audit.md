<!--
Doc-ID: DOC-AUDIT-001
Version: 1.2.0
Status: active
Owner: strategy
Updated: 2026-03-02
-->

# v2/v3 구현 감사 및 수익률 분석 보고서

작성일: 2026-02-28
최종 업데이트: 2026-03-02 (#373 상태표 정합화 반영)
대상 기간: 2026-02-25 ~ 2026-02-28 (실거래)
분석 브랜치: `feature/v3-session-policy-stream`

---

## 1. 계획 대비 구현 감사

### 1.1 완료 판정 기준 (Definition of Done)

아래 3가지를 모두 만족할 때만 `✅ 완료`로 표기한다.

1. 코드 경로 존재: 요구사항을 수행하는 실행 경로가 코드에 존재한다.
2. 효과 검증 통과: 요구사항 효과를 검증하는 테스트/런타임 증적이 존재한다.
3. 추적성 일치: 요구사항 상태와 열린 갭 이슈가 모순되지 않는다.

### 1.2 v2 구현 상태: 부분 완료 (핵심 갭 잔존)

| REQ-ID | 요구사항 | 구현 파일 | 상태 |
|--------|----------|-----------|------|
| REQ-V2-001 | 4-상태 매도 상태기계 (HOLDING→BE_LOCK→ARMED→EXITED) | `src/strategy/position_state_machine.py` | ✅ 완료 |
| REQ-V2-002 | 즉시 최상위 상태 승격 (갭 대응) | `position_state_machine.py:51-70` | ✅ 완료 |
| REQ-V2-003 | EXITED 우선 평가 | `position_state_machine.py:38-48` | ✅ 완료 |
| REQ-V2-004 | 4중 청산 로직 (Hard/BE/ATR Trailing/Model) | `src/strategy/exit_rules.py` | ⚠️ 부분 (`#369`) |
| REQ-V2-005 | Triple Barrier 라벨링 | `src/analysis/triple_barrier.py` | ✅ 완료 |
| REQ-V2-006 | Walk-Forward + Purge/Embargo 검증 | `src/analysis/walk_forward_split.py` | ✅ 완료 |
| REQ-V2-007 | 비용/슬리피지/체결실패 모델 필수 | `src/analysis/backtest_cost_guard.py` | ⚠️ 부분 (`#368`) |
| REQ-V2-008 | Kill Switch 실행 순서 (Block→Cancel→Refresh→Reduce→Snapshot) | `src/core/kill_switch.py` | ⚠️ 부분 (`#377`) |

### 1.3 v3 구현 상태: 부분 완료 (2026-03-02 기준)

| REQ-ID | 요구사항 | 상태 | 비고 |
|--------|----------|------|------|
| REQ-V3-001 | 모든 신호/주문/로그에 session_id 포함 | ⚠️ 부분 | 큐 intent에 `session_id` 누락 (`#375`) |
| REQ-V3-002 | 세션 전환 훅 + 리스크 파라미터 재로딩 | ⚠️ 부분 | 구현 존재, 세션 경계 E2E 회귀 보강 필요 (`#376`) |
| REQ-V3-003 | 블랙아웃 윈도우 정책 | ✅ 완료 | `src/core/blackout_manager.py` |
| REQ-V3-004 | 블랙아웃 큐 + 복구 시 재검증 | ⚠️ 부분 | 큐 포화 시 intent 유실 경로 존재 (`#371`), 재검증 강화를 `#328`에서 추적 |
| REQ-V3-005 | 저유동 세션 시장가 금지 | ✅ 완료 | `src/core/order_policy.py` |
| REQ-V3-006 | 보수적 백테스트 체결 (불리 방향) | ✅ 완료 | `src/analysis/backtest_execution_model.py` |
| REQ-V3-007 | FX 손익 분리 (전략 PnL vs 환율 PnL) | ⚠️ 부분 | 런타임 분리 계산/전달 적용 (`#370`), buy-side `fx_rate` 미관측 시 `fx_pnl=0` fallback |
| REQ-V3-008 | 오버나잇 예외 vs Kill Switch 우선순위 | ✅ 완료 | `src/main.py` — `_should_force_exit_for_overnight()`, `_apply_staged_exit_override_for_hold()` |

### 1.4 운영 거버넌스: 부분 완료 (2026-03-02 재평가)

| REQ-ID | 요구사항 | 상태 | 비고 |
|--------|----------|------|------|
| REQ-OPS-001 | 타임존 명시 (KST/UTC) | ⚠️ 부분 | 문서 토큰 fail-fast 추가, 필드 수준 검증은 `#372` 잔여 |
| REQ-OPS-002 | 정책 변경 시 레지스트리 업데이트 강제 | ⚠️ 부분 | 파일 단위 강제는 구현, 정책 수치 단위 정밀 검증은 `#372` 잔여 |
| REQ-OPS-003 | TASK-REQ 매핑 강제 | ⚠️ 부분 | TASK-REQ/TASK-TEST 강제는 구현, 우회 케이스 추가 점검은 `#372` 잔여 |
| REQ-OPS-004 | source 경로 표준화 검증 | ✅ 완료 | `scripts/validate_ouroboros_docs.py`의 canonical source path 검증 |

---

## 2. 구현 갭 상세

> **2026-03-02 업데이트**: 기존 해소 표기를 재검증했고, 열려 있는 갭 이슈 기준으로 상태를 재분류함.

### GAP-1: DecisionLogger에 session_id 미포함 → ✅ 해소 (#326)

- **위치**: `src/logging/decision_logger.py`
- ~~문제: `log_decision()` 함수에 `session_id` 파라미터가 없음~~
- **해소**: #326 머지 — `log_decision()` 파라미터에 `session_id` 추가, DB 기록 포함
- **요구사항**: REQ-V3-001

### GAP-2: src/main.py 거래 로그에 session_id 미전달 → ✅ 해소 (#326)

- **위치**: `src/main.py`
- ~~문제: `log_trade()` 호출 시 `session_id` 파라미터를 전달하지 않음~~
- **해소**: #326 머지 — `log_trade()` 호출 시 런타임 `session_id` 명시적 전달
- **요구사항**: REQ-V3-001

### GAP-3: 세션 전환 시 리스크 파라미터 재로딩 없음 → ⚠️ 부분 해소 (#327)

- **위치**: `src/main.py`, `src/config.py`
- **해소 내용**: #327 머지 — `SESSION_RISK_PROFILES_JSON` 기반 세션별 파라미터 재로딩 메커니즘 구현
  - `SESSION_RISK_RELOAD_ENABLED=true` 시 세션 경계에서 파라미터 재로딩
  - 재로딩 실패 시 기존 파라미터 유지 (안전 폴백)
- **잔여 갭**: 세션 경계 실시간 전환 E2E 통합 테스트 보강 필요 (`test_main.py`에 설정 오버라이드/폴백 단위 테스트는 존재)
- **요구사항**: REQ-V3-002

### GAP-4: 블랙아웃 복구 DB 기록 + 재검증 → ⚠️ 부분 해소 (#324, #328, #371)

- **위치**: `src/core/blackout_manager.py`, `src/main.py`
- **현 상태**:
  - #324 추적 범위(DB 기록)는 구현 경로가 존재
  - #328 범위(가격/세션 재검증 강화)는 추적 이슈 오픈 상태
  - #371: 큐 포화 시 intent 유실 경로가 남아 있어 `REQ-V3-004`를 완료로 보기 어려움
- **요구사항**: REQ-V3-004

### GAP-5: 시간장벽이 봉 개수 고정 → ✅ 해소 (#329)

- **위치**: `src/analysis/triple_barrier.py`
- ~~문제: `max_holding_bars` (고정 봉 수) 사용~~
- **해소**: #329 머지 — `max_holding_minutes` (캘린더 분) 기반 시간장벽 전환
  - 봉 주기 무관하게 일정 시간 경과 시 장벽 도달
  - `max_holding_bars` deprecated 경고 유지 (하위 호환)
- **요구사항**: REQ-V2-005 / v3 확장

### GAP-6 (신규): FX PnL 분리 부분 해소 (MEDIUM)

- **위치**: `src/db.py` (`fx_pnl`, `strategy_pnl` 컬럼 존재)
- **현 상태**: 런타임 SELL 경로에서 `strategy_pnl`/`fx_pnl` 분리 계산 및 전달을 적용함 (`#370`).
- **운영 메모**: `trading_cycle`은 scanner 기반 `selection_context`에 `fx_rate`를 추가하고, `run_daily_session`은 scanner 컨텍스트 없이 `fx_rate` 스냅샷만 기록한다.
- **잔여**: 과거 BUY 레코드에 `fx_rate`가 없으면 해외 구간도 `fx_pnl=0` fallback으로 기록됨.
- **영향**: USD 거래에서 환율 손익과 전략 손익이 분리되지 않아 성과 분석 부정확
- **요구사항**: REQ-V3-007

---

## 3. 실거래 수익률 분석

### 3.1 종합 성적

| 지표 | 값 |
|------|-----|
| 총 실현 손익 | **-52,481** (KRW + USD 혼합, 통화 분리 집계는 3.4 참조) |
| 총 거래 기록 | 19,130건 (BUY 121, SELL 46, HOLD 18,963) |
| 집계 기준 | UTC `2026-02-25T00:00:00` ~ `2026-02-28T00:00:00`, SELL 45건 (기간 외 1건 제외) |
| 승률 | **39.1%** (18승 / 46매도, 0손익 포함 기준) |
| 평균 수익 거래 | +6,107 |
| 평균 손실 거래 | -7,382 |
| 최대 수익 거래 | +46,350 KRW (452260 KR) |
| 최대 손실 거래 | -26,400 KRW (000370 KR) |
| 운영 모드 | LIVE (실계좌) |

### 3.2 일별 손익

| 날짜 | 매도 수 | 승 | 패 | 일간 손익 |
|------|---------|----|----|-----------|
| 02-25 | 9 | 8 | 1 | +63.21 (USD, 미세 수익) |
| 02-26 | 14 | 5 | 5 | **-32,083.40** (KR 대량 손실) |
| 02-27 | 22 | 5 | 16 | **-20,461.11** (고빈도 매매, 대부분 손실) |

> 정확한 재현: `scripts/audit_queries.sql` 참조.

### 3.3 시장별 손익

| 시장 | 매도 수 | 승률 | 총 손익 |
|------|---------|------|---------|
| **KR** | 17 | 38.5% (0손익 제외, 5/13) | **-56,735 KRW** |
| US_AMEX | 12 | 75% | +4,476 USD |
| US_NASDAQ | 4 | 0% | -177 USD |
| US_NYSE | 13 | 30.8% | -45 USD |

**KR 시장이 손실의 주 원인.** US는 AMEX 제외 시 대체로 손실 또는 보합.

### 3.4 재계산 주석 반영 (통화 분리)

> 산식 주석: 기존 표의 `총 실현 손익 -52,481`은 KRW/USD를 단순 합산한 값으로, 회계적으로 해석 불가.
> 아래는 같은 기간(2026-02-25~2026-02-27, SELL 45건)을 통화별로 분리한 결과.

| 통화 | 매도 수 | 승/패 | 실현 손익 |
|------|---------|-------|-----------|
| KRW | 17 | 5승 / 8패 (4건 0손익) | **-56,735 KRW** |
| USD | 28 | 13승 / 14패 (1건 0손익) | **+4,253.70 USD** |

### 3.5 재계산 주석 반영 (기존 보유 청산 성과 분리)

> 분리 기준: 각 SELL의 직전 BUY가 `rationale LIKE '[startup-sync]%'` 인 경우를
> `기존 보유(시작 시점 동기화 포지션) 청산`으로 분류.

| 구분 | 통화 | 매도 수 | 손익 |
|------|------|---------|------|
| 기존 보유 청산분 | KRW | 10 | **+12,230 KRW** |
| 기존 보유 청산분 | USD | 2 | **+21.03 USD** |
| 신규/전략 진입분만 | KRW | 7 | **-68,965 KRW** |
| 신규/전략 진입분만 | USD | 26 | **+4,232.67 USD** |

추가로, 요청 취지(“기존 보유 수익 종목 정리 수익 제외”)에 맞춰 **기존 보유 청산 중 수익(+PnL)만 제외**하면:

- KRW: `-56,735` → **-113,885 KRW** (기존 보유 수익 +57,150 KRW 제거)
- USD: `+4,253.70` → **+4,232.67 USD** (기존 보유 수익 +21.03 USD 제거)

즉, 기존 성과표는 기보유 청산 이익(특히 KR 452260 +46,350 KRW)을 전략 성과에 포함해
전략 자체 손익을 과대평가한 상태다.

### 3.6 데이터 무결성 점검 (모의투자 혼합 여부 + USD 과대수익 원인)

- `mode` 점검 결과: `live` 19,130건, `paper` 0건  
  → **모의투자 혼합은 확인되지 않음**.
- 다만 USD 손익에는 **체결 매칭 이상치 1건**이 존재:
  - `CRCA` SELL(15주, $35.14, +4,612.15 USD) vs 직전 BUY(146주, $3.5499)
  - BUY/SELL 수량 불일치(146→15) 상태에서 PnL이 계산되어, 역분할/동기화 이슈 가능성이 큼.

보수적 재집계(2026-02-25~2026-02-27, USD SELL 28건):

| 집계 기준 | USD 손익 | 환산 KRW (참고) | KRW 합산 참고값 |
|-----------|----------|-----------------|-----------------|
| 원집계 | **+4,253.70 USD** | +6,167,865 | -56,735 + 6,167,865 = **+6,111,130** |
| 기존보유(startup-sync) 제외 | **+4,232.67 USD** | +6,137,372 | -68,965 + 6,137,372 = **+6,068,407** |
| 수량 일치 체결만 포함 | **-358.45 USD** | -519,753 | -56,735 + (-519,753) = **-576,488** |
| 기존보유 제외 + 수량 일치 체결만 포함 | **-379.48 USD** | -550,246 | -68,965 + (-550,246) = **-619,211** |

> 가정 환율: **1 USD = 1,450 KRW** (2026-02-28 기준 참고 환율).
> 환산 KRW 및 합산값은 비교용 보조지표이며, 회계/정산 기준값과는 분리해 해석해야 한다.

결론적으로 USD 구간의 플러스 성과는 실질적으로 `CRCA` 이상치 1건 영향이 지배적이며,
해당 거래를 무결성 필터로 제외하면 USD 성과는 손실 구간으로 전환된다.

### 3.7 데이터 품질 이슈 요약

- **startup-sync 중복**: BUY 76건 반복 동기화, price=0 38건 → PnL 매칭 왜곡 가능. 분리 집계는 3.5 참조.
- **티커-거래소 드리프트**: 동일 티커가 다중 거래소에 혼재 기록 → ROOT-7 참조.
- **FX PnL 미활성**: 스키마 존재, 운영 데이터 전부 0 → REQ-V3-007 참조.

### 3.8 표준 집계 SQL (재현용)

성과표 재현을 위한 기준 쿼리는 [`scripts/audit_queries.sql`](../../scripts/audit_queries.sql)에 분리되어 있다.

- **Base**: 기간 + LIVE + SELL + 직전 BUY 메타 매칭
- **Q1**: 통화 분리 손익 (KRW/USD 혼합 금지)
- **Q2**: 기존 보유(startup-sync) 제외 성과
- **Q3**: 수량 일치 체결만 포함 (무결성 필터)
- **Q4**: 이상치 목록 (수량 불일치)

---

## 4. 수익률 저조 근본 원인 분석

### ROOT-1: hard_stop_pct 기본값(-2%)이 KR 소형주 변동성 대비 과소

- **현재 설정**: `stop_loss_threshold = -2.0` (`src/main.py:511`), staged exit의 `hard_stop_pct`로 전달
- **v2 계획**: ATR 기반 동적 trailing stop (ExitPrice = PeakPrice - k × ATR)
- **실제 동작**: staged exit는 호출되나, `atr_value`/`pred_down_prob` 등 피처가 0.0으로 공급되어 hard_stop 편향 발동 (ROOT-5 참조)
- **증거**:
  - 000370: 매수 8,040 → 24분 후 -2.74% 손절
  - 033340: 매수 2,080 → 18분 후 -3.13% 손절
  - 229000: -3.7%, -3.25%, -3.2% 반복 손절

### ROOT-2: 동일 종목 반복 매매 (재진입 쿨다운 미구현)

- **문제**: 손절 후 동일 종목 즉시 재매수 → 고가 재진입 → 재손절 반복
- **최악 사례**: 종목 229000
  | 매수가 | 매도가 | 손익 | 보유 시간 |
  |--------|--------|------|-----------|
  | 5,670 | 5,460 | -24,780 | 0.5h |
  | 5,540 | 5,360 | -21,780 | 0.7h |
  | 5,310 | 5,580 | +34,020 (승) | 0.8h |
  | 5,620 | 5,440 | -21,420 | 1.5h |
- **순손실**: 하루 한 종목에서 **-33,960 KRW**

### ROOT-3: 미국 페니스탁/마이크로캡 무분별 진입

- **문제**: $2 이하 종목에 confidence 85~90으로 진입, 오버나잇 대폭락
- **사례**:
  | 종목 | 손실률 | 보유시간 |
  |------|--------|----------|
  | ALBT | -27.7% | ~23h |
  | SMJF | -15.9% | ~23h |
  | KAPA | -18.2% | ~23h |
  | CURX | -10.6% | ~23h |
  | CELT | -8.3% | ~23h |

### ROOT-4: 진화 전략 코드 생성기 문법 오류

- **위치**: `src/strategies/v20260227_*_evolved.py`
- **문제**: 중첩 `def evaluate` 정의 (들여쓰기 오류)
- **영향**: 런타임 실패 → 기본 전략으로 폴백 → 진화 시스템 사실상 무효

### ROOT-5: v2 청산 로직이 부분 통합되었으나 실효성 부족 → ⚠️ 부분 해소 (#325)

**초기 진단 (2026-02-28 감사 기준):**
- `hard_stop_pct`에 고정 `-2.0`이 기본값으로 들어가 v2 계획의 ATR 적응형 의도와 괴리
- `be_arm_pct`/`arm_pct`가 playbook의 `take_profit_pct`에서 기계적 파생(`* 0.4`)되어 v2 계획의 독립 파라미터 튜닝 불가
- `atr_value`, `pred_down_prob` 등 런타임 피처가 0.0으로 공급되어 사실상 hard stop만 발동

**현재 상태 (#325 머지 후):**
- `STAGED_EXIT_BE_ARM_PCT`, `STAGED_EXIT_ARM_PCT` 환경변수로 독립 파라미터 설정 가능
- `_inject_staged_exit_features()`: KR 시장 ATR 실시간 계산 주입, RSI 기반 `pred_down_prob` 공급
- KR ATR dynamic hard stop (#318)으로 `-2.0` 고정값 문제 해소

**잔여 리스크:**
- KR 외 시장(US 등)에서 `atr_value` 공급 경로 불완전 — hard stop 편향 잔존 가능
- `pred_down_prob`가 RSI 프록시 수준 — 추후 실제 ML 모델 대체 권장

### ROOT-6: SELL 손익 계산이 부분청산/수량 불일치에 취약 (CRITICAL) → ✅ 해소 (#322)

> **현재 상태**: #322 머지로 해소됨. 아래는 원인 발견 시점(2026-02-28) 진단 기록.

- **위치**: `src/main.py:1658-1663`, `src/main.py:2755-2760`
- **문제**: PnL 계산이 실제 매도 수량(`sell_qty`)이 아닌 직전 BUY의 `buy_qty`를 사용
  - `trade_pnl = (trade_price - buy_price) * buy_qty`
- **영향**: 부분청산, 역분할/액분할, startup-sync 후 수량 드리프트 시 손익 과대/과소 계상
- **실증**: CRCA 이상치(BUY 146주 → SELL 15주에서 PnL +4,612 USD) 가 이 버그와 정합

### ROOT-7: BUY 매칭 키에 exchange_code 미포함 — 잠재 오매칭 리스크 (HIGH) → ✅ 해소 (#323)

> **현재 상태**: #323 머지로 해소됨. 아래는 원인 발견 시점(2026-02-28) 진단 기록.

- **위치**: `src/db.py:292-313`
- **문제**: `get_latest_buy_trade()`가 `(stock_code, market)`만으로 매칭, `exchange_code` 미사용
- **성격**: 현재 즉시 발생하는 확정 버그가 아닌, 동일 티커가 다중 거래소에 혼재 기록될 때 증폭되는 구조 리스크
- **영향**: 데이터 드리프트 조건(예: CCUP/CRCA 등 다중 exchange 기록)에서 오매칭 → 손익 왜곡 가능

---

## 5. 수익률 개선 방안

### 5.1 즉시 적용 가능 (파라미터/로직 수정)

| 우선순위 | 방안 | 예상 효과 | 난이도 |
|----------|------|-----------|--------|
| P0 | KR 손절선 확대: -2% → -4~5% 또는 ATR 기반 | 노이즈 손절 대폭 감소 | 낮음 |
| P0 | 재진입 쿨다운: 손절 후 동일 종목 1~2시간 매수 차단 | churn & burn 패턴 제거 | 낮음 |
| P1 | US 최소 가격 필터: $5 이하 종목 진입 차단 | 페니스탁 대폭락 방지 | 낮음 |
| P1 | 진화 전략 코드 생성 시 syntax 검증 추가 | 진화 시스템 정상화 | 낮음 |

### 5.2 구조적 개선 현황 (2026-03-01 기준)

**완료 항목 (모니터링 단계):**

| 항목 | 이슈 | 상태 |
|------|------|------|
| SELL PnL 계산을 sell_qty 기준으로 수정 (ROOT-6) | #322 | ✅ 머지 |
| v2 staged exit 피처 공급 + 독립 파라미터 설정 (ROOT-5) | #325 | ✅ 머지 |
| BUY 매칭 키에 exchange_code 추가 (ROOT-7) | #323 | ✅ 머지 |
| 블랙아웃 복구 주문 `log_trade()` 추가 (GAP-4) | #324 | ✅ 머지 |
| 세션 전환 리스크 파라미터 동적 재로딩 (GAP-3) | #327 | ✅ 머지 |
| session_id 거래/의사결정 로그 명시 전달 (GAP-1, GAP-2) | #326 | ✅ 머지 |
| 블랙아웃 복구 가격/세션 재검증 강화 (GAP-4 잔여) | #328 | ✅ 머지 |

**잔여 개선 항목:**

| 우선순위 | 방안 | 난이도 |
|----------|------|--------|
| P1 | US 시장 ATR 공급 경로 완성 (ROOT-5 잔여) | 중간 |
| P1 | FX PnL 운영 활성화 (REQ-V3-007) | 낮음 |
| P2 | pred_down_prob ML 모델 대체 (ROOT-5 잔여) | 높음 |
| P2 | 세션 경계 E2E 통합 테스트 보강 (GAP-3 잔여) | 낮음 |

### 5.3 권장 실행 순서

```
Phase 1 (즉시): 파라미터 조정
  → KR 손절 확대 + 재진입 쿨다운 + US 가격 필터
  → 예상: 가장 큰 손실 패턴 2개(노이즈 손절, 반복 매매) 즉시 제거

Phase 2 (단기): 데이터 정합성 + v2 실효화
  → SELL PnL을 sell_qty 기준으로 수정
  → BUY 매칭 키에 exchange_code 추가
  → 블랙아웃 복구 주문 DB 기록 추가
  → v2 staged exit에 실제 피처(ATR, pred_down_prob) 공급 + 독립 파라미터 설정
  → session_id 명시적 전달
  → 예상: 손익 정확도 확보 + 수익 구간 보호 메커니즘 실효화

Phase 3 (중기): v3 세션 최적화
  → 세션 전환 훅 + 파라미터 재로딩
  → 블랙아웃 재검증
  → 운영 거버넌스 CI 자동화
```

---

## 6. 테스트 커버리지 현황

### 테스트 존재 (통과)

- ✅ 상태기계 승격 (`test_strategy_state_machine.py`)
- ✅ 4중 청산 규칙 (`test_strategy_exit_rules.py`)
- ✅ Triple Barrier 라벨링 (`test_triple_barrier.py`)
- ✅ Walk-Forward + Purge/Embargo (`test_walk_forward_split.py`)
- ✅ 백테스트 비용 검증 (`test_backtest_cost_guard.py`)
- ✅ Kill Switch 순서 (`test_kill_switch.py`)
- ✅ 블랙아웃 관리 (`test_blackout_manager.py`)
- ✅ 주문 정책 저유동 거부 (`test_order_policy.py`)
- ✅ FX 손익 분리 (`test_db.py`)
- ✅ 블랙아웃 복구 후 유효 intent 실행 (`tests/test_main.py:5811`)
- ✅ 블랙아웃 복구 후 정책 거부 intent 드롭 (`tests/test_main.py:5851`)

### 테스트 추가됨 (Phase 1~3, 2026-03-01)

- ✅ KR ATR 기반 동적 hard stop (`test_main.py` — #318)
- ✅ 재진입 쿨다운 (손절 후 동일 종목 매수 차단) (`test_main.py` — #319)
- ✅ US 최소 가격 필터 ($5 이하 차단) (`test_main.py` — #320)
- ✅ 진화 전략 syntax 검증 (`test_evolution.py` — #321)
- ✅ SELL PnL sell_qty 기준 계산 (`test_main.py` — #322)
- ✅ BUY 매칭 키 exchange_code 포함 (`test_db.py` — #323)
- ✅ 블랙아웃 복구 주문 DB 기록 (`test_main.py` — #324)
- ✅ staged exit에 실제 ATR/RSI 피처 공급 (`test_main.py` — #325)
- ✅ session_id 거래/의사결정 로그 명시적 전달 (`test_main.py`, `test_decision_logger.py` — #326)
- ✅ 블랙아웃 복구 후 유효 intent 실행 (`tests/test_main.py:5811`)
- ✅ 블랙아웃 복구 후 정책 거부 intent 드롭 (`tests/test_main.py:5851`)

### 테스트 미존재 (잔여)

- ❌ 세션 전환 훅 콜백 (GAP-3 잔여)
- ❌ 세션 경계 리스크 파라미터 재로딩 단위 테스트 (GAP-3 잔여)
- ❌ 실거래 경로 ↔ v2 상태기계 통합 테스트 (피처 공급 포함)
- ❌ FX PnL 운영 활성화 검증 (GAP-6)

---

## 7. 후속 문서

- **실행 계획**: [85_loss_recovery_action_plan.md](./85_loss_recovery_action_plan.md) — ROOT/GAP 해소를 위한 Phase별 작업 분해 및 Gitea 이슈 연결
- **표준 집계 SQL**: [scripts/audit_queries.sql](../../scripts/audit_queries.sql)

---

*끝.*
