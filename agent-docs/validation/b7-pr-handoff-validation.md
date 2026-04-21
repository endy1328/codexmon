# 작업 패킷 B7 검증

날짜: 2026-04-21

검증 범위: local check bundle, git branch push, GitHub PR 생성,
CI visibility persistence, synthetic `handoff` CLI 경로

## 점검 항목

- `pr_handoff` 상태의 run이 local check bundle을 실행한다
- worktree 변경이 commit되고 branch가 remote로 push된다
- GitHub PR 생성 결과가 persisted PR reference로 저장된다
- PR 본문에 task summary, changed files, local check result, residual risk가 포함된다
- PR 생성 이후 CI visibility snapshot이 저장된다
- CLI `handoff`가 success path를 실행하고 완료 상태를 반환한다

## 실행 검증

- `python3 -m unittest discover -s tests -v`
- `PYTHONPATH=src python3 -m compileall src tests`
- `PYTHONPATH=src python3 -m codexmon doctor`

## 검증 결과

통과

## 메모

- `tests/test_pr_handoff.py`에서 local bare remote 기준 branch push,
  fake GitHub client 기준 PR 생성/CI visibility, CLI `handoff` 경로를 검증한다
- local check bundle이 구성되지 않으면 run이 `halted`로 전환돼 `PR opened`를
  주장하지 못하도록 막는다
- 현재 단계는 B7 완료 상태이며 다음 작업은 단계 C 종단 간 인수 검증이다
