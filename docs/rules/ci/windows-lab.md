# Windows Lab：常驻 Windows 真机的信任边界、任务闭集与代理纪律

> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 运维手册（初始化、日常命令、恢复、月度维护）在 `docs/ci/windows-lab.md`；实测验收记录在
> `docs/implementation/windows-lab/acceptance.md`。结果闭集沿用 ADR 0131（`agent-verification.md`）。
> 看护用例：`tests/test_winlab.py`

<!-- agents-route
sheet: .github/AGENTS.md
order: 135
改到: `scripts/winlab/`
主题: Windows Lab 常驻真机 → `windows-lab.md`
必守要点: 只跑维护者代码、不注册 runner；ForceCommand 只放行登记任务；构建只直连、代理无 direct 出站；凭据不进仓库
-->

## 定位

Windows Lab 是一台常驻的腾讯云 Windows Server 2025（4 vCPU / 8 GB），由维护者的 Mac
经 SSH 驱动，用来给 hosted `windows-latest` 拿不到的东西补**真机证据**：zh-CN / GB2312 代码页、
Defender 实时防护开、`LongPathsEnabled=0` 的默认系统，以及真实 AI 宿主 CLI。

- **它不是 GitHub runner，也不替代 CI。** 不注册 runner、不监听任何 workflow 事件，所以
  `runner-trust-zones.md` 的标签闭集不变；required checks、merge queue 一概不碰。CI 的覆盖面是门禁，
  Lab 的结论只是 PR 正文里的附加证据。
- **可信度：只跑维护者审过的代码。** `winlab.py` 在送代码之前判定：提交已在 `origin/main` 上，
  或 `origin/main..提交` 里每个提交的作者**与**提交者都是本机 `git config user.email`。
  别人的 PR、fork、用户提交的脚本、对抗样本一律拒绝，去一次性隔离环境——
  Compatibility Guardian 也**不**迁到这台机器。
- `agent_verify.py` 仍是本地 Agent 入口（「不上 self-hosted」不变）。Lab 只复用它的
  结果闭集、`aggregate` 与 `judge_pytest`：`agent.py` 部署时同目录带一份，不另写一套。

## 访问边界

