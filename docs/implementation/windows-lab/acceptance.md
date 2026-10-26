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
| `test_qa_first_open_env_and_data::test_project_venv_and_same_name_decoy_…` | FAIL：项目 venv 跑 `scripts/entry.py` 退出 1（stderr 被用例捕获，报告里看不到） | PASS | **更正（10-25）**：与权限无关——夹具没有符号链接调用；main 与 #1413 分支上整文件在 Lab 均通过（run `20261024T173833Z-6eabd8` / `20261024T173647Z-f21b80`），原失败未复现，分片内顺序 / 状态相关未排除；用例已改为失败时把 stderr 带进断言 |
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
| 非管理员 Windows（新发现） | 三条用例需要符号链接特权 | **CI 盲区**：hosted windows-latest 是管理员；`test_a15…`、`test_two_symlinks…` 在非管理员下失败（夹具依赖特权，产品无问题，#1413 修）；`test_qa_first_open…` 后查与权限无关、未复现 |
| 需要真窗口的项（#276 #1071 #1387 #1320 #1315 #1308 #1306 #1304 #1301 #82） | — | NOT TESTED：需要交互会话执行器（尚无） |
| Win10 / Win11 / ARM / Anthropic 真模型（#1200 #1392 等） | — | 这台机器做不到 |

## G. 交互会话、环境矩阵、脚本语料、全部宿主（2026-10-25）

### G1 交互会话执行器（`test desktop-window`，b4）

`admin gui-setup` 后重启：`quser` 见 `tavotto-lab console 1 运行中`，注册表无明文密码。run `20261024T144637Z-d59511`：
`tests/desktop_windows` 13 条**在会话 1 里**真跑（WebView2 155.0.4283.62 + 同版本 msedgedriver），11 passed / 2 failed：

| 用例 | 结论 | 对照 |
| --- | --- | --- |
| `test_clipboard::test_paste_reads_what_another_app_put_there` | FAIL：粘贴没用系统剪贴板里的内容 | hosted nightly（10-23 run 40024588949，main 构建）**同一条、同一句失败** |
| `test_file_picker::test_open_a_script_through_the_system_file_dialog` | FAIL：`element click intercepted`，被新版主页那块「从绘图脚本到可直接编辑的图」挡住 | 同上，nightly 同样失败 |

两条都是产品 / 用例的真问题，不是 Lab 环境：Lab 与 hosted 逐条一致。**没有 issue 在跟踪**——nightly 的「无 Python」腿
至少从 10-21 起一直红着。

### G2 环境矩阵（`test env-matrix`，b4；项目在 `张三 的论文\figures`）

| 腿 | 解释器 | 期望来源 | 结论 |
| --- | --- | --- | --- |
| bundled | 不指定 | bundled + 全科学栈 | PASS |
| cpython-3.13 | python.org 3.13.17 venv | env_override | PASS |
| cpython-3.13-cjk-path | 解释器本身在 `张三 的环境\venv` | env_override | PASS |
| uv-3.10 / 3.12 / 3.14 | python-build-standalone（npmmirror） | env_override | PASS ×3 |
| conda-prefix | Miniforge `conda create -p <任意目录>` | env_override | **FAIL**：没识别出要激活，workerd 直接裸起 worker，渲染进程 `0xC06D007F`（延迟加载找不到 DLL 函数）崩溃 |
| conda-named | Miniforge `conda create -n mpl`（envs\ 下） | env_override | **FAIL**：报「Tavotto 的安装文件不完整（内置渲染环境缺失或损坏）」——同一安装包 bundled 腿是绿的，**归因错误** |

run `20261024T153250Z-94de7e`。对照：conda-prefix 正是 **#1018**（`-p` 建在任意目录的环境要显式信任才激活）/ **#985**
（外部 conda 环境找不到 conda 可执行文件、依赖激活的脚本会裸起）的具体复现，Lab 给出了崩溃码；hosted nightly 的 Conda 腿（10-23）
是第三种表现：识别出要激活 → workerd 不接 → 静默回退到 Python 池 → 冒烟按 `--expect-control-plane workerd` 判红，未见 issue。
Miniforge 26.9 建环境需要把 `conda-launchers` 一起装进目标环境（不然事务失败），已写进任务。

### G3 脚本语料（`test corpus`）

| 档 | run | 结论 |
| --- | --- | --- |
| smoke（与 CI compat-smoke 同口径） | `20261024T153724Z-04d1e4` | PASS：24 case 全部过 discover → execute → capture → open → semantic → edit → replay |
| all | `20261024T161630Z-be2456` | PASS：160 case，执行 159/160，之后各阶段 159/159；分类 full 149 / partial 2 / unsupported_by_design 2 / environment_dependency 7；`product_bugs: []`、worker 崩溃 0；1220 s |

