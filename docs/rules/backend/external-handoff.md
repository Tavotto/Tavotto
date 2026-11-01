# 外部交接（`tavotto open` 与桌面唤起）

> 原文出自 `src/tavotto/AGENTS.md`「外部交接（`tavotto open` 与桌面唤起）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`src/tavotto/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_install_locate.py`、`test_handoff.py`、`test_open_script_route.py`、`test_desktop_launch.py`

<!-- agents-route
sheet: src/tavotto/AGENTS.md
order: 310
改到: `engine/locate.py`、`cli.py`、`handoff.py`、`tavotto open` / `doctor`、桌面 argv
主题: 外部交接 → `external-handoff.md`
必守要点: 发现链唯一权威 `locate.py`；子命令分派在 import Flask 之前；`HandoffError` 一律带稳定 code
-->

完整版在 `docs/adr/0005-external-handoff-and-codex-plugin.md`，改动前先读。
插件侧（codex-plugin/ 的镜像定位器、update_check、技能纪律）见
`codex-plugin/AGENTS.md`。

- **发现链的唯一权威是 `engine/locate.py`**（纯标准库）：`TAVOTTO_CLI` → PATH →
  安装清单 `install.json` → 已知安装位置 → HKCU（只当补充）→ 当前解释器。
  **只装了桌面版也必须能被发现**——这是 2026-08-18 修的那个 bug：装出来的
  `Tavotto.exe` 与 sidecar 都是 GUI 子系统可执行文件，没有真终端时
  `sys.stdout is None`、输出被 `entry.py` 改道进 app.log，**调用方拿到的是空
  stdout**。所以 `packaging/tavotto.spec` 从同一个 Analysis 多出一个
  `console=True` 的 `tavotto-cli`（共用 `_internal/`，只多 ~1.5 MB）。
  **别把 GUI exe 当 CLI 调**，哪怕它接受同样的参数。
  安装清单落在**用户配置目录**（安装目录可能只读、卸载会被删）：安装器装完跑
  `tavotto-cli doctor --json --write-manifest`（让 CLI 自己写，NSIS 不拼 JSON），
  应用每次启动 `locate.refresh_manifest()` 刷一遍（**只补充不抹掉**：pip 装的
  那份是非冻结进程、只去惯例位置找壳，无条件写下去会把桌面版记的非惯例路径
  抹成空，一次 `tavotto --figures …` 就够），卸载器在**删文件之前**移除。
  读的一方要核实里面的路径还在——清单是缓存不是真相。**任何单一机制都不是
  唯一依据**（清单可能没写成、注册表可能被策略锁住），也**不动用户 PATH**。
  `sidecar/Tavotto` 这一段的出处只有 `tauri.conf.json` 的 `bundle.resources`，
  Rust 壳 / locate / NSIS 三处同源。协议与错误码全文在 `docs/handoff-protocol.md`。
- **子命令在 `engine/cli.py`（`open` / `doctor`）**，三个入口都先问它一句：
  `tavotto/cli_entry.py`（pip/pipx 的 console script 与 `python -m tavotto`）、
  `packaging/entry.py`（冻结产物）、`app.main()`（兼容旧调用方式）。
  分派**必须在 import Flask 之前**：一次交接用不上任何 HTTP 端点，却要付
  整个 Flask + RenderCore 的冷启动；更要紧的是 `doctor` 本该是「装坏了怎么查」
  的工具，界面依赖 import 失败时它自己也得能跑
  （`test_subcommands_run_without_flask_or_pymupdf` 看护）。
- **`HandoffError` 一律带稳定 `code`**（`registry_write_failed` /
  `path_not_found` / `launch_failed` …），`--json` 失败也输出一行 JSON。
  文案随时可改，code 不行——调用方按它分诊。裸抛的那条由
  `test_every_handoff_error_carries_a_code` 挡住。
- **入口是 `tavotto open <产物|脚本|目录>`**（`engine/handoff.py`，纯标准库）：
  解析目标 → 登记 stem → 唤起界面。子命令在 argparse **之前**分派——主入口是纯
  flag 形态（`tavotto --figures …`），改成 subparsers 会把既有命令行整个换掉。
  项目 = 含 `tavotto_registry.json` 的那一层（向上找 ≤3 层，**有上限**：静默把上层目录
  当图库会把一整棵源码树当素材扫）。注册表合并复用 `discover.merge`，
  **不另写裁决**；读不懂就报错，绝不重写用户手写的注册表。
