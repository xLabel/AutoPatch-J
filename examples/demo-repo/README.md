Demo fixture for AutoPatch-J.

Suggested shell session:

```text
/init .
/scanner semgrep demo-semgrep.yml
扫描整个仓库的问题
列出问题
@src/main/java/demo/UserService.java 生成 patch
看看 patch
应用这个patch
```

This repository intentionally keeps two Java findings so the first AutoPatch-J walkthrough is repeatable.
