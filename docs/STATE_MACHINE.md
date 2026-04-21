# 상태 머신

날짜: 2026-04-21
상태: 구현 진행 중

## 이 문서의 역할

이 문서는 `codexmon`의 정본 v1 run-state 모델이다. deterministic state,
허용된 transition, 그리고 내부 run state가 `docs/FOUNDATION.md`의 외부 종료
결과 계약에 어떻게 매핑되는지를 정의한다.

## 상태 집합

### `queued`

task가 CLI에서 접수됐지만 preflight가 아직 시작되지 않은 상태다.

### `preflight`

orchestrator가 아래 선행 조건을 검증하는 상태다.

- repository 접근 가능 여부
- lock 획득 가능 여부
- notifier 및 GitHub 설정 존재 여부
- persistence 사용 가능 여부

### `workspace_allocated`

repository lock이 잡혀 있고, run 전용 worktree와 branch가 준비된 상태다.

### `running`

`Codex` adapter가 할당된 worktree 안에서 active runner process를 갖고 있는 상태다.

### `analyzing_failure`

runner가 실패했거나 deterministic failure signal이 발생했고, orchestrator가
이를 policy 기준으로 분류하는 상태다.

### `awaiting_human`

operator 결정이 필요해서 자동화가 멈춘 상태다. 사람이 `approve`, `retry`,
`stop` 중 하나를 내리기 전까지 전진하지 않는다.

### `retry_pending`

정책상 retry가 허용되어, 같은 run record 안에서 다음 attempt를 준비 중인 상태다.

### `pr_handoff`

코드 실행은 끝났고, 시스템이 PR handoff를 만드는 단계다.

- run summary
- changed-files summary
- branch association
- PR 생성 요청

### `completed`

run이 성공적으로 끝났고 PR이 실제로 존재하는 상태다.

### `halted`

orchestrator가 명시적 이유를 남기고 자동화를 종료한 상태다.

### `cancelled`

완료 전에 의도적으로 중지되었고 더 이상 active하지 않은 상태다.

## 허용된 전이

| 출발 | 트리거 | 도착 | 설명 |
| --- | --- | --- | --- |
| `queued` | task accepted | `preflight` | 작업 시작 전 run record가 이미 존재한다. |
| `preflight` | preflight passed | `workspace_allocated` | lock과 workspace가 준비됐다. |
| `preflight` | preflight failed | `halted` | 결과는 `blocked with explicit reason`이다. |
| `workspace_allocated` | runner launched | `running` | attempt 1이 시작된다. |
| `running` | success path reached | `pr_handoff` | local gate 통과, approval debt 없음. |
| `running` | failure, timeout, or loop signal | `analyzing_failure` | 다음 단계 전에 실패를 정규화한다. |
| `running` | approval-required change detected | `awaiting_human` | 결과는 `needs human decision`이 된다. |
| `running` | operator stop or kill switch | `halted` | 이유는 명시적으로 저장된다. |
| `analyzing_failure` | retry allowed | `retry_pending` | automatic retry budget과 fingerprint rule이 통과해야 한다. |
| `analyzing_failure` | human decision required | `awaiting_human` | recovery 경로가 모호하거나 approval이 필요하다. |
| `analyzing_failure` | retry denied | `halted` | 결과는 `blocked with explicit reason`이다. |
| `retry_pending` | runner relaunched | `running` | attempt count가 증가한다. |
| `awaiting_human` | operator approves continuation | `retry_pending` | blocked policy edge가 approval로 해제된다. |
| `awaiting_human` | operator retries | `retry_pending` | policy가 `retryable-by-human`으로 표시한 경우만 허용된다. |
| `awaiting_human` | operator stops | `cancelled` | 사람이 run을 의도적으로 끝낸다. |
| `pr_handoff` | PR opened successfully | `completed` | 결과는 `PR opened`다. |
| `pr_handoff` | PR creation failed without safe retry | `halted` | 결과는 `blocked with explicit reason`이다. |
| 모든 비종단 상태 | kill switch | `halted` | 이후 runner command를 더 시작할 수 없다. |

## 전이 불변식

- run은 `workspace_allocated` 이전에 `running`으로 갈 수 없다
- run은 `pr_handoff`를 거치지 않고 `completed`로 갈 수 없다
- `halted`, `completed`, `cancelled`는 빠져나올 수 없다
- retry budget이 소진된 뒤에는 `running`으로 다시 들어갈 수 없다
- 모든 transition은 후속 부작용을 완료로 간주하기 전에 먼저 저장돼야 한다

## 종료 결과 매핑

제품 계약은 세 가지 외부 결과를 사용한다. 내부 state는 아래처럼 매핑된다.

- `completed`는 `PR opened`에 매핑된다
- `halted`와 `cancelled`는 `blocked with explicit reason`에 매핑된다
- `awaiting_human`은 자율 세션 관점에서 `needs human decision`에 매핑된다

`awaiting_human`은 운영적으로는 pause state이지만, unattended execution 관점에서는
이미 종료 결과에 해당한다.

## 필수 상태 메타데이터

각 run state projection은 최소한 아래 필드를 노출할 수 있어야 한다.

- `run_id`
- `task_id`
- `attempt_number`
- `current_state`
- `state_reason`
- `active_worktree`
- `active_branch`
- `last_failure_fingerprint`
- `approval_status`
- `pr_reference`
- `updated_at`

## 허용되지 않는 경로

orchestrator는 아래 경로를 거부해야 한다.

- `queued -> running`
- `preflight -> completed`
- `running -> completed`
- `awaiting_human -> completed`
- `halted -> retry_pending`

halt 이후 retry는 transition rule을 우회하면 안 된다. 새로운 retry authorization
event를 생성하고 policy를 거쳐 다시 진입해야 한다.
