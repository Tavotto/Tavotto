# codex-plugin/ — Codex 插件、技能与 MCP server 规则（速查表）

仓库级路由与不变量在根 `AGENTS.md`。完整版 ADR：
`docs/adr/0005-external-handoff-and-codex-plugin.md`、
`docs/adr/0006-codex-mcp-app-and-publication-profile.md`、
`docs/adr/0009-codex-workspace-root-authority.md`、
`docs/adr/0069-canvas-payload-under-host-event-cap.md`、
`docs/adr/0078-mcp-session-survives-server-process.md`。改动前先读。
交接的引擎侧（`engine/locate.py` / `engine/handoff.py` / `engine/cli.py`）在
`docs/rules/backend/external-handoff.md`。本层规则的**全文按主题**在 `docs/rules/plugin/`
（2026-09-25 从本文件逐字迁出，#608）：先在下表按改动路径找到主题，读那一份细则与它点名的
ADR，再动手。规则改在细则文件里，并同步这里那一行；这里不放第二份全文。

## 本层不可破坏

- **普通用户安装绝不需要 clone 仓库**；技能的会话入口是「先检查，不安装」，不许把「每会话自动
  marketplace add」加回来；装好 / 升级之后**必须新开 Codex 会话**。
- **插件只翻译不实现**：会话、manifest、override、导出全部落回 `tavotto.engine`；插件里那份路径
  规则是 `engine/locate.py` 的镜像，改一边同步另一边。
- **stdout 归协议独占**；路径范围只有一个权威 `RootAuthority`，越界一律拒、绝不「就近找一个能用的」；
  没装 Tavotto 时降级而不是退出。
- **插件自己的更新检查**四条底线：不阻塞出图（1.5 秒超时）、不污染 stdout、不自动下载执行、
  插件版本 ≠ Tavotto 版本；缓存绝不往插件目录写。
- 桥新增 import = `_BRIDGE_IMPORT` / `BRIDGE_IMPORTS_AT_MIN` / 桥本身**三处同源**一起改。
- 画布产物 `canvas.html` 是构建物、不进 git（ADR 0043）；协议绿灯不能冒充 Codex Desktop iframe 证据。

## 按改动路径找细则

