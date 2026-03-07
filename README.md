# The Ouroboros — 자가 진화형 AI 투자 시스템

KIS(한국투자증권) API로 매매하고, Google Gemini로 판단하며, 자체 전략 코드를 TDD 기반으로 진화시키는 자율 주식 트레이딩 에이전트.

## 아키텍처

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  KIS Broker │◄───►│    Main     │◄───►│ Gemini Brain│
│  (매매 실행) │     │ (거래 루프)  │     │  (의사결정)  │
└─────────────┘     └──────┬──────┘     └─────────────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
       ┌──────┴──────┐ ┌──┴───┐ ┌──────┴──────┐
       │Risk Manager │ │  DB  │ │  Telegram   │
       │  (안전장치)  │ │      │ │ (알림+명령)  │
       └──────┬──────┘ └──────┘ └─────────────┘
              │
     ┌────────┼────────┐
     │        │        │
┌────┴────┐┌──┴──┐┌────┴─────┐
│Strategy ││Ctx  ││Evolution │
│(플레이북)││(메모리)││ (진화)   │
└─────────┘└─────┘└──────────┘
```

**v2 핵심**: "Plan Once, Execute Locally" — 장 시작 전 AI가 시나리오 플레이북을 1회 생성하고, 거래 시간에는 로컬 시나리오 매칭만 수행하여 API 비용과 지연 시간을 대폭 절감.

## 핵심 모듈

| 모듈 | 위치 | 설명 |
|------|------|------|
| 설정 | `src/config.py` | Pydantic 기반 환경변수 로딩 및 타입 검증 (35+ 변수) |
| 브로커 | `src/broker/` | KIS API 비동기 래퍼 (국내 + 해외 9개 시장) |
| 두뇌 | `src/brain/` | Gemini 프롬프트 구성, JSON 파싱, 토큰 최적화 |
| 방패 | `src/core/risk_manager.py` | 서킷 브레이커 + 팻 핑거 체크 (READ-ONLY) |
| 전략 | `src/strategy/` | Pre-Market Planner, Scenario Engine, Playbook Store |
| 컨텍스트 | `src/context/` | L1-L7 계층형 메모리 시스템 |
| 분석 | `src/analysis/` | RSI, ATR, Smart Volatility Scanner |
| 알림 | `src/notifications/` | 텔레그램 양방향 (알림 + 9개 명령어) |
| 대시보드 | `src/dashboard/` | FastAPI 읽기 전용 모니터링 (10개 API) |
| 진화 | `src/evolution/` | 전략 진화 + Daily Review + Scorecard |
| 의사결정 로그 | `src/logging/` | 전체 거래 결정 감사 추적 |
| 데이터 | `src/data/` | 뉴스, 시장 데이터, 경제 캘린더 연동 |
| 백업 | `src/backup/` | 자동 백업, S3 클라우드, 무결성 검증 |
| DB | `src/db.py` | SQLite 거래 로그 (5개 테이블) |

## 안전장치

| 규칙 | 내용 |
|------|------|
| 서킷 브레이커 | 일일 손실률 -3.0% 초과 시 전체 매매 중단 (`SystemExit`) |
| 팻 핑거 방지 | 주문 금액이 보유 현금의 30% 초과 시 주문 거부 |
| 신뢰도 임계값 | Gemini 신뢰도 80 미만이면 강제 HOLD |
| 레이트 리미터 | Leaky Bucket 알고리즘으로 API 호출 제한 |
| 토큰 자동 갱신 | 만료 1분 전 자동으로 Access Token 재발급 |
| 손절 모니터링 | 플레이북 시나리오 기반 실시간 포지션 보호 |

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

### 4. 실행

```bash
# 런타임 paper mode 실행은 금지됨 (#426)
python -m src.main --mode=live

