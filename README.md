# AutoPatch-J

> English · [中文](./README_CN.md)

<p align="center">
  <strong>An AI code repair agent for Java</strong><br/>
  A command-line system built with <code>Workflow</code> as the controller and <code>Agent</code> as the decision engine, covering code inspection, code explanation, patch generation, and human confirmation.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/LLM-DeepSeek-111827?style=flat-square" alt="DeepSeek" />
  <img src="https://img.shields.io/badge/Architecture-Workflow%20%2B%20Agent-4F46E5?style=flat-square" alt="Workflow + Agent" />
  <img src="https://img.shields.io/badge/Scanner-Semgrep-22C55E?style=flat-square" alt="Semgrep" />
  <img src="https://img.shields.io/badge/Index-SQLite%20%2B%20Tree--sitter-0EA5E9?style=flat-square" alt="SQLite + Tree-sitter" />
  <img src="https://img.shields.io/badge/CLI-Rich%20%2B%20prompt--toolkit-F59E0B?style=flat-square" alt="Rich + prompt-toolkit" />
</p>

## Overview

AutoPatch-J is an AI code repair CLI currently aimed at **Java repositories**.  
Instead of treating the model as a free-roaming black-box assistant, it places the model inside a controlled engineering pipeline:

- identify intent first
- resolve scope next
- run static scanning when needed
- advance patch generation one finding at a time
- enter a human confirmation workspace at the end

The project is not trying to make the model "talk more". It is trying to make code repair more stable, verifiable, and reviewable.

## Highlights

### Workflow + Agent, instead of an unconstrained Agent

Control stays in `Workflow`, not in the LLM:

- `Workflow` manages intent, scope, state, and patch queues
- `Agent` handles explanation, triage, patch generation, and patch revision

This keeps the flexibility of an Agent while reducing the common failure modes of a pure Agent setup: scanning the whole repo without restraint, rereading files, drifting across scope boundaries, carrying bloated context, or producing patches that are hard to review.

### Patches are first-class objects, not disposable replies

Each patch is stored as a structured review item with:

- target file
- related finding
- unified diff
- rationale
- syntax validation result

### `@mention` only recognizes files and directories

The formal capability of `@mention` currently includes only:

- files
- directories

For example:

```text
autopatch-j> @src/main/java/demo/UserService.java inspect this file
autopatch-j> @src/main/java/demo explain this directory
```

### The scanner and the LLM work together

The default scanner is **Semgrep**.  
Other scanner adapter slots already exist, but they are not on the main path yet:

- PMD (Planned)
- SpotBugs (Planned)
- Checkstyle (Planned)

The LLM does not patch code "by intuition". It works from real findings and source-code evidence whenever possible.

### Long-running sessions are explicitly controlled

The system keeps multi-turn interaction bounded through:

- scope locking
- tool whitelists
- history dehydration
- compressed chat output
- a patch confirmation workspace

This makes the project feel more like a runnable engineering system than a one-shot chatbot.

## Current Capabilities

### Code inspection

```text
autopatch-j> @LegacyConfig.java check whether this file has obvious issues
autopatch-j> @src/main/java/demo scan this directory
autopatch-j> look for null-pointer risks in this project
```

Characteristics:

- local scan first
- findings are advanced one by one
- supports a single retry after an `old_string` mismatch
- enters patch confirmation automatically after a patch draft is produced

### Code explanation

```text
autopatch-j> @LegacyConfig.java what does this file do
autopatch-j> @src/main/java/demo explain this directory
```

Characteristics:

- no scan is triggered
- single-file explanation does not chase context across files by default
- multi-file explanation allows controlled symbol navigation
- output is compressed into a concise explanation by default

### Patch explanation and patch revision

Once the session enters confirmation mode, follow-up prompts can continue from the current patch:

```text
autopatch-j> why is it changed this way?
autopatch-j> will this affect performance?
autopatch-j> rewrite it with Objects.equals
autopatch-j> add one comment to explain the reason
```

The system distinguishes automatically between:

- `patch_explain`
- `patch_revise`

### Programming-related chat

`general_chat` is currently limited to engineering-related topics:

- programming languages
- algorithms
- debugging
- architecture
- tool usage
- project-specific questions

It is not intended to be a general lifestyle chatbot.

## A Real Execution Path

Using `code_audit` as an example, a full execution goes through the following steps:

1. user input enters `IntentService`
2. `ScopeService` resolves the code scope
3. routing selects `code_audit`
4. `ScanService` performs the local static scan first
5. `AuditBacklogService` advances the findings one by one
6. `AutoPatchAgent` uses tools to gather evidence and generate a patch for the current finding
7. `PatchEngine` handles `old_string` matching, diff generation, and syntax validation
8. `WorkflowService` writes the result into `ActiveWorkspace`
9. the flow enters human confirmation: `apply / discard / revise`

Other routes:

- `code_explain`: `AutoPatchAgent`
- `general_chat`: `ChatService -> AutoPatchAgent`
- `patch_explain / patch_revise`: `WorkflowService + AutoPatchAgent`

## Architecture at a Glance

### `cli/`

The interaction layer, responsible for:

