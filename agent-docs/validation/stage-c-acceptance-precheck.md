# 단계 C 인수 예비 점검

날짜: 2026-04-21

목적: `docs/ACCEPTANCE_CHECKLIST.md`를 기준으로 현재 구현 기준선의
통과 가능 항목과 남은 갭을 먼저 정리한다.

## 현재 확보된 증거

- 작업 시작, run persistence, 상태 조회: `tests/test_cli.py`,
  `tests/test_ledger.py`
- worktree 격리와 repository lock: `tests/test_workspace.py`
- runner lifecycle, timeout, fingerprint, retry: `tests/test_codex_adapter.py`,
  `tests/test_failure_policy.py`
- Telegram notifier와 operator action: `tests/test_telegram_notifier.py`
- PR handoff, git push, PR reference, CI visibility: `tests/test_pr_handoff.py`

## 현재 바로 통과 가능한 항목

- 체크리스트 2. Worktree 격리
- 체크리스트 4. Stall 및 Loop 감지
- 체크리스트 5. Telegram 알림
- 체크리스트 7. PR Handoff

## 추가 작업이 필요한 항목

- 체크리스트 1. 작업 시작
  - `start`만으로 `preflight`까지 자동 진입하는 orchestrator 흐름은 아직 없다
- 체크리스트 3. Run 상태 전이
  - 단일 종단 간 실행에서 `queued -> completed`를 한 번에 재현하는 acceptance run이 아직 없다
- 체크리스트 6. 사람 개입 경로
  - approval-required change diff classification은 아직 구현되지 않았다
- 체크리스트 8. Bounded Halt 동작
  - operator stop은 halt와 lock release까지 기록하지만, active runner process interrupt와
    그 증거를 단일 acceptance 실행으로 묶지는 못했다

## 다음 구현/검증 포커스

- acceptance checklist를 단일 synthetic end-to-end validation으로 묶는다
- approval-required change classification과 evidence 경로를 추가한다
- active runner stop 시 interrupt/termination 증거를 남기는 경로를 보강한다
