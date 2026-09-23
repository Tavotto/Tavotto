# WorkBuddy P0 兼容性 Spike：Tavotto MCP / MCP App 画布

这份文档回答一件事：**Tavotto 现有的 Codex MCP server + MCP App 画布，能不能以最小
改造接进腾讯 WorkBuddy。** 它是工程证据，不是上线结论。

证据分三层，**互相不能冒充**：

| 层 | 是什么 | 能证明 | 不能证明 |
| --- | --- | --- | --- |
| L1 协议探针 | `tests/workbuddy/probe.py`：一个冒充宿主的 stdio client 对**真** server（真启动器 → 真 resolver → 真引擎 → 真 matplotlib）发帧 | server 一侧的每条行为、每个失败分档 | 宿主实际发什么 |
| L2 WorkBuddy 形状假宿主 | `web/e2e/workbuddy-host.spec.ts`：按 WorkBuddy 公开文档写的假宿主（异源 sandbox proxy、>256 KB 由 guest 回拉、严格 CSP、权限框 + 「始终允许」、hostContext）× **真画布产物** × **真 stdio server** | Tavotto 在「文档描述的那种宿主」里能跑完整编辑闭环 | 真 WorkBuddy 是否真的这么做 |
| L3 真 WorkBuddy Desktop | 真客户端 + `tests/workbuddy/record_proxy.py` 旁听录制 | 一切 | —— |

**截至本文写成，L3 一项都没有做：本机没有安装 WorkBuddy。** 所以凡是只能由真客户端
回答的格子一律写 **UNKNOWN**，不因为 L1/L2 是绿的就改写。L3 的操作清单在文末。

## Environment

| 项 | 值 |
| --- | --- |
| OS | macOS 27.0（26A428） |
| architecture | arm64 |
| WorkBuddy version | **未安装**（L3 未执行） |
| Tavotto version | engine / plugin 0.16.0（`serverInfo.version`） |
| 待验树 | `origin/main` = `a9aa23a0`；spike 分支 `spike/workbuddy-p0`（产品代码零改动，只加测试 / 文档） |
| 画布产物 | `codex-plugin/mcp/widget/canvas.html`，**1,354,834 B**，sha256 前缀 `01d62b48d6ec9601`（`build_mcp_widget.py --check` 通过；与桌面上已打好的 `tavotto-workbuddy-0.16.0.zip` 里那份同字节数） |
| MCP server 解释器 | 仓库 `.venv`（Python 3.13.11，经 `TAVOTTO_MCP_PYTHON`） |
| 渲染 worker | Homebrew Python 3.13.11，matplotlib 3.10.8 |
| MCP protocol | client 请求 `2025-06-18`，server 回 `2025-06-18`（支持 `2025-11-25 / 2025-06-18 / 2025-03-26 / 2024-11-05`） |
| MCP Apps protocol | 画布发 `ui/initialize` 的 `protocolVersion = 2026-01-26`（`web/src/mcp/appsBridge.ts`） |
| 浏览器（L2） | Playwright 1.62.1 / Chromium |
| WorkBuddy 文档依据 | 腾讯云「MCP Apps 接入指南 - WorkBuddy Enterprise」（文档 1831/137044，页面标注 2026-08-26 更新）；`open.workbuddy.cn/docs/connector`（连接器包 / `mcp.json` / 版本表） |

## Results

「L1/L2」列是本仓库里能自动化复现的证据；「真 WorkBuddy」列只由 L3 填。**最终判定取
两列里更弱的那个**，L3 缺席时最终判定就是 UNKNOWN。

