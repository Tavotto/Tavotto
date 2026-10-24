# ADR 0149：AI 修复工具箱——一张注册表、五档风险、先做「看」和「试」

日期：2026-10-23 · 状态：**提议**（方案页已由维护者批准；本 PR 只落 D1：注册表 + 风险档 + 「看」「试」两组 + 读取范围 + 本地审计）
相关：[0147 内置执行器](0147-builtin-agent-runner.md)（工具由它的循环调用）、[0138 TAF](0138-tavotto-ai-foundation.md)（§四 执行面不扩大）、
[0139 来源驱动采用](0139-source-backed-figure-adoption.md)（依赖解析口径）。智能修复路由（ADR 0148）若已合入，本 ADR 只提供它调用的工具，不改它的路由。

## 问题

脚本跑不起来时（缺包、解释器选错、后端不对），用户今天只能看一句报错再自己查。让 AI 去查是值得的，但「给 AI 一个 shell」
把用户的整台机器放进了它的执行面：读到凭据、装包进用户环境、联网都没有边界。需要一个**有名字、有档位、有记录**的工具箱。

## 决定

### 一、四组工具，本 PR 做前两组

| 组 | 做什么 | 档 | 本 PR |
|---|---|---|---|
| 看 | 只读：运行报告、读文件 / 列目录 / 找文件 / 搜文本、环境清单与详情、日志尾部、导入名→包名 | `observe` | 做 |
| 试 | 在指定环境起一次性子进程试一下：能不能 `import X`、脚本在临时副本里跑一次、环境体检（`inspect_environment`） | `run` | 做 |
| 改 | Tavotto 自己的东西（项目设置、记住环境）；清单里的动作（装包进 Tavotto 管理的环境） | `tavotto` / `listed` | D2+ |
| 外 | 改用户环境、联网查 PyPI、对外发信 | `user_env` / `outward` | D2+ |

「试」是 **`run` 档**，**不再宣称「试跑什么都不改」**：它要运行用户的代码（`try_run` 的脚本、`try_import` 的导入副作用、`inspect_environment` 的探测），而进程内的写入绊线有盲区（C 扩展 / C 层 `fopen` / `ctypes` 的写绕得过去）。
`run` 档的含义：**以与准备会话 safe 运行同等的保护运行用户代码**——写入绊线（`writetripwire`，ADR 0141）+ 禁止起进程 + 只在缓存里的副本 / 专用目录（`fix_trials/<会话>-<随机>/`，副本 + 专用 TMPDIR + mpl 配置）里跑 + 本地审计；守卫装不上就拒绝（`trial_unguarded`），不裸跑。
**纵深**：有操作系统沙盒就再套一层只许写专用目录的沙盒——macOS `/usr/bin/sandbox-exec`（profile：`(version 1)(allow default)(deny file-write*)(allow file-write* (subpath "<试跑目录 realpath>") (literal "/dev/null") (literal "/dev/tty"))`）、Linux `bwrap`（`--ro-bind / / --dev /dev --proc /proc --tmpfs /tmp --bind <试跑目录> <试跑目录>`，不动网络）；找不到 / 探测起不来就退回绊线，结果里一律带 `os_sandbox: macos-sandbox-exec|bwrap|none` 让模型知道。Windows 暂无（`none`，另开 issue）。
**已知盲区**：`os_sandbox: none` 时 C 层 / `ctypes` 的写不被拦；读与网络任何情形下都不受限（本档只关写）。用户点「使用你的 AI 来修复」开启修复会话即视为授权这一档：`FixContext.for_fix_session(...)` 默认放行 `observe` + `run`，裸构造的 `FixContext` 仍只放行 `observe`。
**native 方式的试跑不属于 observe / run 档**（它用用户自己的环境，写入面不由我们圈定），本 PR 拒绝 `mode=native`，留给 D2 以 `user_env` 档 + 用户确认实现。子进程输出只留 64 KiB 尾部，合计超 8 MiB 即结束子进程（`output_truncated`）。
副本放在 `data_dir()/cache/fix_trials/<会话>-<随机>/`（不放系统临时目录），删除带重试，删不掉如实报 `cleanup_pending` 并留标记，`sweep_stale_trials()` 清残留（每次 `try_run` 开头调用）。