### G4 AI 宿主（`test hosts-live --mode …`，DeepSeek，Direct；判定按 `mcp_recorder` 记下的服务端事实）

| 宿主 | 版本 | L2 | L3 | L4 | L5 | run |
| --- | --- | --- | --- | --- | --- | --- |
| Claude Code CLI | 2.1.310 | PASS | PASS | PASS | PASS | `20261024T154720Z-faa22c` |
| Codex CLI | 0.164.0 | PASS | PASS | PASS | PASS | `20261024T154552Z-0a58d0` |
| CodeBuddy CLI（WorkBuddy 背后的 CLI） | 2.174.0 | PASS | PASS | PASS | PASS | `20261024T160218Z-f46d21` |
| MiniMax Code | 0.6.12 | PASS | PASS | PASS | PASS | `20261024T160337Z-ed8412` |
| DSH | 0.2.1-rc.3 | PASS | PASS | PASS | PASS | `20261024T161209Z-ed3a7c` |
| ZCode | — | NOT TESTED | — | BLOCKED | BLOCKED | 无官方 npm；`-p` 与会话都要 Z.AI 账户 |
| Cursor / Trae / VS Code（Copilot） | — | NOT TESTED | NOT TESTED | BLOCKED | BLOCKED | 桌面 GUI + 各自账户；交互会话执行器已就绪，L2（装得上、起得来）可做 |
| Claude Desktop | — | NOT TESTED | — | BLOCKED | BLOCKED | Anthropic 账户 + 地区政策 |

L5 每行同一组检查：打开、改线宽 / 图例、导出 PDF（`%PDF-`）、第一轮重放 `ok:true`、会话日志里有线宽 3、**全新进程只凭
session_id** 调 `verify_replay` 得到同一 `session_id` 与同一 `patch_hash`。宿主侧调试中记下的接入事实（不是缺陷）：
CodeBuddy 的 MCP 工具是延迟加载的，无头要预授权 `ToolSearch` + `DeferExecuteTool`；MiniMax 的 `--model lab/<id>` 在
cn-prod 路由上不认，用 `provider add --use` 设的默认；Codex 走 `wire_api = "responses"` + `env_key`；DSH 走
`DEEPSEEK_BASE_URL` + `--patch` 插 `dsh-mcp-client`。

### G5 本轮另记

- `test_agent_verify::test_timeout_kills_and_reports_error` 在 Lab 上红（结束进程树用了 30 s > 20 s）：`agent_verify.py`
  用 `taskkill /T`——与 F 节 `test_package_smoke` 的现象同源（非管理员会话里 `taskkill` 不可靠），第三次独立撞到。
- 本 PR 自己的一条用例（字体 zip 成员 `a\b.ttf`）在 hosted Windows 上红、被合并队列踢出：Windows 上 zipfile 把
  反斜杠规范成 `/`。修复后先在 Lab 上跑过 `tests/test_winlab.py` 再推——Lab 用来验 Lab 自己。
- `test_ci_qualification::test_kill_stale_actually_reaches_a_runner_workspace_process` 在 Lab 上红（run
  `20261024T163857Z-c46c62`）。**更正（10-25）**：起初记成第四条「非管理员 Windows 上结束进程不可靠」的证据，复查后
  不是——Lab 执行器给 pytest 设了 `TAVOTTO_CI_STATE_ROOT`，用例没固定这个变量，把传入的 root 当成另一个根，就不收
  runner 工作目录下的进程；本机设同一变量同样红。用例不封闭（自托管 `_lab-qualification` 也设这个变量），与 taskkill
  无关，修复在 `fix/win-nonadmin-process-kill`。「结束进程不可靠」的独立证据是前三处。

## H. 桌面宿主 L2（`test desktop-hosts --mode …`，2026-10-25）

固定版本安装包取自 winget-pkgs 清单（URL + SHA-256），Lab 上下载后核散列、核 Authenticode；非管理员 currentUser 静默装，
`window_probe.py` 在会话 1 里等主窗口（可见、未 cloaked、有标题、属于安装目录进程、稳定 5 s），截图后结束进程、卸载。
海外 CDN 经 DMIT（用户 10-25 授权，一次一个），国内 CDN 直连。

