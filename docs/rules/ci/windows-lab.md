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
- 一次性凭据只经 SSH stdin 进那一次运行的宿主进程环境，Lab 不落盘；证据落盘前抹掉凭据。证据字段按
`docs/implementation/multi-host-mcp/acceptance.md`。