- **桌面契约是 argv `--open <目录> [--stem <stem> | --pick-script <脚本>]`**：
  生产者唯一 `handoff.desktop_argv()`，消费者唯一
  `src-tauri/src/main.rs::parse_open_args()`，两侧各有单测，改一边必须同步
  另一边；macOS 的 `open -na … --args` 之后**复用 desktop_argv 的切片**，
  不再手拼第二份。`--pick-script` 是多 Figure 交接的选择信息（脚本相对
  路径，与 `--stem` 互斥）——壳只透传，Figure 选择器在前端。
  首启：项目 → sidecar 的 `--figures`，stem → 落地 URL 的 `?open=`、
  pick → `?pick=`（browser-new 由 `--open-pick` 带给 `app.main`）；
  已开着窗口：单实例转发 argv → emit `tavotto:open`。两条路汇进前端同一个
  `lib/openRequest.ts`（浏览器模式共用同一套查询参数，定位逻辑只有一份）。
- **`tavotto open script.py` 自动 safe probe（2026-08-26，Session 6）**：
  显式给出 `.py` = 运行意图（总纲原则 5）。`handoff.resolve_script_route`
  的顺序：现有注册表/静态发现的每张图都已有路由（磁盘原件或 runtime
  cache，判据各自唯一：`figcapture.find_original_artifact` /
  `runtimeasset.load_metadata`）→ 复用；否则 probe——本机实例在跑就
  **委托**（`POST /api/registry/probe`，同一个 `_PROBES` 并发闸，409 →
  `probe_in_progress`），否则本进程 `probe_and_register` + 物化 cache
  （只复制热 worker 的预览，绝不二次执行），返回前 `pool.invalidate`
  关净 worker（**不留 orphan**；交接目标进程读注册表 + cache，零重跑，
  看护 `tests/test_open_script_route.py` 的 execution-count 用例）。
  单图直达 stem；多图 `--stem` 显式选或把 `pick` 交给界面选择器，
  `--no-launch` 下必须显式选（`multiple_figures_found`）。稳定 code 表
  在 `docs/handoff-protocol.md`（missing_dependency 映射成
  `native_run_required`，原始 code 在 extra）。`--no-probe` 关掉探测。
- **macOS 唤起走 `open -na <bundle> --args …`，不再直接 exec 包内二进制**
  （2026-08-20 实测修复）：GUI 进程会继承调用方的执行上下文，从受限环境
  （沙箱 shell、无 Aqua 会话）直接 exec 会在 AppKit `RegisterApplication`
  处 SIGABRT——**转发 argv 的第二个实例也一样崩**（NSApplication 初始化先于
  单实例检查），所以旧注释「open 送不到、只能直接 exec」只说对了不带 `-n`
  的那半：`-n` 起的新实例照样把 argv 交给单实例插件转发。`open` 把 spawn
  委托给 launchd，App 落在用户 GUI 会话里。Windows / 裸二进制覆盖仍直接
  spawn。
- **桌面模式的 `ok: true` 是等出来的**（`_launch_desktop_via_open` /
  `_launch_desktop_via_spawn`，带限期轮询、可注入时钟，**不是 sleep**）：
  进程存在且活过稳定窗（或单实例转发完成）才算成功；起来就死回
  `launch_failed` + `exit_code`/`signal`/`log_path`/`retryable`（HandoffError
  的 `extra`，`--json` 逐键并入输出），限期内没出现回 `launch_timeout`。
  **受限沙箱不拉进程（#1149）**：Windows 桌面路径在 spawn **之前**用 ctypes 查进程令牌
  （`_windows_token_sandbox`：`GetTokenInformation(TokenIsAppContainer)` / `IsTokenRestricted`，只看操作系统令牌，
  **不认 `CODEX_*` 之类宿主私有环境变量**），命中就回 `sandboxed_launch`
  （`sandbox` + `retryable: true`），一次 spawn 都不做；查不出来（ctypes / OSError）按原样走。
  **绝不借 explorer.exe / 计划任务等手段代为启动——那是逃逸沙箱**，出路只有调用方在沙箱外重跑。
  **转发卡住不算起来了**：spawn 之前 `_pids_of`（Windows 经 ctypes 枚举进程、按**完整路径**精确比对，不按映像名——sidecar 同名 `Tavotto.exe`，孤儿 sidecar 会被误当成已有实例）已有实例、
  新进程却活过观察窗口不退，且**复查**时旧 pid 仍在（排除新进程自己；复查抛错 / None 保守放行，误杀健康的新实例比假成功更糟）→ 先 terminate / kill（kill 后有界等退出，不留僵尸）再回 `launch_failed` + `reason: "forward_stuck"`；
  原本没有实例的 `process_alive` 与查不出实例（None）的旧行为不变。
  看护同在 `tests/test_desktop_launch.py`。
  sidecar 日志路径由 `handoff.sidecar_log_path()` 按 `brand.DESKTOP_BUNDLE_ID`
  推导（与 tauri 的 app_log_dir 同源）。看护 `tests/test_desktop_launch.py`。
