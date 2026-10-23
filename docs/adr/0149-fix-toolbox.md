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
| 试 | 在指定环境起一次性子进程试一下：能不能 `import X`、脚本在临时副本里跑一次 | `observe` | 做 |
| 改 | Tavotto 自己的东西（项目设置、记住环境）；清单里的动作（装包进 Tavotto 管理的环境） | `tavotto` / `listed` | D2+ |
| 外 | 改用户环境、联网查 PyPI、对外发信 | `user_env` / `outward` | D2+ |

「试」仍是 `observe` 档：不写用户文件、不装东西；`try_run` 跑的是脚本的**临时副本**，且**在 safe 的写入根绊线（`writetripwire`，ADR 0141）下**跑，写入根只有本次试跑目录（副本 + 专用 TMPDIR），越界写被拦并如实报告；守卫装不上就拒绝（`trial_unguarded`），不裸跑。
**native 方式的试跑不属于 observe 档**（它用用户自己的环境，写入面不由我们圈定），本 PR 拒绝 `mode=native`，留给 D2 以 `user_env` 档 + 用户确认实现。子进程输出只留 64 KiB 尾部，合计超 8 MiB 即结束子进程（`output_truncated`）。
副本放在 `data_dir()/cache/fix_trials/<会话>-<随机>/`（不放系统临时目录），删除带重试，删不掉如实报 `cleanup_pending` 并留标记，`sweep_stale_trials()` 清残留（每次 `try_run` 开头调用）。

### 二、五档 tier

`observe`（只读 / 一次性试验）→ `tavotto`（只改 Tavotto 自己的状态）→ `listed`（清单里预先点过头的动作）→ `user_env`（改用户自己的环境，每次问）→ `outward`（出站）。
`ToolSpec` / `Tool` 新增 `tier`（默认 `observe`）与 `waits_for_user`（默认 `False`）；**两者只给 Tavotto 的策略层看，不序列化给模型**
（三种 wire 仍只发 name / description / schema），既有的五个改图工具不变。调用入口 `run_tool` 在会话允许的档之外一律拒绝（`tier_not_allowed`，审计记 `blocked`）。

### 三、读取范围（维护者裁决：整个用户主目录，敏感目录仍硬拦）

* **允许**：用户主目录下任意路径 + 项目根 + 当前项目所用 Python 环境的前缀（可能在主目录外：`/opt`、`/usr/local` 的 conda / venv）。其余（系统目录等）拒绝。
* **硬拦 `DENY`**（规范化 + 解析符号链接后判定；词法路径与解析后路径各判一次；macOS / Windows 上大小写不敏感）：
  `~/.ssh`、`~/.gnupg`、`~/.aws`、`~/.azure`、`~/.kube`、`~/.docker/config.json`、`~/.config/gcloud`、`~/Library/Keychains`、`~/.netrc`、`~/.pypirc`、
  `~/.git-credentials`、`~/.codex/auth.json`、`~/.claude/.credentials.json`、Tavotto 自己存 API key 的 `config.json`、任意 `.env` / `.env.*`、
  `*.pem` / `*.key` / `*.p12` / `id_*`（私钥形状；`.pub` 公钥除外）、Chrome / Chromium / Edge / Brave / Firefox / Safari 的 profile 目录。符号链接指向拦截区也拦。
* **单次读取上限 256 KiB**：更大的给前 N 行 + 截断说明（可用 `start_line` / `max_lines` 续读）；二进制只给类型与大小。
* **项目外的读取记入会话的 `reads` 清单**，供卡片详情列出（「AI 看过项目外的哪些位置」）；跟随文件符号链接时记**解析后的目标**（项目内目标不记）。
* **读到的内容一律先脱敏**：诊断包同一套文本脱敏（`diagnostics.redact_text`：密钥形状、主目录、用户名、邮箱）+ 私钥块、`key=value` / 命令行旗标形的密钥（一张敏感键名表 × 值形状：裸值 / 单引号 / 双引号，含内部空格，整体抹掉）、`Bearer` 令牌。
  find / grep 有条数与墙钟上限，跳过拦截区、`node_modules`、`.git`、`site-packages` 深层，且不跟随目录符号链接；
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
2. 「试」与「看」同档（`observe`），不单独设一档、不逐次问用户。
3. 审计只在本地，不上传、不进遥测；内容不落盘。

## 本阶段不做

`tavotto` 及以上各档的工具；MCP 接法；PyPI 联网查询（`lookup_package` 只查内置映射表，D2）；接进 `ai_bridge` / 设置页 / 卡片 UI（`reads` 清单先挂在上下文对象上）；
`try_run` 接准备会话 / 运行器的现成执行路径（接入面大，D1 用独立的一次性子进程 + 可注入的执行器，见规则文档）；
`inspect_environment` 的包清单只覆盖有 `importlib.metadata` 的 Python（3.8+）。

## 后果

* AI 查问题的能力有了边界：能看什么、能试什么、查了什么都有据可查；以后加改动类工具只是往同一张表里登记新档。
* 代价：脱敏是形状规则，认不出的私有格式会漏——所以拦的是**位置**（DENY）在先，脱敏是第二道。
* 规则全文：`docs/rules/backend/ai-agent-bridge.md`「AI 修复工具箱」一节；看护 `tests/test_fixtools.py`。
