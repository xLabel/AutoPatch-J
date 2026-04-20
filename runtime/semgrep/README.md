# Semgrep Runtime

AutoPatch-J does not use `PATH` to discover Semgrep. The scanner runtime is
managed by AutoPatch-J and resolved in this order:

1. user runtime:

```text
~/.autopatch-j/scanners/semgrep/bin/<platform>/semgrep
```

2. bundled fallback inside this repository:

```text
runtime/semgrep/bin/<platform>/semgrep
```

The Java rule bundle stays in this repository:

```text
runtime/semgrep/rules/java.yml
```

Supported platform tags are generated from the host OS and CPU architecture, for example:

```text
darwin-arm64
darwin-x64
linux-arm64
linux-x64
windows-x64
```

To install a downloaded official Semgrep executable into the AutoPatch-J user runtime:

```bash
python3 scripts/install_semgrep_runtime.py --source /path/to/semgrep
```

If the bundled fallback already exists, this also works:

```bash
python3 scripts/install_semgrep_runtime.py
```

The scanner lookup is intentionally strict. AutoPatch-J does not inspect environment
overrides, virtualenv executables, or the shell `PATH`.

Semgrep cache, settings, and logs are written under the target repository's
`.autopatch-j/runtime/semgrep`.