- **Windows 拉起桌面版一律带「脱离包」策略（#1574）**：Codex 桌面版是 MSIX 包，从它继承包身份的进程在
  `%LOCALAPPDATA%` 下新建的文件会被重定向进 `Packages\<pfn>\LocalCache`（最近项目 / 设置 / 受管环境各一套）。
  实测只有 `CreateProcessW` + `STARTUPINFOEX` + `PROC_THREAD_ATTRIBUTE_DESKTOP_APP_POLICY`（0x00020012）=
  `BREAKAWAY_ENABLE_PROCESS_TREE`（0x01）能脱离，且整棵子树在包外；`subprocess` / `cmd /c start` / `Start-Process` 都不行。
  所以 `_spawn_detached`（桌面 argv 与 browser-new 的唯一 spawn 入口）在 Windows 上先经
  `engine/msixbreakaway.py::spawn_detached`（**唯一实现**，`tests/test_msix_breakaway.py` 扫源码钉住）；
  返回 None（非 Windows / API 不可用 / 任一步失败）才退回原来的 `Popen`，注入的 `spawn`（测试桩）不走它。
  兜底在 Rust 壳（`src-tauri/src/msix_breakaway.rs`，同一契约的镜像）：壳启动时带包身份就用同一策略重新拉起自己后退出，
  环境变量 `TAVOTTO_MSIX_BREAKAWAY` 防循环并把结果（`relaunched` / `still_packaged` / `failed`）交给诊断包。
  **CLI 一侧同理**：`handoff.cli` 开头先 `msixbreakaway.relaunch_cli(["open", *argv])`——带包身份就脱包整条命令重跑
  （继承 stdio、等待并透传退出码；env `TAVOTTO_MSIX_CLI_RELAUNCH` 防循环，重启失败清标记走原路径），因为本地 safe probe 等
  落盘发生在拉起桌面版之前。壳替身起来后**不立刻 exit(0)**：最多等 `HANDOFF_WAIT_MS`（> 启动器观察窗口）并透传替身退出码，
  启动器才能把替身崩溃报成 launch_failed。壳的「脱包在崩溃过滤器之后、Builder 之前」看护是 syn AST（带注释掉调用的自测）。
  已写进包私有区的数据由 `engine/msixmigrate.py` 补回（见下一条）。
- **MSIX 私有区旧数据一次性补回（#1574 善后，用户拍板方案 B，界面零提示）**：`msixmigrate.run_once()` 在 sidecar（`app.main` 的
  `setup_logging()` 之后）与 CLI（`handoff.cli` 的 `relaunch_cli` 之后）各调一次；**只在 Windows 且 `has_package_identity()` 为假时**迁
  （带包身份时写也会被重定向）。源 = `%LOCALAPPDATA%\\Packages\\*\\LocalCache\\{Roaming,Local}\\Tavotto`，多个包按目录 mtime 新→旧，
  后者只补前者没补到的。规则：**只补缺、从不覆盖、原件不删**，临时名 + 改名，失败只记计数。
  搬：`EAGER_ITEMS` 白名单（`origin/`、`state/`、`ai/`、`source-backed/`、`script_backups/`、`runconfigs/`、`inputtranscripts/`、
  `scriptanswer-contexts/`、`profiles/`、`layouts/`、`exports/`、联系信箱、`mcp-sessions/`、`cache/ai_snapshots`、`cache/fix_snapshots`）+
  `cache/ai_history.sqlite3`（sqlite 备份接口取一致快照）；不搬：`cache/` 其余（engine / mathtext / runtime / probe / native / exec-trace /
  日志）、`probe-results/`、`perf-reports/`、`session/`、`codex-marketplace/`、`tutorial/`、锁与 `.tmp`。新增数据目录条目时判断「可再生吗」，
  不可再生就加进白名单。`config.json`：真实处没有就整份带过去；已有则经 `config.transaction()` 保守合并（最近 / 收藏并集真实在前、
  `projects` 缺的条目、`ai.providers` 按 id 缺的记录、`ai.agents` 缺的、`ai` 其它缺的键），已有值不改，其它段（遥测同意等）不碰；
  真实处文件读不出来就不碰。**大目录用到时才搬**：`managedenv.env_dir`（`environments/<指纹>`，校验 manifest + active 代解释器 +
  `pyvenv.cfg` 的 home 在，home 在私有 Python 下则连带按需搬 `private-python/runtimes/<id>`）与 `managedcli.root_dir`（`cli/<名>/`，
  指针 + 入口文件在）调 `msixmigrate.adopt_dir`；复制到临时名 → 逐文件大小核对 → 校验 → 原子改名，校验不过当没有、走原创建 / 下载。
  幂等标记 `state/msix-migration-v1.json`（几个源 / 拷了 / 跳过 / 失败 / config 是否合并 / 按需搬了几个目录）；同步阶段 8 s 预算，超了不写
  `complete`、下次接着补；跨进程锁 `state/msix-migration.lock`。诊断包 `system.msix_data_migration`（闭集 `state` + 数字，不带路径与包族名，
  bundle schema 13）。看护 `tests/test_msix_migrate.py`、`tests/test_windows_regressions.py`。
