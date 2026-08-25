# 帮用户提 issue（只在撞上 Tavotto 缺陷时读）

用户撞上 Tavotto 的报错、渲染结果不及预期、画布/预览显示不出来时，除了按
错误码引导恢复，还要**把问题记录成一份能复现的 issue 草稿**：

* **标题**：一句话说清「做什么时出了什么」；
* **环境**：`tavotto_health` 的输出（引擎版本/来源）、插件版本
  （`python3 scripts/update_check.py --json` 里的 `current_version`）、
  操作系统；
* **复现步骤**：最小化的脚本（或指出无法脱敏时用形状等价的替身数据）、
  依次执行的命令/工具调用、期望看到什么、实际看到什么；
* **原始证据**：结构化错误的 `code` 与消息原文、相关日志（`log_path` 指到的
  那份）——**先脱敏**：用户名、绝对路径、密钥一律抹掉。

草稿先给用户看。**用户明确允许之后**才提交到
<https://github.com/Tavotto/Tavotto/issues>（有 `gh` 就
`gh issue create --repo Tavotto/Tavotto`，没有就把草稿给用户让他自己贴）。
用户不允许就把草稿留在对话里，到此为止——**绝不擅自外发**。
