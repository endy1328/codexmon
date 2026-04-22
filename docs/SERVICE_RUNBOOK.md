# 서비스 런북

날짜: 2026-04-22
상태: 구현 진행 중

## 목적

이 문서는 `codexmon` daemon을 systemd 기준으로 배포하고 운영하는 baseline
절차를 정리한다. v1 범위에서 service packaging의 정본 runbook 역할을 한다.

## 포함 범위

- daemon wrapper 스크립트: `scripts/run-codexmon-daemon.sh`
- systemd unit 템플릿: `ops/systemd/codexmon-daemon.service`
- env 예시: `ops/systemd/codexmon-daemon.env.example`

## 준비 사항

1. 저장소 루트에 `.env`를 준비한다.
2. 최소한 아래 값이 `.env` 또는 systemd env 파일에 들어 있어야 한다.
   - `CODEXMON_DB_PATH`
   - `CODEXMON_REPO_PATH`
   - `CODEXMON_WORKTREE_ROOT`
   - `CODEXMON_GITHUB_OWNER`
   - `CODEXMON_GITHUB_REPO`
   - `CODEXMON_LOCAL_CHECK_COMMAND`
3. Telegram supervision을 쓸 경우 아래를 추가한다.
   - `CODEXMON_TELEGRAM_BOT_TOKEN`
   - `CODEXMON_TELEGRAM_CHAT_ID`

## 설치 절차

1. env 예시를 기준으로 실제 env 파일을 만든다.
2. systemd user unit 경로에 unit 파일을 배치한다.
3. 필요하면 `WorkingDirectory`, `EnvironmentFile` 경로를 실제 설치 위치에 맞게 조정한다.

권장 예시:

```bash
mkdir -p ~/.config/systemd/user
cp /home/u24/projects/codexmon/ops/systemd/codexmon-daemon.service ~/.config/systemd/user/
cp /home/u24/projects/codexmon/ops/systemd/codexmon-daemon.env.example /home/u24/projects/codexmon/ops/systemd/codexmon-daemon.env
systemctl --user daemon-reload
systemctl --user enable --now codexmon-daemon.service
```

## 운영 명령

상태 확인:

```bash
systemctl --user status codexmon-daemon.service
PYTHONPATH=src python3 -m codexmon daemon status --limit 10
PYTHONPATH=src python3 -m codexmon monitor snapshot --json
```

재시작:

```bash
systemctl --user restart codexmon-daemon.service
```

중지:

```bash
systemctl --user stop codexmon-daemon.service
```

## 종료 동작

- service manager는 기본적으로 `SIGTERM`으로 daemon을 중지한다
- `daemon serve`는 `signal:SIGTERM` 또는 `signal:SIGINT` stop reason을 stopped
  heartbeat payload에 남긴다
- active run이 있으면 current tick 종료 또는 crash recovery 경로를 통해 다음 daemon이
  orphaned run을 다시 정리한다

## 운영 시 확인할 것

- `daemon status`에 `started`, `idle`, `stopped` heartbeat가 남는지 확인한다
- `stop_reason`이 예상한 signal 값으로 기록되는지 확인한다
- service restart 뒤 `queued`, `retry_pending`, `pr_handoff` run이 다시 pickup되는지
  확인한다
- orphaned `running`/`analyzing_failure` run이 recovery scan으로 정리되는지 확인한다

## 모니터 운영

- live monitor는 `PYTHONPATH=src python3 -m codexmon monitor serve`로 띄운다
- 브라우저는 `./api/progress`를 우선 읽고, monitor server가 없으면 내장 snapshot으로
  fallback 한다
- 같은 서버는 `/progress.json` 경로도 live JSON으로 제공해 기존 경로와의 호환을 유지한다

## 현재 한계

- 현재 baseline은 systemd 기준 reference implementation이다
- 다른 process manager용 템플릿은 아직 없다