- prompt input
- command dispatch
- panel rendering
- autocomplete

Primary entry point:

- `src/autopatch_j/cli/app.py`

### `core/`

The system backbone, responsible for:

- intent detection: `IntentService`
- session continuity decisions: `ContinuityJudgeService`
- scope resolution: `ScopeService`
- scanning: `ScanService`
- finding backlog management: `AuditBacklogService`
- patch workspace management: `WorkflowService`
- state persistence: `ArtifactManager`
- output shaping: `ChatService`
- patch application rules: `PatchEngine`

### `agent/`

The LLM layer, responsible for:

- task profiles
- the ReAct loop
- tool calls
- prompt composition
- history dehydration

Key files:

- `src/autopatch_j/agent/agent.py`
- `src/autopatch_j/agent/prompts.py`
- `src/autopatch_j/agent/llm_client.py`

### `tools/`

Tool adapters exposed to the Agent:

- `read_source_code`
- `get_finding_detail`
- `propose_patch`
- `search_symbols`

### `scanners/`

The static scanner adapter layer. The only scanner fully wired into the main path right now is **Semgrep**.

### `validators/`

Responsible for:

- `Tree-sitter` syntax validation
- semantic rescans after patch application

## How the LLM Is Used

### Task profiles instead of one generic chat mode

The Agent currently has five explicit task entry points:

- `code_audit`
- `code_explain`
- `general_chat`
- `patch_explain`
- `patch_revise`

Each task owns its own:

- system prompt
- tool whitelist
- output constraints

### Tool permissions are intentionally asymmetric

For example:

- `code_audit`: `get_finding_detail / read_source_code / propose_patch`
- `code_explain`: single-file mode opens only `read_source_code`
- `patch_revise`: `search_symbols / read_source_code / get_finding_detail / propose_patch`

This is not about restricting the model for its own sake. It is about putting model freedom where it is actually useful.

### ReAct is preserved, but bounded by Workflow

The Agent still follows a ReAct-style loop:

1. receive the task prompt
2. decide whether to call a tool
3. observe the result
4. continue until it produces an answer or a patch

But the loop always runs under these constraints:

- tool whitelist
- focus scope
- workspace state
- dehydrated history

That is the key design tradeoff in AutoPatch-J:  
**let the Agent keep intelligence, and let Workflow keep boundaries.**

## Engineering Details

### 1. Finding-by-finding progression

`code_audit` is not "scan once and let the LLM freestyle over the whole result set".  
Instead, `AuditBacklogService` builds a finding backlog and advances it item by item.

Benefits:

- the current target stays explicit
- one failed finding does not swallow the rest
- patch retry stays controlled

### 2. Patch safety chain

At the draft stage, `PatchEngine` checks:

- whether the file exists
- whether `old_string` matches
- whether the match is unique
- whether a diff can be generated
- whether `Tree-sitter` syntax validation passes

After a real `apply`, `SemanticValidator` rescans the target file and verifies that the corresponding finding actually disappeared.

### 3. Context control

The project applies several layers of Context Engineering explicitly:

- resolve `@mention` into real file sets
- inject current workspace state into the workbench prompt
- compress old messages through History Dehydration
- compress chat output and strip Markdown-heavy structure

The goal is not "show the model more". It is "show the model only what is truly useful for the current task".

### 4. SQLite + Tree-sitter indexing

`IndexService` uses:

- `SQLite` for local indexing
- `Tree-sitter` to extract `class / method`

It also keeps explicit degradation state so the system is not fooled by fallback logic that merely "looks like it still works".

### 5. Correcting finding evidence

`FindingSnippetService` prefers reconstructing real code snippets from `path + line range`, instead of blindly trusting the raw snippet returned by the scanner.

This makes finding evidence more stable and reduces the chance that the LLM is misled by dirty or unrelated fragments.

## Quick Start

### Requirements

- Python `3.11+`
- an OpenAI-Compatible LLM endpoint

Install dependencies:

```bash
pip install -e .[test]
```

### Environment Variables

```bash
set LLM_API_KEY=your_api_key
set LLM_BASE_URL=https://api.deepseek.com
set LLM_MODEL=deepseek-v4-flash
```

### Launch

#### Demo Mode

```bash
run.bat
```

The default target is:

```text
examples/demo-repo
```

#### Manual Run

```bash
python -m autopatch_j
```

## Project Layout

```text
src/autopatch_j/
├─ agent/         # LLM client, prompts, ReAct loop, task profiles
├─ cli/           # prompt-toolkit + Rich interaction layer
├─ core/          # workflow, scope, scan, workspace, patch lifecycle
├─ scanners/      # Semgrep and future scanner adapters
├─ tools/         # tools exposed to the Agent
└─ validators/    # syntax and semantic validation

examples/demo-repo/   # built-in demo repository
tests/                # regression tests
```

---

If you want to enter the codebase quickly, start here:

1. `src/autopatch_j/cli/app.py`
2. `src/autopatch_j/core/workflow_service.py`
3. `src/autopatch_j/agent/agent.py`
4. `src/autopatch_j/core/patch_engine.py`
5. `src/autopatch_j/core/scan_service.py`
