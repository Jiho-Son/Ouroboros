<!--
Doc-ID: DOC-ACTION-085
Version: 1.1.0
Status: active
Owner: strategy
Updated: 2026-03-01
-->

# 손실 복구 실행 계획

작성일: 2026-02-28
최종 업데이트: 2026-03-01 (Phase 1~3 완료 상태 반영)
기반 문서: [80_implementation_audit.md](./80_implementation_audit.md) (ROOT 7개 + GAP 5개)

> **2026-03-01 현황**: Phase 1 ✅ 완료, Phase 2 ✅ 완료, Phase 3 ✅ 기본 완료 (ACT-13 고도화 잔여)

---

## 1. 요약

### 1.1 목표

80_implementation_audit.md에서 식별된 7개 근본 원인(ROOT-1~7)과 5개 구현 갭(GAP-1~5)을 해소하여 실거래 손실 구간에서 탈출한다.

### 1.2 성공 기준 (정량)

| 지표 | 현재 | 목표 |
|------|------|------|
| KR 시장 승률 | 38.5% | >= 50% |
| 동일 종목 반복 매매 (일간) | 최대 4회 | <= 2회 |
| US 페니스탁($5 이하) 진입 | 무제한 | 0건 |
| SELL PnL 수량 불일치 건 | 존재 | 0건 |
| 블랙아웃 복구 주문 DB 누락 | 존재 | 0건 |
| session_id 누락 거래 로그 | 다수 | 0건 |
| 진화 전략 syntax 오류율 | 100% (확인된 3건 모두) | 0% |

---

## 2. Phase별 작업 분해

### Phase 1: 즉시 — 손실 출혈 차단 ✅ 완료

가장 큰 손실 패턴(노이즈 손절, 반복 매매, 페니스탁)을 즉시 제거한다.

---

#### ACT-01: KR 손절선 ATR 기반 동적 확대 ✅ 머지

- **ROOT 참조**: ROOT-1 (hard_stop_pct -2%가 KR 소형주 변동성 대비 과소)
- **Gitea 이슈**: feat: KR 손절선 ATR 기반 동적 확대 (-2% → ATR 적응형)
- **Gitea 이슈 번호**: #318
- **변경 대상 파일**: `src/main.py`, `src/strategy/exit_rules.py`, `src/config.py`
- **현재 동작**: `hard_stop_pct = -2.0` 고정값으로 모든 시장에 동일 적용
- **목표 동작**: KR 시장은 ATR(14) 기반 동적 손절선 적용. 최소 -2%, 최대 -7%, 기본값은 `k * ATR / entry_price * 100` (k=2.0)
- **수용 기준**:
  - ATR 값이 존재할 때 동적 손절선이 계산됨
  - ATR 미제공 시 기존 -2% 폴백
  - KR 이외 시장은 기존 동작 유지
- **테스트 계획**:
  - 단위: ATR 기반 손절선 계산 로직 테스트 (경계값: ATR=0, ATR=극단값)
  - 통합: 백테스트 파이프라인에서 KR 종목 손절 빈도 비교
- **의존성**: 없음

---

#### ACT-02: 손절 후 동일 종목 재진입 쿨다운 ✅ 머지

- **ROOT 참조**: ROOT-2 (동일 종목 반복 매매)
- **Gitea 이슈**: feat: 손절 후 동일 종목 재진입 쿨다운 (1~2시간)
- **Gitea 이슈 번호**: #319
- **변경 대상 파일**: `src/main.py`, `src/config.py`
- **현재 동작**: 손절 후 동일 종목 즉시 재매수 가능
- **목표 동작**: 손절(SELL with pnl < 0) 후 동일 종목은 `COOLDOWN_MINUTES` (기본 120분) 동안 매수 차단
- **수용 기준**:
  - 손절 기록이 있는 종목에 대해 쿨다운 시간 내 BUY 시도 시 거부
  - 쿨다운 경과 후 정상 진입 허용
  - 익절(pnl >= 0)에는 쿨다운 미적용
- **테스트 계획**:
  - 단위: 쿨다운 시간 내/외 매수 시도 테스트
  - 통합: 229000 유사 패턴 백테스트 시나리오
- **의존성**: 없음

---

#### ACT-03: US $5 이하 종목 진입 차단 필터 ✅ 머지