| Test | L1 / L2（自动化） | 真 WorkBuddy | 最终 | Evidence |
| --- | --- | --- | --- | --- |
| MCP initialize | PASS | UNKNOWN | UNKNOWN | L1 `handshake`：`serverInfo {tavotto, 0.16.0}`，capabilities `tools` + `resources`，237 ms |
| tools/list | PASS | UNKNOWN | UNKNOWN | 10 个工具齐：health / open / apply / normalize / preflight / export / verify_replay / refresh / session_state / close；open 与 apply 带 `_meta.ui.resourceUri = ui://tavotto/canvas/v1.html` |
| resources/read | PASS | UNKNOWN | UNKNOWN | `resources/list` 一条 `ui://tavotto/canvas/v1.html`、MIME `text/html;profile=mcp-app`；read 回 1,354,834 B |
| 1.35 MB Canvas | PASS（L2） | UNKNOWN | UNKNOWN | L2：host 不预取，proxy 只拿到 `resourceUri`，guest 一侧 `resources/read` 回拉 1,288,907 字符（UTF-16 长度）后挂载；变异「关掉回拉」→ 用例红 |
| Canvas boot | PASS（L2） | UNKNOWN | UNKNOWN | L2：双层 iframe + `connect-src 'none'` 的 CSP 下握手、进 ready、一握手就请求 fullscreen；截图见下 |
| reverse tools/call | PASS（L2） | UNKNOWN | UNKNOWN | L2：画布里拖图例 → 画布发 `tools/call tavotto_apply_overrides`（全量 patches）→ 真 server 渲染 → 新 SVG 回画布；server 侧 `session_state.patches` 逐次变化 |
| permissions | PASS（L2，按文档建模） | UNKNOWN | UNKNOWN | L2：首次拖动弹 1 次、选「始终允许」后再拖 2 次零弹框、`/clear` 后再弹；预检 / 导出各自再弹 1 次（按 (server, tool) 计）。变异「不缓存始终允许」→ 用例红 |
| workspace root | PARTIAL | UNKNOWN | **UNKNOWN / 潜在 BLOCKER** | L1：宿主不声明 roots/elicitation 时 open 回 `no_workspace_root / configure_roots`（fail-closed，未弹任何请求）；声明 elicitation → 确认框带完整 realpath、默认 false；声明 roots → `roots/list` 生效。WorkBuddy 文档对 roots / elicitation / 工作区**一字未提** |
| open figure | PASS | UNKNOWN | UNKNOWN | L1 `full`：c01_line，23 元素，SVG 30 KB，open 972 ms（冷启动 worker），无 `canvas_ui` |
| drag edit | PASS（L2） | UNKNOWN | UNKNOWN | L2 c01：4 次真 apply 全部落到 server；L1：`render_revision 1→2`、`patch_hash` 变、legend bbox 变 |
| preflight | PASS | UNKNOWN | UNKNOWN | L1：`error 2 / warn 8 / suggestion 2`，`blocking: true`（c01 本就不合课题组规范）；L2：画布里点预检经权限闸成功 |
| export | PASS | UNKNOWN | UNKNOWN | L1：`explicit_confirm` 后 PDF 12,366 B、头 `%PDF-`、产物核验 `accepted`；L2：画布里勾「仍要导出」→ 导出，磁盘上有 `c01_line_*.pdf` |
| large figure | PASS（L1/L2） | UNKNOWN | UNKNOWN | `big_fig`（448 元素）：open 结果 199,981 B，`elided.fields = [svg, manifest]`；画布经 `tavotto_session_state` 取件（1,210,961 B，452 ms）进 ready，**没有**第二次 open；拖标题 → apply 1,013,784 B。L2 另量到：**取件本身也要过权限闸**（见 Blockers P1-2） |
| denial path | PASS（L1/L2） | UNKNOWN | UNKNOWN | 工作区拒绝：`workspace_confirmation_declined / ask_user_again`、`sessions: []`；apply 拒绝：guest 拿到 `isError`、server 零调用、`patches == []`，画布显示「渲染失败 + 用户拒绝了本次工具调用 + 仍显示上一版图像 + 重试渲染」。变异「拒绝仍转发」→ 用例红 |
| engine missing | PASS | UNKNOWN | UNKNOWN | L1 `no-engine`（本机天然形态：自管 venv 是 0.15.0 < 最低 0.16.0）：握手正常、`serverInfo.version "0"`、tools/list **只有** `tavotto_health`、不声明资源；open 回结构化 `desktop_only` + 两条恢复路 |
| canvas resource missing | PASS | UNKNOWN | UNKNOWN | L1 `no-widget`：capabilities 不含 resources、没有工具挂 `_meta.ui`；`resources/read` 回 `-32602` + 修法；open 照常成功并带 `canvas_ui {available: false, code: widget_missing}` |
| reconnect | UNKNOWN | UNKNOWN | UNKNOWN | server 侧已知行为：重新 initialize 清掉 elicitation 授权（ADR 0009），会话随进程走。WorkBuddy 何时重启 server、重启后权限缓存是否清零，只能 L3 量 |
| locale | PASS（L2） | UNKNOWN | UNKNOWN | hostContext `locale: en-US` → 画布整页英文（顶栏、属性页、预检列表）；会话中途 `host-context-changed` 切 `zh-CN` 只切了一半（P2-2） |
| theme | FAIL（P2） | UNKNOWN | FAIL（P2） | hostContext `theme: dark` → 画布仍是浅色（`data-theme: null`、`color-scheme: normal`、body `rgb(247,246,243)`）。画布从不读 `theme`，Tavotto 前端也没有暗色主题 |

