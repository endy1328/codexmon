# 단계 C 인수 검증

날짜: 2026-04-21

검증 범위: 첫 구현 슬라이스 `M4` 종료 판정, `docs/ACCEPTANCE_CHECKLIST.md`
전 항목, outcome별 종단 간 acceptance validation suite

## 검증 기준

- 단일 acceptance validation suite가 성공 경로, 실패 제어 경로, 사람 개입 경로,
  bounded halt 경로를 모두 다룬다
- 각 시나리오는 persisted audit data와 CLI/Telegram/PR handoff 경로를 실제로 지난다
- 체크리스트 1~8이 테스트와 persisted event evidence로 다시 연결된다

## 실행 검증

- `python3 -m unittest tests.test_acceptance_validation -v`
- `python3 -m unittest discover -s tests -v`
- `PYTHONPATH=src python3 -m compileall src tests`
- `PYTHONPATH=src python3 -m codexmon doctor`
- `python3 -m json.tool agent-docs/status/progress.json`

## 체크리스트 대응

### 1. 작업 시작

- `tests/test_acceptance_validation.py`
  - `test_success_path_covers_start_transition_telegram_and_pr_handoff`
- 검증 내용
  - `start` CLI가 persisted task/run record를 만들고 `run_id`를 반환한다
  - `run.created` event가 `queued`를 기록한다
  - 이후 `preflight` 진입과 상태 요약 조회가 가능하다

### 2. Worktree 격리

- `tests/test_acceptance_validation.py`
  - `test_success_path_covers_start_transition_telegram_and_pr_handoff`
- 보강 검증
  - `tests/test_workspace.py`
- 검증 내용
  - run별 worktree와 branch가 할당된다
  - repository lock이 저장되고 충돌 run은 거부된다

### 3. Run 상태 전이

- `tests/test_acceptance_validation.py`
  - `test_success_path_covers_start_transition_telegram_and_pr_handoff`
  - `test_approval_required_change_moves_to_awaiting_human_and_can_resume`
  - `test_bounded_halt_stop_interrupts_runner_and_releases_lock`
- 보강 검증
  - `tests/test_ledger.py`
- 검증 내용
  - 성공 경로는 `queued -> preflight -> workspace_allocated -> running -> pr_handoff -> completed`
    전이를 지난다
  - 실패/개입 경로도 정본 상태 머신이 허용한 전이만 사용한다
  - invalid transition은 `state.transition.rejected` event로 남고 예외가 발생한다

### 4. Stall 및 Loop 감지

- `tests/test_acceptance_validation.py`
  - `test_failure_signal_path_covers_timeout_fingerprint_and_retry_budget`
- 보강 검증
  - `tests/test_failure_policy.py`
- 검증 내용
  - timeout/fingerprint/retry budget이 failure signal path로 기록된다
  - automatic retry는 최대 한 번만 수행된다

### 5. Telegram 알림

- `tests/test_acceptance_validation.py`
  - `test_success_path_covers_start_transition_telegram_and_pr_handoff`
  - `test_approval_required_change_moves_to_awaiting_human_and_can_resume`
  - `test_bounded_halt_stop_interrupts_runner_and_releases_lock`
- 보강 검증
  - `tests/test_telegram_notifier.py`
- 검증 내용
  - 시작, approval 대기, 완료, stop 경로가 Telegram notifier를 지난다
  - 원격 `status`, `approve`, `stop`, `retry`가 persisted operator event를 남긴다

### 6. 사람 개입 경로

- `tests/test_acceptance_validation.py`
  - `test_approval_required_change_moves_to_awaiting_human_and_can_resume`
- 보강 검증
  - `tests/test_approval_policy.py`
- 검증 내용
  - approval-required diff classification이 `awaiting_human` 전이와 pending approval을 만든다
  - 외부 결과는 `needs human decision`으로 노출된다
  - `approve`, `retry`는 허용된 재개 전이만 만든다

### 7. PR Handoff

- `tests/test_acceptance_validation.py`
  - `test_success_path_covers_start_transition_telegram_and_pr_handoff`
- 보강 검증
  - `tests/test_pr_handoff.py`
- 검증 내용
  - 성공 run이 persisted PR reference와 CI visibility를 남긴다
  - PR 본문에 task summary, changed files, local check result, residual risk note가 포함된다

### 8. Bounded Halt 동작

- `tests/test_acceptance_validation.py`
  - `test_bounded_halt_stop_interrupts_runner_and_releases_lock`
- 보강 검증
  - `tests/test_telegram_notifier.py`
- 검증 내용
  - operator stop이 active runner interrupt event를 남기고 process를 종료한다
  - 최종 결과는 `blocked with explicit reason`으로 수렴한다
  - halt transition 뒤에만 repository lock이 해제된다

## 검증 결과

통과

## 결론

- 첫 구현 슬라이스 `M4`는 acceptance validation suite 기준으로 종료 가능하다
- 남은 범위는 다음 슬라이스의 supervisor 핵심 런타임과 실제 task orchestration 구현이다
