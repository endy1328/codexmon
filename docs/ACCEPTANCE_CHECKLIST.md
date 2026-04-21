# 인수 기준 체크리스트

날짜: 2026-04-21
상태: 구현 진행 중

## 이 문서의 역할

이 문서는 첫 비공개 데모를 위한 정본 인수 체크리스트다.
마케팅 요약이 아니라 검증 산출물로 작성한다. 아래 각 항목은 관찰 가능한
증거가 있을 때만 통과로 간주한다.

## 1. 작업 시작

- [ ] task 시작 시 persisted task record와 run record가 생성된다.
- [ ] CLI가 이후 `status`, `stop`, `retry`에 필요한 run identifier를 반환한다.
- [ ] run이 persisted state history에서 `queued` 다음 `preflight`로 진입한다.

증거:
- `SQLite`의 task, run, transition row
- run id를 보여주는 CLI 출력

## 2. Worktree 격리

- [ ] run이 전용 worktree와 branch를 할당한다.
- [ ] branch 이름이 run id와 추적 가능하다.
- [ ] repository lock이 잡힌 동안 같은 repository에 대한 두 번째 autonomous run은 거부된다.

증거:
- persisted worktree 및 branch assignment
- worktree가 실제로 존재함을 보여주는 filesystem 상태
- 충돌하는 두 번째 run에 대한 lock rejection event

## 3. Run 상태 전이

- [ ] 정상 run은 `queued`에서 `completed`까지 허용된 transition path를 따른다.
- [ ] 실패 또는 block된 run은 `docs/STATE_MACHINE.md`가 허용한 transition만 사용한다.
- [ ] 유효하지 않은 transition은 조용히 수용되지 않고 error로 저장된다.

증거:
- structured event log의 transition history
- 정본 상태 머신과 대조한 검증자 결과

## 4. Stall 및 Loop 감지

- [ ] `15 minutes` 동안 진행 신호가 없으면 deterministic failure signal이 발생한다.
- [ ] 동일한 failure fingerprint가 반복되면 run이 halt한다.
- [ ] automatic retry는 최대 한 번만, 정책이 허용할 때만 수행된다.

증거:
- reason code가 포함된 persisted timeout event
- 중복 감지를 보여주는 persisted failure fingerprint 기록
- retry budget이 지켜졌음을 보여주는 attempt count history

## 5. Telegram 알림

- [ ] `Telegram`이 시작, halt, approval 대기, 완료 상태 변화를 수신한다.
- [ ] 원격 `status`가 현재 run summary를 반환한다.
- [ ] 원격 `stop`, `retry`, `approve`가 persisted operator event로 orchestrator에 들어온다.

증거:
- notifier delivery log 또는 message reference
- persisted operator action event

## 6. 사람 개입 경로

- [ ] approval-required change가 감지되면 run이 `awaiting_human`으로 이동한다.
- [ ] 자율 세션은 외부 결과 `needs human decision`을 노출한다.
- [ ] operator `approve` 또는 `retry`는 허용된 transition을 통해서만 재개된다.

증거:
- approval-required change에 대한 diff classification event
- `awaiting_human` 진입 transition
- operator action 이후의 재개 transition history

## 7. PR Handoff

- [ ] 성공한 run은 run id와 연결된 PR reference를 생성한다.
- [ ] PR 본문에 task summary, changed-files summary, local check result,
  residual risk note가 포함된다.
- [ ] PR 생성 이후 CI visibility가 노출된다.

증거:
- persisted PR reference
- PR body 내용
- PR 생성 이후의 CI status snapshot 또는 event

## 8. Bounded Halt 동작

- [ ] kill-switch 또는 operator stop이 runner를 중단하고 worktree를 보존한다.
- [ ] 최종 결과는 `blocked with explicit reason`이다.
- [ ] halt event가 durable해진 뒤에만 repository lock이 해제된다.

증거:
- persisted halt reason 및 final transition
- 보존된 worktree 경로
- persistence flush 이후의 lock release event

## 인수 규칙

첫 데모는 위 모든 항목이 단일 종단 간 검증 실행에서 통과하고, 그 결과를
persisted audit data로 재구성할 수 있을 때만 수용한다.