L1 全部场景：`python3 tests/workbuddy/probe.py {handshake,no-authority,deny,full,roots,large,no-engine,no-widget}`；
L2：`cd web && WB_SPIKE_PYTHON=<装了 tavotto 的解释器> npx playwright test e2e/workbuddy-host.spec.ts --project=chromium`
（5/5 通过，三条变异各自打红：不缓存「始终允许」、拒绝仍转发、关掉延迟回拉）。

![L2：c01 拖动 4 次、预检、导出之后](./workbuddy-fakehost-c01-exported-2026-09-23.png)
![L2：拒绝 apply 之后](./workbuddy-fakehost-deny-2026-09-23.png)
![L2：448 元素大图经取件进 ready](./workbuddy-fakehost-large-2026-09-23.png)
![L2：en-US + dark 宿主](./workbuddy-fakehost-en-dark-2026-09-23.png)

### 本次踩到并已排除的假红（留作方法论）

L1 第一轮量到「PDF 导出一律 `artifact_rejected`、规范化 `acceptance_failed`」。逐层缩小后确认
**是探针造的环境**：探针给 server 设了 `PYTHONPATH=<树>/src`，它同样对**启动器自己的解释器**
（Homebrew `python3`）生效，resolver 的候选顺序是「当前解释器」在 `TAVOTTO_MCP_PYTHON` 之前，于是
server 跑在了一个能 import tavotto、却缺必需依赖 PyMuPDF 的解释器上。去掉 PYTHONPATH 后同一流程
全绿。探针与 L2 都已写死「不许带 PYTHONPATH」并注释原因。它顺带暴露了两条与 WorkBuddy 无关的
小问题，记在 P2。

## Compatibility matrix