- **ROOT 참조**: ROOT-3 (미국 페니스탁 무분별 진입)
- **Gitea 이슈**: feat: US $5 이하 종목 진입 차단 필터
- **Gitea 이슈 번호**: #320
- **변경 대상 파일**: `src/main.py`, `src/config.py`
- **현재 동작**: 가격 제한 없이 모든 US 종목 진입 가능
- **목표 동작**: US 시장 BUY 시 현재가 $5 이하이면 진입 차단. 임계값은 `US_MIN_PRICE` 환경변수로 설정 가능
- **수용 기준**:
  - $5 이하 종목 BUY 시도 시 거부 + 로그 기록
  - $5 초과 종목은 기존 동작 유지
  - KR 등 다른 시장에는 미적용
- **테스트 계획**:
  - 단위: 가격별 필터 동작 테스트 (경계값: $4.99, $5.00, $5.01)
- **의존성**: 없음

---

#### ACT-04: 진화 전략 코드 생성 시 syntax 검증 추가 ✅ 머지

- **ROOT 참조**: ROOT-4 (진화 전략 문법 오류)
- **Gitea 이슈**: fix: 진화 전략 코드 생성 시 syntax 검증 추가
- **Gitea 이슈 번호**: #321
- **변경 대상 파일**: `src/evolution/optimizer.py`
- **현재 동작**: 생성된 Python 코드를 검증 없이 파일로 저장
- **목표 동작**: `ast.parse()` + `compile()` 로 syntax 검증 후 통과한 코드만 저장. 실패 시 로그 경고 + 기존 전략 유지
- **수용 기준**:
  - syntax 오류가 있는 코드는 저장되지 않음
  - 검증 실패 시 기존 전략으로 폴백
  - 검증 실패 로그가 기록됨
- **테스트 계획**:
  - 단위: 정상 코드/오류 코드 검증 테스트
  - 기존 `v20260227_*_evolved.py` 파일로 회귀 테스트
- **의존성**: 없음

---

### Phase 2: 단기 — 데이터 정합성 + v2 실효화 ✅ 완료

손익 계산 정확도를 확보하고, v2 청산 로직을 실효화한다.

---

#### ACT-05: SELL PnL 계산을 sell_qty 기준으로 수정 ✅ 머지

- **ROOT 참조**: ROOT-6 (CRITICAL — PnL 계산이 buy_qty 사용)
- **Gitea 이슈**: fix(critical): SELL PnL 계산을 sell_qty 기준으로 수정
- **Gitea 이슈 번호**: #322
- **변경 대상 파일**: `src/main.py` (line 1658-1663, 2755-2760)
- **현재 동작**: `trade_pnl = (trade_price - buy_price) * buy_qty` — 직전 BUY 수량 사용
- **목표 동작**: `trade_pnl = (trade_price - buy_price) * sell_qty` — 실제 매도 수량 사용
- **수용 기준**:
  - 부분청산 시 매도 수량 기준 PnL 계산
  - 기존 전량 매도(buy_qty == sell_qty) 케이스는 동일 결과
  - CRCA 유사 이상치 재발 불가
- **테스트 계획**:
  - 단위: 전량 매도, 부분 매도, 수량 불일치 케이스별 PnL 검증
  - DB: Q4 쿼리(`scripts/audit_queries.sql`)로 이상치 0건 확인
- **의존성**: 없음

---

#### ACT-06: BUY 매칭 키에 exchange_code 추가 ✅ 머지

- **ROOT 참조**: ROOT-7 (BUY 매칭 키에 exchange_code 미포함)
- **Gitea 이슈**: fix: BUY 매칭 키에 exchange_code 추가
- **Gitea 이슈 번호**: #323
- **변경 대상 파일**: `src/db.py` (line 292-313)
- **현재 동작**: `get_latest_buy_trade()`가 `(stock_code, market)`만으로 매칭
- **목표 동작**: `exchange_code`가 존재할 때 매칭 키에 포함. NULL인 경우 기존 동작 유지 (하위 호환)
- **수용 기준**:
  - 동일 티커 다중 거래소 기록 시 정확한 BUY 매칭
  - exchange_code가 NULL인 레거시 데이터에서도 정상 동작
- **테스트 계획**:
  - 단위: 동일 티커 다중 exchange 매칭 테스트
  - 단위: exchange_code NULL 하위 호환 테스트
- **의존성**: 없음

---

#### ACT-07: 블랙아웃 복구 주문에 log_trade() 추가 ✅ 머지