### 二、五档 tier

`observe`（只读）→ `run`（以 safe 同等保护运行用户代码，见上）→ `tavotto`（只改 Tavotto 自己的状态）→ `listed`（清单里预先点过头的动作）→ `user_env`（改用户自己的环境，每次问）→ `outward`（出站）。
`ToolSpec` / `Tool` 新增 `tier`（默认 `observe`）与 `waits_for_user`（默认 `False`）；**两者只给 Tavotto 的策略层看，不序列化给模型**
（三种 wire 仍只发 name / description / schema），既有的五个改图工具不变。调用入口 `run_tool` 在会话允许的档之外一律拒绝（`tier_not_allowed`，审计记 `blocked`）。

### 三、读取范围（维护者裁决：整个用户主目录，敏感目录仍硬拦）

* **允许**：用户主目录下任意路径 + 项目根 + 当前项目所用 Python 环境的前缀（可能在主目录外：`/opt`、`/usr/local` 的 conda / venv）。其余（系统目录等）拒绝。
* **硬拦 `DENY`**（规范化 + 解析符号链接后判定；词法路径与解析后路径各判一次；macOS / Windows 上大小写不敏感）：
  `~/.ssh`、`~/.gnupg`、`~/.aws`、`~/.azure`、`~/.kube`、`~/.docker/config.json`、`~/.config/gcloud`、`~/Library/Keychains`、`~/.netrc`、`~/.pypirc`、
  `~/.git-credentials`、`~/.codex/auth.json`、`~/.claude/.credentials.json`、Tavotto 自己存 API key 的 `config.json`、任意 `.env*`、
  **按文件名只拦明确的凭据文件形状**（整名或固定后缀，**不用 `*token*` / `*secret*` 这类子串通配**，`token_counts.csv`、`password_strength.py` 可读）：`token` / `.token` / `*.token`、`credentials` / `credentials.{json,toml,yml,yaml}` / `*-credentials.json`、`client_secret*.json`、`secrets.{json,yaml,yml,toml}`、`*.secret` / `*.secrets`、`service-account*.json`、`password.txt` / `passwords.txt`、`*.kdbx`、`*.pem` / `*.key` / `*.p12` / `*.pfx` / `*.keystore` / `*.jks` / `id_*`（私钥形状；`.pub` 公钥除外）；**目录不按名拦**（`tokenized-data/` 可读、可当 find / grep 的根），目录只由上面的固定位置表拦；Chrome / Chromium / Edge / Brave / Firefox / Safari 的 profile 目录。符号链接指向拦截区也拦。
