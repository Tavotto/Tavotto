# Windows Lab 运维手册

规则（信任边界、任务闭集、代理纪律）的唯一全文在 `docs/rules/ci/windows-lab.md`；这里只讲怎么做。
实测验收记录：`docs/implementation/windows-lab/acceptance.md`。

## 机器

| 项 | 值 |
| --- | --- |
| 云 | 腾讯云轻量（中国大陆），4 vCPU / 8 GB / 12 Mbps，24 h 在线 |
| 系统 | Windows Server 2025 Datacenter（Desktop Experience），zh-CN，ANSI 代码页 GB2312 |
| 保持默认 | Defender 实时防护开、`LongPathsEnabled=0`、不设 `PYTHONUTF8`——都是用户机器的常态 |
| 账户 | `administrator`（人工维护）、`tavotto-lab`（非管理员，全部自动化） |
| 工作区 | `C:\TavottoLab\{repo,runs,cache,reports,profiles,tools,logs,state,secrets}` |

公网地址、密钥路径只在维护者本机 `~/.ssh/config`：

```sshconfig
Host tavotto-win-lab          # 日常：User tavotto-lab，IdentityFile <lab 用户私钥>
Host tavotto-win-lab-admin    # 人工：User administrator，IdentityFile <管理员私钥>
#   两者都要 StrictHostKeyChecking yes、IdentitiesOnly yes
```

## 从零初始化（新机或重装后）

人工部分（云控制台 / RDP，一次）：

1. RDP 进去，以管理员 PowerShell 启用 OpenSSH Server、放行 22、`DefaultShell` 设成 Windows PowerShell，
   把管理员公钥写进 `C:\ProgramData\ssh\administrators_authorized_keys`（ACL 只留 Administrators / SYSTEM）。
2. 本机 `ssh-keygen -R <IP>` 后首次连接**人工核对**主机指纹（RDP 里 `ssh-keygen -lf C:\ProgramData\ssh\ssh_host_ed25519_key.pub`）。

其余全部在 Mac 上：

```sh
python3 scripts/winlab/winlab.py admin bootstrap     # 目录 + ACL、Python/Node/pnpm/Git/pwsh/Tailscale/sing-box、代理常驻任务
python3 scripts/winlab/winlab.py admin deploy-agent  # 执行器进 tools\winlab\，sshd 给 tavotto-lab 加 ForceCommand
python3 scripts/winlab/winlab.py admin proxy-install # 渲染代理配置进 secrets\，校验并启动 sing-box
python3 scripts/winlab/winlab.py doctor
```

- `tavotto-lab` 账户与它的公钥文件（`C:\ProgramData\ssh\lab_keys\tavotto-lab.pub`）、`AllowUsers`、
  关闭密码 / keyboard-interactive 登录，是第一次开荒时手工做的（见验收记录 A 节）；重装后照做一遍，
  账户密码只存本机钥匙串（`security find-generic-password -s tavotto-win-lab`），Lab 自动化不需要它。
- 安装包走 npmmirror / TUNA（GitHub release 资产从这台机器取不到），exe / msi 一律核 Authenticode；
  sing-box zip 没有签名也没有镜像，由 Mac 下载后按固定 sha256 送上去（`winlab.py` 与
  `bootstrap-admin.ps1` 两处同一个散列）。
- `--with-build-tools` 额外装 VS Build Tools（C++）与 rustup，约 6 GB，只在要本机构建 workerd / Tauri 时用。

### 代理凭据

`~/.config/tavotto-winlab/proxy.json`（0600，**自己填**，值取自你自己的客户端或服务端配置）：

```json
{"trojan":    {"server": "", "server_port": 0, "password": "", "server_name": ""},
 "hysteria2": {"server": "", "server_port": 0, "password": "", "server_name": "",
               "obfs_type": "", "obfs_password": "", "alpn": ["h3"]}}
```

