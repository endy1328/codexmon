# 실행 계획

날짜: 2026-04-21
상태: 구현 진행 중

## 이 문서의 역할

이 문서는 설계 마감에서 첫 통제된 구현 슬라이스로 넘어가기 위한
정본 실행 계획이다. 제품이나 아키텍처 질문을 다시 열지 않고도, 작업을
작업 패킷 단위와 검증 게이트 중심으로 유지한다.

## 현재 위치

설계 기준선은 설계 마감 수준까지 고정됐다.

이미 고정된 것:

- 제품 계약: `docs/FOUNDATION.md`
- 아키텍처 기준선: `docs/ARCHITECTURE_OVERVIEW.md`
- 상태 머신: `docs/STATE_MACHINE.md`
- 정책 기준선: `docs/POLICY_BASELINE.md`
- 인수 대상: `docs/ACCEPTANCE_CHECKLIST.md`
- 첫 구현 슬라이스: `docs/IMPLEMENTATION_SLICE.md`
- code-start gate 통과 기록: `agent-docs/validation/code-start-gate-validation.md`

아직 시작하지 않은 것:

- 작업 패킷 B7 이후 구현
- supervisor 핵심 런타임 구현

## 실행 원칙

- 검증이 실제 결함을 드러내지 않는 한 고정된 v1 결정을 다시 열지 않는다
- 작업은 명시적 의존성을 가진 작업 패킷 단위로 유지한다
- 각 작업 패킷은 다음 작업 패킷의 정식 진척으로 인정되기 전에 검증한다
- 수용된 작업 패킷마다 상태 및 검증 기록을 갱신한다
- 진짜 새로운 중대 결정이 생길 때만 다시 judgment를 연다

## 단계 A: 구현 시작 게이트

목표:
- 설계 마감이 구현 시작을 허용할 만큼 충분한지 확인한다

필수 확인 항목:
- 정본 루트 문서가 내부적으로 일관된다
- `README.md`와 `agent.md`가 정본 문서 모델을 반영한다
- runner, notifier, persistence, policy에 unresolved blocker가 남아 있지 않다
- `docs/IMPLEMENTATION_SLICE.md`의 첫 작업 패킷을 설계 질문 재개 없이 실행할 수 있다

종료 조건:
- 고정된 v1 계약을 바꾸지 않고 구현을 시작할 수 있다

결과:
- 통과
- Git 미초기화 상태는 구현 블로커가 아니라 작업 패킷 B1의 첫 실행 항목으로 처리한다
- 애플리케이션 언어, 패키지 관리자, 테스트 기본선 선택은 설계 재개 이슈가 아니라
  작업 패킷 B1 안에서 확정할 구현 기준선 항목으로 본다
- 작업 패킷 B1이 완료되면서 Git repository 초기화와 baseline CLI/test 구조가 반영됐다
- 작업 패킷 B2가 완료되면서 `SQLite` run ledger, 상태 전이 guard, synthetic
  `start/status` CLI 경로가 반영됐다
- 작업 패킷 B3이 완료되면서 repository lock, deterministic branch/worktree
  allocator, diagnose 경로가 반영됐다
- 작업 패킷 B4가 완료되면서 `Codex` adapter, runner launch/output/exit persistence,
  synthetic `runner run` CLI 경로가 반영됐다
- 작업 패킷 B5가 완료되면서 timeout, failure fingerprint, automatic retry policy,
  synthetic `runner supervise` CLI 경로가 반영됐다
- 작업 패킷 B6이 완료되면서 `Telegram` notifier, outbound alert,
  inbound `status/stop/retry/approve`, synthetic `telegram notify/receive`
  CLI 경로가 반영됐다
- 다음 작업은 작업 패킷 B7다

## 구현 마일스톤 개요

구현은 code-start gate 통과 직후 아래 마일스톤 순서로 진행한다.

| 마일스톤 | 범위 | 포함 작업 패킷 | 핵심 산출물 | 다음 단계로 넘어가는 기준 |
| --- | --- | --- | --- | --- |
| M0 | 구현 진입 승인 | 단계 A | code-start gate 결과, 구현 기준선 승인 | 저장소 초기화와 기술 기준선 수립이 바로 가능하다 |
| M1 | 기반 레이어 구축 | B1-B3 | 저장소 기준선, `SQLite` run ledger, worktree/lock 경로 | runner 없이도 run 생성과 격리 lifecycle이 성립한다 |
| M2 | 실행 및 실패 통제 | B4-B5 | `Codex` adapter, timeout/fingerprint/retry 경로 | 성공/실패/정지 신호가 deterministic하게 기록된다 |
| M3 | 원격 감독 경로 | B6 | `Telegram` 알림과 원격 action round-trip | operator가 원격에서 상태 확인과 개입을 할 수 있다 |
| M4 | 성공 경로 및 인수 | B7 + 단계 C | PR handoff, CI visibility, 종단 간 검증 기록 | 정본 데모 시나리오와 인수 체크리스트가 통과한다 |

