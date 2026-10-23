---
name: export-full-chat
description: 把当前 Claude Code 会话从第一句到现在完整导出成 txt，compact 之前的也在。两种：chatonly（只有对话）和 wholechat（对话加上所有工具调用、代码、输出）。用户说"导出会话 / 导出完整记录 / 导出 compact 之前的记录"时用它。
---

# export-full-chat

`/export` 在 compact 之后只能导出摘要。完整的原始记录一直存在 `~/.claude/projects/<项目>/<会话ID>.jsonl`，这个脚本直接读它，从头导出到现在。

`{baseDir}` 是本 skill 所在目录。

```bash
python3 {baseDir}/scripts/export_chat.py chatonly    # 只有对话
python3 {baseDir}/scripts/export_chat.py wholechat   # 所有内容
python3 {baseDir}/scripts/export_chat.py both        # 两份都出
```

- **chatonly**：`❯` 用户说的话，`⏺` Claude 的回复，compact 的位置标一行 `✻ Conversation compacted`。
- **wholechat**：记录里的全部内容——对话、每次工具调用的完整命令和输出、代码改动、思考、系统注入、compact 摘要、子 agent 记录。

导出的是当前会话（按环境变量 `CLAUDE_CODE_SESSION_ID` 找记录）。参数：`--out-dir` 存放目录（默认当前目录）；`--out` 文件名；`--tz +8` 时区（默认本机时区）。

文件名默认是 `<会话开始时间>-<第一句话>-<chatonly|wholechat>.txt`。导出时会把密钥（`.env` 里的值和常见格式的 key）替换成 `[密钥已隐去]`。

导出后把文件发给用户。