- **ROOT 참조**: GAP-4 (블랙아웃 복구 주문 DB 미기록)
- **Gitea 이슈**: fix: 블랙아웃 복구 주문에 log_trade() 추가
- **Gitea 이슈 번호**: #324
- **변경 대상 파일**: `src/main.py` — `process_blackout_recovery_orders()` 함수 내 복구 주문 실행 경로
- **현재 동작**: 블랙아웃 복구 주문이 실행되나 `log_trade()` 호출 없음 → DB에 기록 안 됨
- **목표 동작**: 복구 주문 실행 후 `log_trade()` 호출하여 DB에 기록. rationale에 `[blackout-recovery]` prefix 추가
- **수용 기준**:
  - 블랙아웃 복구 주문이 trades 테이블에 기록됨
  - rationale로 복구 주문 식별 가능
  - 성과 리포트에 복구 주문 포함
- **테스트 계획**:
  - 단위: 복구 주문 실행 후 DB 기록 존재 확인
  - 통합: 블랙아웃 시나리오 end-to-end 테스트
- **의존성**: 없음

---

#### ACT-08: v2 staged exit에 실제 피처 공급 ✅ 머지

- **ROOT 참조**: ROOT-5 (v2 청산 로직 실효성 부족)
- **Gitea 이슈**: feat: v2 staged exit에 실제 피처(ATR, pred_down_prob) 공급
- **Gitea 이슈 번호**: #325
- **변경 대상 파일**: `src/main.py` (line 500-583), `src/strategy/exit_rules.py`, `src/analysis/technical.py`
- **현재 동작**: `atr_value=0.0`, `pred_down_prob=0.0`으로 공급 → hard stop만 발동
- **목표 동작**:
  - `atr_value`: 보유 종목의 ATR(14) 실시간 계산하여 공급
  - `pred_down_prob`: 최소한 RSI 기반 하락 확률 추정값 공급 (추후 ML 모델로 대체 가능)
  - `be_arm_pct`/`arm_pct`: 독립 파라미터로 설정 가능 (take_profit_pct * 0.4 기계적 파생 제거)
- **수용 기준**:
  - `evaluate_exit()` 호출 시 atr_value > 0 (ATR 계산 가능한 종목)
  - ATR trailing stop이 실제 발동 가능
  - be_arm_pct/arm_pct 독립 설정 가능
- **테스트 계획**:
  - 단위: 피처 공급 경로별 값 검증
  - 통합: 상태기계 전이 시나리오 (HOLDING→BE_LOCK→ARMED→EXITED)
- **의존성**: ACT-01 (ATR 계산 인프라 공유)

---

#### ACT-09: session_id를 거래/의사결정 로그에 명시적 전달 ✅ 머지

- **ROOT 참조**: GAP-1 (DecisionLogger session_id 미포함), GAP-2 (log_trade session_id 미전달)
- **Gitea 이슈**: feat: session_id를 거래/의사결정 로그에 명시적 전달
- **Gitea 이슈 번호**: #326
- **변경 대상 파일**: `src/logging/decision_logger.py`, `src/main.py` (line 1625, 1682, 2769), `src/db.py`
- **현재 동작**:
  - `log_decision()`: session_id 파라미터 없음
  - `log_trade()`: session_id 미전달, 시장 코드 기반 자동 추론에 의존
- **목표 동작**:
  - `log_decision()`: session_id 파라미터 추가, 로그에 기록
  - `log_trade()` 호출 시 런타임 session_id 명시적 전달
- **수용 기준**:
  - 모든 SELL/BUY 로그에 session_id 필드 존재
  - 의사결정 로그에 session_id 필드 존재
  - session_id가 실제 런타임 세션과 일치
- **테스트 계획**:
  - 단위: log_decision() session_id 캡처 테스트
  - 단위: log_trade() session_id 전달 테스트
- **의존성**: 없음

---

### Phase 3: 중기 — v3 세션 최적화 ✅ 기본 완료 (ACT-13 고도화 잔여)

세션 경계 처리와 운영 거버넌스를 강화한다.

---

#### ACT-10: 세션 전환 시 리스크 파라미터 동적 재로딩 ✅ 머지

- **ROOT 참조**: GAP-3 (세션 전환 시 리스크 파라미터 재로딩 없음)
- **Gitea 이슈**: feat: 세션 전환 시 리스크 파라미터 동적 재로딩
- **Gitea 이슈 번호**: #327
- **변경 대상 파일**: `src/main.py`, `src/config.py`
- **현재 동작**: 리스크 파라미터가 시작 시 한 번만 로딩
- **목표 동작**: 세션 경계 변경 이벤트 시 해당 세션의 리스크 파라미터를 재로딩. 세션별 프로파일 지원
- **수용 기준**:
  - NXT_AFTER → KRX_REG 전환 시 파라미터 재로딩 확인
  - 재로딩 이벤트 로그 기록
  - 재로딩 실패 시 기존 파라미터 유지 (안전 폴백)
