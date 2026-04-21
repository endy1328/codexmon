# 작업 패킷 B4 검증

날짜: 2026-04-21

검증 범위: `Codex` adapter 실행 래퍼, runner launch/output/exit persistence,
synthetic `runner run` CLI 경로

## 점검 항목

- 할당된 worktree 안에서 runner command가 시작된다
- launch 요청, 실제 launch, stdout/stderr output, exit event가 event log에 저장된다
- 성공 exit은 `pr_handoff`, 실패 exit은 `analyzing_failure`로 반영된다
- launch failure는 별도 event로 저장되고 run state를 조용히 손상시키지 않는다
- CLI `runner run`이 allocated workspace를 대상으로 adapter 경로를 실행한다

## 실행 검증

- `python3 -m unittest discover -s tests -v`
- `PYTHONPATH=src python3 -m compileall src tests`
- `PYTHONPATH=src python3 -m codexmon doctor`

## 검증 결과

통과

## 메모

- `tests/test_codex_adapter.py`에서 fake runner script 기준으로 success exit,
  failure exit, launch failure, CLI `runner run` 경로를 검증한다
- 기본 adapter 명령은 `codex exec -C <worktree> --json --ephemeral --sandbox workspace-write`
  형태로 고정했다
- 현재 단계는 B4 완료 상태이며 다음 구현 작업은 failure signal path인
  작업 패킷 B5다