`proxy-install` 渲染 `scripts/winlab/sing-box.template.json`，经 scp 直接写进 Lab 的 `secrets\`（仅
SYSTEM / Administrators 可读），本机不留渲染结果，也不打印；同时只把出口地址的 sha256 写进
`state\proxy-egress.sha256` 供探测比对。

### Tailscale（私有接入）

Lab 上已装 Tailscale（unattended，重启后不登录桌面也在线）。首次加入 tailnet 要人工一次：

```powershell
# 管理员 SSH 会话里执行，打开打印出的链接用你的账号批准
& 'C:\Program Files\Tailscale\tailscale.exe' up --unattended --hostname tavotto-win-lab
```

加入后把 `~/.ssh/config` 两个别名的 `HostName` 换成 tailnet 地址，并加 `HostKeyAlias <公网地址>`：
沿用开荒时人工核过的那把主机密钥，不重新信任（换前用 `ssh-keyscan <tailnet 地址>` 比对一次）。Mac 若用
Surge 的内置 Tailscale（配置里的 `[Tailscale tailnet]`），Tailscale.app 停着也能连。之后在腾讯云防火墙
删掉 22 / 3389 的公网放行，只留控制台 VNC 作应急；ACL / Grants 只放 Mac → Lab 的 22 / 3389。

### 防火墙与暴露面

腾讯云的 Windows 镜像**三个防火墙配置文件默认全关**：入站规则形同虚设，过滤只靠云防火墙。开荒时已打开
（入站默认拒、出站放行），22 / 3389 的放行规则（`OpenSSH-Server-In-TCP`、`sshd`、`RemoteDesktop-*`）
来源只留维护者出口网段与 `100.64.0.0/10`（tailnet）。改防火墙前先挂一个 10 分钟后自动还原的计划任务
（`TavottoLab-FirewallDeadman`），新连接验证通过再撤掉——锁在门外时控制台 VNC 是最后的入口。
从 Mac 探测公网端口无效（Surge 增强模式会在本机假接住任何 TCP 连接），要从外部主机测。

### 交互会话（真窗口）

```sh
python3 scripts/winlab/winlab.py admin gui-setup     # 自动登录 tavotto-lab（LSA 机密）+ 不锁屏 + WebView2 调试端口策略
ssh tavotto-win-lab-admin 'Restart-Computer -Force'  # 重启后 quser 应见 tavotto-lab console 运行中
```

之后 `test desktop-window --package <包>` 在那个桌面里跑 `tests/desktop_windows`。**别用 tavotto-lab 去 RDP**
（会抢走控制台会话）；要看桌面用腾讯云控制台 VNC，或以 administrator 另开 RDP 会话。

## 日常

```sh
python3 scripts/winlab/winlab.py doctor                              # 本机配置 + 远端健康检查
python3 scripts/winlab/winlab.py prepare --ref HEAD                  # 可信度检查 → 同步代码 → venv / 字体
python3 scripts/winlab/winlab.py test render-roundtrip
python3 scripts/winlab/winlab.py test pytest --tests tests/test_windows_regressions.py
python3 scripts/winlab/winlab.py test pytest-shard --shard 1/4
python3 scripts/winlab/winlab.py network --mode all                  # L1：direct / trojan / hysteria2
python3 scripts/winlab/winlab.py test hosts                          # L2/L3：Claude Code / Codex CLI
python3 scripts/winlab/winlab.py test hosts-live --mode <宿主>         # L3–L5：claude-code / codex-cli / codebuddy / minimax-code / dsh（DeepSeek）
python3 scripts/winlab/winlab.py test desktop-window --package <包>    # 交互会话里的真窗口用例
python3 scripts/winlab/winlab.py test env-matrix --package <包>        # 内置 / CPython 3.13 / uv 3.10·3.12·3.14 / conda -p·-n / 中文空格路径
python3 scripts/winlab/winlab.py test corpus --mode smoke|all         # CompatBench 语料（smoke 与 CI 同口径）
python3 scripts/winlab/winlab.py test page-shot --mode glance-fonts   # 无头 Edge 截登记页（tests/winlab_pages/），再用 shot --name page.png 取回
python3 scripts/winlab/winlab.py status | reports [--id RUN] | clean --keep 20 | stop
```

输出是 JSON，结论看 `result`（ADR 0131 闭集）；退出码 0 / 1 / 2 / 3 / 4 同 `agent_verify.py`。
原始日志在 Lab 的 `C:\TavottoLab\runs\<run_id>\evidence\`。

### Claude Code 的放行清单（个人本机，不提交）

`.claude/settings.local.json`：只放行日常动词，`admin …` 与裸 `ssh` 一律不放行。

```json
{"permissions": {"allow": [
  "Bash(python3 scripts/winlab/winlab.py doctor)",
  "Bash(python3 scripts/winlab/winlab.py status)",
  "Bash(python3 scripts/winlab/winlab.py reports *)",
  "Bash(python3 scripts/winlab/winlab.py prepare *)",
  "Bash(python3 scripts/winlab/winlab.py test *)",
  "Bash(python3 scripts/winlab/winlab.py network *)",
  "Bash(python3 scripts/winlab/winlab.py clean *)",
  "Bash(python3 scripts/winlab/winlab.py stop)"
]}}
```

真正的边界在服务器端：`tavotto-lab` 的钥匙被 `ForceCommand` 锁在 agent 上，就算放行规则写宽了
也拿不到 shell。云端 Claude 会话没有本机密钥，用不了 Lab。

### 安装包验收

```sh
# Mac：核散列后送进 Lab 的 cache\packages\（管理员 scp；同名 .sha256 一起送，Lab 端执行前再核一次）
python3 scripts/winlab/winlab.py test installer --package <新包>.exe [--from <旧包>.exe] --ref <新包的源码提交>
```

检查与 nightly `windows-install` 同一组（`scripts/winlab/installer-check.ps1`，nightly 是权威），另加升级保留
用户数据与卸载干净。**`--ref` 要指向与安装包同源的提交**：冒烟用的是仓库里的 `scripts/smoke_app.py`，新脚本会
调旧 sidecar 不认识的参数（0.18.0 不认 `--ssl-selftest`，2026-10-24 实测）。源码在不可信分支（如带他人
cherry-pick 的 beta 分支）上时用 main 的冒烟脚本，报告的 `commit` 如实记下。

### 桌面宿主 L2

```sh
python3 scripts/winlab/winlab.py test desktop-hosts --mode vscode   # cursor | trae-cn | workbuddy | zcode | claude-desktop
```

一次一个宿主。升级某个宿主的版本 = 从 winget-pkgs 对应清单（`manifests/<首字母>/<发行方>/<包>/<版本>/*.installer.yaml`）
抄 x64 currentUser 那一项的 `InstallerUrl` 与 `InstallerSha256` 改 `DESKTOP_HOSTS`、走 PR。报告的 `window` 里有
主窗口标题 / 窗口类 / 尺寸，证据目录有截图 `window.png`。

### 桌面宿主里的 AI 扩展 L3–L5

```sh
python3 scripts/winlab/winlab.py test desktop-live --mode vscode-claude --ref HEAD   # VS Code + Claude Code 扩展，DeepSeek
python3 scripts/winlab/winlab.py test desktop-live --mode cursor-claude --ref HEAD   # Cursor + 同一个扩展
python3 scripts/winlab/winlab.py test desktop-live --mode trae-claude --ref HEAD     # TRAE CN + 同一个扩展
python3 scripts/winlab/winlab.py test desktop-native --mode zcode --ref HEAD         # 原生 Agent 首屏探查（六个宿主都可）
```

需要 `secret-init deepseek` 录过 key（同 `hosts-live`）、Trojan 入口在（扩展包经 DMIT，一次一个、散列钉死；入口不在就是
BLOCKED，不会直连）、交互会话在。判据与 `hosts-live` 相同，报告里多 `extension`、`driver`（每轮的窗口 / 面板 / 提交 /
等待结束原因）和 `levels.provenance`（首个 `initialize` 的 clientInfo 与父进程链）。证据在 `evidence\desktop-live\`：
`round1.json` / `round2.json`（驱动器每一步）、`round*-window.png`（工作台截图）、`round*-panel.txt`（面板文字）、
`mcp-recorder.jsonl`。升级扩展 = 改 `DESKTOP_EXTENSIONS` 的版本、URL、散列和 `dir_name`，散列取 Open VSX 公布的
`<包>.sha256`（本机另下一份核对）、走 PR。**改了 `agent.py` / `desktop_driver.py` 要先 `admin deploy-agent`**（执行器在
`tools\winlab\`，不随 `prepare` 更新）。

### 「接入 AI 编辑器」按钮的真机验收

```sh
# Mac：从 release 取 Windows 安装包，与 SHA256SUMS.txt 核对后连同 <包名>.sha256 经管理员 scp 进 Lab 的 cache\packages\
python3 scripts/winlab/winlab.py test desktop-connect --mode zcode-native --package Tavotto-<版本>-Windows-Setup.exe --ref HEAD
python3 scripts/winlab/winlab.py test desktop-connect --mode workbuddy-native --package Tavotto-<版本>-Windows-Setup.exe
```

格与 key 同 desktop-live（`zcode-native` 用落盘专用 key，`workbuddy-native` 用常驻的登录态，没登录就照扫码那一节）。
报告 `levels.connect` / `levels.disconnect` 是按钮那一侧，`connect.written.problems` 列出配置哪里不符，`connect.cli_list` 是
装好的 CLI 自己怎么看这一行；截图在证据目录 `desktop-connect/tavotto-connect-{before,after}.png` 与 `tavotto-disconnect-*.png`。

### 真实模型调用（L4/L5）

只用国内合规的 Anthropic 兼容接口（当前登记 DeepSeek）；Lab 在中国大陆，Anthropic / OpenAI 不在这里调。
密钥在**你自己的终端**里录入（不回显），写 `~/.config/tavotto-winlab/model.json`（0600）：

```sh
python3 scripts/winlab/winlab.py secret-init deepseek
```

`zcode-native` 另用一把**专用、设了消费上限**的 key（ZCode 只认明文配置文件，这一格会在 Lab 的 run 目录里短暂落盘、跑完删除并核对）：

```sh
python3 scripts/winlab/winlab.py secret-init deepseek --ondisk
python3 scripts/winlab/winlab.py test desktop-live --mode zcode-native --ref HEAD
```

报告的 `ondisk_key.wiped` 必须为 true，否则整次是 error。

`workbuddy-native` 不用我们的 key（推理走 WorkBuddy 专用测试账户自带的模型），但要先登录一次。第一次跑时驱动器把登录二维码截成
`login-qr.png`，在 Mac 上取回给手机扫（等到 run id 出现后反复取，图大于约 60 KB 才是加载好的二维码）：

```sh
python3 scripts/winlab/winlab.py test desktop-live --mode workbuddy-native --ref HEAD
python3 scripts/winlab/winlab.py shot --id <run_id> --name login-qr.png --out /tmp/wb-qr.png   # 另开终端
python3 scripts/winlab/winlab.py sweep --workbuddy-logout   # 撤销常驻登录态（删 state\workbuddy-home 与凭据条目）
```

登录态常驻 Lab 的 `state\workbuddy-home`，之后的 run 直接复用，不必再扫。

`claude-desktop-native`（Claude Desktop 第三方推理 → DeepSeek）用同一把 `--ondisk` 专用 key。第一次跑之前做一次管理员动作，
只建 Claude Desktop 的用户级策略键并授 tavotto-lab 写值（不写任何值）；DMIT 要在线（只有 `downloads.claude.ai` 经它）：

```sh
python3 scripts/winlab/winlab.py admin claude-policy-acl   # 一次性
python3 scripts/winlab/winlab.py test desktop-live --mode claude-desktop-native --ref HEAD
```

报告的 `ondisk_key.wiped` 必须为 true、`ondisk_key.policy_key_present` 必须为 false。

建议给 Lab 单独建一把限额的 key。每次 `test hosts-live` / `test desktop-live` 经 SSH stdin 交给那一次运行，Lab 不落盘（交互会话一侧经命名管道，`request.json` 里没有它）。

### 转换真模型复现（convert-live）

在 Lab 上用真模型跑 **Tavotto 真实的 R→Python 转换**（`POST /api/ai/convert`、`agent=codex`、真 `CodexAgent.build_command`、真 Codex CLI，
推理走 DeepSeek 的 OpenAI 兼容 Responses 接口），复现反馈包里 Windows 上的 900 s 超时 / 正常退出却缺目标文件。费用提示：**每次转换都是一次真模型长任务**
（单次时限按源脚本行数取产品同一个公式 `ai_convert.convert_timeout_s`，基线 900 s、封顶 1800 s，再加 120 s 完成余量；`all` 共 5 次，硬上限 8 次），用你给 Lab 单独建的限额 key；密钥规则同 `hosts-live`（经 stdin 到那一次运行的子进程环境，不落盘、不进 argv / 证据）。

前提：

1. `secret-init deepseek` 已录入默认那把 key（见上一节）。
2. 管理员 `admin deploy-agent` 之后才生效——它把新任务 `convert-live` 与驱动 `convert_driver.py` 部署到 Lab（只在用户在场时由用户安排）。
3. 被测提交要带 `scripts/winlab/fixtures/convert/`（`--ref` 会先 `prepare`）。

```sh
python3 scripts/winlab/winlab.py test convert-live --mode baseline  --ref HEAD   # 项目 a、e，英文路径
python3 scripts/winlab/winlab.py test convert-live --mode cjk-path  --ref HEAD   # 项目路径含中文与空格、数据在 ..\data、source("..\\R\\utils.R")
python3 scripts/winlab/winlab.py test convert-live --mode long-path --ref HEAD   # 候选工作区路径逼近 MAX_PATH（LongPathsEnabled=0）
python3 scripts/winlab/winlab.py test convert-live --mode all       --ref HEAD   # 以上三组依次，共 5 次转换
python3 scripts/winlab/winlab.py reports --id <run_id>
```

做法：CODEX_HOME 在 run 目录里（`work\codex-home`，只有 DeepSeek 供应商 + `env_key`，没有 key 本身）；R 项目与转换工作区在**另一棵树**
`C:\TavottoLab\cw\<run_id>\`，任务结束整棵删掉，`shell_snapshots` 也删（模型曾 rg 到它把 key 发出去）。产品那一侧只做两处最小替换
（绕过 `require_usable` / 第三方接口记录，密钥因此只在环境变量里），其余走产品原路径。驱动在本进程里把同一个 app 起在 `127.0.0.1:<空闲端口>` 的真实回环服务上，所有 API 调用走真实 HTTP（方案 A 的写入工具回调 `POST /api/ai/convert/<sid>/target` 需要真实端口；报告里 `target_tool_ok` 即该工具是否被调用且成功，代码里没有该事实为 null）；该子进程环境显式设 `TAVOTTO_INSECURE_NO_AUTH=1`（仅 Lab 隔离实例、只绑 127.0.0.1）。

报告字段：`result`（全部转换完成 passed；有失败 / 超时 failed；驱动没跑完 error）、`counts`（`runs` / `done` / `failed` / `timeout` / `driver_error` /
`not_started` / `target_missing`）、`conversions[]`（每次：`case`、`outcome`、`elapsed_s`、产品的 `status` / `code` / `product_outcome`、
`convert_state` / `convert_failure`、`cli_run_facts`（#1587 的 Codex 运行事实，所 prepare 的代码里有才有）、`workspace_files`、
`workspace_path_len`、`usage`）、`usage_total`（所有 `turn.completed.usage` 之和）、`host`（Codex 版本、供应商、模型）。
原始 Codex JSONL、提示词、argv（长文本只记长度）、产品日志、会话 JSON 在 `evidence\convert-live\<场景-项目>\`。

Lab 在中国大陆，convert-live 只走国内合规的 DeepSeek、只用 `direct` 出口；不提供 Anthropic / OpenAI 官方模型的模式，
经代理出口调用也不行（见 `docs/rules/ci/windows-lab.md`「地区政策」，授权不改变）。

### 应用内托管安装 Codex（managed-cli）

按 `managed_cli_lock.json` 从指定来源真下载、校验、解包、`--version`，不登录、不调模型（#1739）。调的是仓库里的
`scripts/verify_managed_cli.py`：每个来源一个一次性数据目录、验完即删；独立复算装好的整棵树摘要与锁比。只走直连
（Lab 在大陆，`npmjs` 那一格就是国内用户直连 npm 官方源的真实情形）。改了 `agent.py` 要先 `admin deploy-agent`。

```sh
python3 scripts/winlab/winlab.py test managed-cli --mode all    --ref HEAD   # 锁里的来源依次各装一次
python3 scripts/winlab/winlab.py test managed-cli --mode npmjs  --ref HEAD   # 只走 registry.npmjs.org
python3 scripts/winlab/winlab.py test managed-cli --mode mirror --ref HEAD   # 只走 registry.npmmirror.com
```

升托管锁（`docs/RELEASING.md`「发一个新版本」第 2 步）的 PR 入队前跑一次 `--mode all`。

### 渲染测量（render-bench，只读）

不起模型、不联网、不装包。起 Tavotto 后端（Lab venv 源码态，`127.0.0.1` 随机端口，一次性数据目录），对仓库内置的 6 张最简 matplotlib 图
（`scripts/winlab/fixtures/renderbench/`）直接打 `/api/engine/render`，用来量「越改越慢」类反馈：

- **池上限轮转**：同一个后端里依次对 2 / 3 / 4 / 6 张图轮流渲染，每组先预热一遍（不计入）再正式 20 轮；每次记 `server_ms` / `wall_ms` /
  是否冷启动（响应 timings 里有 `script_build_ms`），每组前后数 `app.log` 里「LRU 淘汰」行数，给中位数 / P90。
- **冷启动分解**：同一张图每次**重起后端 + 全新数据目录**后渲染第一次，共 5 次，记 timings 里所有数字段与后端就绪耗时。
- **解释器探针**（每个模式都跑）：渲染解释器 `-c "import sys"`、`import numpy, matplotlib`、`import matplotlib.pyplot` 各 5 次，用来分开「解释器入口慢」与「导入 / 磁盘慢」。

```sh
python3 scripts/winlab/winlab.py test render-bench --mode rotation           --ref HEAD   # 轮转 + 探针
python3 scripts/winlab/winlab.py test render-bench --mode cold               --ref HEAD   # 冷启动 + 探针；项目在 cache 下含中文与空格的路径
python3 scripts/winlab/winlab.py test render-bench --mode cold-store         --ref HEAD   # 渲染解释器 = WindowsApps 别名入口的 Python（perf-fixtures 装的）
python3 scripts/winlab/winlab.py test render-bench --mode cold-drive-p       --ref HEAD   # 项目放在 VHDX 盘 P:\科研 数据\项目\
python3 scripts/winlab/winlab.py test render-bench --mode cold-store-drive-p --ref HEAD   # 别名入口 + 另一块盘（最接近用户的组合）
python3 scripts/winlab/winlab.py test render-bench --mode all                --ref HEAD   # rotation + cold + 探针
```

解释器与项目位置都由 `--mode` 选，写死在 `agent.py`，不收自由路径（没有透传通道）。`cold-store*` / `cold-*drive-p` 依赖下一节的 `admin perf-fixtures`，
没装就是 `environment_unavailable`。每个模式的**解释器探针**同时计时三个解释器的 `-c "import sys"` / `import numpy, matplotlib` / `import matplotlib.pyplot`：
Lab venv、别名入口 `WindowsApps\python.exe`、别名背后的真实 exe（`%LOCALAPPDATA%\Python\pythoncore-3.11*\python.exe`）——别名入口慢不慢直接看 `import_sys` 这一项的差。
前提同 `convert-live`：管理员 `admin deploy-agent` 之后才生效（部署 `render_bench_driver.py`，只在用户在场时由用户安排）。
报告 `bench.rotation[].sets[]` / `bench.cold[]` / `bench.probe[]` 是全部原始数据，`rotation_summary` 是一眼表；
只测 Python 池控制面，没有 `tavotto-workerd` 二进制时 workerd 记为 `skipped`。驱动在 Mac 上也能直接跑（`python scripts/winlab/render_bench_driver.py --help`），用于出基线。

#### 夹具：`admin perf-fixtures`（一次性管理员动作，用户在场时执行）

```sh
python3 scripts/winlab/winlab.py admin perf-fixtures            # 幂等，可重复跑
python3 scripts/winlab/winlab.py admin perf-fixtures --remove   # 撤销
```

它在 Lab 上改了这些东西，也只有这些：

1. `C:\TavottoLab\perf\perf-fixtures.vhdx`（4 GB 动态 VHDX，diskpart 创建 / 挂载，NTFS，卷标 `TavottoPerf`，盘符 `P:`；不依赖 Hyper-V 模块），
   计划任务 `TavottoLab-PerfVhd`（SYSTEM，开机时重新挂载）。盘上建 `P:\科研 数据\项目\`，**只在这个目录上**授 `tavotto-lab` 修改权限（`Set-Acl`，不碰盘根）。
   另外 `.vhdx` **文件**本身也授 `tavotto-lab` 修改并重新挂载一次：挂上的虚拟磁盘沿用镜像文件的安全描述符，文件只继承 SYSTEM / Administrators 时 tavotto-lab 连卷都打不开（`P:` 上任何 stat 都是 WinError 5，盘内 NTFS 授权不起作用）。
   它**只覆盖「另一块盘 + 中文路径」，不限速、不模拟 U 盘的速度**（VHDX 的 QoS 限速要 Hyper-V；底下是 Lab 自己的云盘）。
2. Python 安装管理器 MSIX（python.org 官方发布，固定版本与 SHA-256 写在 `winlab.py` 的 `PYMANAGER_*`）：**Mac 端直连 python.org 下载**（显式不走代理）、核大小与散列后经 scp 送到
   `C:\TavottoLab\perf\`，Lab 上再核 SHA-256 与 Authenticode（有效且签名者含 Python Software Foundation）才 `Add-AppxProvisionedPackage -Online -SkipLicense`
   （系统级预置：`tavotto-lab` 下次登录时得到它，`WindowsApps` 里出现 `python.exe` / `py.exe` 等应用执行别名）。
3. 一次性登录任务 `TavottoLab-PerfPython`（`tavotto-lab` 登录时运行 `perf-python-setup.ps1`）：等别名出现 → `py install --yes 3.11` → 用清华镜像装 `matplotlib>=3.8,<3.12` 与 `numpy>=1.24,<3`
   → 核对别名 `python.exe` 能 import 它们 → 写 `C:\TavottoLab\cache\perf-python-setup.log`（末行 `done`）→ 删除自己。安装脚本写在 `C:\TavottoLab\perf\`（只继承 SYSTEM / Administrators），所以写完立刻 `icacls` 授 tavotto-lab 读与执行——漏了它登录任务会触发但 PowerShell 打不开 `-File`（结果码 `0xFFFD0000`、不写日志）。执行时 tavotto-lab 已登录就直接 `Start-ScheduledTask`；否则让它注销再登录（或重启）。

这不是用户机器上的「微软商店版 Python 3.11」（那是商店里的独立包 `PythonSoftwareFoundation.Python.3.11`，Server 2025 没有商店），而是 python.org 官方的新机制：
同样是 `WindowsApps` 里的应用执行别名（走 AppX 激活而不是普通 exe），运行时则装在 `%LOCALAPPDATA%\Python\`。别名机制相同，所以「别名入口本身慢不慢」可以回答，运行时差异不能。

撤销：`admin perf-fixtures --remove` 删两个计划任务、卸载并删除 VHDX、移除预置包与全部用户的包；`tavotto-lab` 的 `%LOCALAPPDATA%\Python\` 是用户目录，手动删。

### 黄金快照

腾讯云 CAM 子账号只授 `lighthouse:DescribeInstances` / `DescribeSnapshots` / `CreateInstanceSnapshot`
（不能回滚、不能删），密钥存本机钥匙串（服务名 `tavotto-lab-tencent`，账户 `SecretId` / `SecretKey`）：

```sh
python3 scripts/winlab/winlab.py clean --keep 0                      # 先清 run 目录（报告保留）
python3 scripts/winlab/winlab.py admin snapshot --name tavotto-winlab-golden-YYYYMMDD
```

快照含 `secrets\`（代理凭据）：不要共享或跨账号复制。回滚 / 删除快照只在控制台由你做。

## 三种状态（不是三台虚拟机）

- **Regression**：在 `tavotto-lab` 账户里跑已审查的回归用例（`render-roundtrip` / `pytest`）。
- **Fresh**：每个 run 的 `TAVOTTO_DATA_DIR` / `MPLCONFIGDIR` / `TEMP` 都在新建的 run 目录下；
  这只是「全新的 Tavotto 配置」，**不是**全新的 Windows——系统、已装软件、注册表都还是那一份。
- **Upgrade**：`test installer --from <旧包>`：先装旧包、冒烟、在应用数据里放标记，再装新包，核版本号变化与标记仍在。

需要真正的全新 Windows（首次启动、无 Python、用户名含中文……）仍以 nightly `windows-install` 矩阵与
一次性 VM 为准。

## 恢复与维护

- 重启：sshd、Tailscale、`TavottoLab-SingBox` 计划任务都开机自启；`doctor` 复核。
- 卡住的任务：`winlab.py stop`（结束持锁进程，Job Object 带走整棵子孙树）。
- 磁盘：`clean --keep N`；缓存（`cache\`）保留以免重复下载。
- **每月**：`doctor`；Windows 更新（管理员，直连）后重启并复核；`network --mode all` 看 DMIT 两条
  通路；清 `runs\`；核对 `bootstrap-admin.ps1` 的版本钉子是否需要随 CI 升级（Python 小版本、Node、pnpm）。
- **快照**：初始化稳定后可在腾讯云控制台建一份系统快照作黄金镜像；回滚 / 重装要用户另行授权。
