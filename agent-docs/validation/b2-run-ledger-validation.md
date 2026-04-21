# 작업 패킷 B2 검증

날짜: 2026-04-21

검증 범위: `SQLite` 기반 run ledger, 상태 전이 guard, synthetic CLI 조회 경로

## 점검 항목

- `SQLite` schema와 migration 방식이 코드로 고정됐다
- task, run, attempt, event, state transition 모델이 durable하게 저장된다
- failure fingerprint, approval, PR reference 저장 구조가 존재한다
- 허용된 state transition은 저장되고 invalid transition은 거부된다
- runner 없이도 synthetic run 생성과 status query가 가능하다
- CLI `start`, `status`, `doctor`가 현재 persistence 기준선과 연결된다

## 실행 검증

- `PYTHONPATH=src python3 -m codexmon doctor`
- `PYTHONPATH=src python3 -m codexmon start "B2 synthetic CLI run"`
- `python3 -m unittest discover -s tests -v`
- `PYTHONPATH=src python3 -m compileall src tests`

## 검증 결과

통과

## 메모

- `tests/test_ledger.py`에서 synthetic run 생성, 허용된 transition 경로, invalid transition 거부,
  auxiliary record persistence를 검증한다
- `tests/test_cli.py`에서 `start -> status` 경로와 `doctor` 출력 기준선을 검증한다
- 현재 단계는 B2 완료 상태이며 다음 구현 작업은 repository lock과 worktree allocator인
  작업 패킷 B3다
