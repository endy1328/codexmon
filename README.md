# codexmon

`codexmon`은 무인 AI 코딩 세션을 감독하기 위한 policy-first supervisor다.
기존 코딩 runner를 감싸서 작업을 전용 worktree에 격리하고, stall과 loop를
감지하며, 각 자율 실행을 아래 세 가지 명시적 결과 중 하나로 끝내는 것이
목표다.

- `PR opened`
- `blocked with explicit reason`
- `needs human decision`

## 현재 상태

이 저장소는 이제 `구현 진행 중` 단계다.

현재 존재하는 것:
- 정본 설계 문서 세트
- 고정된 v1 통합 및 정책 결정
- 인수 및 구현 슬라이스 계획 산출물
- code-start gate 통과 기록
- Python 3.11 기반 저장소 초기화와 개발 기준선
- 최소 CLI skeleton과 baseline test

아직 존재하지 않는 것:
- supervisor 핵심 런타임
- 실제 task orchestration 구현
- runner, Telegram, GitHub 연동 코드
- 후속 작업 패킷 검증 산출물

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

현재 기준선은 설계 마감과 code-start gate 검토까지 끝난 상태다.
다음 프로젝트 단계는 `docs/IMPLEMENTATION_SLICE.md`와
`docs/EXECUTION_PLAN.md`를 기준으로 마일스톤 M1의 다음 작업인
작업 패킷 B2부터 진행하는 것이다.

## 구현 기준선

현재 구현 기준선은 다음과 같다.

- Python 3.11
- `src/` 레이아웃 패키지 구조
- 표준 라이브러리 중심 baseline CLI
- `unittest` 기반 테스트
- `Makefile` 기반 기본 실행 명령

빠른 시작:

- `make run`
- `make doctor`
- `make test`
- `make check`