- 两个账户：`administrator` 只给人工维护与 `winlab.py admin …`；日常一律 `tavotto-lab`（非管理员）。
- `tavotto-lab` 的公钥经 `Match User` 指到 `C:\ProgramData\ssh\lab_keys\`，并且
  **`ForceCommand` 只进 `C:\TavottoLab\tools\winlab\agent.cmd`**，禁 TTY 与一切转发。
  这把钥匙在服务器端就拿不到任意 shell；Claude 的放行清单只是第二道线，不是唯一一道。
- 执行器装在 `tools\winlab\`，测试账户只读：测试代码改不了执行器。
- **不复用连接**：Win32-OpenSSH 在一条复用（ControlMaster）连接上只设一次 `SSH_ORIGINAL_COMMAND`，
  后续会话执行的仍是第一条命令（2026-10-24 实测）。`winlab.py` 固定 `ControlMaster=no`，本机别名也不开复用。
- SSH 只开公钥（`PasswordAuthentication no` + `KbdInteractiveAuthentication no`），本机
  `StrictHostKeyChecking yes`，**不许** `=no` 或 `accept-new` 绕过。
- 连接参数只在本机 `~/.ssh/config`（别名 `tavotto-win-lab` / `tavotto-win-lab-admin`），不进仓库。

## 任务闭集与结果

- 动词闭集：`doctor` `status` `prepare` `test` `reports` `clean` `stop`。任务闭集在
  `agent.py` 的 `TASKS`，每条写 `claim` 与 `unverified`；`winlab.py` 导入同一份做参数预检。
- 参数可审计、没有透传通道：`--tests` 只收 `tests/…\.py(::节点)?`，`--shard` 只收 `N/M`，
  网络模式只收登记值。新增任务就是在 `TASKS` 加一条，argv 只调仓库里已有的验证器。
- 结果用 ADR 0131 的闭集，**任何非 passed 都不聚合成 passed**：skip 让结论降为 `skipped`；
  前置不满足（venv 缺、代理入口不在、宿主未登录）是 `environment_unavailable`。
  对外的验收表映射为：passed → PASS、failed / error → FAIL、environment_unavailable → BLOCKED、
  not_run / skipped → NOT TESTED（skipped 要写清跳过了什么）。
- 每次运行一个 run id（`YYYYMMDDTHHMMSSZ-xxxxxx`）、一份 `report.json`（含提交、环境事实、
  证据 sha256）；日志可能含本机路径，只留在 Lab，不贴进公开 PR。
- 测试进程不设 `PYTHONUTF8`、不设 `PYTHONDONTWRITEBYTECODE`（与 CI 一致）：在用户默认态下跑——ANSI 代码页、
  照常写 `.pyc`——正是 Lab 的价值之一；要「不留字节码」的验证器在自己的子进程里设。

## 交互会话执行器（真窗口）

- SSH 在 session 0，没有用户桌面：真窗口、WebView2 渲染、窗口消息一律交给**已登录的交互会话**。
  `tavotto-lab` 开机自动登录控制台（密码在 LSA 机密 `DefaultPassword`，**不进注册表**）；它名下的计划任务
  `TavottoLab-GUI`（`/IT`，只在交互会话运行）拉起 `gui_host.py`，按 agent 写进 `state\gui\` 的请求、用同一套
  Job Object 跑登记任务的 argv，结果写回。会话不在 → `environment_unavailable`，**绝不退回 session 0 假装跑过**；
  结果里的 `session_id` 为 0 也判 error。`stop` 同时给交互会话那一半留停止标记。
- **交互会话请求不装密钥**：`request.json` 明文落盘，只放公开环境与一个 `env_pipe` 管道名；密钥（`run_in_gui(secret_env=…)`，
  按**值**从环境里拆出，不看变量名）由 session 0 的 agent 建一条命名管道——单实例、拒绝远程客户端、DACL 只给 `tavotto-lab` 自己
  的 SID——写一次即关句柄（不 `DisconnectNamedPipe`：断开后读端下一次 read 得 errno 22；关掉则是正常 EOF，管道名随之消失），
  `gui_host.py` 读一次合进子进程环境后即关，管道名必须是 agent 生成的形状（不认任意路径）；读不到就不跑命令。
  证据落盘前仍照旧抹掉凭据。
- 不要以 `tavotto-lab` 身份 RDP：会抢走控制台会话（断开后桌面不渲染）。人工维护用 administrator 的独立会话。
- WebView2 调试端口（127.0.0.1:9222）由 HKLM 策略为 `Tavotto.exe` / `Tavotto beta.exe` 常开——与 nightly 同一条
  策略，只是在专用机上不每次删。msedgedriver 按本机 WebView2 版本从微软下载点取，核 Authenticode 与版本号。

## 资源与清理

- 同一时刻只有一个任务（`state\job.lock`，持有者进程已不在才回收）。
- 每个任务一个 Job Object：`KILL_ON_JOB_CLOSE` + 6 GB 内存上限，子进程挂进 Job 前保持挂起；
  超时、`stop`、任务结束都会结束整棵进程树。
- `clean` 只删 `runs\` 下 run id 形状的目录，正在跑的不删；判断不靠字符串前缀：父目录与目标
  都不许是链接 / junction，规范化后父目录必须就是 `runs\`，递归时遇到链接只删链接本身。
- 不为测试回滚系统、重装、格式化；黄金快照的创建与回滚要用户另行授权。

## 代理纪律（DMIT 是用户的日常节点，优先级高于 Lab）

- Lab 上的 sing-box 以 SYSTEM 常驻，两个只绑 `127.0.0.1` 的 mixed 入口：7890 → Trojan、
  7891 → Hysteria 2，`set_system_proxy: false`；配置里**没有 direct 出站**，私网 / 本机域名
  `block`，`final: block`——代理坏了请求只会失败，不会悄悄直连。TLS 校验永不关。
- 模式按**单个进程**的环境变量选（`direct` 去掉全部代理变量），从不改系统 / WinHTTP 代理；
  `NO_PROXY` 固定包含回环、`::1`、`100.64.0.0/10`、`.ts.net`，本地 MCP 与 tailnet 流量不进代理。
- 只有 `network` 任务（与将来获批的真实宿主联网验收）走代理；pytest、构建、依赖、安装包一律直连
  （国内镜像 + Authenticode 核签名）。一次只一个联网任务、小请求、有限样本。
- 唯一例外是 `desktop-hosts` 的安装包（用户 2026-10-25 授权）：只有海外 CDN 上的固定版本安装包经 DMIT 下载，
  国内 CDN 的（VS Code、TRAE CN、WorkBuddy）照旧直连；一次一个、每个有体积上限、下完先核散列再核签名，
  缓存后不重复下载。走哪条线路写死在 `DESKTOP_HOSTS` 里，不经 `--network` 开放。
- `desktop-live` 的**扩展包**（`.vsix`，写死在 `DESKTOP_EXTENSIONS`）同一规矩，范围只限登记的固定版本：Open VSX 公布的
  sha256 与代码里钉的逐字一致，经 DMIT 一次一个、有体积上限、散列不符即删，代理入口不在就是 `environment_unavailable`，
  绝不直连。Open VSX 的证书链止于 Starfield Root CA G2，而 Lab（大陆）的 Windows 根存储里没有它：这一条下载路径优先用
  venv 里 certifi 的 CA 包（报告 `trust_source=certifi`），venv 没有 certifi 才退回只多信**一个**钉死 DER 散列的公开根
  （`pinned-root`）；不改系统根存储、不影响别的下载，TLS 校验照旧全开，信任源里没有这条链就如实 `environment_unavailable`。
- 代理模式下先核出口：经代理取到的出口 IP 的 sha256 必须等于 `proxy-install` 写下的那个散列，
  否则 `failed`；入口不在就是 `environment_unavailable`，不探测、不回落。
- 凭据只在本机 `~/.config/tavotto-winlab/proxy.json`（0600）与 Lab 的 `secrets\`（仅 SYSTEM /
  Administrators）；不进仓库、AGENTS、报告、CI 日志、PR、终端输出。DMIT 服务端只读，
  不改配置、不重启、不升级。

## AI 宿主分层

**判定只认 Tavotto 这一侧的事实**：`hosts-live` 把宿主的 MCP 配置指向 `mcp_recorder.py`（透明转发字节、只记方法名 /
工具名 / 成败 / `session_id`·`patch_hash`·`ok` 三个有界字段，参数与结果正文不记），L3 = 宿主自己发了 `tools/list`
并拿到 Tavotto 工具，L4 = 有成功的 `tools/call`，L5 = 改图 → 导出 PDF（执行器核 `%PDF-`）→ 重放一致 → 会话日志里
真有修改 → **全新宿主进程**只凭 `session_id` 恢复，`session_id` 与 `patch_hash` 都与第一轮编辑后一致。各宿主同一套判据，
不解析宿主各自的输出格式。宿主的用户级配置隔离在 run 目录（`CODEX_HOME` / `CLAUDE_CONFIG_DIR` / 隔离的
`USERPROFILE` / `DSH_HOME`），宿主自报「要登录官方账户」且一次工具都没调到 → BLOCKED，不是 FAIL。

L1 网络可达（`network`）→ L2 官方客户端可装可启动 → L3 真宿主自己连上 Tavotto MCP →
L4 真宿主执行工具调用 → L5 科研绘图任务到编辑、保存、重开的闭环。低层通过不推出高层。
`hosts` 任务只到 L3，且只认宿主 CLI 自己的连接检查（`claude mcp list`）；只读配置的
（`codex mcp list`）记 `config_tested`，不升格。模拟 MCP client 永远不算宿主验收。L4/L5 需要
用户授权的账户与真实模型调用，未授权就是 BLOCKED。
- **地区政策**：Lab 在中国大陆。Anthropic / OpenAI 的真实模型调用不在这台机器上做——经代理出口调用等于绕开服务商
  的地区限制，授权解决不了政策问题；这些格保持 BLOCKED。L4/L5 用国内合规的 Anthropic 兼容接口驱动**真宿主**
  （`hosts-live`），报告写明宿主与模型分别是谁，不把它写成「Claude 模型通过」。
- **桌面宿主 `desktop-hosts --mode <id>` 只做 L2**：安装包的 URL 与 SHA-256 固定在 `agent.py` 的 `DESKTOP_HOSTS`
  （取自 microsoft/winget-pkgs 的清单），下载后核散列、核 Authenticode 为 Valid；非管理员静默装（currentUser）；
  `window_probe.py` 在交互会话里启动它，**主窗口**出现才算——可见、未被 DWM 隐藏、有标题、尺寸像窗口、属于安装目录
  里的进程、连续稳定 5 s；然后结束它起的全部进程并卸载。VS Code 系用 run 目录里的 `--user-data-dir`。宿主自带的
  Agent 要各自账户的一律 BLOCKED；要什么由 `desktop-native --mode <宿主>` 实测记录（见下）。Claude Desktop 的标准模式在 Lab
  首屏即「App unavailable in region」，只装、只起到这一屏；L3+ 只走下面的 `claude-desktop-native`（第三方推理）。
- **原生 Agent 首屏探查 `desktop-native --mode <宿主>`**：装法同 L2，`native_probe.py` 在交互会话里起宿主（调试端口同下只绑回环；
  宿主拒绝这个开关就不带它再起一次、只记窗口标题与截图），等首屏文字连续两次取样不变且不是「准备中」（最多 240 s），记下每个
  页面的文字与截图，**不打字、不登录、不调模型**；唯一允许的点击是 `NATIVE_PEEK` 里按宿主登记的那一个入口按钮（逐字，如 ZCode 的
  「Use API key」），只为看下一屏要填什么。报告 `native.login_markers` / `byok_markers` 是启发式词表，截图才是证据；L3+ 仍记 BLOCKED。
- **桌面宿主 + 其中的 AI 扩展 `desktop-live --mode <宿主-扩展>`（L3–L5）**：宿主 × 扩展的闭集在 `DESKTOP_LIVE`
  （`vscode-claude` / `cursor-claude` / `trae-claude`：三个 VS Code 系宿主 + 同一个 `anthropic.claude-code`；`zcode-native`：
  ZCode 自己的 Agent，见下），宿主装法同 L2，
  扩展包固定版本与散列（见代理纪律），用宿主安装目录里自带的命令行入口（`bin\*.cmd`，Cursor 在 `resources\app\bin\`，不在 PATH 里找）
  `--install-extension` 装进 run 目录的 `--extensions-dir`。**判据与 `hosts-live` 同一个 `judge_live`，一字不改**：
  宿主里的扩展经 `mcp_recorder` 指向的 Tavotto MCP 做事，L4/L5 看的还是录制器记下的服务端事实。区别只有谁发话：
  `desktop_driver.py`（交互会话一侧、标准库）起宿主，等同 L2 的主窗口，经 CDP 进扩展 webview，把提示词填进输入框并点发送，
  等录制器安静**且面板不忙**（首个流量后 45 s 无新行、面板上没有 Stop 按钮；上限 1200 s，到上限如实报 `max`），存证（截图 + 面板文字，不参与判定），按 Job 结束进程树。第二轮是
  **全新的宿主进程**，只拿 `session_id`。
  - **打开面板与首启引导**：VS Code 走扩展的 URI 入口（`vscode://…/open`），不认再走命令面板；Cursor 打开扩展 URI 前弹模态确认框，
    TRAE 不登记协议，二者只走命令面板（最多试三次，每次先 Esc）。首启引导页（TRAE 的 `setup.html`）**只点 `ONBOARDING_BUTTONS`
    白名单**里逐字相等的按钮（开始 / 跳过 / 下一步 / 继续 / 稍后…，不含登录、注册、授权、同意），点了什么进 progress；引导后宿主换
    进程重开窗口时 CDP 重连，「宿主退出」= 起的进程退了**且**安装目录下没有顶层窗口。
  - **CDP 只绑回环**：只传 `--remote-debugging-port=<随机空闲端口>`、从不传 `--remote-debugging-address`，驱动器实测该端口
    在本机非回环地址上连不上（连得上就中止）；跑完随 Job 一起关，不进代理（`NO_PROXY` 含回环）。选择器随固定版本的扩展钉死。
  - **进程链判据（L3 的前置条件，加在 `judge_live` 之外）**：录制器带 `--host-root <宿主安装目录>`，首个 `initialize` 时多记
    `client`（clientInfo 的 name / version）与 `ancestry`（父进程链**映像名**，最多 8 级；链上祖先的创建时间必须早于孩子，防 pid
    复用）和 `host_ancestor`（祖先里最近一个映像在安装目录下的路径）。不记命令行参数。`host_ancestor` 为空 = L3 failed，
    且 L4/L5 一并 failed（那条流量不是宿主里的扩展发的）；第二轮同理，否则 L5 failed。
  - **配置隔离**：隔离的 `CLAUDE_CONFIG_DIR`，`claude mcp add` 用扩展随包的 `claude.exe` 写 tavotto → 录制器；该目录的
    `settings.json` 事先定好全部权限，面板里不留没人点的弹窗：放行 `mcp__tavotto` 与只读的 Read / Glob / Grep（工具结果超过扩展的
    MCP 输出上限会落成文件，模型要读回来），Bash / PowerShell / Write / Edit / Web* / Task 明确 `deny`（拒绝立即回给模型）；
    不用 `bypassPermissions`；VS Code 的 `settings.json` 只有行为开关（含 `claudeCode.disableLoginPrompt`），
    **不写任何密钥**，扩展从宿主进程环境取 `ANTHROPIC_BASE_URL` 等，密钥经上面的命名管道进环境。
  - **结论取值**：扩展要求登录官方账户（只看**扩展自己 webview** 的文字；宿主自己的登录提示，如 Cursor 的 AI 功能要登录，不算）
    且一次工具都没调到 → `environment_unavailable`（BLOCKED）；驱动器没能操作面板
    （没找到输入框、填不进、点不了发送）→ `error`，不推定是产品问题；宿主明确拒装扩展（版本不兼容）→ levels 记
    `not_supported`、整次 `skipped`（NOT TESTED），附 `ext-install` 日志与截图，**不记 FAIL**（宿主限制，不是 Tavotto 的故障）；
    其余装不上 → `failed`。`not_supported` 只出现在 levels 里，不是 ADR 0131 结果闭集的新成员。
  - **原生 Agent 格 `zcode-native`**（`DesktopLive(native=True)`）：不装扩展，用 ZCode 自己的 Agent。首启只点
    `NATIVE_ONBOARDING_BUTTONS`（Use API key / Skip for now / Skip…，**不含** Continue——API Key 表单上的 Continue 会提交空 key——
    也不含任何 Connect / 登录）；输入框认第一个可见的 textarea / 可编辑区；面板出现权限确认框（`PERMISSION_PROMPT_RE`）就如实以
    `permission_prompt` 收场。隔离：`ZCODE_DATA_BASE_DIR=<run>\work\zdata`；模型写 `zdata\.zcode\v2\provider_config.json`
    （`anthropic-messages`，DeepSeek 的 `…/anthropic/v1`）；运行配置（MCP → 录制器、权限）写项目目录、`zdata\.zcode\cli\` 与
    非项目对话的默认工作目录 `zdata\.zcode\workspace\default\.zcode\` 三处——只写前两处时桌面版读不到。权限：`allowedTools` 只列
    tavotto 的**完整工具名**（从服务端 `HANDLERS` 现读；ZCode 不认前缀，桌面版也不认配置里的 `yolo`），执行 / 写入 / 上网的内置工具
    明确禁用。进程链判据同上（`host_ancestor` = `ZCode.exe`）。
  - **落盘例外（`DESKTOP_LIVE_ONDISK_MODES` = `zcode-native`，用户 2026-10-26 同意；`claude-desktop-native` 见下，2026-10-27）**：ZCode 只从明文 `provider_config.json`
    读 key、不支持环境变量替换。所以这一格用**另一把专用、设了消费上限的 key**——用户在自己的终端 `secret-init deepseek --ondisk`
    录入，存本机 `model-ondisk.json`（0600），与默认那把相同就拒收；缺了就不跑，**绝不退回默认那把**。key 只写本次 run 的
    `zdata`；`run_desktop_live_task` 的 `finally` 里 `wipe_ondisk_key` 删掉该文件、抹掉 run 目录里其它出现，再扫 run 目录与 ZCode 的
    应用数据目录（`%APPDATA%` / `%LOCALAPPDATA%` 下 `ZCode*`，发现即删）核对**一处都没有**；留下了就整次 `error`。报告带
    `ondisk_key: {wiped, residual, unreadable, checked_app_dirs}`——核对流式扫任意大小的文件，读不了的算没核对完；单个文件抹不掉不中断收尾；
    收尾放在卸载外面的 `finally`（卸载抛异常也清）；上一次 run 被强行结束时，下一次 `zcode-native` 开跑前先删掉所有旧 run 里残留的
    `provider_config.json`（报告 `swept_stale_ondisk_configs`）。默认那把（hosts-live 与其它 desktop-live 格）照旧不落盘。
  - **原生 Agent 格 `workbuddy-native`**：WorkBuddy 自己的 Agent，推理用**登录账户自带的模型**（界面可见，如 GLM-5.3-Flash；
    报告 `model.provider = workbuddy-account`），不经我们的任何 key。登录态是例外，见下条。首启跳过 `splash.html`；先选工作区
    （选择器已显示项目文件夹名就不点；否则菜单「Open Local Folder」→ 系统选文件夹框：先点掉挡在前面的「位置不可用」类报错框，
    再把路径写进控件 id 1152 / ComboBox 里的输入框、按 IDOK，没关就再按，最多三下；每步记 `dialog_diag`）。原生宿主的编辑器不认
    `execCommand` 填的字（发送键一直灰）：先真实点一下输入框、再 CDP `Input.insertText`，落空才退回 `execCommand` + 真实空格退格；
    发送依次试回车 / 按钮 / Ctrl+Enter，每试一次截图。MCP 写常驻家目录的 `.workbuddy\mcp.json`（每次 run 改指本次的录制器）；
    用户自加的 MCP 要在连接器页点「Trust」才加载——执行器替这一步在同目录 `mcp-approvals.json` 只记 tavotto 一条（键 =
    WorkBuddy 5.8.3 `computeTrustConfigHash` 的指纹 `::tavotto`），报告 unverified 写明。Tavotto 的会话日志在宿主的
    `LOCALAPPDATA`（常驻家目录下）里找。进程链判据同上（`host_ancestor` = `WorkBuddy.exe`）。
  - **登录态常驻例外（仅 `workbuddy-native*`，用户 2026-10-27 同意）**：WorkBuddy 要账户登录。用**专用测试账户**；登录方式是
    驱动器在本次 run 自己的 Edge 配置里打开登录页、每 15 s 截 `login-qr.png`，Mac 端经只读的 `winlab shot` 取回给用户手机扫，
    **聊天里不收密码 / 验证码**。登录态常驻 `state\workbuddy-home`（该宿主的整个用户目录：`USERPROFILE` / `APPDATA` /
    `LOCALAPPDATA` 都指过去），不进证据、不随 run 删，下一次 run 直接复用；`winlab sweep --workbuddy-logout` 结束 WorkBuddy
    进程、重置它自设的 ACL、删掉整个目录并清凭据管理器里的 WorkBuddy 条目，撤销即用它。每次 run 的 Edge 登录配置照旧随 run 删、
    开跑前扫掉旧 run 的残留。`workbuddy-native-phone` 是手机号 + 短信验证码的登录方式：值只经 `winlab login-input` 的 stdin
    送进正在跑的 run，驱动器读到即删；**未实测通过**（短信未到）。
  - **原生 Agent 格 `claude-desktop-native`（用户 2026-10-27：「不连接claude的官方服务，只用DeepSeek连接」）**：Claude
    Desktop 的官方第三方推理（Claude Desktop on 3P），`inferenceProvider = gateway` → DeepSeek 的 Anthropic 兼容接口，**不登录
    Anthropic、不调 Anthropic 模型**。官方文档「Disabling all Anthropic-bound connections」的六条全关（两类遥测、非必要服务、
    自动更新、模型目录、WebFetch 预检）；Cowork 与 Code 关，只开 Chat；执行 / 写入 / 上网的内置工具与应用自带的工作区 shell
    `mcp__workspace__bash` 列进 `disabledBuiltinTools`。tavotto 经 `managedMcpServers` 起本地 stdio → 录制器，`toolPolicy` 只放行
    tavotto 自己的工具名。模型：应用只收指向 Anthropic 模型名的路由，所以路由名是 `claude-sonnet-4-5`、显示名「DeepSeek (…)」，
    DeepSeek 按其官方文档把 `claude-sonnet*` 映射到 `deepseek-flash`；报告 `model.route` 与 `model.model` 分开记。
    - **配置位置**：本地 `configLibrary` 被忽略（标准模式先加载 claude.ai、在地区页停住），只有用户级策略
      `HKCU\SOFTWARE\Policies\Claude` 能让它进 3P。普通用户写不进 Policies，所以 **`winlab admin claude-policy-acl`（用户同意的
      一次性管理员动作）**只建这一个键、只授 tavotto-lab `SetValue, ReadKey`，**不写任何值、不经手 key**；每次 run 由 agent 以
      tavotto-lab 身份写值（含 key，落盘例外同 ZCode：`secret-init deepseek --ondisk` 那把专用限额 key），`wipe_ondisk_key` 跑完删光
      全部值并核对（`ondisk_key.policy_key_present` 必须为 false）；`configLibrary` 也照写一份在 run 家目录与真实 `LOCALAPPDATA`，同样删。
    - **唯一经 DMIT 的流量**（用户 2026-10-27：「如果一定要下载，就是用dmit吧」）：会话开始时应用要从 `downloads.claude.ai` 取内置
      的 Claude Code（直连超时，会话起不来）。本次 run 在回环上起一个 PAC（`egressProxyPacUrl`）：**只有这一个主机**
      `PROXY 127.0.0.1:<trojan>`，其余（含 DeepSeek 推理）`DIRECT`；DMIT 没在听就 `environment_unavailable`，不退回直连。报告
      `egress` 写明。安装包只用 L2 时已缓存、已核散列的那份，这一格从不下载安装包。
    - **驱动**：应用拒绝调试端口，不走 CDP。`--force-renderer-accessibility` + PowerShell 的 UI Automation 读控件树；首屏只点
      `UIA_ONBOARDING_BUTTONS`（Continue / Skip / Not now / Get started 逐字相等），名字带 sign in / Claude.ai / account 的一律不点；
      输入用真实键鼠（点输入框、剪贴板 + Ctrl+V、回车）；面板有 Stop / responding / Preparing 时不判安静。宿主自报地区不可用就
      `environment_unavailable`（BLOCKED），**不绕**。Squirrel 的 `claude.exe` 是启动壳（1 s 就退），等安装目录下的主窗口。
    - **已知余项**：这一版的工作区 shell 配置关不掉——报告 `host_tool_uses` / `host_shell_commands` 从会话记录里列出模型实际用过的
      工具名与命令原文（截 300 字）；验收那次它只跑了一条只读的 `ls /sessions/*/mnt/`（找 Tavotto 落成文件的大结果）。
  - CC-Switch 的落盘例外**不在此**（单独的 PR 与单独的规则条目）。
- 一次性凭据只经 SSH stdin 进那一次运行的宿主进程环境（交互会话里再经命名管道），除上面登记的 `zcode-native` / `claude-desktop-native`（key）与 `workbuddy-native*`（账户登录态）外 Lab 不落盘；证据落盘前抹掉凭据。证据字段按
`docs/implementation/multi-host-mcp/acceptance.md`。
