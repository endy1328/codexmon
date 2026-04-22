# 마일스톤 M8 검증

날짜: 2026-04-22
상태: 통과

## 검증 대상

- `daemon serve`가 external stop reason을 stopped heartbeat에 남긴다
- service packaging 자산이 일관된 경로를 참조한다
- systemd baseline runbook과 자산이 저장소 기준선과 맞는다

## 실행한 검증

- `python3 -m unittest tests.test_daemon_runtime -v`
- `python3 -m unittest tests.test_packaging_assets -v`
- `python3 -m unittest discover -s tests -v`
- `PYTHONPATH=src python3 -m compileall src tests`
- `PYTHONPATH=src python3 -m codexmon doctor`
- `python3 -m json.tool agent-docs/status/progress.json`
- `progress-monitor.html` embedded JSON sync 확인

## 검증 결과

- `tests/test_daemon_runtime.py`
  - `daemon serve`가 service manager stop reason을 수용하고 `stopped` heartbeat에
    `signal:SIGTERM`을 남기는 경로를 검증했다
  - 기존 queued pickup, recovery, resume pickup 경로는 유지됐다
- `tests/test_packaging_assets.py`
  - systemd unit, env example, daemon wrapper script 존재 여부와 경로 참조를 검증했다
- `docs/SERVICE_RUNBOOK.md`
  - 설치, 운영, 종료 동작, 현재 한계를 문서화했다
- `codexmon doctor`
  - 서비스 패키징 이후 baseline 설정과 버전 `0.0.0.6` 노출을 확인했다
- `progress.json` / embedded snapshot
  - HTML fallback snapshot과 JSON 파일이 동일한 상태를 가리키는지 확인했다
- 전체 회귀 `54`개 테스트가 모두 통과했다

## 메모

- 현재 packaging baseline은 systemd 기준 reference implementation이다
- progress monitor live DB integration은 다음 마일스톤 범위로 남는다
