# Windows Lab 开荒验收记录（2026-10-24）

规则 `docs/rules/ci/windows-lab.md`，运维 `docs/ci/windows-lab.md`。本页每一项都在真机上执行过，
没有执行的写 NOT TESTED / BLOCKED，不用代码检查代替。状态：PASS / FAIL / BLOCKED / NOT TESTED。
地址、凭据、出口 IP 一律不写；每个 run id 的 `report.json` 留在 Lab 的 `reports\`（`winlab.py reports --id`），
原始日志在 `runs\<run_id>\` 且会被 `clean` 轮转——本页较早几轮的原始日志已轮转掉，报告仍在。

## 环境

| 项 | 值 |
| --- | --- |
| 系统 | Windows Server 2025 Datacenter 10.0.26100（Desktop Experience），zh-CN，ANSI cp936 |
| 默认态保留 | Defender 实时防护开、`LongPathsEnabled=0`、`PYTHONUTF8` 未设 |
| 工具 | Python 3.13.17、Node 22.23.4、pnpm 11.27.1、Git 2.56.0.windows.3、PowerShell 7.6.8、Tailscale 1.104.2、sing-box 1.14.3 |
| 控制端 | Mac mini，`scripts/winlab/winlab.py`（分支 `feat/windows-lab`） |

## A. 基础设施

| 验收项 | 状态 | 证据 / 说明 |
| --- | --- | --- |
| Windows Server 2025 正确安装 | PASS | `doctor`：`Windows-2025Server-10.0.26100-SP0` |
| SSH 公钥登录 | PASS | 两个别名均 `BatchMode=yes` 登录成功；`StrictHostKeyChecking yes` |
| 密码 / keyboard-interactive 登录关闭 | PASS | 关闭后 `PubkeyAuthentication=no` 连接得到 `Permission denied (publickey).` |
| 非管理员账户跑测试 | PASS | `tavotto-lab`：`IsInRole(Administrators)=False`；全部任务以它执行 |
| lab 钥匙拿不到 shell | PASS | ForceCommand 后 `whoami`、`powershell -c whoami` 都只得到 agent 的 `invalid choice`；`-L` 端口转发同样进 agent |
| 测试账户读不到代理凭据 | PASS | `doctor.checks.secrets_not_readable_by_lab_user=true`（`secrets\sing-box.json` 存在时测） |
| Tailscale 私有连接 | BLOCKED | 已装（unattended），未登录：需要用户批准登录链接；Mac 端 Tailscale 当时处于 stopped |
| 重启后 SSH 自动恢复 | PASS | `Restart-Computer` 后约 30 s 回来；无任何交互式会话时 sshd / Tailscale 服务 / `TavottoLab-SingBox` 均 Running，7890/7891 仍只在回环，防火墙三档仍开，`doctor` passed；重启后 `network` run `20261024T104336Z-16f263` 三模式 passed |
| 无需 RDP 完成无头测试 | PASS | 本页全部任务经 SSH 完成，开荒期间无 RDP 操作（首次放公钥除外） |
| 公网无不必要的 SSH/RDP 暴露 | PASS（临时方案） | 镜像默认**防火墙三档全关**；已开启并把 22 / 3389 限到维护者出口 /24 + `100.64.0.0/10`。从外部主机测：22 / 3389 / 5985 / 445 均 closed。Tailscale 接入后应再收窄云防火墙 |

开荒中实测到的两个坑（已在执行器里处理并有用例）：

- **复用连接串命令**：ControlMaster 复用时，后续会话拿到的是第一条命令的 `SSH_ORIGINAL_COMMAND`
  （先 `doctor` 再 `status`，第二次执行的仍是 `doctor`）→ `winlab.py` 固定不复用。
- **单任务锁被误释放**：基于 `tasklist` / `taskkill` 的存活判定把一个仍在运行的分片判成已退出，`stop` 删了锁，
  下一片并行起跑；第一片结束时又无条件删掉了第二片的锁。改为 `OpenProcess` + `GetExitCodeProcess` 判存活、
  `TerminateProcess` 结束、释放只删自己 run id 的锁（三条用例 + 反证）；实机复验：`network` 运行中 `stop` →
  agent 进程消失、无残留 python 进程、锁释放（run `20261024T092531Z-432950`）。
- **GitHub 大传输不通**：release 资产 HTTP 000、`git clone` 25 分钟 0 字节；网页 HEAD 正常 → 代码改走
  Mac 的 git bundle（首次 58 MB、之后增量 0.1 MB），字体由 Mac 核 sha256 后送，安装包走 npmmirror / TUNA。

## B. 代理（run `20261024T070525Z-0b0a74`）

| 验收项 | 状态 | 证据 / 说明 |
| --- | --- | --- |
| sing-box 配置校验 | PASS | Mac 与 Lab 两处 `sing-box check` 均 0 |
| 入口只本机监听 | PASS | `127.0.0.1:7890` / `:7891`；`doctor` 非回环地址连接失败 |
| Direct 可用 | PASS | GitHub 3/3、国内镜像 3/3；Anthropic API 直连 403、OpenAI API 直连超时（区域限制，符合预期） |
| Proxy-Trojan | PASS | 出口散列 = DMIT；Anthropic / OpenAI API 401（链路与 TLS 通、无凭据）；流式 8/8 块、3.7 s 展开 |
| Proxy-Hysteria2 | PASS | 出口散列 = DMIT（UDP/QUIC 可达）；同上 401；流式 8/8 块 |
| 代理与直连可区分 | PASS | 同一目标在三种模式下状态码不同（403/超时 vs 401），出口散列只在代理模式等于 DMIT |
| 代理故障不降级直连 | PASS | 入口不在 → `environment_unavailable` 且不发请求（用例）；指向死端口的请求 `ConnectionRefusedError`（实测） |
| DMIT 未中断 | PASS | 探测前后 sing-box `NRestarts=0`、`ActiveEnterTimestamp` 不变，load 0.13 → 0.01 |
| 大型依赖未进代理 | PASS | pip / npm / 安装包全部直连镜像；bootstrap 脚本不含代理（用例钉住） |

首响应延迟（每目标 3 次，样本小，不是稳定性结论）：

| 目标 | Trojan | Hysteria 2 |
| --- | --- | --- |
| api.anthropic.com | 718–772 ms | 416–430 ms |
| api.openai.com | 672–747 ms | 416–428 ms |
| github.com | 722–765 ms | 365–382 ms |

两轮（开荒时、重启后 run `20261024T104336Z-16f263`）Hysteria 2 的首响应都低约 300–350 ms，两条都全部成功；
重启后那一轮直连 GitHub 0/3（直连本身不稳）。要下「哪个更稳」的结论需要跨天、跨时段的
多轮样本，本页不下。

## C. Tavotto

| 验收项 | 状态 | 证据 / 说明 |
| --- | --- | --- |
| `render-roundtrip`（真渲染 + PDF/SVG/PNG 导出 + 保存重载 + MCP 往返，9 个文件） | skipped（按闭集记 NOT TESTED；0 失败） | run `20261024T073541Z-3c0f2b`：366 条，359 passed / 0 failed / 7 skipped，804 s。7 条 skip 都是用例按平台设计的（2 条 POSIX 专属语义、2 条 Windows 默认不许普通用户建符号链接、3 条 POSIX 权限位）；按闭集 skip 不算 PASS |
| 首轮暴露的问题 | — | run `20261024T072957Z-18fef6`：46 failed。① 新加的 `scripts/winlab/*.py` 没钉 UTF-8 输出——`test_windows_regressions` 在真 cp936 上抓到，已修；② venv 少装 matplotlib（CI 在 `[dev]` 之外单独装）——Lab 配方问题，已按 CI 补齐 |
| 后端全量分片 `--shard 1/8`（CI 同口径：`-rs -vv --durations=50` + 900 s 挂死看门狗） | FAIL | run `20261024T092639Z-ea46b3`：3693 条，3610 passed / 13 failed（8 个用例，含子测试）/ 70 skipped，4296 s。定性见下 |
| 安装包静默安装 / 升级 | NOT TESTED | 安装包任务未登记（需要 release 产物的下载路径；GitHub release 资产在这台机器上取不到） |
| 测试报告可重现 | PASS | 每次 run 一份 `report.json`（提交 SHA、环境事实、证据 sha256），`reports --id` 可取回 |
| 卡住任务可结束 | PASS | 见 A 节「单任务锁」：`stop` 实机结束整棵进程树并释放锁 |
| 工作区安全清理 | PASS | 不跟随 junction / 符号链接、父目录校验、跳过活动 run 都有用例；实机 `clean --keep 6` 删 4 个最旧 run 目录、报告 JSON 全留 |

### 分片 1/8 的失败定性（单独重跑稳定复现，run `20261024T104001Z-c8fedf`，32 s——不是超时或资源争用）

| 用例 | 非管理员（tavotto-lab） | 管理员同一检出同一 venv | 定性 |
| --- | --- | --- | --- |
| `test_layout_recovery_backend::test_a15_bad_files_do_not_block_the_listing` | FAIL：`WinError 1314` 建符号链接无特权 | PASS | **CI 盲区**：hosted windows-latest 以管理员运行，非管理员用户的 Windows 从没被跑过；用例假设能建符号链接 |
| `test_qa_first_open_env_and_data::test_project_venv_and_same_name_decoy_…` | FAIL：项目 venv 跑 `scripts/entry.py` 退出 1（stderr 被用例捕获，报告里看不到） | PASS | 同上，随权限变化；具体是夹具还是产品路径依赖特权待定 |
| `test_compat_explore_data::RealExecution` 6 条 | FAIL：`parent_native` 执行数为 None | FAIL | 与权限无关；待查（Server 2025 / cp936 / 该组在 hosted Windows 上是否真跑） |

以上是本次开荒**发现**的问题，不在本 PR 修：本 PR 只交付 Lab 本身。按 `.github/AGENTS.md`，
「只在别人电脑上发生」的先写 `tests/test_windows_regressions.py` 用例再修，另开 PR。

速度：不变式套件在 Lab 上单条最慢 315 s（`test_invariants_engine` InvMix），整片约为 hosted 的两倍。
没有为提速关 Defender——那会改掉 Lab「用户默认态」的意义；去掉 `PYTHONDONTWRITEBYTECODE`（与 CI 一致）后
同一片未再出现挂起。

## D. AI 集成（run `20261024T074956Z-79364f`）

| 宿主 | 版本 | L1 | L2 | L3 | L4 | L5 |
| --- | --- | --- | --- | --- | --- | --- |
| Claude Code CLI | 2.1.310 | PASS（Trojan/Hy2） | PASS | PASS：宿主自己的 `claude mcp list` 拉起 Tavotto MCP，输出 `tavotto: … √ Connected` | BLOCKED | BLOCKED |
| Codex CLI | codex-cli 0.164.0 | PASS | PASS | NOT TESTED（`codex mcp list` 只读配置不连接：config_tested） | BLOCKED | BLOCKED |
| Tavotto Codex / Claude 插件 | — | — | NOT TESTED | NOT TESTED | BLOCKED | BLOCKED |
| WorkBuddy / Cursor / Trae / VS Code | — | — | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED |

- L3 证明的是握手成功，不证明宿主列出了全部工具；MCP 由仓库 `server.py` 直接拉起，没经过插件的
  `mcp/launch` 与插件安装路径。
- L4 / L5 需要用户授权的账户、Lab 上的登录与真实模型调用，均未获授权：BLOCKED，不推定。
- 网络模式：宿主安装与 L2/L3 均为 Direct（npmmirror）；L1 的 Trojan / Hy2 结论见 B 节。

## E. 安全与仓库

| 验收项 | 状态 | 说明 |
| --- | --- | --- |
| 现有 CI 不削弱 | PASS | 未改任何 workflow、Gate、merge queue、required checks |
| Compatibility Guardian 信任边界不变 | PASS | 未迁移、未改 |
| 不让公开 PR 在 Lab 跑 | PASS | 可信度判定只放行 origin/main 或作者 + 提交者都是维护者的提交；有用例 |
| 不泄露凭据 | PASS | 凭据只在 Mac 0600 文件与 Lab `secrets\`；报告只存出口散列；终端从未打印 |
| 不影响 ci-infra runner | PASS | 不注册 runner、不改 `actionlint.yaml` |
