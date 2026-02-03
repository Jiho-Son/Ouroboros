# The Ouroboros — 자가 진화형 AI 투자 시스템

KIS(한국투자증권) API로 매매하고, Google Gemini로 판단하며, 자체 전략 코드를 TDD 기반으로 진화시키는 자율 주식 트레이딩 에이전트.

## 아키텍처

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  KIS Broker │◄───►│    Main     │◄───►│ Gemini Brain│
│  (매매 실행) │     │ (거래 루프)  │     │  (의사결정)  │
└─────────────┘     └──────┬──────┘     └─────────────┘
                           │
                    ┌──────┴──────┐
                    │Risk Manager │
                    │  (안전장치)  │
                    └──────┬──────┘
                           │
                    ┌──────┴──────┐
                    │  Evolution  │
                    │ (전략 진화)  │
                    └─────────────┘
```

## 핵심 모듈

| 모듈 | 파일 | 설명 |
|------|------|------|
| 설정 | `src/config.py` | Pydantic 기반 환경변수 로딩 및 타입 검증 |
| 브로커 | `src/broker/kis_api.py` | KIS API 비동기 래퍼 (토큰 갱신, 레이트 리미터, 해시키) |
| 두뇌 | `src/brain/gemini_client.py` | Gemini 프롬프트 구성 및 JSON 응답 파싱 |
| 방패 | `src/core/risk_manager.py` | 서킷 브레이커 + 팻 핑거 체크 |
| 진화 | `src/evolution/optimizer.py` | 실패 패턴 분석 → 새 전략 생성 → 테스트 → PR |
| DB | `src/db.py` | SQLite 거래 로그 기록 |

## 안전장치

| 규칙 | 내용 |
|------|------|
| 서킷 브레이커 | 일일 손실률 -3.0% 초과 시 전체 매매 중단 (`SystemExit`) |
| 팻 핑거 방지 | 주문 금액이 보유 현금의 30% 초과 시 주문 거부 |
| 신뢰도 임계값 | Gemini 신뢰도 80 미만이면 강제 HOLD |
| 레이트 리미터 | Leaky Bucket 알고리즘으로 API 호출 제한 |
| 토큰 자동 갱신 | 만료 1분 전 자동으로 Access Token 재발급 |

## 빠른 시작

### 1. 환경 설정

```bash
cp .env.example .env
# .env 파일에 KIS API 키와 Gemini API 키 입력
```

### 2. 의존성 설치

```bash
pip install ".[dev]"
```

### 3. 테스트 실행

```bash
pytest -v --cov=src --cov-report=term-missing
```

### 4. 실행 (모의투자)

```bash
python -m src.main --mode=paper
```

### 5. Docker 실행

```bash
docker compose up -d ouroboros
```

## 테스트

35개 테스트가 TDD 방식으로 구현 전에 먼저 작성되었습니다.

```
tests/test_risk.py   — 서킷 브레이커, 팻 핑거, 통합 검증 (11개)
tests/test_broker.py — 토큰 관리, 타임아웃, HTTP 에러, 해시키 (6개)
tests/test_brain.py  — JSON 파싱, 신뢰도 임계값, 비정상 응답 처리 (15개)
```

## 기술 스택

- **언어**: Python 3.11+ (asyncio 기반)
- **브로커**: KIS Open API (REST)
- **AI**: Google Gemini Pro
- **DB**: SQLite
- **검증**: pytest + coverage
- **CI/CD**: GitHub Actions
- **배포**: Docker + Docker Compose

## 프로젝트 구조

```
The-Ouroboros/
├── .github/workflows/ci.yml     # CI 파이프라인
├── docs/
│   ├── agents.md                # AI 에이전트 페르소나 정의
│   └── skills.md                # 사용 가능한 도구 목록
├── src/
│   ├── config.py                # Pydantic 설정
│   ├── logging_config.py        # JSON 구조화 로깅
│   ├── db.py                    # SQLite 거래 기록
│   ├── main.py                  # 비동기 거래 루프
│   ├── broker/kis_api.py        # KIS API 클라이언트
│   ├── brain/gemini_client.py   # Gemini 의사결정 엔진
│   ├── core/risk_manager.py     # 리스크 관리
│   ├── evolution/optimizer.py   # 전략 진화 엔진
│   └── strategies/base.py       # 전략 베이스 클래스
├── tests/                       # TDD 테스트 스위트
├── Dockerfile                   # 멀티스테이지 빌드
├── docker-compose.yml           # 서비스 오케스트레이션
└── pyproject.toml               # 의존성 및 도구 설정
```

## 라이선스

이 프로젝트의 라이선스는 [LICENSE](LICENSE) 파일을 참조하세요.