- **테스트**: `test_main.py`에 설정 오버라이드/리로드/폴백 단위 테스트 포함. **잔여**: 세션 경계 실시간 전환 E2E 보강
- **의존성**: ACT-09 (session_id 인프라)

---

#### ACT-11: 블랙아웃 복구 시 가격/세션 재검증 강화 ✅ 머지

- **ROOT 참조**: GAP-4 잔여 (가격 유효성, 세션 변경 재적용 미구현)
- **Gitea 이슈**: feat: 블랙아웃 복구 시 가격/세션 재검증 강화
- **Gitea 이슈 번호**: #328
- **변경 대상 파일**: `src/main.py` (line 694-791), `src/core/blackout_manager.py`
- **현재 동작**: stale BUY/SELL 드롭 + order_policy 검증만 수행
- **목표 동작**:
  - 복구 시 현재 시세 조회하여 가격 유효성 검증 (진입가 대비 급등/급락 시 드롭)
  - 세션 변경 시 새 세션의 파라미터로 재검증
- **수용 기준**:
  - 블랙아웃 전후 가격 변동 > 임계값(예: 5%) 시 주문 드롭
  - 세션 변경 시 새 세션 파라미터로 재평가
- **테스트 계획**:
  - 단위: 가격 변동 시나리오별 드롭/실행 테스트
  - 통합: 블랙아웃 + 세션 전환 복합 시나리오
- **의존성**: ACT-07 (복구 주문 DB 기록), ACT-10 (세션 파라미터 재로딩)

---

#### ACT-12: Triple Barrier 시간장벽을 캘린더 시간(분) 기반으로 전환 ✅ 머지

- **ROOT 참조**: GAP-5 (시간장벽이 봉 개수 고정)
- **Gitea 이슈**: feat: Triple Barrier 시간장벽을 캘린더 시간(분) 기반으로 전환
- **Gitea 이슈 번호**: #329
- **변경 대상 파일**: `src/analysis/triple_barrier.py`
- **현재 동작**: `max_holding_bars` (고정 봉 수) 사용
- **목표 동작**: `max_holding_minutes` (캘린더 시간) 기반으로 전환. 봉 주기와 무관하게 일정 시간 경과 시 장벽 도달
- **수용 기준**:
  - 분 단위 시간장벽이 봉 주기 변경에도 일관 동작
  - 기존 max_holding_bars 하위 호환 (deprecated 경고)
- **테스트 계획**:
  - 단위: 다양한 봉 주기(1분, 5분, 15분)에서 시간장벽 일관성 테스트
  - 기존 triple_barrier 테스트 회귀 확인
- **의존성**: 없음

---

#### ACT-13: CI 자동 검증 (정책 레지스트리 + TASK-REQ 매핑) ✅ 기본 구현 완료, 고도화 잔여

- **ROOT 참조**: REQ-OPS-002 (정책 변경 시 레지스트리 업데이트 강제), REQ-OPS-003 (TASK-REQ 매핑 강제)
- **Gitea 이슈**: infra: CI 자동 검증 (정책 레지스트리 + TASK-REQ 매핑)
- **Gitea 이슈 번호**: #330
- **현재 동작**: `.gitea/workflows/ci.yml`에서 `scripts/validate_governance_assets.py` + `scripts/validate_ouroboros_docs.py` 자동 실행
- **잔여 고도화**: PR 본문 REQ/TASK/TEST 강제 레벨 상향, 정책 파일 미업데이트 시 CI 실패 기준 강화
- **의존성**: 없음

---

## 3. 검증 계획

### 3.1 단위 테스트

- 모든 ACT 항목에 대해 개별 테스트 작성
- 커버리지 >= 80% 유지
- 현재 CI 기준 전체 테스트 통과 확인 (2026-03-01 기준 998 tests collected)

### 3.2 통합 테스트

- 백테스트 파이프라인: Phase 1 적용 전후 KR 시장 손절 빈도, 반복 매매 횟수, 승률 비교
- 상태기계 통합: Phase 2 피처 공급 후 4중 청산 로직 end-to-end 시나리오
- 블랙아웃 복합: Phase 3 세션 전환 + 블랙아웃 복구 시나리오

### 3.3 실환경 검증

- Paper trading은 실환경과 괴리가 커 검증 신뢰도 부족 → **소액 live 운용**으로 검증
- Phase별 투입 기준: 단위/통합 테스트 통과 → 소액 live (1~2일) → 모니터링 → 정상 확인 후 본운용

---

## 4. 의존성 그래프

