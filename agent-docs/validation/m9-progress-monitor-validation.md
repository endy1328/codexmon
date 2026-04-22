# 마일스톤 M9 검증

날짜: 2026-04-22
상태: 통과

## 검증 대상

- `monitor snapshot`이 durable runtime state를 live JSON으로 출력한다
- `monitor serve`가 HTML과 `/api/progress`를 함께 서빙한다
- `progress-monitor.html`이 live API를 우선 읽고 내장 snapshot을 fallback으로 유지한다
- embedded snapshot과 `progress.json`이 동일한 기준선을 가리킨다

## 실행한 검증

- `python3 -m unittest tests.test_progress_monitor -v`
- `python3 -m unittest tests.test_cli -v`
- `python3 -m unittest discover -s tests -v`
- `PYTHONPATH=src python3 -m compileall src tests`
- `PYTHONPATH=src python3 -m codexmon doctor`
- `PYTHONPATH=src python3 -m codexmon monitor snapshot --json`
- `python3 -m json.tool agent-docs/status/progress.json`
- `progress-monitor.html` embedded JSON sync 확인

## 검증 결과

- `tests/test_progress_monitor.py`
  - live snapshot builder가 active run, daemon heartbeat, watch item 정리를 반영하는지 검증했다
  - lightweight HTTP monitor server가 HTML과 live JSON route를 함께 제공하는지 검증했다
- `tests/test_cli.py`
  - `monitor snapshot` CLI가 live JSON을 출력하는지 검증했다
  - `monitor serve` CLI가 server info를 노출하고 종료 시 server close를 보장하는지 검증했다
- `codexmon monitor snapshot`
  - live JSON 출력과 JSON 형식 유효성을 확인했다
- `progress.json` / embedded snapshot
  - fallback snapshot과 외부 JSON 파일이 동일한 M9 완료 기준선을 가리키는지 확인했다
- 전체 회귀 `58`개 테스트가 모두 통과했다

## 메모

- monitor HTML은 `codexmon monitor serve`로 열었을 때 live DB API를 우선 사용한다
- file 직접 열기나 live API 실패 시에는 embedded snapshot으로 fallback 한다
- `/progress.json` route도 live JSON을 제공해 기존 경로와의 호환을 유지한다
