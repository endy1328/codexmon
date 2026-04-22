# 아키텍처 개요

날짜: 2026-04-22
상태: 구현 진행 중

## 이 문서의 역할

이 문서는 `codexmon`의 정본 v1 아키텍처 기준선이다. 시스템 경계,
고정된 통합 대상, 컴포넌트 책임, deterministic control과 AI 보조 역할의
분리를 정의한다.

상세 상태 전이 규칙은 `docs/STATE_MACHINE.md`에 있다.
상세 안전 정책은 `docs/POLICY_BASELINE.md`에 있다.

## 고정된 v1 경계

첫 아키텍처 타깃은 의도적으로 좁다.

- 배포 대상당 하나의 repository
- repository당 하나의 active autonomous run
- 첫 coding runner는 `Codex`
- 첫 notifier는 `Telegram`
- 첫 local control plane은 CLI
- 첫 supervisor runtime은 synchronous single-run execution
- local polling daemon worker, orphan crash recovery, systemd service packaging baseline이 구현됐다
- 다음 runtime 확장은 progress monitor live DB 연동이다
- 첫 durable store는 로컬 `SQLite`
- 첫 GitHub 범위는 PR 생성 + CI 가시화

auto-merge는 범위 밖이다.

## 상위 런타임 흐름

1. 로컬 CLI가 task 요청과 run record를 만들고, 필요하면 즉시 runtime 실행을 시작한다.
2. orchestrator가 preflight 검증을 수행하고 repository execution
   lock을 획득한다.
3. orchestrator가 전용 worktree와 branch를 할당한다.
4. `Codex` adapter가 해당 worktree 안에서 runner를 실행한다.
5. orchestrator가 run event, state transition, command summary,
   failure fingerprint를 `SQLite`에 저장한다.
6. deterministic policy가 timeout, approval gate, 반복 failure fingerprint,
   stop 요청을 평가하고, 성공 경로에서는 PR handoff 직전 approval debt를 확인한다.
7. `Telegram` notifier가 상태 변화를 알리고, 제한된 원격 동작
   `status`, `stop`, `retry`, `approve`를 수신한다.
8. run이 성공하면 GitHub handoff 경로가 PR을 생성하고 PR reference와
   CI visibility 상태를 기록한다.
9. background daemon worker는 `queued`, `retry_pending`, `pr_handoff` run을
   polling하고, orphaned `running`/`analyzing_failure` run을 recovery하며,
   service manager 아래에서도 일관된 stop reason과 heartbeat를 남긴다.

## 컴포넌트 책임

### Orchestrator

orchestrator는 시스템 오너다. 아래를 소유한다.

- run lifecycle 생성, 시작, 일시정지, halt, 완료
- 모든 state transition
- repository execution lock
- worktree 및 branch 할당
- timeout budget과 retry budget
- approval gating
- failure fingerprint 비교
- event persistence 및 audit record 생성
- notification dispatch
- PR handoff orchestration
- kill-switch 실행

### Codex Adapter

`Codex` adapter는 첫 runner를 감싸는 얇은 경계다. 아래를 담당한다.

- 올바른 workspace context로 `Codex` 실행
- task instruction과 run metadata를 세션에 전달
- stdout, stderr, exit code, timing, heartbeat 성격의 출력 수집
- runner 고유 process signal을 orchestrator event로 변환

adapter는 policy를 소유하지 않는다. retry, approval, halt 조건, merge
동작을 결정하지 않는다.

### Telegram Notifier

`Telegram` notifier는 v1의 유일한 원격 surface다. 아래를 담당한다.

- run 상태 요약 및 경보 발송
- 제한된 동작 `status`, `stop`, `retry`, `approve` 노출
- operator action을 orchestrator에 deterministic event로 반환

notifier는 business rule을 소유하지 않는다. transport boundary일 뿐이다.

### GitHub Handoff

v1의 GitHub 경계는 의도적으로 작다. 아래를 담당한다.

- run branch를 remote branch와 연결하거나 push
- run summary metadata를 포함한 PR 생성
- PR이 만들어진 이후 CI visibility 노출

아래는 담당하지 않는다.

- auto-merge
- reviewer assignment logic
- deployment 또는 release automation

### Persistence Layer

`SQLite`는 제품이 local-first, single-operator, event-oriented라는 점 때문에
고정된 v1 persistence 선택이다.

최소 저장 대상은 아래와 같다.

- tasks
- runs
- attempts
- state transitions
- event log entries
- failure fingerprints
- approvals
- worktree 및 branch assignment
- PR reference와 CI visibility snapshot
- runtime heartbeat record

event persistence는 append-oriented여야 한다. 현재 run state는 기록된
event 및 transition history의 projection이지, 유일한 진실 원본이 아니다.

## 격리 모델

격리 모델은 필수 조건이다.

- `1 task = 1 run = 1 worktree = 1 branch`
- active run끼리 mutable workspace를 재사용하지 않는다
- branch 이름은 deterministic하고 run id와 추적 가능해야 한다
- v1은 overlapping autonomous run을 피하기 위해 repository-wide execution
  lock을 사용한다
- cleanup는 persistence가 안전해지기 전 audit artifact를 파괴하면 안 된다

path-scoped concurrency는 이후로 미룬다. v1은 병렬성보다 정확성을 택한다.

## Event Log 경계

orchestrator는 run이 왜 특정 종료 상태에 도달했는지 재구성할 수 있는 최소
event stream을 기록해야 한다. 여기에는 다음이 포함된다.

- lifecycle event
- runner launch 및 exit signal
- timeout 및 idle 감지
- orphaned run recovery와 recovery interrupt signal
- failure fingerprint
- approval 요청과 operator 응답
- notifier 전달 기록
- PR handoff event

raw runner log는 structured event log와 별도 저장소에 있어도 되지만,
정책 판단의 정본 원본은 structured event log여야 한다.

## Deterministic Control 과 AI 보조 역할 분리

### Deterministic 책임

아래는 AI에 위임하지 않는다.

- state transition
- timeout enforcement
- retry budget enforcement
- repository locking
- worktree allocation
- approval gating
- forbidden-action enforcement
- PR handoff gate check
- kill-switch 동작

### AI 보조 책임

AI는 아래를 보조할 수 있다.

- 할당된 worktree 안의 코드 생성
- operator 가독성을 위한 실패 요약
- PR summary 초안 작성
- human-facing risk note 작성

### AI가 소유하면 안 되는 책임

AI는 아래를 소유하지 않는다.

- retry 허가
- approval 결정
- destructive Git operation
- merge safety
- repository lock 우회
- policy 변경

## 최소 CLI 표면

v1 CLI는 아래 동사를 지원해야 한다.

- `start`
- `execute`
- `status`
- `stop`
- `retry`
- `approvals`

정확한 flag 모양은 바뀔 수 있지만, 위 동사 집합 자체는 첫 슬라이스의 필수
제품 표면이다.
