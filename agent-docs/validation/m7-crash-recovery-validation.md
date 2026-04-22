# 마일스톤 M7 검증

날짜: 2026-04-22
상태: 통과

## 검증 대상

- daemon recovery scan이 orphaned `running`/`analyzing_failure` run을 찾는다
- orphaned runner process를 interrupt한 뒤 recovery policy가 `retry_pending` 또는
  `halted`로 정리된다
- recovery terminal state에서 repository lock이 해제된다

## 실행한 검증

- `python3 -m unittest tests.test_daemon_runtime -v`
- `python3 -m unittest tests.test_failure_policy -v`
- `python3 -m unittest tests.test_ledger -v`
- `python3 -m unittest discover -s tests -v`
- `PYTHONPATH=src python3 -m compileall src tests`

## 검증 결과

- `tests/test_daemon_runtime.py`
  - orphaned running process를 daemon이 interrupt하고 같은 tick에서 재시도해
    `completed`까지 가는 경로를 검증했다
  - 기존 queued pickup, approval 뒤 resume pickup, heartbeat 기록 경로도 유지됐다
- `tests/test_failure_policy.py`
  - recovery 진입점이 기존 retry budget과 fingerprint policy를 그대로 재사용하는지
    검증했다
- `tests/test_ledger.py`
  - recoverable run query가 `running`, `analyzing_failure`를 recovery 우선순위대로
    반환하는지 검증했다
- 전체 회귀 `52`개 테스트가 모두 통과했다

## 메모

- 현재 crash recovery는 orphaned runner를 interrupt 후 retry 또는 halt로 복구하는
  baseline이다
- 기존 orphaned process에 스트림을 재부착하는 live reattachment와 외부 process
  manager 연동은 다음 마일스톤 범위로 남는다
