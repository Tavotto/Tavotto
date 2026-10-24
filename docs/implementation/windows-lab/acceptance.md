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
| Tailscale 私有连接 | PASS | 用户批准登录后 Lab 入 tailnet；Mac 经 Surge 内置 Tailscale（Tailscale.app 停着也通）。tailnet 上 `ssh-keyscan` 拿到的 ed25519 主机密钥与公网地址已钉的同一把；两个别名改走 tailnet + `HostKeyAlias` 后 `doctor` passed，Lab 侧来源地址落在 `100.64.0.0/10` |
| 重启后 SSH 自动恢复 | PASS | `Restart-Computer` 后约 30 s 回来；无任何交互式会话时 sshd / Tailscale 服务 / `TavottoLab-SingBox` 均 Running，7890/7891 仍只在回环，防火墙三档仍开，`doctor` passed；重启后 `network` run `20261024T104336Z-16f263` 三模式 passed |
| 无需 RDP 完成无头测试 | PASS | 本页全部任务经 SSH 完成，开荒期间无 RDP 操作（首次放公钥除外） |
| 黄金系统快照 | PASS | 用户授权后：`clean --keep 0`、无任务无残留进程，`admin snapshot` 建 `tavotto-winlab-golden-20261024`（`lhsnap-b5p550zr`，NORMAL）；子账号只有查询 + 建快照权限，回滚 / 删除不经 Lab 工具 |
| 公网无不必要的 SSH/RDP 暴露 | PASS | 镜像默认**防火墙三档全关**；已开启并把 22 / 3389 限到维护者出口 /24 + `100.64.0.0/10`；Tailscale 接入后用户删掉了云防火墙的公网放行。从外部主机测：22 / 3389 / 5985 / 445 均 closed |

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
| 安装包静默安装 / 升级 / 卸载 | PASS | 见 F 节：b4 全新 21/21、b3→b4 升级 32/32、v0.18.0 全新 21/21 |
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

## D. AI 集成

| 宿主 | 版本 | L1 | L2 | L3 | L4 | L5 |
| --- | --- | --- | --- | --- | --- | --- |
| Claude Code CLI | 2.1.310 | PASS（Trojan/Hy2） | PASS | PASS：宿主自己的 `claude mcp list` 拉起 Tavotto MCP，`tavotto: … √ Connected` | PASS（模型 DeepSeek，见下） | PASS（模型 DeepSeek，见下） |
| Codex CLI | codex-cli 0.164.0 | PASS | PASS | NOT TESTED（`codex mcp list` 只读配置不连接：config_tested） | BLOCKED | BLOCKED |
| Tavotto Codex / Claude 插件 | — | — | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED |
| WorkBuddy / Cursor / Trae / VS Code | — | — | NOT TESTED | NOT TESTED | NOT TESTED | NOT TESTED |

**L4 / L5 怎么做的**（`test hosts-live`，run `20261024T120811Z-57afdd`、复跑 `20261024T120915Z-5f7e49`，各约 55 s，两次都 passed）：

- 宿主是真 Claude Code CLI 2.1.310；推理走 DeepSeek 的 Anthropic 兼容接口（`deepseek-flash`），网络 Direct。
  **Lab 在中国大陆：Anthropic / OpenAI 的真实模型调用按服务商地区政策不在这台机器上做**（经 DMIT 出口调用等于绕开
  地区限制），对应格仍是 BLOCKED；`CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` 关掉宿主发往 Anthropic 的遥测 / 更新检查。
- 凭据：本机 0600 文件，经 SSH stdin 交给那一次运行、只进宿主进程环境；Lab 不落盘，证据落盘前再抹一次。
- L4：宿主执行了 `tavotto_health` / `tavotto_open_figure` / `tavotto_apply_overrides` 并拿到成功结果。
- L5：真实 matplotlib 图（两条带误差带的曲线 + 图例）→ 宿主打开、线宽 3 + 图例移到左上 → 导出 PDF → 重放校验
  `ok: true` → **第一个宿主进程退出**（MCP server 随之结束，内存里的会话没了）→ 全新宿主进程只凭 `session_id`
  调 `tavotto_verify_replay`，服务端从会话日志恢复、从零重放一致。执行器独立核对：PDF 头是 `%PDF-`、会话日志
  `mcp-sessions/<sid>.json` 里真有线宽 3 那条、两次重放结果都是 `ok: true`。

调试中暴露、已记下的宿主兼容事实（不是 Lab 的缺陷）：

- **结果超过 Claude Code 的单次 MCP 结果上限**：两条线的简单图，`tavotto_open_figure` / `tavotto_session_state` 的
  结果（后者 84 KB）被 Claude Code 转存成文件、模型只拿到一句提示，要多花十来轮 `Grep` 去读；`--max-turns 16`
  时有一轮因此没走完。值得在 MCP 侧给模型调用一个有界摘要（`apply_overrides` 已有 `summary=true`）。
- **出版预检会挡住导出**（正确行为）：测试图 88.9 mm 宽不合默认规范，场景里明确写了「用户已确认」才带 `explicit_confirm`。
- 重新 `tavotto_open_figure` 是**新会话**（从脚本原样开始）；「保存后重开」是凭 `session_id` 恢复。

