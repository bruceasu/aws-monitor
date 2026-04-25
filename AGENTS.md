# AGENTS.md
Always repond in Chinese.


## Purpose
This Python repository is maintained with human oversight and AI coding agents.
Optimize for correctness, readability, explicit behavior, and safe incremental changes.

---

## Core Working Rules

### 1. Plan first for non-trivial work
Before modifying files, first provide:
1. Task understanding
2. Relevant modules / packages
3. Assumptions
4. Missing information
5. Proposed implementation plan
6. Risks
7. Validation steps

Do not directly edit code for medium or large tasks.

### 2. Keep changes scoped and reviewable
Only change files relevant to the task.
Avoid opportunistic cleanup or broad refactors unless explicitly required.

### 3. Make behavior explicit
Prefer explicit code and predictable control flow.
Avoid hidden side effects, implicit global state changes, and overly clever abstractions.

### 4. Treat dependencies carefully
Dependency changes are high-risk.
Request approval before:
- adding packages
- changing pinned versions
- changing Python version targets
- changing packaging or toolchain config

### 5. Make every change verifiable
Every meaningful change should include:
- relevant tests
- lint / format checks
- type checks where configured
- runtime verification where appropriate

---

## Python-Specific Engineering Rules

### Structure
- Follow the existing project layout and module boundaries.
- Keep business logic out of framework entrypoints when possible.
- Avoid mixing CLI/UI/web concerns with domain logic.

### Readability
- Prefer straightforward code over clever shortcuts.
- Use clear names and explicit control flow.
- Keep functions focused and testable.

### Typing
- Preserve and improve type hints where the project already uses them.
- Do not remove useful type hints without reason.
- Treat typed codepaths as important verification surfaces.

### Error Handling
- Do not swallow exceptions silently.
- Preserve error semantics unless the task explicitly changes them.
- Add context to raised exceptions only when it improves debuggability without obscuring the original cause.

### Configuration
- Respect existing configuration patterns:
  - environment variables
  - settings objects
  - config files
- Do not introduce a new configuration style casually.

### Dependencies
Treat changes to:
- `pyproject.toml`
- `requirements.txt`
- `requirements-dev.txt`
- `poetry.lock`
- `uv.lock`
- `Pipfile`
as high-risk and call them out explicitly.

---

## Testing Expectations

Prefer:
1. format
2. lint
3. typecheck
4. unit tests
5. integration tests
6. targeted runtime verification if relevant

Add or update tests for:
- edge cases
- error handling
- serialization / parsing logic
- framework endpoints when relevant
- command behavior for CLI tools when relevant

---

## High-Risk Changes

Pause and explain before:
- dependency changes
- packaging changes
- environment/configuration changes
- auth / permission changes
- data model or migration changes
- concurrency / async behavior changes
- deployment or CI changes

Explain:
- what changes
- why it is needed
- what could break
- how it will be validated

---

## Preferred Workflow

### Phase 1: Understand
Identify:
- entrypoints
- core modules
- tests affected
- configuration surfaces

### Phase 2: Plan
Propose a concise and reviewable plan.

### Phase 3: Implement
Apply the smallest correct change.
Preserve nearby style and conventions.

### Phase 4: Verify
Use repository-standard commands first.

### Phase 5: Report
Summarize:
- files changed
- behavior changed
- assumptions
- risks
- validation
- harness improvements suggested

---

## Validation

Prefer:
```bash
./scripts/verify.sh
```


If unavailable, use repository-standard tooling, for example:
```bash
pytest
ruff check .
ruff format --check .
mypy .
```

Only run commands that match the repository tooling.

Do not claim success without reporting what actually ran.

## Documentation Expectations

Update docs when changing:

- CLI behavior
- API behavior
- environment variables
- setup instructions
- configuration
- developer workflow

Communication Style

For non-trivial tasks, structure responses as:

- Understanding
- Relevant Modules
- Assumptions
- Plan
- Implementation
- Validation
- Risks
- Harness improvements suggested