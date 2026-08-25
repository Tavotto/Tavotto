# 桌面交接（只在要交给 Tavotto 桌面窗口时读）

**只在用户明确要外部窗口、或需求超出 MCP 工具能力时才走这条**（多图拼版、
加画布标注、(a)(b) 编号、版本历史、把修改写回原始 PDF/PNG）。日常改图一律
用 MCP 工具与内嵌画布——桌面窗口不是它的替代品，反过来也一样。
执行本技能自带的（路径相对本技能目录）：

```
python3 scripts/handoff.py <脚本路径>
```

它做三件事：产物缺失或比脚本旧就先跑一遍脚本 → 把产物登记进图库注册表 →
唤起 Tavotto（桌面应用优先，没装就用浏览器模式）。**退出码非零就是没做完**
（0 成功 / 1 脚本报错 / 2 路径或交接失败 / 3 这台机器上用不了 Tavotto /
4 图不可参数化）。

用户只装了 Tavotto 桌面程序也没关系——不必让他再装 Python，也不必让他配
`TAVOTTO_CLI`，脚本会自己找到桌面版自带的命令行。

输出是一行 JSON，**你必须读**：

* `"parameterizable": true` —— 成了，交接完成。
* `"parameterizable": false` —— **没成，要修**。多半是脚本没和产物放在同一个目录
  （契约 1），或产物名静态解不出（契约 3）。改完重跑重新交接，不要把它当噪音略过。
* `"error_code": "tavotto_missing"` —— 用户机器上没有 Tavotto。按输出里的 `hint`
  引导安装；图已经画好了，脚本和产物都在原处，装完再执行同一条命令即可。
* `"error_code": "desktop_found_cli_missing"` —— **他装了桌面版，只是版本旧**
  （那一版没带命令行）。让他去 Releases 更新一次，**不要**让他「先装 Tavotto」
  ——他会去装一个已经装着的东西，然后发现还是不行。
* `"error_code": "registry_write_failed"` —— 图库目录不可写。把图和脚本换到一个
  可写的目录，或让用户修好权限，然后重新交接。原文件一个字节都没动。
* `"error_code": "launch_failed"` —— 桌面应用**起来了但没活下来**（或起不来）。
  `ok: true` 是等出来的：CLI 会等桌面进程存在且就绪，崩了就带着
  `exit_code` / `signal` / `log_path` 回这条。把这三样念给用户
  （`signal: "SIGABRT"` 多半是安装损坏或从受限环境启动 GUI），指给他
  `log_path` 的 sidecar 日志与 `~/Library/Logs/DiagnosticReports/` 的崩溃
  报告；`retryable: false` 时**不要**自己重试一个已知会崩的程序。
* `"error_code": "launch_timeout"` —— 唤起后进程在限期内没出现。
  `retryable: true`，可以重试一次；再超时就把 `log_path` 给用户。
* 顺便留意 `conflicts`（两个脚本抢同一个 stem）和 `dynamic_names`（某些脚本的产出名
  静态解不出）——只报告不自动裁决，需要时告诉用户。
* `"update"` —— 插件自己有新版本时才出现（每 24 小时最多查一次）。按
  `references/first-run-and-recovery.md` 的「插件有新版本」一节处理：收尾提醒
  一次，不阻塞、不自动升级。

完整的错误码清单与排障步骤在
<https://github.com/Tavotto/Tavotto/blob/main/docs/handoff-protocol.md>
（sparse 安装与插件发行包里只有 `codex-plugin/`，仓库的 `docs/` 不随包分发，
所以这里用仓库 URL 而不是相对路径）。

交接后改了代码：重开一次会话（`tavotto_close_session` 再 `tavotto_open_figure`），
或者再交接一次给桌面窗口——Tavotto 会重扫产物并定位到这张图，用户已经排好的版和
已经调过的元素不会丢。重复交接同一张只选中，不叠第二份。