* **单次读取上限 256 KiB**：更大的给前 N 行 + 截断说明（可用 `start_line` / `max_lines` 续读）；二进制只给类型与大小。
* **项目外的读取记入会话的 `reads` 清单**，供卡片详情列出（「AI 看过项目外的哪些位置」）；跟随文件符号链接时记**解析后的目标**（项目内目标不记）。
* **读到的内容一律先脱敏**：诊断包同一套文本脱敏（`diagnostics.redact_text`：密钥形状、主目录、用户名、邮箱）+ 私钥块、`key=value` / 命令行旗标形的密钥（**键名后缀族** × 值形状：裸值 / 单引号 / 双引号，含内部空格，整体抹掉。键名——驼峰 / 下划线 / 连字符 / 点分、大小写不敏感——以 `key` / `token` / `secret` / `password` / `passwd` / `pwd` / `credential(s)` / `auth` / `signature` / `sig` 结尾即凭据：`AccountKey`、`SharedAccessKey`、`clientSecret`、`aws_secret_access_key`、`x-ms-signature`、`sas_sig`；`primary_key` / `sort_key` / `cache_key` / `monkey`、`max_tokens` 等整键匹配的非凭据豁免，光秃秃的 `key` 只在值带引号时才抹）、`;` 分隔的连接串（同一规则逐项处理，`AccountName` 保留、`AccountKey` 抹）与 `scheme://user:password@host` 的 password、`Bearer` 令牌，最后一道**高熵兜底**：长度 ≥ 32、字符集 `[A-Za-z0-9+/=_-]`、同时含大写 / 小写 / 数字、且不是路径 / 标识符（被 `/` `_` `-` 切开后纯字母单词 ≥ 3 个）的连续串抹成 `***`（小写十六进制哈希、UUID、路径、包文件名保留）。
  find / grep 有条数与墙钟上限，跳过拦截区、`node_modules`、`.git`、`site-packages` 深层，且不跟随目录符号链接；
  **试跑副本**对项目里的**文件**符号链接：目标经 `ReadScope.check` 可读就把目标内容复制成副本里的同名普通文件（脚本照常读得到），目标在拦截区 / 不可读 / 悬空则不复制；目录符号链接不跟随。没复制的在结果里写 `copy_incomplete: N linked file(s) were not copied (…)`（目录同样一行），不写链接目标；
  `grep_files` 是**字面量**搜索（不接受正则，从根上没有灾难性回溯）。
* **fixtools 启动的每个子进程**（`try_run` / `try_import` / `inspect_environment`）都走同一条受守卫的执行路径：safe 写入根绊线 + 缓存目录下的专用临时目录 + 统一的重试清理与残留清扫；守卫装不上就不执行。

### 四、本地审计

`data_dir()/ai/fix_audit.jsonl`，每次调用一行：`session`、`step`、`tool`、`tier`、参数摘要、`approved_by`（`policy` | `user` | `blocked`）、`result{ok, code, summary}`。
路径类参数保留，其余字符串只记长度；**工具返回的文本与文件内容一概不落盘**。超过 1 MiB 滚成 `.1`（只留一代）；审计失败不影响工具调用。

### 五、两种接法共用一张注册表

`engine/fixtools/__init__.py` 的 `REGISTRY: dict[str, FixTool]`（规格含 tier + `handler(ctx, args)`）与接法无关。内置执行器经
`build_agent_tools(ctx)` 适配成原生工具；CLI 经 MCP 的接法（D3）读同一张表、走同一个 `run_tool`（档位检查、脱敏、审计不会因接法而不同）。
工具拿到**注入的 `FixContext`**（项目根、假主目录、环境 id → 解释器表、报告回调……），不 import app。环境一律用会话给出的**环境 id** 指认，不接受任意解释器路径。

### 六、维护者三条裁决（2026-10-23）

1. 读取范围是整个用户主目录，不是只读项目目录；敏感目录仍硬拦。
2. 「试」单设 `run` 档（原与「看」同档的说法被 Codex 第六轮推翻：运行用户代码不是只读）；不逐次问用户，开启修复会话即授权这一档。
3. 审计只在本地，不上传、不进遥测；内容不落盘。

## 本阶段不做

`tavotto` 及以上各档的工具；MCP 接法；PyPI 联网查询（`lookup_package` 只查内置映射表，D2）；接进 `ai_bridge` / 设置页 / 卡片 UI（`reads` 清单先挂在上下文对象上）；
`try_run` 接准备会话 / 运行器的现成执行路径（接入面大，D1 用独立的一次性子进程 + 可注入的执行器，见规则文档）；
`inspect_environment` 的包清单只覆盖有 `importlib.metadata` 的 Python（3.8+）。

## 后果

* AI 查问题的能力有了边界：能看什么、能试什么、查了什么都有据可查；以后加改动类工具只是往同一张表里登记新档。
* 代价：脱敏是形状规则，认不出的私有格式会漏——所以拦的是**位置**（DENY）在先，脱敏是第二道。
* 规则全文：`docs/rules/backend/ai-agent-bridge.md`「AI 修复工具箱」一节；看护 `tests/test_fixtools.py`。

