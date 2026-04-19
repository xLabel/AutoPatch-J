# Semgrep Runtime

AutoPatch-J can run a Semgrep binary from this repository without modifying the user's shell `PATH`.

Place the executable at:

```text
runtime/semgrep/bin/<platform>/semgrep
```

Place the Java rule bundle at:

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

The scanner lookup is intentionally strict: AutoPatch-J only executes the binary under
`runtime/semgrep/bin/<platform>/`. It does not inspect environment overrides, `.venv`,
or the shell `PATH`.

Semgrep cache, settings, and logs are still written under the target repository's `.autopatch/runtime/semgrep`.
