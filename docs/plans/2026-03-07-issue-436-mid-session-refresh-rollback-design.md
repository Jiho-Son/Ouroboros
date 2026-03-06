# Design: Mid-Session Refresh Rollback Guard

**Date:** 2026-03-07
**Issue:** #436

## 배경

mid-session refresh는 기존 open playbook을 메모리에서 비우고 새 playbook을 다시 생성한다. 현재 구현은 생성 실패 시 pre-refresh playbook을 복원하도록 되어 있지만, 이 경로를 직접 고정하는 회귀 테스트는 없다.

## 목표

mid-session refresh 중 playbook 재생성이 실패해도, 기존 open playbook이 메모리 캐시에 복원된다는 것을 테스트로 보장한다.

## 범위

- 구현 로직 변경 없음
- `run_daily_session()` 또는 `trading_cycle()` 전반 리팩터링 없음
- 이미 존재하는 rollback 경로에 대한 테스트만 추가

## 접근

### 1. 최소 범위 통합 테스트

기존 메인 루프 테스트 패턴을 따라, 다음 조건만 만족하는 테스트를 추가한다.

- mid-session refresh가 발동하는 세션/시간 조건
- 기존 playbook이 메모리에 존재함
- refresh 후 `generate_playbook()`이 실패함
- 최종적으로 기존 playbook이 다시 사용 가능한 상태로 복원됨

### 2. 관찰 지점

테스트는 내부 지역 변수에 직접 접근하지 않고, 루프 종료 후 관찰 가능한 상태로 검증한다.

- `playbooks` 캐시에 남아 있는 playbook 객체 정체성
- 새 empty playbook으로 대체되지 않았는지
- 실패 알림 경로가 함께 호출되는지 여부

### 3. 왜 이 방식인가

- 이슈 핵심은 rollback 보장이지 구조 변경이 아니다.
- helper 추출 없이 현재 로직을 그대로 검증하므로 회귀 방지 가치가 가장 높다.
- 기존 `tests/test_main.py` 패턴과 맞아 mocking 범위를 최소화할 수 있다.

## 테스트 전략

- RED: mid-session refresh 후 generation 실패 시 fallback 복원이 보장된다는 테스트를 먼저 추가
- GREEN: 필요 시 최소한의 관찰 훅만 추가하거나, 기존 공개 동작만으로 테스트 성립
- 회귀: mid-session trigger 관련 기존 테스트와 함께 재실행

## 성공 기준

- 새 테스트가 실패 경로를 직접 검증한다.
- 기존 mid-session refresh 조건 테스트가 유지된다.
- production code 변경은 필요 최소로 제한되거나, 가능하면 0이다.
