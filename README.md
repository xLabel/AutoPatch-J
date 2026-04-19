# AutoPatch-J

Minimal AI coding patch agent.

Current checkpoint:

- interactive CLI shell
- `/init` project bootstrap
- `.autopatch/` local state
- repository indexing
- `@mention` path resolution with interactive disambiguation
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
@src/main/java/com/foo/UserService.java scan this file
/status
/show-findings
修复第1个问题
@src/main/java/com/foo/UserService.java 生成 patch
/draft-fix 1
/draft-edit Demo.java "guard string compare"
/preview-edit Demo.java "call();" "safeCall();"
/show-pending
应用这个patch
/apply-pending
/show-validation
```

## Scan behavior

- if the prompt contains scan intent and includes `@mention`, AutoPatch-J scans that scope
- if the prompt contains scan intent without `@mention`, AutoPatch-J scans the whole repository
- current fallback routing is keyword-based; if `OPENAI_API_KEY` is present, scan decisions can go through OpenAI
- after findings are loaded, prompt-level patch requests such as `修复第1个问题` can draft a pending edit
- if multiple findings are active, AutoPatch-J asks for a finding index or a narrower `@mention`

The scan wrapper expects `semgrep` on `PATH`. If it is missing, the CLI returns a clear error and keeps session state intact.

## Edit review gate

- `/draft-fix` asks the model to draft one minimal search-replace edit for a selected finding
- `/draft-edit` asks the model to propose one minimal search-replace edit for a target file
- `/preview-edit` only previews a `search-replace` edit and stores it as pending
- `/show-pending` shows the current pending diff
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
