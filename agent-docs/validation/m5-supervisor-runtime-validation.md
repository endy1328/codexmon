# 마일스톤 M5 검증

날짜: 2026-04-22

검증 범위: synchronous supervisor runtime baseline, `start --execute`, `execute`,
preflight gate, approval gate orchestration, terminal state lock release

## 점검 항목

- queued run이 preflight를 거쳐 workspace를 할당받는다
- runtime이 failure policy, approval policy, PR handoff를 단일 흐름으로 묶는다
- 성공 경로가 `completed`와 PR reference로 끝난다
- risky success diff가 `awaiting_human`으로 이동한다
- terminal state에서 repository lock이 deterministic하게 해제된다
- CLI `start --execute`, `execute`가 runtime 경로를 호출한다

## 실행 검증

- `python3 -m unittest tests.test_orchestrator -v`
- `python3 -m unittest tests.test_cli -v`
- `python3 -m unittest discover -s tests -v`
- `PYTHONPATH=src python3 -m compileall src tests`
- `PYTHONPATH=src python3 -m codexmon doctor`

## 검증 결과

통과

## 메모

- `tests/test_orchestrator.py`가 성공 경로와 approval gate 경로를 직접 검증한다
- `tests/test_cli.py`가 `execute`와 `start --execute` 위임 경로를 검증한다
- runtime은 single-process synchronous baseline이며, background daemon/heartbeat는
  다음 단계 범위로 남긴다