## 단계 B: 첫 구현 슬라이스 작업 패킷

이제 아래 실행 단위가 필수 작업 패킷 순서다.

### 작업 패킷 B1: Repository 초기화와 개발 기준선

- 목표: code-start 기준선과 저장소 규약을 확정한다
- 선행 의존성: 단계 A
- 산출물: 구현 가능한 repository 기준선
- 검증: code-start checklist 및 repository baseline 검토

### 작업 패킷 B2: Run Record 및 Event Persistence

- 목표: `SQLite` 기반 durable run ledger를 만든다
- 선행 의존성: 작업 패킷 B1
- 산출물: task/run/event/transition persistence 경로
- 검증: synthetic run 생성 및 transition 검증
- 현재 상태: 완료

### 작업 패킷 B3: Worktree Allocator

- 목표: repository lock과 worktree/branch 할당을 강제한다
- 선행 의존성: 작업 패킷 B2
- 산출물: deterministic workspace lifecycle
- 검증: 성공적 할당과 충돌 run 거부
- 현재 상태: 완료

### 작업 패킷 B4: Codex Adapter

- 목표: 할당된 worktree 안에서 `Codex`를 실행하고 관찰한다
- 선행 의존성: 작업 패킷 B3
- 산출물: runner launch, log capture, adapter event stream
- 검증: controlled adapter invocation과 persisted lifecycle event
- 현재 상태: 완료

### 작업 패킷 B5: Failure Signal Path

- 목표: runner 결과에 retry, timeout, fingerprint 정책을 적용한다
- 선행 의존성: 작업 패킷 B4
- 산출물: deterministic halt, retry, escalation 결정
- 검증: timeout, duplicate fingerprint, retry-budget scenario
- 현재 상태: 완료

### 작업 패킷 B6: Telegram Notifier

- 목표: 첫 remote supervision 경로를 추가한다
- 선행 의존성: 작업 패킷 B5
- 산출물: outbound alert와 inbound operator action
- 검증: `status`, `stop`, `retry`, `approve`의 notifier round-trip
- 현재 상태: 완료

### 작업 패킷 B7: PR Handoff

- 목표: `PR opened` 성공 경로를 완성한다
- 선행 의존성: 작업 패킷 B6
- 산출물: PR 생성, handoff summary, CI visibility record
- 검증: controlled success-path run과 persisted PR reference

## 마일스톤별 진행 방식

### 마일스톤 M0: 구현 진입 승인

목표:
- 구현 시작을 막는 설계·운영 블로커가 더 이상 없음을 공식 확인한다

세부 작업:
- 루트 정본 문서와 보조 운영 기록 간 충돌 여부 최종 확인
- 현재 기획 단계의 열린 질문이 구현 블로커인지 여부 재판정
- 첫 구현 패킷을 수행할 작업 환경, 권한, 로컬 도구 접근성 확인
- 구현 단계 시작 시 갱신할 상태/검증 문서 목록 확정

핵심 산출물:
- code-start gate 통과 기록
- 구현 시작 시점의 기준 상태 스냅샷

검증 포인트:
- 설계 질문 재개 없이 작업 패킷 B1을 시작할 수 있다
- Git 미초기화 상태가 구현 시작 시 해결될 첫 작업으로 명확히 배치돼 있다

현재 상태:
- 완료
- 후속 구현은 마일스톤 M4의 작업 패킷 B7부터 이어진다

### 마일스톤 M1: 기반 레이어 구축

목표:
- run ledger와 workspace 격리 기반을 먼저 완성해서 이후 runner 통합이 얇아지게 한다

포함 범위:
- 작업 패킷 B1
- 작업 패킷 B2
- 작업 패킷 B3

세부 작업:
- Git repository 초기화와 기본 branch 규약 확정
- 프로젝트 디렉터리 구조, 패키지/모듈 경계, 테스트 기본선 수립
- 애플리케이션 언어, 패키지 관리자, 테스트 러너, lint/format 기준 확정
- `SQLite` schema 초안 작성
  - tasks
  - runs
  - attempts
  - state transitions
  - event log
  - approvals
  - failure fingerprints
  - worktree/branch assignment
- 상태 전이 guard와 event append 경로 구현
- repository-wide lock primitive 결정 및 구현
- branch naming 규칙과 worktree create/remove lifecycle 구현
- crash 이후에도 orphaned state를 복구할 수 있도록 최소 진단 경로 마련

핵심 산출물:
- 구현 가능한 저장소 기준선
- `SQLite` 기반 run ledger
- worktree allocator와 repository lock

검증 포인트:
- synthetic run 생성, 조회, 상태 전이가 가능하다
- invalid transition이 거부된다
- 두 번째 autonomous run이 lock 때문에 거부된다

