# Requirements Log

프로젝트 진화를 위한 사용자 요구사항 기록.

이 문서는 시간순으로 사용자와의 대화에서 나온 요구사항과 피드백을 기록합니다.
새로운 요구사항이 있으면 날짜와 함께 추가하세요.

---

## 2026-02-05

### API 효율화
- Gemini API는 귀중한 자원. 종목별 개별 호출 대신 배치 호출 필요
- Free tier 한도(20 calls/day) 고려하여 일일 몇 차례 거래 모드로 전환
- 배치 API 호출로 여러 종목을 한 번에 분석

### 거래 모드
- **Daily Mode**: 하루 4회 거래 세션 (6시간 간격) - Free tier 호환
- **Realtime Mode**: 60초 간격 실시간 거래 - 유료 구독 필요
- `TRADE_MODE` 환경변수로 모드 선택

### 진화 시스템
- 사용자 대화 내용을 문서로 기록하여 향후에도 의도 반영
- 프롬프트 품질 검증은 별도 이슈로 다룰 예정

### 문서화
- 시스템 구조, 기능별 설명 등 코드 문서화 항상 신경쓸 것
- 새로운 기능 추가 시 관련 문서 업데이트 필수

---

## 2026-02-06

### Smart Volatility Scanner (Python-First, AI-Last 파이프라인)

**배경:**
- 정적 종목 리스트를 순회하는 방식은 비효율적
- KIS API 거래량 순위를 통해 시장 주도주를 자동 탐지해야 함
- Gemini API 호출 전에 Python 기반 기술적 분석으로 필터링 필요

