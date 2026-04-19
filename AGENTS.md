# AutoPatch-J Agent Guide

## Collaboration rules

1. Every small but complete feature slice or modification should be committed to git promptly.
2. Commit messages should be clear and, by default, written in Chinese unless a term is best kept in English.
3. Before each commit, run the smallest relevant verification for the changed slice.
4. Do not bundle unrelated refactors into the same commit.
5. When preparing a commit, run `git add` and `git commit` sequentially. Do not parallelize them, or they may race on `.git/index.lock`.

## Project direction

- `AutoPatch-J` is a local CLI agent focused on Java repositories.
- Prefer explicit, inspectable building blocks over opaque agent frameworks.
- Keep the core loop understandable: session state, context building, tool dispatch, validation, and approval gates.

## Implementation boundaries

- Use Python as the orchestration language.
- Avoid introducing heavy dependencies until they remove real complexity.
- Keep file edits minimal and scoped.
- Patch generation and patch application must remain separate concerns.
- Side-effecting actions should stay behind explicit user confirmation.

## Commit style

- Good examples:
  - `实现最小 CLI 骨架与项目初始化`
  - `接入扫描路由与 Semgrep 结果归一化`
  - `抽离 AgentDecision 决策层`

- Avoid vague messages such as:
  - `update`
  - `fix`
  - `misc changes`

## Verification defaults

- For Python code, prefer focused `unittest` coverage for the changed slice.
- Smoke-test the CLI whenever command behavior changes.

## Git workflow pitfalls

- Avoid running `git add` and `git commit` in parallel. They both need to update git index state and can contend on `.git/index.lock`.
- If a commit step fails with an index lock message, first confirm whether the lock file has already disappeared. Retry the command sequentially before considering any cleanup.
