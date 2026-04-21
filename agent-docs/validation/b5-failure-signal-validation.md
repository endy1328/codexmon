# 작업 패킷 B5 검증

날짜: 2026-04-21

검증 범위: timeout, failure fingerprint, automatic retry policy,
synthetic `runner supervise` CLI 경로

## 점검 항목

- idle timeout과 wall-clock timeout이 deterministic event로 저장된다
- 실패 시 normalized failure fingerprint가 기록된다
- 같은 fingerprint가 반복되면 run이 halt된다
- automatic retry budget이 최대 한 번만 허용된다
- CLI `runner supervise`가 adapter 실행 뒤 policy decision까지 반영한다

## 실행 검증

- `python3 -m unittest discover -s tests -v`
- `PYTHONPATH=src python3 -m compileall src tests`
- `PYTHONPATH=src python3 -m codexmon doctor`

## 검증 결과

통과

## 메모

- `tests/test_failure_policy.py`에서 duplicate fingerprint halt, idle timeout,
  wall-clock timeout, CLI `runner supervise` 경로를 검증한다
- 기본 정책 기준선은 automatic retry budget `1`, idle timeout `900s`,
  wall-clock timeout `7200s`로 환경 변수에서 읽는다
- 현재 단계는 B5 완료 상태이며 다음 구현 작업은 `Telegram` notifier인
  작업 패킷 B6다
