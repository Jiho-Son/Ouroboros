# OOR-890 Runtime Monitor Pane Log Race Design

**Problem:** `scripts/run_overnight.sh` 는 runtime monitor PID를 확인한 직후 `runtime_verify_*.log` 를 한 번만 조회한다. monitor 프로세스가 살아 있어도 첫 log write가 조금 늦으면 tmux runtime monitor pane이 조용히 빠지고, 운영 로그에는 그 이유가 남지 않는다.

## Options

### Option A: tmux pane 생성 직전에 bounded retry로 log discovery를 안정화한다

- `scripts/run_overnight.sh` 에 `runtime_verify_*.log` 조회 helper를 추가한다.
- 짧은 timeout/poll 간격 동안 최신 runtime monitor log를 반복 조회한다.
- 발견되면 pane을 붙이고, 끝까지 못 찾으면 skip 이유를 `RUN_LOG` 에 남긴다.
- 장점: 현재 구조를 유지하면서 race 경계만 좁고 명확하게 방어할 수 있다.
- 단점: startup 경로에 아주 짧은 대기 시간이 추가된다.

### Option B: runtime monitor가 미리 빈 log 파일을 touch 하게 만든다

- `scripts/runtime_verify_monitor.sh` 가 시작 즉시 `OUT_LOG` 를 생성한 뒤 log loop를 돈다.
- 장점: `run_overnight.sh` 의 pane 로직은 거의 바꾸지 않아도 된다.
- 단점: pane/log coupling이 monitor 구현 세부에 숨어서, pane skip 이유 관측성 문제를 따로 풀지 못한다.

### Option C: tmux pane을 pid 기준 placeholder 명령으로 먼저 띄운 뒤 나중에 교체한다

- monitor pane을 먼저 만들고, log file이 나타나면 pane 명령을 재설정한다.
- 장점: pane 누락 자체는 사라진다.
- 단점: tmux 제어가 복잡해지고 현재 harness 범위를 넘는 상태 관리가 늘어난다.

## Recommendation

Option A를 채택한다.

- race는 `run_overnight.sh` 의 one-shot discovery 경계에서 발생하므로 그 경계를 직접 방어하는 편이 가장 국소적이다.
- skip 이유를 같은 helper에서 로그로 남기면 운영 관측성과 테스트 가능성이 함께 올라간다.
- 테스트는 fake `tee` 지연으로 "monitor pid는 alive지만 log file은 늦게 생김" 경계를 결정적으로 만들 수 있다.

## Design

- `scripts/run_overnight.sh` 에 최신 runtime monitor log 탐색 helper를 추가한다.
- helper는 기본 짧은 wait window 동안 `runtime_verify_*.log` 를 반복 조회한다.
- helper는 아래 두 경로를 분리해 `RUN_LOG` 에 남긴다.
  - log 발견: pane 추가, 필요하면 "wait 후 발견" info 기록
  - 미발견: pane skip + 원인(`timeout`, `monitor exited`, `log missing`) warning 기록
- 테스트는 `tests/test_runtime_overnight_scripts.py` 에서 fake `tee` 를 통해 runtime monitor log 생성만 지연시켜 경계를 직접 검증한다.

## Testing

- red test 1: delayed `tee` 로 log 생성이 늦어져도 retry window 안이면 tmux pane이 추가된다.
- red test 2: wait window보다 더 늦게 log가 생기면 pane은 생략되고 skip 이유가 `run_*.log` 에 남는다.
- green 이후 targeted shell regression과 `ruff` 로 회귀를 확인한다.
