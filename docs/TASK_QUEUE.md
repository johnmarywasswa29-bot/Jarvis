# Task Queue

Task execution with priorities, states, retries, dependencies, and persistence.

## Status flow

PENDING → READY | BLOCKED | WAITING  
READY → RUNNING  
RUNNING → WAITING | COMPLETED | FAILED  
BLOCKED → READY when dependencies completed  
FAILED → READY via retry or requeue_failed  
ANY terminal → CANCELLED

## Dependencies

- Task IDs in `depends_on` must reach COMPLETED before dependency statuses are promoted
- Dependency cycles are rejected at enqueue

## Scheduling

- `scheduled_time` tasks enter WAITING until `tick()` promotes them
- `TaskScheduler` supports delayed callbacks

## Integration

- Goal progress updates are triggered from completion via optional `goal_manager`
- Events publish through `TaskEventBus`
