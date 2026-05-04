# AGENTS.md

Repo-wide guidance for coding agents working in this repository.

## Working Rules

- When discussing behavior or design, inspect the current source files first. The code is the source of truth; both this file and `docs/` may lag behind. On this redesign branch, `drafts/` is the up-to-date (partial) design-of-record for in-flight work.
- When the user asks for a documentation update or a design discussion, do not make code changes unless they explicitly ask for implementation.
- When asked to commit, make a single commit unless the user explicitly asks to split the work.
- Commit messages should be a one-line summary only, with no body and no `Co-Authored-By`.
- C++ source files must contain only ASCII characters.
- Do not create custom C++ namespaces in this project.
- Don't use `--` as punctuation in comments or docs. Prefer `;`, `:`, parentheses, or separate sentences.
- Assume C++ classes are non-copyable and non-movable unless the class definition explicitly says otherwise. The codebase doesn't use `= delete` ceremony, so this rule is the only signal. When adding a class or reviewing a module, check that nothing copies or moves a resource owner; flag it if it does.

## Pipeline

```
DSL (.py) -> Python compiler -> binary blob -> C++ decoder -> Program -> engine -> compositor -> strip
```

## Key Invariants

- The controller owns canonical session time; devices follow it.
- A session survives device detach; transport attachment and active serving are tracked separately.
- `source_layer < dependent_layer` ordering is compiler-enforced; runtime code does not re-validate it.
- Alpha is per-pixel in `hsva_t`, not a per-layer compositor setting.

## Test Gotchas

- `pytest.ini` adds `controller/` and `compiler/` to `PYTHONPATH`, so run Python tests from the repo root unless you have a specific reason not to.
- Tests marked `runtime_integration` expect an isolated local runtime: no other `./elemctl server`, `./elemctl sim`, or legacy `network_sim` processes should be running unless you intentionally bypass the guard with `ELEMCTL_TEST_ALLOW_BUSY_RUNTIME=1`.
- C++ test fixtures under `test/fixtures/` are CMake-generated from the Python compiler. If you change compiler output or fixture-generation code, regenerate before trusting C++ test results.
