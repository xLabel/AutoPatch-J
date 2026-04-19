# Semgrep Runtime

AutoPatch-J can run a Semgrep binary from this repository without modifying the user's shell `PATH`.

Place the executable at:

```text
runtime/semgrep/bin/<platform>/semgrep
```

Supported platform tags are generated from the host OS and CPU architecture, for example:

```text
darwin-arm64
darwin-x64
linux-arm64
linux-x64
windows-x64
```

The scanner lookup order is:

1. explicit `AUTOPATCH_SEMGREP_BIN`
2. `runtime/semgrep/bin/<platform>/semgrep`
3. `.venv/bin/semgrep`
4. `semgrep` from `PATH`

Semgrep cache, settings, and logs are still written under the target repository's `.autopatch/runtime/semgrep`.
