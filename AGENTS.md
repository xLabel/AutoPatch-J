# Repository Guidelines

## Project Structure & Module Organization

AutoPatch-J is a Python CLI agent for evidence-guided Java code repair. Source code lives in `src/autopatch_j/`. Key packages include `cli/` for command routing and workflows, `agent/` for ReAct execution and task profiles, `tools/function_calls/` for LLM-callable tools, `core/` for domain services, patching, review state, project indexing, and memory, `llm/` for provider adapters, and `scanners/` for static scan integrations. Tests live in `tests/`. Demo inputs are in `examples/`, and design notes are in `docs/`.

## Build, Test, and Development Commands

- `pip install -e .[test]`: install the package in editable mode with test dependencies.
- `pytest -q`: run the full regression suite.
- `pytest tests/test_source_read_tools.py -q`: run a focused test file.
- `autopatch-j`: start the CLI after installation.

The project reads configuration from environment variables such as `AUTOPATCH_LLM_API_KEY`, `AUTOPATCH_LLM_BASE_URL`, and `AUTOPATCH_LLM_MODEL`.

## Coding Style & Naming Conventions

Use Python 3.10+ typing and keep changes scoped to the current task. Prefer existing local patterns over new abstractions. Use `snake_case` for functions, variables, and modules; `PascalCase` for classes; and enum-like tool names in `FunctionToolName`. Keep comments short and only where they clarify non-obvious behavior.

## Testing Guidelines

Tests use `pytest`. Add focused tests for behavior changes, especially tool schemas, task profiles, path/focus guardrails, patch generation, and source-reading edge cases. Name tests with `test_...` and keep fixtures local unless reused. Before finishing a code change, run the relevant focused tests; run `pytest -q` for broad refactors.

## Agent-Specific Instructions

Read this file before making repository changes. Do not edit tracked files until the user explicitly authorizes implementation. Without authorization, limit work to read-only analysis, planning, and recommendations. Keep local or personal instructions in `AGENTS.local.md`; do not commit that file.

## Commit & Pull Request Guidelines

Commit only after user review or explicit commit approval. Stage only files changed for the current task, using precise paths and `git status --short` before committing.

Commit messages must use `<type>: <lowercase english phrase>`. Allowed types include `fix`, `feat`, `refactor`, `docs`, `test`, and `chore`. The subject must be English only, all lowercase, and must not reference identifiers that introduce uppercase letters.

Examples:

- `fix: handle empty source context`
- `test: cover source block fallback`
- `refactor: split source reading tools`

Pull requests should describe the behavioral change, list verification commands, and note any user-facing workflow or tool-schema changes.