**修订（2026-10-23，Codex #1353 第五轮）**：写入绊线只管本解释器，子进程不在它的视野里，所以「试」的子进程里**一律不许再起进程**（`subprocess.Popen` / `os.system` / `os.exec*` / `os.spawn*` / `os.posix_spawn` / `os.fork` / `pty.spawn` / `os.startfile` 由第二个 audit hook 拦下、记进 `blocked_spawns` 并告诉模型）。体检探测代码本身也不起进程（不用 `platform.platform()`，它会起 `uname` / `file`）。仍有的盲区：C 扩展在 C 层直接写文件——要彻底关住只能靠操作系统级沙盒，不在本 PR 范围内。复制副本改为惰性枚举、过上限即只复制脚本；复制到一半失败时结果在清理之后才组装（照样报 `cleanup_pending`）。

## 七、D2a：「改」组、快照与回滚（2026-10-23）

「改」组六个工具登记进同一张注册表（`engine/fixtools/change.py`）：`install_packages`（`listed`：常见大包名单内不问、名单外 `ctx.ask`）、`switch_execution`（`tavotto`）、`change_python`（`tavotto`）、
`select_environment`（`user_env`，一律问）、`convert_script`（`tavotto`，见文末 2026-10-24 修订：只交接、不写文件）、`rollback`（`tavotto`）。
**同意由工具自己经 `FixContext.ask` 取得**：`FIX_SESSION_TIERS` 扩成 observe / run / tavotto / listed / user_env / outward（放行 ≠ 不问）；没有 `ask` 或回答不是 yes 一律 `needs_approval`、审计 `approved_by=blocked`，用户点了 yes 记 `user`。
会改东西的工具执行前先打快照（项目设置两个键 + 目标环境 `pip freeze` + 环境目录清单，`data_dir()/cache/fix_snapshots/<会话>/`），审计行多 `snapshot`；打不出快照就不改；`rollback` 设置精确恢复、包按差异卸 / 装回。
**不新写任何执行路径**：装 / 卸包走 `deprepair._pip_install` / `_pip_uninstall` + 环境独占锁，切运行方式走 `execpolicy.set_mode`，换环境走 `projectenv.remember`；`change_python` 的执行器由 `FixContext` 注入（#1342 / D2b 接）。
`switch_execution` 的 `safe` / `native` 落在既有的项目级 `execution_policy`（`safe_only` / `compatible`，即「允许 safe 被自己的守卫拦下时回退 native」）上；放宽用户明确选过的 `safe_only` 要先问。
同批修 #1361 三条（试跑副本跳过特殊文件、环境列表如实报截断并可筛、经链接复制的项目外读取记入 `reads`）。规则全文见 `docs/rules/backend/ai-agent-bridge.md` 的「D2a」一节。
**`run_command` 不在 D2a，移到后续 PR（已由 #1374 的修订落地，见文末）**：它要在操作系统沙盒里跑任意命令，而沙盒逃逸面太大——Linux 桌面上 bwrap 只隔离挂载，`/run/user/<uid>/bus` 仍可连，命令能 `systemd-run --user` 让宿主的 user manager 改项目；macOS 同类（Apple Events 让 Finder 写、`launchctl` 等）。
需要能抵御进程代理逃逸的沙盒后再做：Linux 隔离 `/run` 与 D-Bus 等、macOS 限制 mach-lookup / Apple Events，Windows 见 #1358。这台机器上要改东西只能用专用工具。

**修订（2026-10-23，D2b）**：「问与报」组落地（`fixtools/ask.py`）与修复会话（`engine/fixsession.py`）：`ctx.ask` 由会话实现，卡片经 SSE 提示 + `GET/POST /api/ai/fix/<id>[/answer|/cancel]` 与前端交互；
`pick_file` 的路径过 `ReadScope.grant`（敏感硬拦照旧，只放被选的路径）；`prepare_diagnostics` 只弹「发送」卡，上传仍是 ADR 0118 的逐次确认流程，不附源码（用户在对话框里自己勾）。
`ai_fix_available` 在 D2b 仅在有可用的 API 连接时为真（CLI 经 MCP 的接法见下一条 D3 修订）。内置执行器的循环在本进程线程里跑（工具要碰应用状态），不起 `agent_runner` 子进程。

