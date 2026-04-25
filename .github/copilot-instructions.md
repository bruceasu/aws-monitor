# .github/copilot-instructions.md

## Repository onboarding for Copilot

This is a Python codebase maintained with human review and AI assistance.
Prioritize readability, correctness, scoped changes, and verification.

Read `AGENTS.md` first.
This file adds Copilot/GitHub workflow guidance.

---

## Default Behavior

### Plan before coding
For non-trivial tasks, first provide:
- understanding of the task
- affected modules / packages
- assumptions
- missing information
- implementation plan
- risks
- validation steps

Do not jump directly into implementation for medium or large changes.

### Keep changes reviewable
- prefer small diffs
- avoid unrelated cleanup
- make behavior changes explicit
- explain non-obvious decisions

---

## Python Repository Expectations

- follow existing package structure
- keep business logic out of framework glue where possible
- preserve configuration patterns already in the repo
- prefer explicit and testable code
- preserve typing and linting conventions where present

Treat dependency and environment changes as high-risk.

---

## Testing and Validation

Every non-trivial change should be validated.

Preferred order:
1. format
2. lint
3. typecheck
4. unit tests
5. integration tests

If available, prefer:
```bash
./scripts/verify.sh
```

Otherwise use repository-standard tools such as:
```bash
pytest
ruff check .
ruff format --check .
mypy .
```
Include in final summaries and PR descriptions:

- validation commands run
- pass/fail status
- anything not verified


## High-Risk Areas

Before making changes, explicitly call out risk when touching:

- dependencies
- environment/configuration
- auth/security
- migrations/data model
- async/concurrency behavior
- deployment or CI config

These changes should include stronger validation and clearer explanation.

## PR / Issue Alignment

When implementing from a task or issue:

- align to the stated goal
- respect non-goals
- do not silently expand scope
- identify follow-up work separately

Recommended final summary structure:

- summary
- affected modules
- tests / validation
- risks
- follow-ups
- harness improvements suggested

## Documentation updates

When behavior, setup, or developer workflow changes:
- update relevant docs
- update examples if applicable
- mention migration or rollout considerations


---

## Response format for non-trivial tasks

Use this structure where possible:

### Understanding
### Assumptions
### Plan
### Implementation
### Validation
### Risks
### Follow-ups

---

## Harness Engineering feedback loop

If a task exposed any weakness in the engineering harness, report it explicitly.

Examples:
- unclear requirements
- missing module ownership docs
- absent test harness for a critical path
- weak architectural guardrails
- no single verification entrypoint

Label these as:

`Harness improvements suggested`