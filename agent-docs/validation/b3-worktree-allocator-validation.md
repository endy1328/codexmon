# 작업 패킷 B3 검증

날짜: 2026-04-21

검증 범위: repository-wide execution lock, deterministic branch/worktree allocator,
release/diagnose 경로

## 점검 항목

- repository-wide autonomous execution lock이 durable하게 기록된다
- `1 task = 1 run = 1 worktree = 1 branch` 규칙에 맞는 deterministic branch/worktree가 생성된다
- run이 `queued -> preflight -> workspace_allocated` 경로를 통해 workspace를 할당한다
- lock이 잡힌 동안 두 번째 run은 거부되고 explicit reason으로 halt된다
- release 경로가 lock을 반납하고 cleanup 시 worktree released 상태를 기록한다
- diagnose 경로가 active lock, persisted assignment, git worktree 등록 상태를 함께 보여준다

## 실행 검증

- `python3 -m unittest discover -s tests -v`
- `PYTHONPATH=src python3 -m compileall src tests`
- `PYTHONPATH=src python3 -m codexmon doctor`

## 검증 결과

통과

## 메모

- `tests/test_workspace.py`에서 temp git repository 기준으로 allocation 성공,
  lock conflict 거부, release/diagnose 경로를 검증한다
- CLI 기준선은 `workspace allocate`, `workspace release`, `workspace diagnose`
  명령으로 확장됐다
- 현재 단계는 B3 완료 상태이며 다음 구현 작업은 `Codex` adapter인
  작업 패킷 B4다