| 组件 | 判定 | 依据 |
| --- | --- | --- |
| Tavotto Engine | **Reusable unchanged** | L1/L2 全程零改动；会话、override、预检、导出、重放全部落回 `tavotto.engine.*` |
| MCP bridge（`tavotto_mcp/bridge.py`） | **Reusable unchanged** | 同上；10 个工具语义与宿主无关 |
| MCP server（`tavotto_mcp/server.py` + 启动器） | **Reusable with adapter** | 协议、体积预算、降级模式都与宿主无关；要改的是**文案**：`roots.py` 的下一步写死「重启 Codex」、降级 server / health 写「新开一次 Codex 会话」、「Codex 里的内嵌画布」（`roots.py` 10 行、`tavotto_mcp/server.py` 13 行、`bridge.py` 23 行、启动器 19 行提到 Codex，部分是注释） |
| roots（`RootAuthority`） | **Unknown** | 五个来源里，WorkBuddy 能落进哪一个取决于它是否声明 `roots` / `elicitation`——文档没写。若两者都没有：`cwd` 是连接器目录会被拒（按设计），只剩 `TAVOTTO_MCP_ROOTS` 这一格（见推荐方案） |
| widget metadata（`widget.py`） | **Reusable unchanged**（待 L3 确认） | 标准 `_meta.ui.resourceUri` 是第一等公民；`ui/resourceUri` 与 `openai/*` 别名 WorkBuddy 文档未提、应被忽略。一处疑点：资源描述里**没有 `size`**，WorkBuddy 若靠声明体积判断 >256 KB，会把 1.35 MB 当小资源内联进 ACP 通知 |
| appsBridge（`web/src/mcp/appsBridge.ts`） | **Reusable unchanged** | 裸 JSON-RPC over postMessage、对 host 请求一律回 `{}`（teardown 安全）、`window.openai` 只作 feature-detect 兜底；在异源 sandbox proxy + 内层 iframe 里工作正常 |
| Canvas（`web/src/canvas/*` + `web/src/mcp/*`） | **Reusable with adapter**（小） | 同一份产物 L2 全通过；缺 theme 跟随（P2-1）、中途切 locale 半生效（P2-2）、启动屏写「Connecting to Codex…」（P1-3） |
| Skill（`skills/tavotto-figure/`） | **Reusable with adapter** | 图文件契约 / 出版风格 / 兼容性 / issue 草稿与宿主无关；入口状态机与 `first-run-and-recovery.md`（117 行里 17 行是 Codex 安装与「新开会话」）要写 WorkBuddy 版 |
| runtime resolver（`codex-plugin/mcp/server.py`） | **Reusable with adapter** | A（开发机）可用；B（只有桌面版）仍只能 `--provision`；C（全新机器）当前架构**不够**：MCP server 与画布不在 wheel 里（`pyproject.toml` 的 `exclude` 挡住 `codex-plugin/`，`[project.scripts]` 只有 `tavotto`），pip 装完也起不了 MCP server |
| 版本 / 发布通道 | **WorkBuddy-specific** | 连接器包格式（`connector-meta.json` + `mcp.json` + `icon.svg` + `skills/`）与 Codex 插件清单不同，需要一条独立的打包出口 |

## Blockers

**只列会阻止上线的。** 带「待 L3」的条目是「现在不知道是不是 blocker」，不是「已确认」。

### P0

1. **工作区授权没有已知的可信入口（待 L3）。** WorkBuddy 文档没有 roots / elicitation / 工作区
   的任何描述；连接器 `mcp.json` 的 `cwd: "."` 是连接器目录，`RootAuthority` 按设计拒绝它。如果真
   客户端两样都不声明，Tavotto 在 WorkBuddy 里**打不开任何一张图**（`no_workspace_root`）——这不是
   bug，是边界在正确地 fail-closed。不许用 `TAVOTTO_MCP_ROOTS=/` 或「信任模型给的路径」绕过。
2. **真客户端未验证（L3 缺席）。** 1.35 MB 资源在真 WorkBuddy 里是否真走延迟回拉、ACP 通知有没有
   类似 Codex 的 1 MiB 事件上限、反向 `tools/call` 的真实授权形态——都只有文档说法。

### P1

1. **全新机器装不上（Q6-C）。** 当前 MCP server 只随 Codex 插件分发；WorkBuddy v5.0.0+ 的托管
   Python 运行时能装 PyPI 包，但 PyPI 上的 `tavotto` 不含 MCP server 与画布。需要一个通用
   `tavotto-mcp` 入口（server + 画布进 wheel 或独立包）。
2. **大图显示前就要授权一次。** 大图的 open 结果按 ADR 0069 省略 manifest，画布必须经反向
   `tools/call tavotto_session_state` 取件——在 WorkBuddy「反向调用默认弹框」的模型下，用户在**看见
   图之前**先面对一个权限框；且每个会话里 session_state / apply / preflight / export 各弹一次（按
   (server, tool) 计），`/clear` 或重启后全部重来。拖动本身不会每次弹（不是 P0 UX blocker），但首屏
   体验要设计。可能的缓解要先问官方：`_meta.ui.visibility` / 只读工具是否可免授权。
