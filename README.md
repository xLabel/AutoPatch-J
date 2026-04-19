# AutoPatch-J

Minimal AI coding patch agent.

Current checkpoint:

- interactive CLI shell
- `/init` project bootstrap
- `.autopatch/` local state
- repository indexing
- `@mention` path resolution with interactive disambiguation
- `@query` + `Tab` path autocomplete when `readline` is available
- scanner adapter abstraction with local Semgrep Java rules
- prompt-driven scan routing
- prompt-driven patch drafting from active findings
- prompt-driven pending patch apply confirmation
- Semgrep wrapper with normalized findings artifacts
- pending edit review/apply gate
- OpenAI-compatible LLM planner with streaming tool-call aggregation

## Run

```bash
python3 scripts/bootstrap_local_runtime.py
.venv/bin/python -m autopatch_j
```

The bootstrap creates a local `.venv`, installs AutoPatch-J in editable mode, installs the Python dependencies declared by this project, installs `semgrep`, and creates the runtime launcher under `runtime/semgrep/bin/<platform>/semgrep`.

## Test

```bash
python3 -m unittest discover -s tests -t . -v
```

Inside the shell:

```text
/init
/status
/tools
@UserService<Tab> scan this file
@src/main/java/com/foo/UserService.java scan this file
/reindex
列出问题
修复第1个问题
@src/main/java/com/foo/UserService.java 生成 patch
看看 patch
应用这个patch
```

## Scan behavior

- if the prompt contains scan intent and includes `@mention`, AutoPatch-J scans that scope
- if the prompt contains scan intent without `@mention`, AutoPatch-J scans the whole repository
- typing `@query` and pressing `Tab` can autocomplete repository paths; blank `@` prefers recent mentions first
- selected `@mention` files are truncated into code snippets and injected into decision/draft prompts
- run `/reindex` after the repository adds, deletes, or renames files so `@mention` candidates stay fresh
- scanner execution now goes through a Java scanner adapter; current supported backend: `semgrep`
- default Semgrep config is the local Java rule set at `runtime/semgrep/rules/java.yml`
- AutoPatch-J only executes Semgrep from `runtime/semgrep/bin/<platform>/semgrep`
- environment overrides, virtualenv executables, and shell `PATH` are intentionally ignored for scanner execution
- semgrep subprocess state, settings, logs, and cache are localized under `.autopatch/runtime/semgrep` instead of `~/.semgrep`
- use `/tools` to inspect local scanner and validator readiness
- natural-language scan decisions go through the LLM planner; there is no rule-based scan fallback
- prompt-level review requests such as `列出问题` reuse the current findings artifact instead of forcing a re-scan
- after findings are loaded, prompt-level patch requests such as `修复第1个问题` can draft a pending edit
- if multiple findings are active, AutoPatch-J asks for a finding index or a narrower `@mention`

## Runtime Diagnosis

- run `/status` for project readiness plus current work summary
- run `/tools` to inspect whether the current machine is ready for:
  - repository initialization
  - scanner execution through `runtime/semgrep/bin/<platform>/semgrep`
  - Tree-sitter Java syntax validation through Python modules
  - LLM planning
  - LLM patch drafting

If the runtime Semgrep launcher is missing, the CLI returns a clear error and keeps session state intact.

Tree-sitter validation is a Python dependency, not an npm dependency. The project declares:

- `tree-sitter`
- `tree-sitter-java`

The bootstrap script installs those packages into the project-local `.venv`.

## Demo fixture

- a runnable sample repository lives at `examples/demo-repo`
- inside that sample repo, run `/init`, then use `扫描整个仓库的问题`
- the sample contains two Java findings, which makes it easy to demonstrate:
  - whole-repo scan
  - `@mention` narrowing
  - one-finding patch drafting
  - pending diff review
  - apply + ReScan validation

## Edit review gate

- prompt-level patch requests ask the model to draft one minimal search-replace edit for a selected finding
- if the first draft misses the unique match or introduces Java syntax errors, AutoPatch-J feeds the preview result back once and retries one corrected draft
- prompt `看看 patch` also shows the current pending diff
- prompt `应用这个patch` also applies the current pending edit
- Java edits require Tree-sitter validation before apply; if `tree_sitter` or `tree_sitter_java` is missing, preview still works but apply is blocked
- after apply, AutoPatch-J records a post-apply ReScan validation artifact

## Decision Engine

- default without an LLM key: natural-language agent actions are unavailable
- with an OpenAI-compatible endpoint, the LLM planner chooses `scan`, `draft_patch`, or `respond`
- planner calls use streaming so reading and routing do not feel frozen
- patch drafting uses non-streaming JSON mode because the full JSON object must be parsed before preview
- optional environment variables:
  - `AUTOPATCH_LLM_API_KEY`
  - `AUTOPATCH_LLM_BASE_URL`
  - `AUTOPATCH_LLM_MODEL`
  - `OPENAI_API_KEY` and `OPENAI_BASE_URL` are accepted as compatibility aliases
