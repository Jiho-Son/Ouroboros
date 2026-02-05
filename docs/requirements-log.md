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
