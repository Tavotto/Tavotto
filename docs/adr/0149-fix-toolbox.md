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
`select_environment`（`user_env`，一律问）、`convert_script`（`tavotto`，另存 `<原名>_matplotlib.py`，原脚本不动）、`rollback`（`tavotto`）。
**同意由工具自己经 `FixContext.ask` 取得**：`FIX_SESSION_TIERS` 扩成 observe / run / tavotto / listed / user_env / outward（放行 ≠ 不问）；没有 `ask` 或回答不是 yes 一律 `needs_approval`、审计 `approved_by=blocked`，用户点了 yes 记 `user`。
会改东西的工具执行前先打快照（项目设置两个键 + 目标环境 `pip freeze` + 环境目录清单，`data_dir()/cache/fix_snapshots/<会话>/`），审计行多 `snapshot`；打不出快照就不改；`rollback` 设置精确恢复、包按差异卸 / 装回。
**不新写任何执行路径**：装 / 卸包走 `deprepair._pip_install` / `_pip_uninstall` + 环境独占锁，切运行方式走 `execpolicy.set_mode`，换环境走 `projectenv.remember`；`change_python` / `convert_script` 的执行器由 `FixContext` 注入（#1342 / D2b 接），没注入返回 `unavailable`。
`switch_execution` 的 `safe` / `native` 落在既有的项目级 `execution_policy`（`safe_only` / `compatible`，即「允许 safe 被自己的守卫拦下时回退 native」）上；放宽用户明确选过的 `safe_only` 要先问。
同批修 #1361 三条（试跑副本跳过特殊文件、环境列表如实报截断并可筛、经链接复制的项目外读取记入 `reads`）。规则全文见 `docs/rules/backend/ai-agent-bridge.md` 的「D2a」一节。
**`run_command` 不在 D2a，移到后续 PR**：它要在操作系统沙盒里跑任意命令，而沙盒逃逸面太大——Linux 桌面上 bwrap 只隔离挂载，`/run/user/<uid>/bus` 仍可连，命令能 `systemd-run --user` 让宿主的 user manager 改项目；macOS 同类（Apple Events 让 Finder 写、`launchctl` 等）。
需要能抵御进程代理逃逸的沙盒后再做：Linux 隔离 `/run` 与 D-Bus 等、macOS 限制 mach-lookup / Apple Events，Windows 见 #1358。这台机器上要改东西只能用专用工具。
`convert_script` 保留持锁结构（单个脚本，没有枚举竞态）：转换器只拿临时副本，原脚本哈希变了用执行前读到的原字节经 `scriptlock` 恢复。
