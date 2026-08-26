# codex-plugin/ — Codex 插件、技能与 MCP server 规则

仓库级路由与不变量在根 `AGENTS.md`。完整版 ADR：
`docs/adr/0005-external-handoff-and-codex-plugin.md`、
`docs/adr/0006-codex-mcp-app-and-publication-profile.md`、
`docs/adr/0009-codex-workspace-root-authority.md`。改动前先读。
交接的引擎侧（`engine/locate.py` / `engine/handoff.py` / `engine/cli.py`）在
`src/tavotto/AGENTS.md` 的「外部交接」。

## 首次使用契约（2026-08-25，勿破坏）

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
- 安装命令两条分开写（不用 `&&`）；GitHub 源的 `--sparse` 必须同时含
  `.agents/plugins` 与 `codex-plugin`（市场清单引用仓库内的本地插件目录）。
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

## 插件本体与市场

- **Codex 插件在 `codex-plugin/`**，市场清单在仓库根 `.agents/plugins/marketplace.json`
  （仓库即市场根）。**已不再是 skills-only**：2026-08-18 起同时带一个本地 stdio
  MCP server 与内嵌画布；交接这条路一字未改。**仍不做 `.app.json`**（需要
  OpenAI 侧注册的托管 App id）。pyproject 的 `exclude` 显式挡住 `codex-plugin/`
  进 wheel/sdist。插件版本 == `tavotto.__version__`（`tests/test_codex_plugin.py` 看护）。
- **插件里那份路径规则是 `engine/locate.py` 的镜像**（插件 import 不到 tavotto，
  这份重复无法避免）。能避免的是两边悄悄漂开：
  `tests/test_install_locate.py::test_plugin_mirrors_the_locator` 在
  Windows/macOS/Linux × 有无环境变量 × 空格与中文的矩阵上逐条比对两侧输出，
  改一边必须同步另一边。两侧都**一个 pathlib 都不用**（`Path()` 按 `os.name`
  分派，在 macOS 上连构造一条 Windows 路径都做不到）。
- **插件自己的更新检查在 `codex-plugin/.../scripts/update_check.py`**：
  每 24 小时一次（失败 1 小时后可重试）、1.5 秒超时、缓存落
  `config_dir()/codex-plugin-update.json`（**绝不往插件目录写**——那儿归 Codex
  管、可能只读、升级时整个被换掉）。四条底线：不阻塞出图、**不污染 stdout**
  （调用方读的是最后一行 JSON）、不自动下载执行、**插件版本 ≠ Tavotto 版本**
  （当前版本只从 plugin.json 读，`min_tavotto_version` 比的是 `tavotto open`
  回报的那个版本）。清单由 `scripts/make_plugin_manifest.py` 在 **release.yml**
  生成——**不能挪进 desktop-tauri.yml 的 updater-manifest**，那个 job 没配
  minisign 私钥就整个跳过，插件的更新通道会跟着悄悄停而且全绿。
- **技能的第一条硬约定：脚本与产物同目录、且必须先落成文件**（禁 `python -c` 出图）
  ——「stem ↔ 产出它的脚本」是图能不能双击进去改的全部依据。自检不靠祈祷：
  `scripts/handoff.py` 读 `tavotto open --json` 的 `registry.parameterizable`，
  为 false 时**退出码 4**。图出来了但只是死图，那不是成功。

## MCP server 与内嵌画布（2026-08-18）

ADR 0005 的「skills-only / 不做 MCP server」这一条**已被 ADR 0006 推翻**
（交接那条路不变）。

- 插件清单加 `"mcpServers": "./.mcp.json"`；`.mcp.json` 是**本地 stdio**
  （`command: python3` + `args: ["./mcp/server.py"]` + `cwd/env_vars/tool_timeout_sec`）。
  字段形状取自 Codex 官方插件装出来的清单，**不要猜**。
- **`codex-plugin/mcp/tavotto_mcp/` 只翻译不实现**：会话、manifest、override、patch 规范化、
  导出全部落回 `tavotto.engine.{pool,registry,handoff,patchspec,profiles,preflight}`。
  发给 worker 的 patches 与 Flask `/api/engine/render` 走同一条路径，所以 ADR 0003 的
  等价性不变式原样成立（`tests/test_mcp_roundtrip.py` 用真 matplotlib + 真 stdio 逐条验：
  热态 == 全新 worker 重放、figure 尺寸变、axes 几何变、关掉重开）。
- **stdout 归协议独占**：`rpc.hijack_stdout()` 把 `sys.stdout` 改道到 stderr，**必须先
  存下真正的 stdout 句柄**（`_REAL_STDOUT`）。顺序反了协议帧全写到 stderr 上，症状是
  「initialize 永远等不到响应」且零报错（开发期真撞到过）。
