# 首次使用契约

> 2026-09-25 迁自 `codex-plugin/AGENTS.md`「首次使用契约（2026-08-25，勿破坏）」（#608：Codex 自动拼接的 32 KiB 上限），
> 正文一字未改。速查行在 `codex-plugin/AGENTS.md` 的「按改动路径找细则」表里；这里是这一主题的**唯一全文**，
> 改规则改这里，并同步那一行。

- **普通用户安装绝不需要 clone 仓库**，也不需要 pnpm/npm/cargo/Tauri/前端构建/
  `run.sh`/测试套件/editable install。README 的「在 Codex 中第一次使用
  Tavotto」是普通用户的唯一入口；源码安装只留给贡献者。
- **技能的会话入口是「先检查，不安装」**（SKILL.md 最前部的状态机）：
  `tavotto_health` 健康时本会话零安装、零联网；缺什么修什么（缺插件才装
  插件、缺引擎才 provision/装引擎）；`desktop_only` 不说「没装 Tavotto」；
  插件更新只在收尾提醒一次；工具缺失 = 给两条安装命令 + 要求新开会话 + 停止。
  **不许把「每会话自动 marketplace add」加回来**——同一会话工具不重载，
  重装只有网络开销（tests/test_codex_plugin.py 看护）。
- 插件安装/升级/引擎装好之后**必须新开 Codex 会话**；`codex plugin list` 的
  enabled ≠ 当前会话拿得到工具。
- 安装命令两条分开写（不用 `&&`）；GitHub 源只需 `--sparse .agents/plugins`：市场清单
  的插件来源是 `git-subdir → plugin-stable`（ADR 0043），插件本体来自发行分支，不从源码
  checkout 里取。唯一出处 `brand.CODEX_SPARSE_PATHS`，README / 恢复文档由它派生。
- SKILL.md 收敛为「触发条件 + 会话入口状态机 + 核心图文件契约 + MCP 工具
  顺序 + 完成判据」，细节按需读 `skills/tavotto-figure/references/`：
  first-run-and-recovery（安装/provision/错误码/新会话）、figure-contract
  （同目录/静态产物名/main()/模板）、publication-style（尺寸/字号/克制/组图）、
  desktop-handoff（交接与退出码）、issue-reporting（脱敏草稿 + 用户同意）、
  compatibility（能改什么）。**SKILL.md 里必须写清什么情况读哪份**。

- `agents/openai.yaml` 的 `dependencies.tools` 声明本插件的 MCP server 依赖：
  `type: mcp` + `value` == `.mcp.json` 的 server key（`tavotto`）+
  `transport: stdio` + `command` == `.mcp.json` 的 `command`。schema 来自
  codex-rs 的 `SkillToolDependency`（type/value/description/transport/
  command/url），改 `.mcp.json` 必须同步这里（pytest 看护）。