3. **宿主名写死成 Codex 的用户可见文案。** 画布启动屏（`splashConnecting`「Connecting to Codex…」、
   `splashNoHost`）、授权失败的下一步（「重启 Codex 再试」）、降级 server 与 health（「新开一次 Codex
   会话」「Codex 里的内嵌画布」）。WorkBuddy 用户会被指去操作一个不存在的应用。

### P2

1. 画布不读 `hostContext.theme`，前端也没有暗色主题；暗色宿主里是一块浅色画布（Codex 同样如此）。
2. 会话中途 `host-context-changed` 改 `locale`：属性页 / 预检列表切过去了，`McpApp` 顶栏（`mc()`
   文案）没有重渲染。WorkBuddy 文档写 locale「不变」，影响小。
3. 拒绝授权在画布上显示为「渲染失败」——宿主的拒绝文字会被原样摆出来，但状态徽标说的是渲染失败。
4. resolver 只验 `import tavotto.engine`，不验必需依赖（PyMuPDF）；这样的解释器上每次 PDF 导出都
   `artifact_rejected`。MCP 那条错误只有 code，丢了 `error_params.failed`（哪项检查没过）。
5. 资源描述不带 `size`（见 matrix 的 widget metadata 一行）。

## Recommended architecture

原方案：

```text
Tavotto Buddy App
        ↓
Tavotto Connector
        ↓
generic Tavotto MCP Runtime
        ↓
Tavotto Engine + Canvas
```

**证据支持下面三层，不支持（也不否定）最上面那层。**

* **Engine + Canvas：支持「One Canvas, Multiple Hosts」。** 同一份 `canvas.html` 在 Codex（08-24 /
  09-22 真机）与 WorkBuddy 形状宿主（L2）里都跑完整闭环，没有一处需要 fork；host 差异（theme、
  文案）都是 feature detection / 文案层的事。
* **generic Tavotto MCP Runtime：支持，而且是 Q6-C 的唯一解。** 当前 server 已经与宿主无关（L1），
  缺的是**分发**：把 `tavotto_mcp` + 画布做成可 pip 安装的 `tavotto-mcp` 入口，WorkBuddy 连接器
  声明托管 Python 运行时去装它；Codex 插件也可以改为调用它，两边不再各带一份。
* **Tavotto Connector：支持，但它的核心不是包格式，是工作区授权。** 若 L3 证明 WorkBuddy 声明
  elicitation 或 roots，现有 `RootAuthority` 原样可用；若都不声明，最小且不破坏安全边界的适配是：
  连接器用 token 模式的表单（`token-schema.json`，凭证只存本机、以环境变量注入 stdio 子进程）让**用户
  亲手填**允许的目录，注入成 `TAVOTTO_MCP_ROOTS`——这落在 `RootAuthority` 已有的第一档「服务器所有者
  的显式配置」，不需要改 `roots.py`。这条路是否可行（token 字段能否注入 stdio env）待 L3。
* **Tavotto Buddy App：没有证据。** 本次没有验证任何 Buddy 首页 / 场景能力，按任务书也不该验证。

## 需要向 WorkBuddy 官方确认的问题

1. MCP client 在 `initialize` 里声明哪些 capability？支持 `roots`（及 `roots/list`）吗？支持
   `elicitation/create`（form 模式）吗？在 `tools/call` 执行期间发 server→client 请求会被转给用户吗？
2. stdio 连接器进程的 `cwd` 是什么（连接器目录 / 当前工作空间 / 其他）？会不会注入任何「当前工作
   空间」的环境变量或 URI？有没有一个「用户选择的目录」能力给 MCP server？
3. `>256 KB 不预取` 是按什么判断的——实际读一次的体积，还是 `resources/list` 里声明的 `size`？
   ACP 通知（工具结果推给 UI）有没有体积上限？超了是截断、清空 `structuredContent`，还是报错？
