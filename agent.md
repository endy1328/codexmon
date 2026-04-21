# 멀티 에이전트 운영 모델

## 목적

이 저장소는 `구현 진행 중` 단계다. code-start gate는 통과되었고,
에이전트는 정본 설계 기준선을 유지하면서 구현과 검증을 진행한다.

## 정본 원본

루트 `docs/*.md` 문서가 제품 및 설계 결정의 정본이다.

`agent-docs/planning/`, `agent-docs/status/`, `agent-docs/validation/`, `agent-docs/decisions/`
하위 문서는 운영 기록 또는 이력 문서다. 보조 기록과 루트 설계 문서가 충돌하면
루트 설계 문서를 우선한다.

문서 분류 규칙:
- `docs/`: 프로그램 개발을 위한 정본 제품/설계 문서
- `agent-docs/`: 에이전트 하네스 운영 문서, 상태 기록, 검증 기록, 판단 이력
- 루트 `agent.md`: 하네스 운영 규칙의 진입점

## 현재 고정 기준선

새로운 judgment로 바뀌기 전까지 작업 기준선은 아래와 같다.

- 첫 runner: `Codex`
- 첫 notifier: `Telegram`
- 로컬 durable store: `SQLite`
- GitHub 범위: PR 생성 + CI 가시화만 포함
- 종료 결과 계약: `PR opened`, `blocked with explicit reason`,
  `needs human decision`

## 현재 모드

제품 코드 개발이 허용된다.

구현 단계에서 허용되는 작업:
- 제품 소스 파일 추가
- 저장소 초기화와 개발 기준선 수립
- 의존성 설치
- 빌드, 테스트, 검증 경로 추가
- 런타임 및 통합 경로 구현
- 상태 및 검증 문서 갱신

구현 단계에서도 여전히 금지되는 작업:
- 고정된 v1 제품 결정 재개
- 승인 없이 범위 외 기능 추가
- 정본 정책을 우회하는 임의 구현
- 운영 기록 갱신 없이 중요한 결정만 채팅에 남기는 행위

현재 기술 기준선:
- Python 3.11
- `src/` 패키지 구조
- stdlib-first CLI와 `unittest` baseline
- 추가 개발 도구는 필요 시 점진적으로 도입

## 의사결정 및 검증 규칙

중대한 제품, 아키텍처, 정책, 통합 결정이 아직 열려 있다면 fresh judgment를
사용한다.

다음 작업에는 fresh judgment가 필수는 아니다.
- 서식 정리
- 문구 정리
- 이력 문서 보수
- 이미 승인된 계획을 정본 문서로 반영하는 작업

중요한 문서 작업에는 여전히 검증이 필요하다. 별도 verification agent를 쓰지
않는다면, 메인 에이전트가 명시적 일관성 검토를 수행하고 validation 기록을
갱신한 뒤 작업을 닫아야 한다.

## 필수 흐름

실질적인 작업은 아래 순서를 따른다.

1. `Inventory`
2. `Plan`
3. 실제 결정이 열려 있을 때만 `Judge`
4. `Execute`
5. `Verify`
6. `Refresh Docs`
7. `Synthesize`

강제 흐름:
`plan -> execute -> verify -> refresh docs`

## 문서 갱신 규칙

수용된 작업이 끝나면 영향받은 standing document를 갱신한다.

최소 확인 대상:
- `README.md`
- `agent.md`
- 루트 `docs/*.md`
- 관련 `agent-docs/status/`, `agent-docs/validation/` 문서

중요한 결정은 채팅 출력에만 남기면 안 된다.

## 이 단계의 종료 조건

구현 단계는 아래 원칙이 유지될 때만 계속된다.

- 정본 루트 문서가 최신 상태다
- code-start gate 검토가 끝났다
- 인수 기준이 존재한다
- 첫 구현 슬라이스가 정의되어 있다
- 상태 및 검증 기록이 최신이다

이 조건이 깨지면 구현을 멈추고 문서 또는 판단 기준부터 복구한다.