다음 단계로 넘기는 조건:
- runner가 없어도 orchestrator 핵심 상태와 격리 규칙이 독립적으로 검증된다

### 마일스톤 M2: 실행 및 실패 통제

목표:
- `Codex` 실행과 실패 판단 경로를 연결해 unattended run의 핵심 제어 루프를 만든다

포함 범위:
- 작업 패킷 B4
- 작업 패킷 B5

세부 작업:
- `Codex` 실행 래퍼와 세션 시작 경로 구현
- task instruction, run id, workspace context 전달 포맷 확정
- stdout/stderr/exit signal 수집과 structured adapter event 변환
- runner heartbeat 성격의 진행 신호 수집
- idle timeout 감지 구현
- wall-clock timeout 감지 구현
- failure fingerprint normalization 구현
- duplicate fingerprint halt rule 구현
- automatic retry budget enforcement 구현
- halt reason, retry decision, human escalation reason의 기록 형식 통일

핵심 산출물:
- `Codex` adapter
- timeout, fingerprint, retry 정책 실행 경로
- deterministic halt / retry / escalation 기록

검증 포인트:
- controlled invocation에서 launch, output, exit event가 저장된다
- idle timeout이 설계된 조건에서 발생한다
- 동일 fingerprint 2회 발생 시 즉시 halt한다
- automatic retry가 최대 1회만 수행된다

다음 단계로 넘기는 조건:
- 로컬에서 사람이 붙어 보지 않아도 run이 bounded하게 끝나는 제어 루프가 생긴다

### 마일스톤 M3: 원격 감독 경로

목표:
- operator가 터미널 앞에 없을 때도 상태 확인과 개입이 가능하게 한다

포함 범위:
- 작업 패킷 B6

세부 작업:
- `Telegram` bot credential/config 처리 경로 구현
- run 상태 변경 알림 포맷 설계
- `status`, `stop`, `retry`, `approve` inbound command 처리 경로 구현
- notifier delivery failure 시 재시도 또는 오류 기록 규칙 구현
- 원격 action을 orchestrator event와 approval event에 매핑
- kill-switch와 remote stop의 상호작용 점검

핵심 산출물:
- outbound `Telegram` 알림
- inbound operator action round-trip

검증 포인트:
- 상태 변화가 `Telegram`으로 전달된다
- `status`가 현재 run summary를 반환한다
- `stop`, `retry`, `approve`가 유효한 state transition으로 이어진다

다음 단계로 넘기는 조건:
- unattended run이 remote supervision이 가능한 상태가 된다

현재 상태:
- 완료
- 다음 구현은 마일스톤 M4의 작업 패킷 B7이다

### 마일스톤 M4: 성공 경로 및 인수 완료

목표:
- 성공 경로를 `PR opened`까지 닫고, 첫 데모를 검증 가능한 결과로 끝낸다

포함 범위:
- 작업 패킷 B7
- 단계 C

세부 작업:
- local check bundle 연동 방식 구현
- PR body 생성기와 handoff summary 구조 구현
- GitHub PR 생성 경로 구현
- PR reference 및 CI visibility persistence 구현
- end-to-end demo task 시나리오 준비
- `docs/ACCEPTANCE_CHECKLIST.md` 기준 검증 실행
- 결과를 `agent-docs/validation/`에 기록하고 상태 문서 갱신

핵심 산출물:
- PR handoff success path
- 첫 종단 간 검증 기록

검증 포인트:
- 성공한 run이 실제 PR reference를 남긴다
- PR 본문에 summary, changed files, check result, residual risk가 포함된다
- acceptance checklist 전 항목이 증거와 함께 검증된다

종료 조건:
- 첫 private demo를 재현 가능한 방식으로 시연할 수 있다

## 단계 C: 종단 간 인수

목표:
- 첫 구현 슬라이스가 정본 데모와 인수 체크리스트를 만족함을 증명한다

필수 검증:
- `docs/FOUNDATION.md`의 시나리오 실행
- `docs/ACCEPTANCE_CHECKLIST.md` 전체 항목 검증
- `agent-docs/status/current-status.md` 갱신
- `agent-docs/validation/` 아래 전용 검증 기록 추가

종료 조건:
- 설계 범위를 다시 열지 않고도 bounded unattended run을 시연할 수 있다

## 첫 Slice 이후로 미루는 항목

아래는 의도적으로 첫 실행 계획에서 제외한다.

- Slack 또는 다중 notifier 지원
- `Codex` 외 runner abstraction
- path-scoped concurrent run
- auto-merge
- dashboard 또는 TUI 제품 작업
- multi-repository orchestration

## 즉시 다음 작업

마일스톤 M1, M2, M3는 완료됐다. 다음 작업은 마일스톤 M4의 작업 패킷 B7이고,
이후 단계 C 인수 검증으로 이어진다.
