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

建议给 Lab 单独建一把限额的 key。每次 `test hosts-live` / `test desktop-live` 经 SSH stdin 交给那一次运行，Lab 不落盘（交互会话一侧经命名管道，`request.json` 里没有它）。

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