| 宿主 | 版本 | 下载线路 | 签名 | 主窗口 | L2 | L3+ | run |
| --- | --- | --- | --- | --- | --- | --- | --- |
| VS Code | 1.141.2 | 直连 | Valid · Microsoft Corporation | 「Visual Studio Code」8.4 s | PASS | BLOCKED（Copilot 账户） | `20261024T173951Z-271d2a` |
| TRAE CN | 3.4.4 | 直连 | Valid · 北京引力弹弓科技有限公司 | 「TRAE CN」20.0 s | PASS | BLOCKED（TRAE 账户） | `20261024T174527Z-78c4b7` |
| WorkBuddy 桌面 | 5.8.3 | 直连 | Valid · Tencent Technology (Shenzhen) | 「WorkBuddy」7.2 s | PASS | BLOCKED（腾讯账户；其 CLI 已 L5，见 G4） | `20261024T174804Z-f024cc` |
| Cursor | 3.20.14 | DMIT Trojan | Valid · Anysphere, Inc. | 「Cursor Agents」35.4 s | PASS | BLOCKED（Cursor 账户） | `20261024T175152Z-c549e6` |
| ZCode | 3.14.1 | DMIT Trojan | Valid · 北京智谱华章科技股份有限公司 | 「ZCode」8.0 s | PASS | BLOCKED（Z.AI 账户） | `20261024T181616Z-2c6296` |
| Claude Desktop | 2.42304.2 | DMIT Trojan | Valid · Anthropic, PBC | 「Claude」6.9 s（登录页） | PASS | BLOCKED（Anthropic 账户 + 地区政策） | `20261024T181003Z-e8f7d3` |

六个都卸载干净、无残留进程。过程中记下的事实：ZCode（electron-builder NSIS）卸载项的 `InstallLocation` 为空、
`DisplayIcon` 指向 `.ico`，第一次 run `20261024T175426Z-2f5c94` 因此找不到主程序判 FAIL（执行器缺陷，不是宿主问题），
改成在图标 / 卸载器目录里找后通过。Server 2025 上没装 VC++ 2015+ 运行库（winget 记为 ZCode 的依赖），ZCode 照样起得来。

**下一步（用户 10-25：下周做）**：桌面版 L3–L5 只对「底下跑 Claude Code / Codex」的路径可做——VS Code / Cursor / TRAE
里的 Claude Code 扩展与 Codex 扩展，隔离配置接 DeepSeek，判定仍按 `mcp_recorder`；另加 CC-Switch 的 MCP 同步路径一条。

## I. 桌面宿主里的 AI 扩展 L3–L5（`test desktop-live --mode …`，2026-10-26）

VS Code 1.141.2 + Open VSX `anthropic.claude-code` 2.1.310（win32-x64，sha256 `ebb9f501…cd39c`，经 DMIT），推理走 DeepSeek
（`deepseek-flash`，Anthropic 兼容接口）。驱动器经 CDP 进扩展 webview 填提示词、点发送；判据同 `hosts-live`，另加进程链。

| 级别 | 结论 | 证据 |
| --- | --- | --- |
| L2 | PASS | 主窗口「Visual Studio Code」约 4 s |
| L3 | PASS | 两轮首个 `initialize` 的 clientInfo 均为 `claude-code 2.1.310`，父进程链 `claude.exe ← Code.exe`（安装目录内） |
| L4 | PASS | 第一轮服务端：health → open_figure → apply_overrides → export → verify_replay，全部 ok |
| L5 | PASS | 改动进会话日志、导出 PDF、重放一致；全新 VS Code 进程凭 session_id 重放并关闭会话 |

run `20261026T133335Z-c0827a`（第一轮 165 s、第二轮 74 s）。扩展没要求 claude.ai 登录（`ANTHROPIC_AUTH_TOKEN` 在宿主进程环境里）。

前七次 run 依次暴露的执行器问题（都已修，都不是 Tavotto 产品的故障）：Open VSX 证书链止于 Lab 根存储里没有的
Starfield Root CA G2；扩展目录实际叫 `anthropic.claude-code-2.1.310`（无平台后缀）；密钥管道写完 `DisconnectNamedPipe`
使读端 errno 22（`20261026T130344Z-7e8fcd`）；只看录制器安静会在模型思考时提前收场（`20261026T130906Z-feb0dd`）；
面板里的 Bash 权限弹窗没人点而挂住（`20261026T132012Z-44023f`）。

### Cursor / TRAE CN + 同一个扩展（2026-10-26）