**요구사항:**
1. KIS API 거래량 순위 API 통합 (`fetch_market_rankings`)
2. 일별 가격 히스토리 API 추가 (`get_daily_prices`)
3. RSI(14) 계산 기능 구현 (Wilder's smoothing method)
4. 필터 조건:
   - 거래량 > 전일 대비 200% (VOL_MULTIPLIER)
   - RSI < 30 (과매도) OR RSI > 70 (모멘텀)
5. 상위 1-3개 적격 종목만 Gemini에 전달
6. 종목 선정 배경(RSI, volume_ratio, signal, score) 데이터베이스 기록

**구현 결과:**
- `src/analysis/smart_scanner.py`: SmartVolatilityScanner 클래스
- `src/analysis/volatility.py`: calculate_rsi() 메서드 추가
- `src/broker/kis_api.py`: 2개 신규 API 메서드
- `src/db.py`: selection_context 컬럼 추가
- 설정 가능한 임계값: RSI_OVERSOLD_THRESHOLD, RSI_MOMENTUM_THRESHOLD, VOL_MULTIPLIER, SCANNER_TOP_N

**효과:**
- Gemini API 호출 20-30개 → 1-3개로 감소
- Python 기반 빠른 필터링 → 비용 절감
- 선정 기준 추적 → Evolution 시스템 최적화 가능
- API 장애 시 정적 watchlist로 자동 전환

**참고:** Realtime 모드 전용. Daily 모드는 배치 효율성을 위해 정적 watchlist 사용.

**이슈/PR:** #76, #77

---

## 2026-02-10

### 코드 리뷰 시 플랜-구현 일치 검증 규칙

**배경:**
- 코드 리뷰 시 플랜(EnterPlanMode에서 승인된 계획)과 실제 구현이 일치하는지 확인하는 절차가 없었음
- 플랜과 다른 구현이 리뷰 없이 통과될 위험

**요구사항:**
1. 모든 PR 리뷰에서 플랜-구현 일치 여부를 필수 체크
2. 플랜에 없는 변경은 정당한 사유 필요
3. 플랜 항목이 누락되면 PR 설명에 사유 기록
4. 스코프가 플랜과 일치하는지 확인

**구현 결과:**
- `docs/workflow.md`에 Code Review Checklist 섹션 추가
  - Plan Consistency (필수), Safety & Constraints, Quality, Workflow 4개 카테고리

**이슈/PR:** #114

---

## 2026-02-16

### 문서 v2 동기화 (전체 문서 현행화)

**배경:**
- v2 기능 구현 완료 후 문서가 실제 코드 상태와 크게 괴리
- 문서에는 54 tests / 4 files로 기록되었으나 실제로는 551 tests / 25 files
- v2 핵심 기능(Playbook, Scenario Engine, Dashboard, Telegram Commands, Daily Review, Context System, Backup) 문서화 누락

**요구사항:**
1. `docs/testing.md` — 551 tests / 25 files 반영, 전체 테스트 파일 설명
2. `docs/architecture.md` — v2 컴포넌트(Strategy, Context, Dashboard, Decision Logger 등) 추가, Playbook Mode 데이터 플로우, DB 스키마 5개 테이블, v2 환경변수
3. `docs/commands.md` — Dashboard 실행 명령어, Telegram 명령어 9종 레퍼런스
4. `CLAUDE.md` — Project Structure 트리 확장, 테스트 수 업데이트, `--dashboard` 플래그
5. `docs/skills.md` — DB 파일명 `trades.db`로 통일, Dashboard 명령어 추가
6. 기존에 유효한 트러블슈팅, 코드 예제 등은 유지

**구현 결과:**
- 6개 문서 파일 업데이트
- 이전 시도(2개 커밋)는 기존 내용을 과도하게 삭제하여 폐기, main 기준으로 재작업

**이슈/PR:** #131, PR #134

### 해외 스캐너 개선: 랭킹 연동 + 변동성 우선 선별

**배경:**
- `run_overnight` 실운영에서 미국장 동안 거래가 0건 지속
- 원인: 해외 시장에서도 국내 랭킹/일봉 API 경로를 사용하던 구조적 불일치

**요구사항:**
1. 해외 시장도 랭킹 API 기반 유니버스 탐색 지원
2. 단순 상승률/거래대금 상위가 아니라, **변동성이 큰 종목**을 우선 선별
3. 고정 티커 fallback 금지

**구현 결과:**
- `src/broker/overseas.py`
  - `fetch_overseas_rankings()` 추가 (fluctuation / volume)
  - 해외 랭킹 API 경로/TR_ID를 설정값으로 오버라이드 가능하게 구현
- `src/analysis/smart_scanner.py`
  - market-aware 스캔(국내/해외 분리)
  - 해외: 랭킹 API 유니버스 + 변동성 우선 점수(일변동률 vs 장중 고저폭)
  - 거래대금/거래량 랭킹은 유동성 보정 점수로 활용
  - 랭킹 실패 시에는 동적 유니버스(active/recent/holdings)만 사용
- `src/config.py`
  - `OVERSEAS_RANKING_*` 설정 추가

**효과:**
- 해외 시장에서 스캐너 후보 0개로 정지되는 상황 완화
- 종목 선정 기준이 단순 상승률 중심에서 변동성 중심으로 개선
- 고정 티커 없이도 시장 주도 변동 종목 탐지 가능

### 국내 스캐너/주문수량 정렬: 변동성 우선 + 리스크 타기팅

**배경:**
- 해외만 변동성 우선으로 동작하고, 국내는 RSI/거래량 필터 중심으로 동작해 시장 간 전략 일관성이 낮았음
- 매수 수량이 고정 1주라서 변동성 구간별 익스포저 관리가 어려웠음

**요구사항:**
1. 국내 스캐너도 변동성 우선 선별로 해외와 통일
2. 고변동 종목일수록 포지션 크기를 줄이는 수량 산식 적용

**구현 결과:**
- `src/analysis/smart_scanner.py`
  - 국내: `fluctuation ranking + volume ranking bonus` 기반 점수화로 전환
  - 점수는 `max(abs(change_rate), intraday_range_pct)` 중심으로 계산
  - 국내 랭킹 응답 스키마 키(`price`, `change_rate`, `volume`) 파싱 보강
- `src/main.py`
  - `_determine_order_quantity()` 추가
  - BUY 시 변동성 점수 기반 동적 수량 산정 적용
  - `trading_cycle`, `run_daily_session` 경로 모두 동일 수량 로직 사용
- `src/config.py`
  - `POSITION_SIZING_*` 설정 추가

**효과:**
- 국내/해외 스캐너 기준이 변동성 중심으로 일관화
- 고변동 구간에서 자동 익스포저 축소, 저변동 구간에서 과소진입 완화

## 2026-02-18

### KIS 해외 랭킹 API 404 에러 수정

**배경:**
- KIS 해외주식 랭킹 API(`fetch_overseas_rankings`)가 모든 거래소에서 HTTP 404를 반환
- Smart Scanner가 해외 시장 후보 종목을 찾지 못해 거래가 전혀 실행되지 않음

**근본 원인:**
- TR_ID, API 경로, 거래소 코드가 모두 KIS 공식 문서와 불일치

**구현 결과:**
- `src/config.py`: TR_ID/Path 기본값을 KIS 공식 스펙으로 수정
- `src/broker/overseas.py`: 랭킹 API 전용 거래소 코드 매핑 추가 (NASD→NAS, NYSE→NYS, AMEX→AMS), 올바른 API 파라미터 사용
- `tests/test_overseas_broker.py`: 19개 단위 테스트 추가

**효과:**
- 해외 시장 랭킹 스캔이 정상 동작하여 Smart Scanner가 후보 종목 탐지 가능

### Gemini prompt_override 미적용 버그 수정

**배경:**
- `run_overnight` 실행 시 모든 시장에서 Playbook 생성 실패 (`JSONDecodeError`)
- defensive playbook으로 폴백되어 모든 종목이 HOLD 처리

**근본 원인:**
- `pre_market_planner.py`가 `market_data["prompt_override"]`에 Playbook 전용 프롬프트를 넣어 `gemini.decide()` 호출
- `gemini_client.py`의 `decide()` 메서드가 `prompt_override` 키를 전혀 확인하지 않고 항상 일반 트레이드 결정 프롬프트 생성
- Gemini가 Playbook JSON 대신 일반 트레이드 결정을 반환하여 파싱 실패

**구현 결과:**
- `src/brain/gemini_client.py`: `decide()` 메서드에서 `prompt_override` 우선 사용 로직 추가
- `tests/test_brain.py`: 3개 테스트 추가 (override 전달, optimization 우회, 미지정 시 기존 동작 유지)

**이슈/PR:** #143