- **路径范围只有一个权威 `RootAuthority`**：显式 `TAVOTTO_MCP_ROOTS` → host 明确
  声明后的 `roots/list`（Roots 已弃用，只作兼容）→ 用户经 `elicitation/create`
  批准、只活在本连接内的精确 realpath → 宿主工作区变量 → 安全 cwd。模型传来的
  `project_path` 只是候选，不能自证权限；相对路径只有恰好一个可信根时才解析。
  确认框默认 false，拒绝/取消/超时一律 fail-closed，重新 initialize 清掉授权；
  root 改变后旧 session 必须回 `workspace_root_changed`。server→client 请求只能在
  活跃 `tools/call` 内发，reader pump 必须保序且有界等待。越界一律拒，**绝不
  「就近找一个能用的」**。看护 `tests/test_mcp_roots.py`、双向协议用例与
  `tests/test_mcp_stdio.py`。**没装 Tavotto 时降级而不是退出**（降级 server 握手正常、每个工具说人话）
  ——静默退出在 Codex 里表现为「插件没有工具」。
- **启动器 `mcp/server.py` 是运行时解析器（2026-08-20 重做）**：候选链
  当前解释器 → `TAVOTTO_MCP_PYTHON`（显式，失败要指名道姓报
  `engine_unavailable`）→ `TAVOTTO_WORKER_PYTHON`/设置里的 worker.python →
  **插件自管 venv**（`<配置目录>/mcp-runtime/venv`，`--provision` 建、
  钉插件版本、绝不碰用户全局环境）→ 从 CLI 反推 shebang → PATH。**每个候选
  都要真的验证 `import tavotto.engine`**；frozen `tavotto-cli` 永远出不了
  候选。降级 server 的 tools/list **只列 `tavotto_health`**（不把六个不可用
  工具伪装成可用），`serverInfo.version` 固定 "0"，六个工具名的调用回结构化
  错误 + 恢复步骤，不声明任何资源。`--health` 输出一行 JSON 体检（引擎/
  画布/桌面版/每个候选的结论与耗时）。真 server 也有 `tavotto_health` 工具
  （出图前的能力门槛）；widget 缺失时 open/apply 在 structuredContent 里带
  `canvas_ui: {available: false, code: "widget_missing"}` 并在文字里说出口，
  `resources/read` 对缺失产物报「缺失 + 修法」而不是回空 HTML。
  看护 `tests/test_mcp_resolver.py` + `tests/test_mcp_stdio.py`。
  **装完插件/引擎必须新开 Codex 会话**——已开的会话不重载工具，
  `codex plugin list` 的 enabled 不代表 server 健康（README 里写明了）。
- **导出先预检**：有 error **或 `not_verifiable`** 且没有 `explicit_confirm` 时
  一张图都不出（`needs_confirm`，与导出对话框同一判据；`blocking` 仍只表示
  error）。PNG 的 dpi 与 profile 的 `min_raster_dpi` 比一次，复用同一个
  `raster-dpi` id 与同一张 severity 表。默认格式取**这次调用**的 profile，
  默认导出目录也要过 `check_scope`。强制导出与确认项都记进 proof。
- **会话不抱 worker 引用**：池的 `MAX_ALIVE` 与桥的 `MAX_SESSIONS` 是两个数，
  必然打架——每次操作前 `pool.get()` 重新取（`Session.acquire()`）。
  会话**渲染成功之后**才登记，否则失败的 open 会堆满账本并挤掉在用的会话。
- **内嵌画布 = Tavotto 前端那一份代码**（`CanvasStage`/`OverlaySvg`/`interactions.ts`/
  `ElementInspector`/既有 stores），拖拽、命中、吸附、undo、patch 状态**没有第二份实现**。
  唯一改动是 `web/src/lib/engineTransport.ts`：一个**可选覆盖**（HTTP ↔ `tools/call`）。
  它**不 import `lib/api`**——搬默认实现进去会与 api 绕成环（TDZ），而且既有单测大量
  `vi.mock('@/lib/api')` 打桩 `engineRender`，实测会炸 7 个文件。
- UI 只挂在 `tavotto_open_figure` / `tavotto_apply_overrides` 上（其余工具的产出是文字与
  文件，挂 UI 只会让画布不停重建）；CSP 的 `connectDomains` **是空的**（sidecar 端口动态，
  写不进白名单，这也是必须走 `tools/call` 的原因）；**绝不用「开浏览器」冒充内嵌画布**；
  iframe 的 `localStorage`/`widgetState` **不存业务数据**。
- 画布产物 `codex-plugin/mcp/widget/canvas.html` 是**受管构建物**（进 git）：
  `python scripts/build_mcp_widget.py`，`--check` 在 CI 的 frontend job 与 pytest 里各看一道。
  **改了 `web/src` 就得重跑**，否则用户装到的是上一版画布（功能全在、只是旧、零报错）。
- **协议绿灯不能冒充 Codex Desktop iframe 证据**。真实验收必须按
  `docs/acceptance/codex-desktop-canvas.md`：新任务、真实 capability JSON、先取消
  证明 fail-closed、再人工批准精确路径、同一任务里出现并实际交互画布，且保留截图与
  工具 metadata；缺一项就继续写“未验证”。
- **内嵌 Codex 画布不发遥测**（widget 打包同一份前端代码，但没人调
  `setTelemetryEnabled`）——这是决定，不是疏漏。

## 验证

```sh
.venv/bin/python -m pytest tests/test_mcp_server.py tests/test_mcp_roundtrip.py \
  tests/test_codex_plugin.py tests/test_preflight.py tests/test_install_locate.py
python scripts/build_mcp_widget.py --check     # 改了 web/src 就得重建
python codex-plugin/mcp/server.py --self-check # MCP 手动冒烟
```
