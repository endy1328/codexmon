# 마일스톤 M6 검증

날짜: 2026-04-22

검증 범위: polling daemon worker baseline, runtime heartbeat persistence,
`daemon run-once`, `daemon serve`, `daemon status`, async operator resume pickup

## 점검 항목

- runnable run이 priority 순서로 조회된다
- daemon이 queued run을 pickup해서 `completed`까지 실행한다
- operator `approve` 이후 `retry_pending` run을 daemon이 다시 pickup한다
- heartbeat가 SQLite에 저장되고 CLI `daemon status`로 조회된다
- CLI `daemon run-once`가 daemon runtime에 위임된다

## 실행 검증

- `python3 -m unittest tests.test_daemon_runtime -v`
- `python3 -m unittest tests.test_cli -v`
- `python3 -m unittest tests.test_ledger -v`
- `python3 -m unittest discover -s tests -v`
- `PYTHONPATH=src python3 -m compileall src tests`
- `PYTHONPATH=src python3 -m codexmon doctor`

## 검증 결과

통과

## 메모

- `tests/test_daemon_runtime.py`가 queued pickup, approval 후 resume pickup,
  serve loop heartbeat를 검증한다
- `tests/test_ledger.py`가 runtime heartbeat persistence와 runnable run 조회를 검증한다
- `tests/test_cli.py`가 `daemon run-once`, `daemon status`, `doctor` 노출 필드를 검증한다
- 현재 daemon은 local polling worker baseline이며, running state crash recovery와
  외부 service packaging은 다음 단계 범위로 남는다