```
Phase 1 (병렬 실행 가능)
  ACT-01 #318 ─┐
  ACT-02 #319  │  (모두 독립)
  ACT-03 #320  │
  ACT-04 #321 ─┘

Phase 2
  ACT-05 #322 ─┐
  ACT-06 #323  │  (대부분 독립)
  ACT-07 #324  │
  ACT-09 #326 ─┘
  ACT-08 #325 ←── ACT-01 #318 (ATR 인프라 공유)

Phase 3
  ACT-10 #327 ←── ACT-09 #326 (session_id 인프라)
  ACT-11 #328 ←── ACT-07 #324, ACT-10 #327
  ACT-12 #329     (독립)
  ACT-13 #330     (독립)
```

### Phase 간 관계

- Phase 1 → Phase 2: Phase 1 완료가 Phase 2의 전제 조건은 아니나, Phase 1로 출혈 차단 후 Phase 2 진행 권장
- Phase 2 → Phase 3: ACT-09(session_id)가 ACT-10(세션 재로딩)의 전제, ACT-07+ACT-10이 ACT-11의 전제

---

## 5. 롤백 계획

### Phase 1 롤백

- 각 ACT는 독립적이므로 개별 revert 가능
- 손절선(ACT-01): 기존 -2% 고정값으로 복원
- 쿨다운(ACT-02): 쿨다운 체크 제거
- 가격 필터(ACT-03): 필터 조건 제거
- syntax 검증(ACT-04): 검증 스킵, 기존 저장 로직 복원

### Phase 2 롤백

- PnL 수정(ACT-05): buy_qty 기준으로 복원 (단, 데이터 정합성 후퇴 감수)
- exchange_code(ACT-06): 매칭 키에서 제거
- 블랙아웃 DB(ACT-07): log_trade() 호출 제거
- 피처 공급(ACT-08): 0.0 공급으로 복원
- session_id(ACT-09): 파라미터 제거, 자동 추론 복원

### Phase 3 롤백

- 세션 재로딩(ACT-10): 시작 시 1회 로딩으로 복원
- 블랙아웃 재검증(ACT-11): 기존 stale 드롭만 유지
- 시간장벽(ACT-12): max_holding_bars로 복원
- CI(ACT-13): CI 워크플로우 제거

### 롤백 절차

1. 해당 ACT의 PR branch에서 `git revert` 수행
2. 기존 테스트 전체 통과 확인
3. 실환경 투입 전 소액 live 검증

---

## 6. 미진 사항 (2026-03-01 기준)

Phase 1~3 구현 완료 후에도 다음 항목이 운영상 미완료 상태이다.

### 6.1 운영 검증 필요

| 항목 | 설명 | 우선순위 |
|------|------|----------|
| FX PnL 운영 활성화 | `fx_pnl`/`strategy_pnl` 컬럼 존재하나 모든 운영 데이터 값이 0 | P1 |
| 세션 경계 E2E 통합 테스트 보강 | `test_main.py`에 단위 테스트 존재; 세션 경계 실시간 전환 E2E 미작성 | P2 |
| v2 상태기계 통합 end-to-end | 실거래 경로에서 HOLDING→BE_LOCK→ARMED→EXITED 전체 시나리오 테스트 미작성 | P2 |

### 6.2 아키텍처 수준 잔여 갭

| 항목 | 설명 | 배경 문서 |
|------|------|-----------|
| CI 자동 검증 고도화 (#330) | 기본 구현 완료(`validate_governance_assets.py` CI 연동); 규칙/강제수준 고도화 필요 | REQ-OPS-002, REQ-OPS-003 |
| pred_down_prob ML 모델 대체 | 현재 RSI 프록시 사용 — 추후 실제 GBDT/ML 모델로 대체 권장 | ROOT-5, ouroboros_plan_v2.txt §3.D |
| KR/US 파라미터 민감도 분석 | v2 계획의 be_arm_pct/arm_pct/atr_k 최적값 탐색 미수행 | ouroboros_plan_v2.txt §8 |

### 6.3 v3 실험 매트릭스 미착수

ouroboros_plan_v3.txt §9에 정의된 3개 실험이 아직 시작되지 않았다.

| 실험 ID | 시장 | 포커스 | 상태 |
|---------|------|--------|------|
| EXP-KR-01 | KR | NXT 야간 특화 (p_thresh 0.65) | ❌ 미착수 |
| EXP-US-01 | US | 21h 준연속 운용 (atr_k 2.5) | ❌ 미착수 |
| EXP-HYB-01 | Global | KR 낮 + US 밤 연계 레짐 자산배분 | ❌ 미착수 |

---

*끝.*
