# 기반 문서

날짜: 2026-04-21
상태: 구현 진행 중

## 이 문서의 역할

이 문서는 `codexmon`의 정본 제품 계약이다. MVP 약속, 대상 사용자,
첫 데모 시나리오, 명시적 비범위, 그리고 v1 실행이 반드시 만족해야 하는 종료
결과 계약을 고정한다.

## 제품 테제

무인 코딩 세션은 코드 생성 레이어보다 운영 레이어에서 더 자주 실패한다.
핵심 pain point는 개발자가 AI 코딩 세션을 몇 시간 동안 혼자 돌려도, 그것이
bounded하고 legible한 상태로 끝날 것이라는 신뢰를 가지기 어렵다는 점이다.

`codexmon`은 코딩 runner를 대체하는 제품이 아니다. 기존 runner 위에
deterministic control, isolation, observability, escalation, clean end-state
계약을 제공하는 supervisor다.

## 대상 사용자

초기 대상 사용자는 다음과 같다.

- solo founder, staff engineer, 또는 높은 자율성을 가진 개발자
- CLI, Git, GitHub workflow에 익숙한 사용자
- 이미 AI 코딩 도구를 실무 작업에 사용 중인 사용자
- 완전한 자율성보다 예측 가능한 안전성과 handoff를 더 중시하는 사용자
- 집중 작업 시간, 저녁, 야간에 장시간 세션을 돌리고 싶은 사용자

## 핵심 pain point

v1이 해결하려는 문제는 운영 신뢰성 부족이다.

- runner가 아무 설명 없이 멈춘다
- 같은 실패를 반복하면서 진전이 없다
- 세션이 branch/workspace 상태를 애매하게 남긴다
- 세션이 usable PR 또는 명시적 block 이유 없이 끝난다

## MVP 약속

v1 약속은 의도적으로 좁고 검증 가능해야 한다.

- 단일 repository
- 한 번에 하나의 task
- run마다 하나의 고립된 worktree와 branch
- 첫 coding runner는 `Codex`
- 첫 remote notifier는 `Telegram`
- 첫 local durable store는 `SQLite`
- GitHub handoff는 PR 생성과 CI 가시화까지만 포함

제품 약속은 모든 v1 자율 실행이 아래 세 가지 결과 중 하나로 끝난다는 것이다.

- `PR opened`
- `blocked with explicit reason`
- `needs human decision`

## 명시적 비범위

첫 버전에는 아래 항목을 포함하지 않는다.

- multi-repository orchestration
- multi-runner routing
- Slack 등 추가 notifier surface
- dashboard-first UX
- protected branch에 대한 autonomous merge
- 충돌이 많은 auto-rebase 또는 auto-resolution
- 배포 자동화
- self-modifying policy behavior

## 고정된 첫 데모 시나리오

첫 데모는 아래 경로로 고정한다.

1. 사용자가 로컬 CLI에서 단일 repository 대상 task를 시작한다.
2. `codexmon`이 해당 run 전용 worktree와 branch를 할당한다.
3. `Codex` adapter가 그 workspace 안에서 단일 구현 시도를 실행한다.
4. orchestrator가 로그, diff, deterministic run signal을 관찰한다.
5. run이 stall 또는 loop에 빠지면 `codexmon`이 `Telegram` 알림을 보내고
   정책에 따라 halt 또는 escalation한다.
6. run이 성공하면 `codexmon`이 GitHub PR을 생성하고 CI visibility를 노출한다.
7. 자율 세션은 반드시 위 세 가지 종료 결과 중 하나에 도달한 뒤 끝난다.

## 성공 및 실패 종료 상태

이 결과들은 선택적 요약 문구가 아니라 제품 계약이다.

### `PR opened`

아래가 모두 만족될 때만 사용한다.

- run branch가 존재하고 run id와 추적 가능하다
- local deterministic gate가 통과했다
- PR 생성이 실제로 성공했다
- PR 본문에 usable handoff summary가 포함된다

### `blocked with explicit reason`

자동화가 멈추면서 이유를 설명할 수 있을 때 사용한다. 유효한 이유 예시는:

- 반복된 normalized failure fingerprint
- idle timeout
- wall-clock timeout
- preflight 또는 policy 위반
- 허용된 retry가 없는 deterministic local check 실패
- operator stop

### `needs human decision`

자동화가 알려진 경계에 도달하여 operator 입력을 기다릴 때 사용한다.
예시는 다음과 같다.

- approval-required change class가 감지됨
- 실패 이후 recovery path가 모호함
- 정책상 orchestrator 단독으로 넘을 수 없는 위험이 존재함

## MVP 완료 정의

구현 시작에 충분한 planning 상태는 아래가 모두 참일 때다.

- 위 v1 범위가 루트 설계 문서 전체에 일관되게 반영되어 있다
- 상태 머신과 정책 기준선이 명시돼 있다
- 인수 체크리스트가 존재한다
- 첫 구현 슬라이스가 작업 패킷 단위로 쪼개져 있다
- `README.md`와 `agent.md`가 정본 문서 구조를 가리킨다

첫 데모 구현이 충분하다고 볼 수 있는 조건은, 제품 범위를 다시 열지 않고도
고정된 데모 시나리오를 실제로 재현할 수 있는 것이다.
