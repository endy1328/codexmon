# 구현 슬라이스

날짜: 2026-04-21
상태: 구현 진행 중

## 이 문서의 역할

이 문서는 첫 구현 슬라이스의 정본 정의다. 구현을 시작하지 않고도
v1 MVP를 실행 가능한 작업 패킷으로 분해한다.

## 슬라이스 목표

가장 작은 end-to-end 경로로 아래를 가능하게 해야 한다.

- run 시작
- 전용 worktree에서 작업 격리
- `Codex` 실행
- bounded failure signal 감지
- `Telegram` 알림 전송
- 성공 시 PR 생성

## 작업 패킷 순서

권장 실행 순서는 아래와 같다.

1. repository 초기화와 개발 기준선
2. run record 및 event persistence
3. worktree allocator
4. `Codex` adapter
5. failure signal path
6. `Telegram` notifier
7. PR handoff

## 마일스톤 묶음

작업 패킷은 아래 마일스톤 단위로 관리한다.

- 마일스톤 M1: 작업 패킷 1-3
- 마일스톤 M2: 작업 패킷 4-5
- 마일스톤 M3: 작업 패킷 6
- 마일스톤 M4: 작업 패킷 7과 종단 간 인수

각 마일스톤은 앞선 마일스톤의 검증이 끝나기 전까지 시작하지 않는다.

## 작업 패킷 1: Repository 초기화와 개발 기준선

- 목적: 제품 기능 구현 전, 저장소를 안전한 구현 시작 상태로 준비한다.
- 입력: 정본 루트 설계 문서, 로컬 개발 환경, Git 초기화 결정
- 출력: 구현 가능 저장소 기준선, 합의된 프로젝트 skeleton, 로컬 개발 규약,
  code-start gate 기록
- 선행 의존성: 설계 마감과 code-start 승인
- 완료 조건: 저장소가 첫 구현 슬라이스를 안전하게 수용할 준비가 됐다
- 검증 방법: code-start checklist 통과 여부와 저장소 기준선 일치 여부 확인
- 갱신할 문서: `README.md`, `agent.md`, `agent-docs/status/current-status.md`,
  `agent-docs/validation/*`, `docs/EXECUTION_PLAN.md`

세부 구현 항목:
- Git repository 초기화
- 기본 branch 규약 및 `.gitignore` 기준선 확정
- 프로젝트 루트 구조와 모듈 경계 생성
- 애플리케이션 언어와 패키지 관리자 확정
- 테스트, lint, format 실행 기본선 확정
- 로컬 개발 환경 변수/설정 파일 템플릿 작성

## 작업 패킷 2: Run Record 및 Event Persistence

- 목적: 이후 모든 작업 패킷이 의존하는 durable run ledger를 만든다.
- 입력: 정본 상태 모델, 정책 기준선, `SQLite` persistence 결정
- 출력: task/run schema, state transition persistence, event append path,
  failure fingerprint persistence 계약
- 선행 의존성: 작업 패킷 1
- 완료 조건: runner integration 없이도 run 생성, transition 저장, query가 가능하다
- 검증 방법: synthetic run을 만들고 허용된 transition은 저장되며, invalid transition은
  거부되는지 확인
- 갱신할 문서: `agent-docs/status/current-status.md`, `agent-docs/validation/*`,
  그리고 schema 관련 설계가 바뀌면 해당 설계 문서

세부 구현 항목:
- `SQLite` schema 및 migration 방식 정의
- task/run/attempt/event/state-transition 모델 생성
- failure fingerprint 저장 구조 생성
- approval 및 PR reference 저장 구조 생성
- state transition guard와 projection query 구현
- synthetic fixture 기반 저장/조회 검증 작성

## 작업 패킷 3: Worktree Allocator

- 목적: `1 task = 1 run = 1 worktree = 1 branch` 격리 계약을 강제한다.
- 입력: 작업 패킷 2의 run record, repository lock 정책, branch naming 규칙
- 출력: repository lock 획득, worktree 할당, branch 매핑, cleanup policy hook
- 선행 의존성: 작업 패킷 2
- 완료 조건: run이 repository를 예약하고, worktree를 할당하며, lock을 deterministic하게
  반납할 수 있다
- 검증 방법: worktree 하나를 성공적으로 할당하고, lock이 잡힌 동안 충돌하는 두 번째 run이
  거부되는지 확인
- 갱신할 문서: `agent-docs/status/current-status.md`, `agent-docs/validation/*`,
  필요하면 `docs/ACCEPTANCE_CHECKLIST.md`