| 改到 | 主题（细则在 `docs/rules/plugin/`） | 必守要点 | 看护 |
| --- | --- | --- | --- |
| `skills/tavotto-figure/SKILL.md` 与 `references/`、README 的首次使用章节、安装命令、`agents/openai.yaml` | 首次使用契约 → `first-use-contract.md` | 先检查不安装；装完新开会话；安装命令两条分开写、唯一出处 `brand.CODEX_SPARSE_PATHS`；`openai.yaml` 的 `command` 与 `.mcp.json` 同步 | `tests/test_codex_plugin.py`、`tests/test_codex_install_cli.py` |
| `mcp/launch.cmd`、`.mcp.json` 的 `command`、`mcp/server.py` 的 resolver 与 `--provision` | MCP 启动器、运行时解析与自管环境 → `mcp-launcher-and-provision.md` | sh / cmd 双语启动器的形态约束；每个候选都真的验证 `import tavotto.engine`；venv 只在区间内的解释器上建；自管环境落后时后台重装、互斥只用内核文件锁 | `tests/test_mcp_resolver.py`、`tests/test_mcp_stdio.py`、`tests/test_codex_install_cli.py` |
| `.codex-plugin/plugin.json`、`.agents/plugins/marketplace.json`、路径镜像、`update_check.py`、`scripts/handoff.py` | 插件本体与市场 → `plugin-and-marketplace.md` | 插件版本 == `tavotto.__version__`；路径规则镜像 `engine/locate.py`、一个 pathlib 都不用；更新检查四条底线；脚本与产物同目录、死图退出码 4 | `tests/test_codex_plugin.py`、`tests/test_install_locate.py`、`tests/test_plugin_update_check.py` |
| `tavotto_refresh_project`、`bridge.refresh_project()` | 改过脚本之后的显式刷新 → `refresh-project.md` | 两条路都不复制 discover、不 probe、不跑脚本；项目只来自授权；结果里没有绝对路径；用例绝不真的探 5089 | `tests/test_mcp_server.py`、`tests/test_mcp_resolver.py` |
| `tavotto_normalize_figure`、`bridge.normalize_figure()`、`engine/normalize.py` | 已有图的保留式规范化 → `normalize-figure.md` | 桥只翻译；任何异常都回退；提交之后会话挂合同；用户目标直接决定的规则不挡事务 | `tests/test_normalize.py`、`tests/test_mcp_normalize.py`、`tests/test_codex_plugin.py` |
| 工具结果体积、`CANVAS_INLINE_BUDGET_BYTES`、`tavotto_session_state`、`web/src/mcp/boot.ts` | 工具结果的体积预算与画布取件 → `result-budget-and-session-state.md` | 只有单图 open 守预算、量紧凑 UTF-8；apply 不守；取件只读不重渲染；画布启动三路都不自己发起 open | `tests/test_mcp_server.py`、`web/src/mcp/boot.test.ts`、`web/e2e/mcp-canvas.spec.ts` |
| `tavotto_mcp/sessionjournal.py`、`_restore_session` | 会话跨进程恢复 → `session-journal.md` | 记录不能自证权限、越界拒在渲染之前；明确释放的不复活；落盘失败不让工具调用失败 | `tests/test_mcp_session_journal.py`、`tests/test_mcp_roundtrip.py` |
| `integrations/configure.py`、`HOSTS` 表、`RELOAD_HINT` / `RESTART_HOST` | 非 Codex 宿主 → `non-codex-hosts.md` | 同一份包、同一个启动器、同一份 Skill；不写任何文件；授权只来自 `--project-root`；文档证明不了的字段不加 | `tests/test_mcp_configure.py`、`tests/test_mcp_host_profiles.py`、`tests/test_plugin_candidate.py` |
| `bridge.export`、`_inspection_summary`、导出前预检 | 导出：先预检与产物核验 → `export-preflight-and-inspection.md` | 有 error 或 `not_verifiable` 且没确认就一张不出；与 HTTP 导出同一份检查器接线；「未核验」永远不是「已核验」 | `tests/test_mcp_export_inspection.py`、`tests/test_mcp_server.py` |
| `tavotto_mcp/` 的分层、`rpc.hijack_stdout()`、`RootAuthority` / `roots.WORKSPACE_FAILURES` | MCP server：只翻译、stdout、路径范围 → `server-core-and-roots.md` | 只翻译不实现；先存 `_REAL_STDOUT` 再改道；授权失败分档、「没弹框」绝不报成用户拒绝；越界一律拒 | `tests/test_mcp_roundtrip.py`、`tests/test_mcp_roots.py`、`tests/test_mcp_stdio.py` |
| `_bridge_error_from_worker`、`workdir=` / `prepare_dependencies=` 参数 | 首开的「需要输入」与跑前依赖准备 → `first-open-questions.md` | 与桌面 / HTTP 是同一份决定；不替用户猜、不替用户授权；批量 open 不接受准备参数 | `tests/test_mcp_server.py` |
| `stems` / `discover_stems`、`_safe_preflight()`、`_live_session_for()`、`Session.acquire()` | 批量打开与会话账本 → `sessions-and-batch-open.md` | 一张失败不回滚整批、「没尝试」是第三个桶；open 之后每一步都不许把已登记会话带走；会话不抱 worker 引用、渲染成功后才登记 | `tests/test_mcp_server.py` |
| `web/src/lib/engineTransport.ts`、`mcp/widget/canvas.html`、`scripts/build_mcp_widget.py`、画布 CSP | 内嵌画布 → `embedded-canvas.md` | 画布 = Tavotto 前端那一份代码、唯一改动是 `engineTransport` 的可选覆盖；产物不进 git；不用「开浏览器」冒充；不发遥测 | `scripts/ci/check_generated_untracked.py`、`web/e2e/mcp-canvas.spec.ts`、`docs/acceptance/codex-desktop-canvas.md` |

## 验证

```sh
.venv/bin/python -m pytest tests/test_mcp_server.py tests/test_mcp_roundtrip.py \
  tests/test_codex_plugin.py tests/test_preflight.py tests/test_install_locate.py \
  tests/test_normalize.py tests/test_mcp_normalize.py
python scripts/build_mcp_widget.py --check     # 改了 web/src 就得重建
python codex-plugin/mcp/server.py --self-check # MCP 手动冒烟
```