4. 反向 `tools/call` 的授权：「始终允许」能否在连接器层面按工具预先声明（例如只读工具、或
   `_meta.ui.visibility: ["app"]` 的工具）？`permissions.allow` 是用户侧还是连接器侧配置？
5. iframe 何时创建——工具开始（`tool-input`）时还是结果返回时？sandbox proxy 生成的 CSP 原文是什么？
6. token 模式（`token-schema.json`）的字段能否以环境变量注入 **stdio** 连接器？
7. v5.0.0 托管 Python 运行时：Python 版本、能否指定 `pip install` 的包与版本、隔离环境放在哪、升级
   时怎么处理？
8. 连接器能否在本地开发时直接从一个目录 / zip 导入（不经市场）？日志在哪里？

## L3 真客户端验收清单（装好 WorkBuddy 并登录之后）

目标：每一格都从**录制日志**判，而不是从截图猜。录制代理 `tests/workbuddy/record_proxy.py` 夹在
WorkBuddy 与真 server 之间，只旁听不改帧，日志落 `~/tavotto-workbuddy-record/record-*.jsonl`。

1. 记录 WorkBuddy 版本（关于页）与本树 `git rev-parse HEAD`；画布产物先 `python scripts/build_mcp_widget.py --check`。
2. 连接器管理页 →「自定义连接器」，按它要的 MCP 配置格式填（**不要**设 `TAVOTTO_MCP_ROOTS`、不设 `cwd`）：

   ```json
   {
     "mcpServers": {
       "tavotto": {
         "command": "python3",
         "args": ["<本树>/tests/workbuddy/record_proxy.py"],
         "env": { "TAVOTTO_MCP_PYTHON": "<装了 tavotto 0.16.0 的解释器>" }
       }
     }
   }
   ```

   信任 / 启用后，看第一份 jsonl 的 `start`（cwd、PATH、有没有工作区相关变量）与 `initialize` 帧
   （`params.capabilities`、`clientInfo`、`protocolVersion`）。→ 填 **Q1 / Q5**。
3. 新对话里只让它调 `tavotto_health`；保存 `root_authority`。→ 填 **Q1**、**Q5**（`source`、`client.capabilities.advertised`）。
4. 让它用**绝对路径**打开 `tests/acceptance/corpus` 的 `c01_line`。
   * 日志里出现 `elicitation/create` → 先**拒绝**：结果必须是 `workspace_confirmation_declined`、无会话；再开一次并**批准**。
   * 出现 `roots/list` → 记下宿主回的 roots。
   * 两者都没有 → 结果应是 `no_workspace_root`：**Q5 = BLOCKER**，停在这里，改做第 9 步的 token 方案探查。
5. 画布出现后截图；日志里找：`resources/read` 是否由宿主在 open **之后**发出（延迟回拉）、画布有没有发
   `tavotto_session_state`（小图不该发）。→ **Q2**。
6. 在画布里拖图例 ×1：权限框截图、选「始终允许」；日志里应恰好多一条 `tools/call tavotto_apply_overrides`
   且 `sc.ok: true`。再拖 ×3：数权限框次数（应为 0）与 apply 条数（应为 3）。→ **Q3 / Q4**。
7. `/clear` 后再拖 ×1、以及断开重连连接器后再拖 ×1：各弹不弹框。→ **Q4**、reconnect。
8. 第一次拖动就选「拒绝」（新会话里）：日志里**不应**出现 apply；画布保留上一版图像。→ denial。
9. 大图：同样的连接器打开 `tests/workbuddy/fixtures/large` 的 `big_fig`：open 结果应带 `elided`；日志里
   应有一条画布发的 `tavotto_session_state`，没有第二次 open；截图画布进 ready。→ large figure。
10. 切 WorkBuddy 语言（中 / 英）、主题（亮 / 暗），各截一张新会话的画布。→ locale / theme。
11. 预检、导出 PDF（画布里勾「仍要导出」）；确认 PDF 落在 corpus 目录下。→ preflight / export。

任何一步缺证据就把对应格子留在 UNKNOWN，不以 L1/L2 的绿替代。
