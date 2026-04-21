# 정책 기준선

날짜: 2026-04-21
상태: 구현 진행 중

## 이 문서의 역할

이 문서는 `codexmon`의 정본 v1 정책 기준선이다. orchestrator가 반드시
deterministic하게 강제해야 하는 최소 retry, timeout, approval, lock, audit,
halt 동작을 고정한다.

## 재시도 예산

- automatic retry budget: `1`
- run당 최대 autonomous attempt 수: `2`
- automatic retry는 `analyzing_failure` 이후에만 허용된다
- 같은 normalized failure fingerprint가 이미 같은 run 안에서 나타났다면
  automatic retry를 허용하지 않는다
- operator가 트리거하는 `retry`는 halt 또는 pause 이유가
  `retryable-by-human`으로 분류된 경우에만 허용된다

retry budget은 runner가 아니라 orchestrator가 소유한다.

## 시간 초과 규칙

- idle timeout: `15 minutes`
- attempt당 wall-clock timeout: `120 minutes`
- notifier 전달 지연은 어느 timeout도 연장하지 않는다

idle timeout은 의미 있는 runner 진행이 해당 기간 동안 전혀 없다는 뜻이다.
진행 신호의 최소 집합은 아래다.

- 새로운 stdout 또는 stderr 활동
- 새로운 structured runner event
- 할당된 worktree에서의 새로운 file diff 활동

위 셋이 `15 minutes` 동안 하나도 없으면 run은 idle failure event를 발생시키고
`running`에서 빠져나와야 한다.

## 실패 지문 중단 규칙

orchestrator는 각 실패 attempt를 가장 작은 안정 집합으로 정규화하여
failure fingerprint를 만든다. 최소 포함 요소는 아래다.

- 실패한 command 또는 check 이름
- exit code 또는 failure class
- 지배적인 error token 또는 최상위 failing test target

같은 normalized fingerprint가 한 run 안에서 두 번 관찰되면, run은 즉시
`blocked with explicit reason`으로 halt해야 한다.

이 halt는 automatic retry 대상이 아니다.

## 잠금 모델

v1은 coarse하고 deterministic한 lock 모델을 사용한다.

- repository-wide autonomous execution lock 하나
- repository당 active autonomous run 하나
- lock은 `preflight` 단계에서 획득한다
- lock은 final transition과 persistence flush가 끝난 뒤에만 해제한다

path-scoped concurrency는 이후 과제로 미룬다. active worktree 밖의 human edit는
막지 않지만, repository-wide autonomous lock 규칙은 그대로 유지한다.

## 승인 필요 변경 분류

run은 아래 변경 class가 감지되면 사람 승인 대기로 멈춰야 한다.

- schema 또는 migration 파일
- authentication 또는 authorization 로직
- infrastructure, deployment, CI workflow 설정
- secret 또는 sensitive configuration 처리
- dependency manifest 또는 lockfile 변경
- `5 files` 초과 또는 `300 lines` 초과 삭제

분류는 deterministic하고 diff 기반이어야 한다. listed class와 매칭되면
orchestrator는 `awaiting_human`으로 이동해야 한다.

## 금지 동작

명시적 operator 승인 없이는 아래를 절대 수행하면 안 된다.

- force-push
- auto-merge
- reset-hard 류의 destructive Git cleanup
- supervisor 자체 state store와 log를 제외하고 할당된 worktree 밖에 쓰기
- supervised runner session 안에서 policy 파일 수정
- repository execution lock 우회
- kill-switch 이후 계속 진행

forbidden은 runner에게 "조심하라"고 요청하는 문제가 아니라, orchestrator가
즉시 멈춰야 하는 조건이다.

## PR 인계 게이트

run은 아래가 모두 참일 때만 PR을 열 수 있다.

- run state가 `pr_handoff`다
- 미해결 approval requirement가 없다
- forbidden-action 위반이 없다
- run branch와 worktree가 persistence에 기록돼 있다
- 해당 repository의 required local check bundle이 통과했다
- task summary, changed file, check result, residual risk note를 포함한
  handoff summary가 존재한다

GitHub CI visibility는 PR 생성 이후 기록한다. CI 성공은 v1에서 정보일 뿐이며,
auto-merge 권한을 부여하지 않는다.

## Audit Log 최소 필드

structured audit record는 최소한 아래 필드를 담아야 한다.

- `task_id`
- `run_id`
- `attempt_number`
- `event_time`
- `actor_type`
- `actor_id`
- `state_from`
- `state_to`
- `reason_code`
- `instruction_summary`
- `workspace_path`
- `branch_name`
- `runner_signal`
- `failure_fingerprint`
- `approval_request_id`
- `approval_result`
- `changed_files_summary`
- `check_summary`
- `telegram_message_ref`
- `pr_reference`

## Kill-Switch 동작

kill switch는 로컬 CLI 또는 `Telegram` `stop`으로 트리거될 수 있다.

발동 시 orchestrator는 아래 순서를 따라야 한다.

1. kill-switch event 저장
2. 새로운 runner command 시작 즉시 중단
3. active runner process에 interrupt 전송
4. runner가 제때 종료하지 않으면 process termination으로 승격
5. 최종 halt transition과 reason 저장
6. operator에게 통지
7. persistence가 durable해진 뒤에만 repository lock 해제

kill switch는 추후 분석을 위해 worktree와 audit trail을 보존한다.

## 로컬 점검 정책

제품 수준 규칙은 고정하지만, repository별 command는 고정하지 않는다.

- 각 supervised repository는 PR handoff 전에 fast local check bundle을 선언해야 한다
- `codexmon`은 repository check를 자동 탐지하거나 임의 생성하지 않는다
- check bundle이 구성되지 않았다면 run은 `PR opened`를 주장할 수 없다

이 규칙은 하나의 보편적 test command를 강제하지 않으면서도 제품 계약을
deterministic하게 유지한다.