# 대시보드 활성화
python -m src.main --mode=live --dashboard
```

### 5. Docker 실행

```bash
docker compose up -d ouroboros
```

## 지원 시장

| 국가 | 거래소 | 코드 |
|------|--------|------|
| 🇰🇷 한국 | KRX | KR |
| 🇺🇸 미국 | NASDAQ, NYSE, AMEX | US_NASDAQ, US_NYSE, US_AMEX |
| 🇯🇵 일본 | TSE | JP |
| 🇭🇰 홍콩 | SEHK | HK |
| 🇨🇳 중국 | 상하이, 선전 | CN_SHA, CN_SZA |
| 🇻🇳 베트남 | 하노이, 호치민 | VN_HNX, VN_HSX |

`ENABLED_MARKETS` 환경변수로 활성 시장 선택 (기본: `KR,US`).

## 텔레그램 (선택사항)

거래 실행, 서킷 브레이커 발동, 시스템 상태 등을 텔레그램으로 실시간 알림 받을 수 있습니다.

### 빠른 설정

1. **봇 생성**: 텔레그램에서 [@BotFather](https://t.me/BotFather) 메시지 → `/newbot` 명령
2. **채팅 ID 확인**: [@userinfobot](https://t.me/userinfobot) 메시지 → `/start` 명령
3. **환경변수 설정**: `.env` 파일에 추가
   ```bash
   TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
   TELEGRAM_CHAT_ID=123456789
   TELEGRAM_ENABLED=true
   ```
4. **테스트**: 봇과 대화 시작 (`/start` 전송) 후 에이전트 실행

**상세 문서**: [src/notifications/README.md](src/notifications/README.md)

### 알림 종류

- 🟢 거래 체결 알림 (BUY/SELL + 신뢰도)
- 🚨 서킷 브레이커 발동 (자동 거래 중단)
- ⚠️ 팻 핑거 차단 (과도한 주문 차단)
- ℹ️ 장 시작/종료 알림
- 📝 시스템 시작/종료 상태

### 양방향 명령어

`TELEGRAM_COMMANDS_ENABLED=true` (기본값) 설정 시 9개 대화형 명령어 지원:

| 명령어 | 설명 |
|--------|------|
| `/help` | 사용 가능한 명령어 목록 |
| `/status` | 거래 상태 (모드, 시장, P&L) |
| `/positions` | 계좌 요약 (잔고, 현금, P&L) |
| `/report` | 일일 요약 (거래 수, P&L, 승률) |
| `/scenarios` | 오늘의 플레이북 시나리오 |
| `/review` | 최근 스코어카드 (L6_DAILY) |
| `/dashboard` | 대시보드 URL 표시 |
| `/stop` | 거래 일시 정지 |
| `/resume` | 거래 재개 |

**안전장치**: 알림 실패해도 거래는 계속 진행됩니다.

## 테스트

998개 테스트가 41개 파일에 걸쳐 구현되어 있습니다. 최소 커버리지 80%.

```
tests/test_main.py               — 거래 루프 통합
tests/test_scenario_engine.py    — 시나리오 매칭
tests/test_pre_market_planner.py — 플레이북 생성
tests/test_overseas_broker.py    — 해외 브로커
tests/test_telegram_commands.py  — 텔레그램 명령어
tests/test_telegram.py           — 텔레그램 알림
... 외 35개 파일  ※ 파일별 수치는 CI 기준으로 변동 가능
```

**상세**: [docs/testing.md](docs/testing.md)

## 기술 스택

- **언어**: Python 3.11+ (asyncio 기반)
- **브로커**: KIS Open API (REST, 국내+해외)
- **AI**: Google Gemini Pro
- **DB**: SQLite (5개 테이블: trades, contexts, decision_logs, playbooks, context_metadata)
- **대시보드**: FastAPI + uvicorn
- **검증**: pytest + coverage (998 tests)
- **CI/CD**: Gitea CI (`.gitea/workflows/ci.yml`)
- **배포**: Docker + Docker Compose

## 프로젝트 구조

```
The-Ouroboros/
├── docs/
│   ├── architecture.md          # 시스템 아키텍처
│   ├── testing.md               # 테스트 가이드
│   ├── commands.md              # 명령어 레퍼런스
│   ├── context-tree.md          # L1-L7 메모리 시스템
│   ├── workflow.md              # Git 워크플로우
│   ├── agents.md                # 에이전트 정책
│   ├── skills.md                # 도구 목록
│   ├── disaster_recovery.md     # 백업/복구
│   └── requirements-log.md      # 요구사항 기록
├── src/
│   ├── analysis/                # 기술적 분석 (RSI, ATR, Smart Scanner)
│   ├── backup/                  # 백업 (스케줄러, S3, 무결성 검증)
│   ├── brain/                   # Gemini 의사결정 (프롬프트 최적화, 컨텍스트 선택)
│   ├── broker/                  # KIS API (국내 + 해외)
│   ├── context/                 # L1-L7 계층 메모리
│   ├── core/                    # 리스크 관리 (READ-ONLY)
│   ├── dashboard/               # FastAPI 모니터링 대시보드
│   ├── data/                    # 외부 데이터 연동
│   ├── evolution/               # 전략 진화 + Daily Review
│   ├── logging/                 # 의사결정 감사 추적
│   ├── markets/                 # 시장 스케줄 + 타임존
│   ├── notifications/           # 텔레그램 알림 + 명령어
│   ├── strategy/                # 플레이북 (Planner, Scenario Engine)
│   ├── config.py                # Pydantic 설정
│   ├── db.py                    # SQLite 데이터베이스
│   └── main.py                  # 비동기 거래 루프
├── tests/                       # 998개 테스트 (41개 파일)
├── Dockerfile                   # 멀티스테이지 빌드
├── docker-compose.yml           # 서비스 오케스트레이션
└── pyproject.toml               # 의존성 및 도구 설정
```

## 문서

- **[문서 허브](docs/README.md)** — 전체 문서 라우팅, 우선순위, 읽기 순서
- **[아키텍처](docs/architecture.md)** — 시스템 설계, 컴포넌트, 데이터 흐름
- **[테스트](docs/testing.md)** — 테스트 구조, 커버리지, 작성 가이드
- **[명령어](docs/commands.md)** — CLI, Dashboard, Telegram 명령어
- **[컨텍스트 트리](docs/context-tree.md)** — L1-L7 계층 메모리
- **[워크플로우](docs/workflow.md)** — Git 워크플로우 정책
- **[에이전트 정책](docs/agents.md)** — 안전 제약, 금지 행위
- **[백업/복구](docs/disaster_recovery.md)** — 재해 복구 절차
- **[요구사항](docs/requirements-log.md)** — 사용자 요구사항 추적

## 라이선스

이 프로젝트의 라이선스는 [LICENSE](LICENSE) 파일을 참조하세요.