## F. 最新包验收与 issue 对照（2026-10-24）

包：beta **0.19.0b4**（source `163bab5a7`，只在本机归档、未发布到 GitHub）、beta 0.19.0b3、正式版 **v0.18.0**
（GitHub release）。Mac 按归档 / release 的 `SHA256SUMS` 核过再送进 Lab，Lab 执行前再核一次。
`test installer`（`scripts/winlab/installer-check.ps1`，与 nightly `windows-install` 同一组检查）：

| run | 包 | 结论 | 说明 |
| --- | --- | --- | --- |
| `20261024T125822Z-94e117` | b4 全新 | PASS 21/21 | 注册表安装位置、sidecar + 内置 runtime + workerd + AGPL、壳→sidecar、杀壳后 sidecar 15 s 内跟随退出、内置 runtime 冒烟（`--expect-source bundled --expect-control-plane workerd` + 全科学栈）、覆盖安装再冒烟、卸载后注册表干净 |
| `20261024T131211Z-5b0382` | b3 → b4 升级 | PASS 32/32 | 另证 `0.19.0-beta.3 → 0.19.0-beta.4`、应用数据里的标记跨升级保留 |
| `20261024T131735Z-2e770b` | v0.18.0 全新（`--ref v0.18.0`） | PASS 21/21 | 用 main 的冒烟脚本时失败：它调 0.18.0 不认识的 `--ssl-selftest`（run `…131458Z-cbe649`）——冒烟脚本要与包同源，已写进运维手册 |

开荒期间顺带确认：SSH 会话（session 0、无交互桌面）里壳也能起 WebView 并拉起 sidecar（sidecar 日志里有前端发出的
`/api/projects`、`/api/engine/environment` 请求），渲染解释器为内置 runtime。

**issue 对照**（pytest 两轮：run `20261024T122601Z-b8a167` 422 条、`20261024T131959Z-c66fd8` 237 条；Lab 是非管理员、cp936、Defender 开）：

| issue | 在 Lab 上 | 结论 |
| --- | --- | --- |
| #35 Windows 安装 / 升级链路 | 静默安装、升级、覆盖、卸载 | PASS（安装界面那几屏与 SmartScreen、Win10/11 客户端仍未验） |
| #1331 布局会话并发写丢条目 | `test_layout_session` 两轮 | 两轮均通过（flaky 类，未复现，不代表已修） |
| #1122 / #603 渲染子进程预算 / 超时恢复 | `test_rendercore_renderchild` 两轮 | 两轮均通过（未复现） |
| #1310 / #1198 AI 会话 / 历史库并发 `database is locked` | `test_ai_session_rev` / `test_ai_history` 两轮 | 两轮均通过（未复现） |
| #1112 跨进程会话锁 c34 负载饿死 | `test_layout_recovery_backend` | c34 两轮通过；同文件的 `test_a15` 因非管理员无符号链接特权失败（下条） |
| #1123 package_smoke 回退实例 | `test_package_smoke` 两轮 | **复现到相邻问题**：`test_termination_leaves_neither_the_server_nor_its_worker` 两轮都红——期望 `taskkill`，实际 `taskkill` 返回 1、退到 `terminate`；与本 PR 执行器早先撞到的「非管理员会话里 `taskkill /T` 不可靠」同一现象 |
| #1264 / #1150 文件身份（大小写 / 硬链接 / Unicode） | `test_capture_saved_facts_identity` / `test_export_identity` / `test_override_identity` | 通过，唯 `test_two_symlinks_are_two_entries_not_normalization` 因无符号链接特权失败 |
| #1213 versionstore | `test_versionstore` | 通过（LongPaths=0） |
| #1256 Guardian data_source（中文文件名） | `test_compat_guardian_families` | 通过（cp936） |
| #1294 自动采用用户 Python | `test_environment_adoption` | 通过（Lab 的 Python 3.13 作用户 Python） |
| 非管理员 Windows（新发现） | 三条用例需要符号链接特权 | **CI 盲区**：hosted windows-latest 是管理员；`test_a15…`、`test_two_symlinks…`、`test_qa_first_open…` 在非管理员下失败 |
| 需要真窗口的项（#276 #1071 #1387 #1320 #1315 #1308 #1306 #1304 #1301 #82） | — | NOT TESTED：需要交互会话执行器（尚无） |
| Win10 / Win11 / ARM / Anthropic 真模型（#1200 #1392 等） | — | 这台机器做不到 |

## E. 安全与仓库

| 验收项 | 状态 | 说明 |
| --- | --- | --- |
| 现有 CI 不削弱 | PASS | 未改任何 workflow、Gate、merge queue、required checks |
| Compatibility Guardian 信任边界不变 | PASS | 未迁移、未改 |
| 不让公开 PR 在 Lab 跑 | PASS | 可信度判定只放行 origin/main 或作者 + 提交者都是维护者的提交；有用例 |
| 不泄露凭据 | PASS | 凭据只在 Mac 0600 文件与 Lab `secrets\`；报告只存出口散列；终端从未打印 |
| 不影响 ci-infra runner | PASS | 不注册 runner、不改 `actionlint.yaml` |
