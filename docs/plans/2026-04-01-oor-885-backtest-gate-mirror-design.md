# OOR-885 Backtest Gate Local Mirror Design

**Problem:** GitHub Actions `Backtest Gate` 는 최근에도 성공 실행되고 있지만, canonical/main repo의 local `data/backtest-gate` 는 더 이상 갱신되지 않아 harness freshness 경보가 오탐을 내고 있다.

## Options

### Option A: runtime monitor가 latest scheduled artifact를 local mirror로 동기화

- `scripts/runtime_verify_monitor.sh` 가 main branch에서만 주기적으로 GitHub `Backtest Gate` schedule run을 조회한다.
- 최신 성공 run의 `backtest-gate-logs` artifact를 `data/backtest-gate` 로 내려받아 local freshness signal을 복구한다.
- 장점: canonical repo에서 이미 지속 실행되는 monitor 경로를 활용하므로 별도 host cron 없이 자동 복구된다.
- 단점: `gh` 조회를 주기적으로 수행해야 하므로 rate/오류 처리 방어가 필요하다.

### Option B: GitHub workflow가 repo에 heartbeat commit을 남긴다

- `backtest-gate.yml` 이 성공 시 repo 파일을 commit/push 해서 freshness를 남긴다.
- 장점: local downloader가 없어도 freshness signal이 남는다.
- 단점: CI가 repo state를 mutate 하게 되어 정책/권한/loop 리스크가 크다.

### Option C: 외부 harness가 GitHub run/artifact를 직접 조회한다

- harness monitor 기준을 local file mtime 대신 GitHub run 상태로 바꾼다.
- 장점: signal source가 실행 원천과 직접 일치한다.
- 단점: 이번 세션 범위를 벗어나며, 현재 저장소 안에서는 구현할 수 없다.

## Recommendation

Option A를 채택한다.

- 현재 문제는 실행 자체가 아니라 local mirror 부재다.
- 이 저장소 안에서 autonomous하게 복구 가능한 유일한 경로다.
- branch-scoped runtime 경계를 이미 가진 `scripts/runtime_verify_monitor.sh` 에 main 전용 sync를 붙이면 canonical repo에서만 local signal을 갱신할 수 있다.

## Design

- 새 helper `scripts/sync_backtest_gate_artifact.sh` 를 추가한다.
- helper는 `gh run list --workflow backtest-gate.yml --branch main --event schedule` 로 최신 성공 run id를 찾는다.
- helper는 `gh run download <run-id> -n backtest-gate-logs` 로 artifact를 임시 디렉터리에 내려받고, `data/backtest-gate` 로 복사한다.
- helper는 마지막으로 동기화한 run id를 marker 파일에 기록해 동일 run의 중복 다운로드를 피한다.
- `scripts/runtime_verify_monitor.sh` 는 main branch에서만 일정 주기마다 helper를 호출하고, 성공/skip/failure 결과를 자신의 runtime log에 기록한다.
- 문서에는 local `data/backtest-gate` freshness가 GitHub scheduled gate artifact mirror라는 점을 명시한다.

## Testing

- shell-level regression test로 monitor가 fake `gh` 를 통해 latest scheduled artifact를 local log dir에 미러링하는지 확인한다.
- helper의 marker 동작을 검증하는 focused shell test를 추가한다.