세부 구현 항목:
- repository-wide lock primitive 구현
- deterministic branch naming 구현
- worktree create/remove lifecycle 구현
- run과 worktree/branch mapping persistence 연결
- crash 이후 lock/worktree 진단 경로 작성

## 작업 패킷 4: Codex Adapter

- 목적: 첫 지원 runner를 실행하고 관찰한다.
- 입력: 할당된 worktree, run metadata, task instruction,
  `docs/ARCHITECTURE_OVERVIEW.md`의 adapter 경계
- 출력: runner launch path, stdout/stderr capture, exit signal capture,
  structured adapter event
- 선행 의존성: 작업 패킷 3
- 완료 조건: `Codex`가 run workspace 안에서 시작되고, lifecycle을 orchestrator가
  관찰할 수 있다
- 검증 방법: 통제된 runner invocation을 수행하고 launch, heartbeat 성격 출력,
  exit event가 저장되는지 확인
- 갱신할 문서: `agent-docs/status/current-status.md`, `agent-docs/validation/*`

세부 구현 항목:
- `Codex` 실행 wrapper 구현
- task instruction과 run metadata 주입 방식 구현
- stdout/stderr/exit signal capture 구현
- adapter event normalization 구현
- launch 실패와 즉시 종료 케이스 처리

## 작업 패킷 5: Failure Signal Path

- 목적: runner 동작을 bounded halt 또는 retry 결정으로 변환한다.
- 입력: adapter event, timeout 규칙, failure fingerprint 규칙, retry budget
- 출력: idle timeout 감지, wall-clock timeout 감지, fingerprint 비교,
  retry eligibility 판단
- 선행 의존성: 작업 패킷 4
- 완료 조건: 반복 실패, idle stall, retry 거부가 모두 run ledger에 deterministic하게
  기록된다
- 검증 방법: timeout 처리, duplicate fingerprint halt, automatic retry 1회를
  증명하는 synthetic 또는 controlled failure scenario 실행
- 갱신할 문서: `agent-docs/status/current-status.md`, `agent-docs/validation/*`,
  정책이 실제로 바뀌는 경우에만 `docs/POLICY_BASELINE.md`

세부 구현 항목:
- idle timeout 감지기 구현
- wall-clock timeout 감지기 구현
- failure fingerprint normalization 구현
- duplicate fingerprint halt rule 구현
- automatic retry budget enforcement 구현
- halt / retry / escalation reason code 정규화

## 작업 패킷 6: Telegram Notifier

- 목적: 첫 remote supervision 경로를 제공한다.
- 입력: structured run event, terminal outcome, 필수 remote verb
- 출력: outbound state notification, inbound operator action
  `status`, `stop`, `retry`, `approve`
- 선행 의존성: 작업 패킷 5
- 완료 조건: operator가 볼 수 있는 notification이 존재하고, remote action이 run ledger에
  저장된다
- 검증 방법: notifier 경로를 시뮬레이션 또는 실제 실행하여 remote action이 유효한
  transition을 만드는지 확인
- 갱신할 문서: `agent-docs/status/current-status.md`, `agent-docs/validation/*`

세부 구현 항목:
- `Telegram` bot 설정 및 credential 주입 경로 구현
- 상태 알림 메시지 포맷 구현
- `status`, `stop`, `retry`, `approve` command parser 구현
- inbound action과 orchestrator event 연결
- notifier 실패 시 오류 기록 및 재시도 정책 반영

## 작업 패킷 7: PR Handoff

- 목적: v1 성공 경로를 완성한다.
- 입력: 성공한 run state, branch 매핑, local check 결과, handoff summary 입력,
  GitHub credential/config
- 출력: PR 생성 요청, persisted PR reference, CI visibility snapshot,
  최종 `PR opened` 결과
- 선행 의존성: 작업 패킷 6
- 완료 조건: 성공한 run이 요구된 handoff 내용을 담은 PR을 실제로 만들고 결과를 durable하게
  기록한다
- 검증 방법: controlled success-path run을 수행하고 PR reference, summary body,
  PR 생성 후 CI visibility가 저장되는지 확인
- 갱신할 문서: `agent-docs/status/current-status.md`, `agent-docs/validation/*`,
  구현이 시작된 뒤 `README.md`

세부 구현 항목:
- local check bundle 실행 경로 구현
- PR summary/body generator 구현
- GitHub PR creation 연동 구현
- PR reference 및 CI visibility persistence 구현
- end-to-end demo task와 handoff artifact 검증

## 슬라이스 종료 조건

이 슬라이스는 `docs/ACCEPTANCE_CHECKLIST.md`의 체크리스트가 실제 종단 간
검증 실행에서 통과할 때만 완료로 본다.
