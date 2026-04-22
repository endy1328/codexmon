# 계획 작업 인벤토리

날짜: 2026-04-22

## 이 문서의 역할

이 파일은 보조 작업 인벤토리와 마일스톤 로그다. 정본 범위와 첫 슬라이스 정의는
루트 설계 문서에 있다.

정본 원본:
- `docs/FOUNDATION.md`
- `docs/IMPLEMENTATION_SLICE.md`
- `docs/EXECUTION_PLAN.md`

## 완료된 계획 작업

- MVP 제품 계약 고정
- 아키텍처 기준선 고정
- 정본 상태 머신 추가
- 정본 정책 기준선 추가
- 인수 체크리스트 추가
- 첫 구현 슬라이스 작업 패킷 정의 추가
- 구현 단계 마일스톤 및 세부 작업 순서 정리
- code-start gate 검토 및 통과 기록 추가
- 작업 패킷 B1 완료
- 작업 패킷 B2 완료
- 작업 패킷 B3 완료
- 작업 패킷 B4 완료
- 작업 패킷 B5 완료
- 작업 패킷 B6 완료
- 작업 패킷 B7 완료
- approval-required diff classification과 local control plane 보강 완료
- 단계 C acceptance validation suite 완료
- 마일스톤 M4 완료
- synchronous supervisor runtime baseline 완료
- 마일스톤 M5 완료
- daemon worker baseline 완료
- 마일스톤 M6 완료
- crash recovery baseline 완료
- 마일스톤 M7 완료
- service packaging baseline 완료
- 마일스톤 M8 완료
- progress monitor live DB baseline 완료
- 마일스톤 M9 완료
- `README.md`, `agent.md`를 정본 문서 모델에 맞게 정렬

## 다음 운영 작업

- 별도 범위 승인 전까지 필수 구현 마일스톤 없음

## 검증 누적

- `agent-docs/validation/b1-repository-baseline-validation.md`
- `agent-docs/validation/b2-run-ledger-validation.md`
- `agent-docs/validation/b3-worktree-allocator-validation.md`
- `agent-docs/validation/b4-codex-adapter-validation.md`
- `agent-docs/validation/b5-failure-signal-validation.md`
- `agent-docs/validation/b6-telegram-notifier-validation.md`
- `agent-docs/validation/b7-pr-handoff-validation.md`
- `agent-docs/validation/stage-c-acceptance-validation.md`
- `agent-docs/validation/m5-supervisor-runtime-validation.md`
- `agent-docs/validation/m6-daemon-runtime-validation.md`
- `agent-docs/validation/m7-crash-recovery-validation.md`
- `agent-docs/validation/m8-service-packaging-validation.md`
- `agent-docs/validation/m9-progress-monitor-validation.md`


## 이후로 미루는 항목

- multi-runner 지원
- 추가 notifier 채널
- path-scoped concurrent run
- auto-merge
- dashboard UX