- **前端交接三条纪律**（`applyOpenRequest`）：① 同项目**绝不**调
  `projectStore.open`（那条路 switchDocument 成空白文档，用户排的版当场没）；
  ② 必须重扫素材（交接的图刚写到磁盘，实例手里那份 panels 是旧的）；
  ③ 找不到就说找不到，绝不退而求其次选别的面板。重复交接同一张只选中，不叠第二份。
- **交接三态（ADR 0131 第六节）**：`ok` 的语义不变，只加性地给 `launch.render`
  （`ok|failed|unverified` + `verified` + 稳定 `code`/`reason`）与插件顶层 `launch_success` /
  `render_success` / `verified_success`。确认信号是**经认证的 HTTP 查询** `GET /api/handoff/render-status`
  （`/api/engine/render` 收尾把结论记进 `engine/handoffreceipt.py` 的进程内有界结论表；CLI 唤起**前**取
  `since`、之后有界轮询；凭据 = 浏览器模式的完整 secret 或桌面 sidecar 发布的 scoped 只读 token
  `handoff-status-<端口>.json`，只对该端点有效，ADR 0008 修订）。**CLI 不往磁盘写任何东西**。
  到点没结论 = `unverified/timeout`，没连上实例 = `unverified/channel_unavailable`，绝不记 failed；`verified` 的判据只在 `handoffreceipt.summarize` 一处，插件只搬运。看护
  `tests/test_handoff_render_receipt.py`。
  **重复交接已显示的图（#1154）**：前端命中已显示面板只选中不渲染，会让 CLI 白等满预算；所以 `applyOpenRequest` 在那条路径上
  经**会话认证**的 `POST /api/handoff/display-ack` 回当前渲染状态（`ok`/`failed`/`displayed`），后端按当前时间、同形状记进同一张 `LEDGER`
  （`registry_match` 后端自己查，`verified` 仍只走 `summarize`）；`displayed` = `unverified/already_displayed`。scoped token 不能写这个入口；
  老前端不发 = 等到超时。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 发现链唯一权威 `locate.py`，清单是缓存不是真相、不动用户 PATH
- 子命令分派在 import Flask 之前
- 桌面契约 `desktop_argv()` ↔ `parse_open_args()`
- macOS 走 `open -na … --args`

## 来源记录（ADR 0139 PR3 第一片）

- `engine/originrecord.py`：插件交接跑过脚本之后写的**弱线索**，键是原件字节 sha256，落
  `data_dir()/origin/<sha>.json`；有界（`MAX_RECORDS`，满了先清字节已变 / 文件已没的）、`atomicio` 原子写、
  白名单校验（形似路径的值拒收）、**读坏了 = 无记录（fail-open）**——与写回闸 / 采用记录
  （`artifactcontext`，fail-closed）相反：它只用于展示，**绝不进任何判定**。
- 写：只有插件 `handoff.py` 在 `ran: true` 时经 `tavotto open --origin-json`（仅记录模式）写；
  不存解释器路径 / argv / 环境变量。读：`app._source_backed_result` 在响应的 `source_backed.origin`
  只读展示（含本次重画的 `redraw` 版本）；不触发换解释器、不放宽校验。
- 看护：`tests/test_origin_record.py`（记录有界 / 原子 / fail-open / 隐私；插件真跑脚本记的是脚本进程的实际值、
  只在 ran:true 写；准入 origin 在场判定不变）。