**修订（2026-10-24，D3）**：只连了 Codex / Claude Code（没有 API 连接）的用户也能用「使用你的 AI 来修复」：CLI 经 Tavotto 自己的 MCP 服务的**修复模式**调用同一张 `REGISTRY`。
`ai_fix_available` = 有内置循环能用的 API 连接**或**有可用的 Codex / Claude Code；`POST /api/ai/fix` 先看 API 连接（走 D2b 的内置循环，不变），没有才起 CLI。
**结构**：MCP 服务（`tavotto fix-mcp`，`engine/fixmcp.py`，纯标准库）一个工具也不自己执行——`tools/list` 与 `tools/call` 经本机回环 HTTP 回调应用进程里正在跑的修复会话
（`GET …/tools`、`POST …/tool`），由 `run_tool(tool, 会话上下文, 参数, 审计)` 执行，所以 `ctx.ask` 弹卡、改动前快照、审计、项目作用域全是 D2b 那一份，MCP 层没有第二套档位 / 同意。
回调凭**每个修复会话一枚的 scoped token**（`secrets` 生成、`compare_digest`、只对该会话的这两个路径有效，会话结束 / 取消即失效，同 `status_token` 的做法，`security.py`）；
令牌只经环境变量传（CLI 自己的环境 → MCP 子进程），不进 argv / 日志 / 事件 / 审计。CLI 的 stdout 读掉丢弃，**结论只认 `finish` 工具**；退出时没调 `finish` = `failed`（闭集原因 `cli_no_finish` / `cli_exit` / `cli_start` / `budget`）。
**只读口径（维护者裁决：接受 Codex 这边的风险，两边都开放）**：修复会话里 Claude 与 Codex 的自带命令行都只许**只读**。Claude Code 经本次启动参数 `--settings` 的 `permissions`：
`Read(...)` deny 规则从 `readscope` 硬拦表**现生成**（不手抄第二份），写类 / 联网工具全 deny，只允许 MCP 工具、`Read` / `Grep` / `Glob` 与一小张只读命令白名单，`--permission-mode dontAsk`（没明确允许的一律拒、不弹确认）；
Codex 用 `--sandbox read-only`——它的自带命令行**读不受限**（凭据位置的 DENY 管不到它），写被沙盒拦。这是已知并接受的风险，不是遗漏。
所有改动只能经 MCP 工具（fixtools）。MCP 配置只出现在本次启动参数里，**不写 `~/.codex`、`~/.claude`**；Tavotto 不读、不存 CLI 的登录凭据。
**不在 D3**：Windows 沙盒、让 CLI 自带命令行也走审计（做不到，它不经 `run_tool`）。规则全文：`docs/rules/backend/ai-agent-bridge.md` 的「D3」一节。

