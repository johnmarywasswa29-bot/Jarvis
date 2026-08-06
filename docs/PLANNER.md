# Planner V3

Plan validation and structured execution metadata.

## Inputs

- Prefer active goal context when available
- Legacy string plans are still accepted where needed

## Outputs

- Ordered `PlanStep` list with confidence, estimated duration, confirmation flags

## Rules

- Step IDs must be unique
- No missing dependencies as references

