# CODEX.md

## Role
You are operating as a local coding agent for a Python codebase.
Follow AGENTS.md first, then apply these Python-specific execution preferences.

---

## Default Mode

For medium or high complexity work, begin in planning mode.
Before editing files, provide:
- Understanding
- Relevant modules / packages
- Assumptions
- Missing information
- Step-by-step plan
- Risks
- Validation commands

Prefer `/plan` first for:
- new features
- refactors
- dependency changes
- async/concurrency changes
- framework or configuration changes

---

## Execution Preferences

### Preferred sequence
1. inspect only relevant files
2. identify affected modules, entrypoints, tests, and config
3. produce a plan
4. apply minimal code changes
5. add or update tests
6. run validation
7. summarize results and remaining risks

### Editing rules
- keep changes localized
- preserve module boundaries
- follow existing typing style
- avoid broad rewrites
- prefer simple, explicit code

---

## Python-Specific Guidance

### Dependency and Environment Management
Treat changes to package/dependency files as high-risk.
Do not casually change dependency managers or environment tooling.

### Typing
If the project uses type hints and static checking, preserve that pattern.
Prefer adding type hints for new public functions where consistent with the codebase.

### Async / Concurrency
Treat async behavior, threading, multiprocessing, and task queues as risk-sensitive.
If changing these areas, explicitly call out:
- execution model changes
- race-condition risks
- shutdown/retry semantics
- validation strategy

### Frameworks
Follow existing framework conventions.
Do not introduce a new architectural style casually.

---

## Approval Required Before

- adding or changing dependencies
- changing packaging / build config
- changing Python version targets
- changing auth/security logic
- changing schema / migrations
- changing deployment or CI config
- broad refactors across many modules

---

## Validation Preferences

Prefer:
```bash
./scripts/verify.sh
```

## Otherwise use repository-standard commands only.

Common examples:
```bash
pytest
ruff check .
ruff format --check .
mypy .
```
Always report:

- exact commands run
- pass/fail results
- what remains unverified

## Output Format

Use this structure for non-trivial tasks:

### Understanding
### Relevant Modules
### Assumptions
### Plan
### Implementation
### Validation
### Risks
### Harness improvements suggested


## Definition of Done

A task is not done until:

- the requested behavior is implemented
- relevant tests are updated or added
- dependency/config impacts are called out
- validation is run or limitations are explicitly stated
- docs are updated if needed

## Forbidden Shortcuts

Do not:

- skip tests silently
- add dependencies casually
- change runtime/config behavior without explanation
- perform hidden broad refactors
- remove type hints without reason
- claim correctness without evidence