| 宿主 | 版本 | L2 | L3（进程链） | L4 | L5 | run |
| --- | --- | --- | --- | --- | --- | --- |
| VS Code | 1.141.2 | PASS | PASS（`claude.exe ← Code.exe`） | PASS | PASS | `20261026T133335Z-c0827a` |
| Cursor | 3.20.14 | PASS | PASS（`claude.exe ← Cursor.exe`） | PASS | PASS | `20261026T141938Z-ff41f1` |
| TRAE CN | 3.4.4 | PASS | PASS（`claude.exe ← Trae CN.exe`） | PASS | PASS | `20261026T151200Z-f5a12a` |

三个宿主都没要求登录各自的账户就能用扩展（Cursor 自己的 AI 功能要登录，但不挡扩展）。途中修的执行器问题：Cursor 打开扩展 URI
前的模态确认框挡住命令面板、Cursor 自己的登录提示被误判成扩展要登录（`20261026T134946Z-82ba4a`）；TRAE 首启引导页
`setup.html` 被当成工作台、引导后换进程重开窗口（CDP 断开 `20261026T145044Z-553df4`、误判宿主退出 `20261026T145631Z-d4aaf2`）、
扩展未激活时命令面板一次打不开（`20261026T150227Z-682f81`）。

### 原生 Agent 首屏（`desktop-native`，不登录、不打字）

| 宿主 | 首屏 | 结论 | run |
| --- | --- | --- | --- |
| VS Code | 内置 Chat：「Sign In」「Models」 | 要 GitHub 账户（首屏没有不登录的自带 key 入口） | `20261026T140104Z-7a817b` |
| Cursor | 「Cursor’s AI features require you to be logged in」 | 要 Cursor 账户 | `20261026T140328Z-da3aae` |
| TRAE CN | 首启引导「欢迎使用 / 开始」 | 引导之后未探（扩展路径已 L5） | `20261026T143125Z-b0be4b` |
| WorkBuddy | 先「Preparing Environment」，之后「Sign In」 | 要腾讯账户 | `20261026T143420Z-5e4e92` |
| ZCode | 「Connect to Z.ai / Connect to BigModel / Use API key」 | **可不登录、自带 key** → 已做到 L5（见下） | `20261026T141359Z-fa7e7c` |
| Claude Desktop | 带调试端口起不出窗口；不带时窗口「Claude」 | 拒绝调试端口，文字未取到 | `20261026T143831Z-c456bc` |

### ZCode 原生 Agent（`zcode-native`，2026-10-27）

ZCode 3.14.1 自己的 Agent，自定义 Anthropic 兼容接口接 DeepSeek（`deepseek-flash`，专用限额 key，落盘例外），首启「Use API key →
Skip for now → Skip」，没登录 Z.ai / BigModel。

| 级别 | 结论 | 证据 |
| --- | --- | --- |
| L2 | PASS | 主窗口「ZCode」约 6 s |
| L3 | PASS | 两轮 clientInfo `zcode 0.16.9`，父进程链里 `ZCode.exe`（安装目录内） |
| L4 | PASS | health → open_figure → apply_overrides → export → verify_replay 全部 ok |
| L5 | PASS | PDF 导出、重放 23 个元素一致；全新 ZCode 进程凭 session_id 重放并关闭 |

run `20261026T160936Z-fe61a2`，`ondisk_key.wiped = true`（run 目录与 `%APPDATA%\ZCode` 均无残留）。途中：只写项目目录与
`cli\` 时桌面版读不到 MCP 与权限（`20261026T153350Z-c4c8f7`）；配置里的 `yolo` 不生效、工具名不认前缀，第一次工具调用就停在权限框
（`20261026T154420Z-ca5927`）。

**产品侧发现**：`tavotto_open_figure` 的结果（含内嵌 SVG 与完整 manifest）超过 Claude Code 扩展的 MCP 输出上限，扩展把它
落成文件，模型要用 Read / Grep 读回来才能取 gid / prop；真实用户在 VS Code 面板里会因此看到读文件的权限确认。另记 issue 跟进。

## E. 安全与仓库

| 验收项 | 状态 | 说明 |
| --- | --- | --- |
| 现有 CI 不削弱 | PASS | 未改任何 workflow、Gate、merge queue、required checks |
| Compatibility Guardian 信任边界不变 | PASS | 未迁移、未改 |
| 不让公开 PR 在 Lab 跑 | PASS | 可信度判定只放行 origin/main 或作者 + 提交者都是维护者的提交；有用例 |
| 不泄露凭据 | PASS | 凭据只在 Mac 0600 文件与 Lab `secrets\`；报告只存出口散列；终端从未打印 |
| 不影响 ci-infra runner | PASS | 不注册 runner、不改 `actionlint.yaml` |
