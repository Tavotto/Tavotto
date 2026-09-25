# 分层验收矩阵

**实现完成、候选验证、客户端验证、正式发布是四件事。** 本表只记录有证据的层级：

| 状态 | 含义 |
| --- | --- |
| `documented` | 官方文档证明接入机制存在（见 `hosts.md` 的证据等级），没跑任何东西 |
| `config_tested` | 生成器对该 profile 的输出过了独立期望（`tests/test_mcp_host_profiles.py`） |
| `protocol_tested` | 按生成的配置从解包的完整包起 server，用**模拟客户端**跑通了（不是该品牌客户端） |
| `host_verified` | 在具体版本的真实客户端里跑通，并留有证据（版本 / OS / 产物 SHA / 步骤 / 脱敏结果） |
| `blocked` | 有明确阻断（写明原因） |
| `not_run` | 没跑 |
| `not_applicable` | 该 surface 没有这项能力（例如纯 CLI 没有 iframe） |

**把同一个假客户端的 `clientInfo` 换成八个名字不算八个宿主的验收**；在 VS Code 终端里跑 `claude`
只证明 Claude Code CLI；Claude Code 通过不证明 Claude Desktop 聊天；Trae 导入 JSON 不等于智能体能调用；
宿主能预览网页不证明 MCP Apps 的反向 `tools/call`、状态同步与导出。

## 矩阵（2026-09-24，分支 `claude/peaceful-ritchie-1obkel`；本环境没有任何真实宿主客户端）

协议级证据的来源：`tests/test_mcp_configure.py::test_the_generated_config_starts_the_unpacked_server`（七个输出
JSON 的 profile **各自**生成的配置原样起 server、握手、列工具、调 health）、`tests/test_mcp_host_workflow.py`
（claude-desktop 配置 + 只会 tools/call 的客户端走完真图；会话记录按**当前连接的授权**恢复——授权同一项目的
另一个宿主进程能接着用、授权别处的拿不到）、`tests/test_plugin_candidate.py` 末条（真实候选 + vscode 配置读到逐字
相同的画布资源）。**DSH 的 Cordis YAML 没有被任何测试当配置消费过**（只比了文本），所以它的工具流程是
`not_run`——不从「同一份启动描述」推定。`protocol_tested` 只说明那份配置在协议层能跑，不说明那家宿主会怎么起它。

| id | 宿主 / surface | 配置 | 工具完整流程 | Skill | 内嵌画布 | 桌面交接 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `codex` | Codex（回归基线） | config_tested | protocol_tested | native_skill: not_run | not_run | not_run | 未改 Codex 路径；既有 `tests/test_codex_plugin.py` / `test_codex_install_cli.py` / `test_plugin_candidate.py` 全绿。真宿主画布验收仍按 `docs/acceptance/codex-desktop-canvas.md`，本次没有重跑 |
| `cursor` | Cursor 本地 Agent | config_tested | protocol_tested | native_skill: not_run | not_run | not_run | schema 证据 search_snippet |
| `zcode` | ZCode 本地 MCP | config_tested | protocol_tested | instruction_fallback: config_tested | not_run | not_run | schema 证据 search_snippet；不做插件 manifest / 市场 |
| `dsh` | DeepSeek Harness（dsh-mcp-client stdio） | config_tested | not_run | native_skill: not_run | not_run | not_run | YAML patch；toolCallTimeoutMs 换算自 1800 s |
| `workbuddy` | WorkBuddy 本地 MCP | config_tested | protocol_tested | instruction_fallback: config_tested | not_run | not_run | schema 证据 search_snippet；`.workbuddy/` 路径未核实，只指向界面 |
| `claude-code` | Claude Code CLI | config_tested | protocol_tested | native_skill: not_run | not_applicable | not_run | 纯 CLI 基线是工具流程；IDE 集成 / 桌面 Code 标签页另起子行 |
| `claude-desktop` | Claude Desktop 本地聊天 | config_tested | protocol_tested | instruction_fallback: config_tested | not_run | not_run | 没有本机文件写入：出图脚本由用户保存；`.mcpb` 不做 |
| `trae` | Trae / TraeCode 本地 IDE | config_tested | protocol_tested | instruction_fallback: config_tested | not_run | not_run | 登记与「智能体已启用」分开记；CN / 国际版、IDE / SOLO 均未跑 |
| `vscode` | VS Code GitHub Copilot Agent | config_tested | protocol_tested | native_skill: not_run | not_run | not_run | `chat.mcp.apps.enabled` 与组织策略只记录、不替用户开 |

子行（已测的其他 surface 在这里加行，不替代上面的主行）：暂无。

每条变成 `host_verified` 时，在下面补一段证据：宿主品牌、surface / harness、CN / 国际版（适用时）、
客户端版本、OS、是否本地会话、完整包版本与 `content_digest`、安装方式、配置来源（生成命令）、
逐步结果（脱敏：不写用户项目的绝对路径）。没有这段证据的 `host_verified` 一律视为无效。

## 可重复的真实宿主验收步骤

对每个宿主都一样，差别只在第 2、3 步的位置（`configure.py` 的 stderr 会写出来）：

1. 从候选或 Release 取 `codex-plugin-<版本>.zip`，解压到带空格的目录；`python3 <包>/mcp/server.py --health`
   记录引擎状态与 `content_digest`（`plugin_stage.py digest <包>`）。
2. `python3 <包>/integrations/configure.py --host <id> --project-root <测试项目>`，按 stderr 合并配置；
   先故意把 `--project-root` 指到另一个目录，确认打开测试项目时报 `path_out_of_scope`（fail closed）。
3. 按 stderr「确认加载」核对宿主的 MCP 列表；**在所用智能体里**启用工具。
4. 对话里调 `tavotto_health`：`server.package_dir` 是这份包、`checks.workspace_authorized.ok` 为真。
5. Skill：原生入口按 stderr 复制整个 `tavotto-figure/`；否则 `--emit instructions`。记录是 native_skill /
   plugin_skill / instruction_fallback 哪一种，并确认宿主确实读到（例如让它说出开工三问）。
6. 用仓库里 `tests/test_mcp_roundtrip.py` 的 `SCRIPT`（两条曲线、标题、图例、坐标标签）在测试项目里出图；
   让模型：打开 → 标题字号与图例位置 → 再读状态 → 线宽 → 预检 → 导出 PDF + PNG。核对：两轮修改都在、
   导出回执 `files[].manifest.verdict == accepted`、源脚本 sha256 未变、`tavotto_verify_replay` 一致。
7. 画布（宿主支持 MCP Apps 时）：小图 waiting → ready；在画布里拖一次图例，再让 Agent 改线宽，两者都在；
   再开一张超过 inline 预算的大图（几百个元素），`elided` 出现、画布经 `tavotto_session_state` 取件后可交互。
   截图 + 工具 metadata 存档；没有真实截图就写 not_run，不伪造。
8. 桌面交接（只在用户要求时）：`scripts/handoff.py <脚本>` 退出码 0 且 `parameterizable: true`；不把外部
   窗口叫作内嵌画布。

没装客户端、缺账户 / 授权、无法操作真实 UI 时：本行保持 `not_run` 并写明原因。
