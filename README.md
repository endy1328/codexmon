# codexmon

`codexmon`은 무인 AI 코딩 세션을 감독하기 위한 policy-first supervisor다.
기존 코딩 runner를 감싸서 작업을 전용 worktree에 격리하고, stall과 loop를
감지하며, 각 자율 실행을 아래 세 가지 명시적 결과 중 하나로 끝내는 것이
목표다.

현재 개발 버전은 `0.0.0.7`이다. 버전 형식은 `major.major.minor.minor` 4자리 고정이며,
왼쪽 두 자리는 메이저 버전, 오른쪽 두 자리는 마이너 버전으로 사용한다.

- `PR opened`
- `blocked with explicit reason`
- `needs human decision`

## 현재 상태

이 저장소는 이제 `구현 진행 중` 단계이며, 첫 구현 슬라이스, 마일스톤 `M4`,
`M5` supervisor runtime baseline, `M6` daemon worker baseline, `M7` crash
recovery baseline, `M8` service packaging baseline, `M9` progress monitor live
DB baseline이 완료됐다.

현재 존재하는 것:
- 정본 설계 문서 세트
- 고정된 v1 통합 및 정책 결정
- 인수 및 구현 슬라이스 계획 산출물
- code-start gate 통과 기록
- Python 3.11 기반 저장소 초기화와 개발 기준선
- `SQLite` 기반 durable run ledger
- 상태 전이 guard와 synthetic persistence 검증
- repository-wide execution lock
- deterministic branch/worktree allocator와 진단 경로
- `Codex` adapter와 fake runner 기반 lifecycle 검증
- timeout, fingerprint, retry budget 기반 failure signal path
- `Telegram` notifier와 outbound/inbound supervision 경로
- approval-required diff classification과 로컬 `approvals scan` 경로
- GitHub PR handoff와 CI visibility persistence 경로
- active runner interrupt 증거를 포함한 bounded halt 경로
- 단계 C acceptance validation suite와 체크리스트 대응 검증 기록
- synchronous supervisor runtime과 실제 task orchestration baseline
- `start --execute`, `execute` CLI를 통한 end-to-end run orchestration
- SQLite runtime heartbeat persistence와 `daemon status` 조회 경로
- `daemon run-once`, `daemon serve`를 통한 background worker baseline
- queued/retry_pending/pr_handoff run 자동 pickup과 operator approve 후 비동기 재개
- orphaned `running`/`analyzing_failure` run을 daemon이 retry 또는 halt로 복구하는 경로
- orphaned runner interrupt, duplicate fingerprint halt, recovery lock release 검증
- `daemon serve`의 SIGTERM/SIGINT stop hook과 service-manager 친화적 종료 경로
- systemd unit 템플릿, daemon wrapper 스크립트, service runbook
- live progress snapshot builder와 lightweight HTTP monitor server
- 최소 `start`, `status`, `stop`, `retry`, `approvals`, `workspace`, `runner`,
  `telegram`, `handoff`, `doctor`, `version`, `execute`, `daemon`, `monitor` CLI와 baseline test

아직 존재하지 않는 것:
- 추가 process manager 템플릿
- progress monitor를 위한 별도 인증/접근 제어 계층

## 고정된 v1 결정

- 첫 runner: `Codex`
- 첫 원격 notifier: `Telegram`
- persistence: 로컬 `SQLite`
- GitHub 범위: PR 생성 + CI 가시화
- merge 정책: v1에서 auto-merge 제외
- 격리 모델: `1 task = 1 run = 1 worktree = 1 branch`

## 정본 문서

아래 루트 `docs/` 문서들이 제품 및 설계 결정의 단일 진실 원본이다.

- `docs/FOUNDATION.md`
- `docs/ARCHITECTURE_OVERVIEW.md`
- `docs/STATE_MACHINE.md`
- `docs/POLICY_BASELINE.md`
- `docs/ACCEPTANCE_CHECKLIST.md`
- `docs/IMPLEMENTATION_SLICE.md`
- `docs/EXECUTION_PLAN.md`
- `docs/SERVICE_RUNBOOK.md`

## 보조 운영 기록

아래 문서들은 계속 유지하지만, 정본 설계 원본은 아니다.

- `agent.md`
- `agent-docs/planning/*`
- `agent-docs/status/*`
- `agent-docs/validation/*`
- `agent-docs/decisions/*`

하네스 운영 문서는 `agent-docs/` 아래에 모으고, 프로그램 자체의 제품/설계 문서는
계속 `docs/` 아래에 둔다.

## 왜 필요한가

장시간 코딩 세션은 코드 생성 자체보다 운영 측면에서 더 자주 실패한다.

- 아무 알림 없이 멈춘다
- 같은 실패를 반복한다
- branch나 workspace 상태를 애매하게 남긴다
- 쓸 수 있는 PR handoff 없이 끝난다

`codexmon`은 이런 세션을 더 똑똑하게 만드는 도구가 아니라, 더 bounded하고
legible하며 recoverable하게 만드는 도구다.

## 다음 단계

첫 구현 슬라이스와 M5-M9 runtime 확장 마일스톤은 닫힌 상태다.
현재 계획 문서 기준으로 필수 구현 마일스톤은 모두 완료됐고, 이후 확장은
별도 judgment 또는 범위 승인 대상이다.

## 구현 기준선

현재 구현 기준선은 다음과 같다.

- Python 3.11
- `src/` 레이아웃 패키지 구조
- 표준 라이브러리 중심 baseline CLI
- `SQLite` run ledger와 synthetic `start/status` 조회 경로
- repository lock과 `.codexmon/worktrees` 기반 allocator
- `Codex` adapter와 event-capturing `runner run` 경로
- failure policy가 적용된 `runner supervise` 경로
- `Telegram` notifier와 `telegram notify/receive` 경로
- 로컬 control plane `stop`, `retry`, `approvals`, `approvals scan` 경로
- PR handoff가 적용된 `handoff` 경로
- synchronous `start --execute`, `execute` runtime 경로
- `daemon run-once`, `daemon serve`, `daemon status` background worker 경로
- orphaned run crash recovery와 recovery-driven retry/halt 경로
- SIGTERM/SIGINT stop hook, daemon wrapper 스크립트, systemd service packaging baseline
- live monitor snapshot builder와 `monitor serve` HTTP server
- stage C acceptance validation suite와 인수 체크리스트 대응 검증
- `unittest` 기반 테스트
- `Makefile` 기반 기본 실행 명령

빠른 시작:

- `make run`
- `make doctor`
- `make test`
- `make check`
- `make monitor-serve`
- `PYTHONPATH=src python3 -m codexmon start "작업 요약" --execute`
- `PYTHONPATH=src python3 -m codexmon daemon serve`
- `PYTHONPATH=src python3 -m codexmon monitor snapshot --json`
