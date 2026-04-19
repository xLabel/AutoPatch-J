# AutoPatch-J

Minimal AI coding patch agent.

Current checkpoint:

- interactive CLI shell
- `/init` project bootstrap
- `.autopatch/` local state
- repository indexing
- `@mention` path resolution with interactive disambiguation
- `@query` + `Tab` path autocomplete when `readline` is available
- scanner adapter abstraction with configurable backend
- prompt-driven scan routing
- prompt-driven patch drafting from active findings
- prompt-driven pending patch apply confirmation
- Semgrep wrapper with normalized findings artifacts
- pending edit review/apply gate
- optional OpenAI `Responses API` decision engine

## Run

```bash
python3 -m autopatch_j
```

Inside the shell:

```text
/init .
/env
/scanner
/scanner semgrep rules/demo.yml
@UserService<Tab> scan this file
@src/main/java/com/foo/UserService.java scan this file
/reindex
/status
/show-findings
列出问题
修复第1个问题
@src/main/java/com/foo/UserService.java 生成 patch
/draft-fix 1
/draft-edit Demo.java "guard string compare"
/preview-edit Demo.java "call();" "safeCall();"
/show-pending
看看 patch
应用这个patch
/apply-pending
/show-validation
```

## Scan behavior

- if the prompt contains scan intent and includes `@mention`, AutoPatch-J scans that scope
- if the prompt contains scan intent without `@mention`, AutoPatch-J scans the whole repository
- typing `@query` and pressing `Tab` can autocomplete repository paths; blank `@` prefers recent mentions first
- selected `@mention` files are truncated into code snippets and injected into decision/draft prompts
- run `/reindex` after the repository adds, deletes, or renames files so `@mention` candidates stay fresh
- scanner execution now goes through a Java scanner adapter; current supported backend: `semgrep`
- set `AUTOPATCH_SCANNER=semgrep` to select the current backend explicitly
- set `AUTOPATCH_SEMGREP_CONFIG` to override the default Semgrep config (`p/java`)
- use `/scanner` to inspect the active scanner and project-level overrides
- use `/scanner semgrep [config]` to persist the scanner choice into the project
- use `/scanner reset` to clear project overrides and fall back to env/defaults
- current fallback routing is keyword-based; if `OPENAI_API_KEY` is present, scan decisions can go through OpenAI
- prompt-level review requests such as `列出问题` reuse the current findings artifact instead of forcing a re-scan
- after findings are loaded, prompt-level patch requests such as `修复第1个问题` can draft a pending edit
- if multiple findings are active, AutoPatch-J asks for a finding index or a narrower `@mention`

## Runtime diagnosis

- run `/env` to inspect whether the current machine is ready for:
  - repository initialization
  - scanner execution
  - Tree-sitter Java syntax validation
  - OpenAI decision routing
  - OpenAI patch drafting

The scan wrapper expects `semgrep` on `PATH`. If it is missing, the CLI returns a clear error and keeps session state intact.

## Edit review gate

- `/draft-fix` asks the model to draft one minimal search-replace edit for a selected finding
- `/draft-edit` asks the model to propose one minimal search-replace edit for a target file
- `/preview-edit` only previews a `search-replace` edit and stores it as pending
- `/show-pending` shows the current pending diff
- prompt `看看 patch` also shows the current pending diff
- `/apply-pending` writes the pending edit to disk
- `/clear-pending` drops the pending edit without changing files
- prompt `应用这个patch` also applies the current pending edit
- Java edits require Tree-sitter validation before apply; if `tree_sitter` or `tree_sitter_java` is missing, preview still works but apply is blocked
- after apply, AutoPatch-J records a post-apply ReScan validation artifact
- `/show-validation` shows the latest post-apply validation result

## Decision engine

- default: rule-based routing
- if `OPENAI_API_KEY` is present, AutoPatch-J switches to an OpenAI `Responses API` decision engine
- the same API key also enables the OpenAI edit drafter used by `/draft-edit`
- the same API key also enables `/draft-fix`, which drafts from the active findings artifact
- optional environment variables:
  - `AUTOPATCH_OPENAI_MODEL`
  - `OPENAI_BASE_URL`
