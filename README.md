# AutoPatch-J

Minimal AI coding patch agent.

Current checkpoint:

- interactive CLI shell
- `/init` project bootstrap
- `.autopatch/` local state
- repository indexing
- `@mention` path resolution with interactive disambiguation
- rule-based scan routing
- Semgrep wrapper with normalized findings artifacts
- pending edit review/apply gate

## Run

```bash
python3 -m autopatch_j
```

Inside the shell:

```text
/init .
@src/main/java/com/foo/UserService.java scan this file
/status
/preview-edit Demo.java "call();" "safeCall();"
/show-pending
/apply-pending
```

The model/tool loop is not wired yet. This slice establishes the repository and scope primitives that later agent steps will build on.

## Scan behavior

- if the prompt contains scan intent and includes `@mention`, AutoPatch-J scans that scope
- if the prompt contains scan intent without `@mention`, AutoPatch-J scans the whole repository
- current routing is keyword-based; it will be replaced by LLM tool decisions later

The scan wrapper expects `semgrep` on `PATH`. If it is missing, the CLI returns a clear error and keeps session state intact.

## Edit review gate

- `/preview-edit` only previews a `search-replace` edit and stores it as pending
- `/show-pending` shows the current pending diff
- `/apply-pending` writes the pending edit to disk
- `/clear-pending` drops the pending edit without changing files
- Java edits require Tree-sitter validation before apply; if `tree_sitter` or `tree_sitter_java` is missing, preview still works but apply is blocked
