# Goal Manager

Structured long-horizon progress tracking.

## Entities

- `Goal` with `steps`, `completed_steps`, `status`, `created_at`, `updated_at`
- IDs persist across restarts via `GoalStorage`

## Lifecycle

PENDING → ACTIVE → DONE | FAILED

## Integrations

- Planner can read active goals for goal-aware plans
- Task completion updates goal progress by matching `task.goal_id` and `task.step_id`