**修订（2026-10-24，D3 真机后）**：① 「项目还没有可用环境」不许直接放弃：提示词（内置循环与 CLI 同一份）规定先 `propose_fix` 一句话说清整套方案，用户确认后
`change_python`（新建 Tavotto 受管环境，`version` 可省，缺省按脚本 / 项目声明与支持区间挑；执行器是 `pyversionfix` 的计划 + 执行，应用侧默认注入）→ `install_packages` 到新环境 id → `try_run` → `finish(fixed)`。
② 三种模式（Codex CLI / Claude CLI / 内置循环）都**看得见在干什么、卡住时用户知道原因**：会话公开状态多 `activity`（最近一次工具活动）与 `stalled`（现在卡住了及原因）；
看门狗发现「CLI 报告 MCP 调用开始而应用 20 秒没收到回调」（`cli_tool_not_delivered`）、「CLI 在等它自己的审批」（`cli_waiting_approval`）、「120 秒既无工具活动也无输出」（`stalled`），
卡住持续 5 分钟自动取消，`outcome=failed`、原因码即上述之一，并进 `fix_audit.jsonl`（`tool=_session`）与诊断包「修复经过」。等用户点卡片、工具自己在跑都不算卡住。
③ 从源头减少 CLI 审批：Codex 对本服务 `default_tools_approval_mode="approve"`；**`--ignore-user-config` 只在 Tavotto 已显式给了 provider + 型号（用户为 Codex 选了第三方接口）时加**，
去掉用户自己的 MCP 服务与 hooks；官方登录态不加（默认型号 / 认证方式可能就写在用户的 config.toml 里，忽略它会换掉用户的设定），那种情况靠看门狗兜底。Tavotto 仍不读 CLI 的凭据文件。
Claude 的 `--permission-mode dontAsk` 下未放行的工具直接拒绝（不等待），stream-json 里的拒绝读出来进 `activity` 与审计，不算卡住。

**修订（2026-10-24，#1374：`run_command` 回来，两档）**：D2a 拆掉的 `run_command` 按用户裁决「两档都要」落地（`fixtools/command.py` + `fixtools/cmdsandbox.py`），`tier=user_env`，每条命令一张 approve 卡（卡上是完整命令）。
**第①档（默认）= 加固沙盒内执行**：可写只有命令自己的临时目录（cwd 与 TMPDIR）、目标 Python 环境前缀、pip / conda 的用户缓存；项目与其余一切只读，凭据位置（`readscope.DENY_HOME_RELATIVE`）连读都拒；**允许联网**（pip / conda / R 装包要用）。
#1371 评审点名的「沙盒只管文件、管不住宿主里本来就有权限的代理进程」逐条堵：Linux bwrap 把 `/run`（含 `/run/user/<uid>/bus`、docker.sock）与 `/tmp`（X11 路径套接字）换成空 tmpfs，`--unshare-ipc/pid/uts`、`--new-session`、`--die-with-parent`，环境里清掉 `DBUS_*` / `XDG_RUNTIME_DIR` / `DISPLAY` / `WAYLAND_DISPLAY` / `SSH_AUTH_SOCK` / `GPG_AGENT_INFO` 等；
macOS sandbox-exec 在「只许写清单内目录」之上 `deny appleevent-send`、`deny mach-lookup`（只放行一张来自 Apple `system.sb` 的最小只读白名单）、`deny process-exec`（osascript / launchctl / open 等代理入口）、`deny network-outbound (remote unix-socket)`（只放 mDNSResponder）、`deny signal`（只放 self / children / same-sandbox）。硬拦表照旧并补上代理入口（`osascript` `launchctl` `open` `systemd-run` `systemctl` `busctl` `docker` `crontab` `schtasks` …）与包装器看穿（`env sudo x`）。
**如实写下的盲区**：Linux 上**抽象命名空间 unix 套接字**仍可达（不 `--unshare-net`，要联网装包）。评估过 bwrap 的 `--seccomp`：BPF 读不到 `connect` 的地址（在用户内存里），所以无法「只拦抽象地址」；只能整个禁 `socket(AF_UNIX)`，那会弄坏 asyncio / multiprocessing，`socketpair` 加数据报 `connect` 还能绕——**不做**；要在沙盒外做事走第②档。
**第②档 = 沙盒外执行（用户单独点头）**：触发只有两种——模型显式 `outside_sandbox: true` 加一句 `reason`，或第①档因沙盒拒写失败后结果里**提示**模型可以这样申请；**从不自动升级**（第①档失败后不会无卡执行第②档）。弹一张单独、红色强调的卡（approve 卡的 `danger: true`）：完整命令 + 「这条命令会在沙盒外运行，可以修改你电脑上的文件」+ 模型的理由；主按钮「在沙盒外运行」、次按钮「不了」；只对这一条命令有效，不记成长期授权。
执行前备份项目内全部用户脚本（按后缀保守纳入、读原始字节不要求能解码，跟随链接按真实路径去重，**1000 个 / 32 MiB 封顶，超限直接拒绝第②档**），审批之后取基线、**持这些脚本的锁**（`scriptlock.script_guards`，非阻塞抢全、抢不齐退避）跑完命令，事后核对，被改 / 被删的经 `scriptlock` 恢复原字节并报 `scripts_restored`，恢复失败如实报 `scripts_restore_failed`。命令退出后先结束并确认整棵进程树再核对；放不下卡片的命令审批前就拒（用户看到的 = 实际执行的）。**这一层是尽力而为，不是保证**（盲区见规则文件的「run_command」一节）。
Windows（#1358 无沙盒）与没有 bwrap 的 Linux 只有第②档，第①档返回 `sandbox_unavailable` 并说明。两档共同：不经 shell、cwd 默认临时目录（第②档可选项目目录并在卡上写明）、去掉密钥类环境变量、超时默认 60 s / 上限 300 s、输出有界并脱敏、执行前打快照、审计行多 `sandbox: hardened|outside|none` 与 `command_tier`、看门狗照旧（工具在跑不算卡住）。
**修订（2026-10-24，convert_script 交接）**：`convert_script` 不再自己转写，改为**交接**给 ADR 0143 已有的「生成可编辑版本」流程。原设计要 `FixContext.script_converter` 同步写出 `<原名>_matplotlib.py`，但生产里从没人注入它，AI 永远拿到 `unavailable`。
现在：工具只校验（项目内的 `.py`、`library` 取 `rasterhint.LIBRARIES` 或取报告里的位图提示、`ai_convert.enabled()`），在 `ctx.handoff` 记 `{kind, script, library}`，不写文件、不打快照；AI 随后 `finish(outcome="convert")`（`convert` 只有已交接才合法），
会话公开状态带 `handoff`，前端（`aiFixStore.settle`）清掉修复条目、记下这张准备卡交接给了哪个脚本，并启动 `convertStore.start`，同一张卡接上 ADR 0143 的进度 / 对照 / 「采用」。
理由：① 复用已验证的候选工作区、运行校验、对照视图与 O_EXCL 新建兄弟脚本，不再造第二条写脚本的路径；② 不在修复会话里嵌套第二个 AI（费用、取消与审计都只有一层）；③ 原脚本一个字节不动，用户在对照视图点「采用」才是同意。
`script_converter` 字段与「临时副本 → 执行器 → 核对 / 恢复」整段删除（`snapshots.restore_script` 留给 `run_command` 第②档恢复脚本用）。规则细则见 `docs/rules/backend/ai-agent-bridge.md` 的 D2a 一节。

**修订（2026-10-24，`link_data_file`：缺数据时 AI 找、用户点头、接上）**：新工具 `link_data_file`（`fixtools/link.py`，`tier=tavotto`，`waits_for_user`）。缺数据（报告 `missing_input`）时 AI 用 `find_files` / `read_file` 在项目、往上的各级目录与主目录里找同名文件并与脚本用法对照；
唯一且确定就直接 `link_data_file`（卡片里写依据），多个或不确定先 `ask_user` 列候选（带「都不是，我自己选」→ `pick_file`），再接上、`try_run`、`finish`。**一律弹 approve 卡**，没有界面 = `needs_approval`；
同意后先打快照（项目设置新增 `input_remap` 键）再走与数据失联弹窗**同一个** `inputremap.link`（ADR 0106 修订）。`requested` 只认报告 `missing_input` 载荷与脚本源码里找不到的常量，且必须是改指救得回的读取；`chosen` 过 `ctx.scope`；脚本永不改。
`try_run` 的守卫进程里装上同一份规则（`figcapture.install_input_remap`，按文件加载），否则接上的数据在试跑里验证不了。规则应用后通过 `FixContext.on_input_remap_changed` 通知应用（收会话、取消试运行、告诉窗口，与端点同一串）。

