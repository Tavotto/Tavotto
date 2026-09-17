# 外部交接（`tavotto open` 与桌面唤起）

> 原文出自 `src/tavotto/AGENTS.md`「外部交接（`tavotto open` 与桌面唤起）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

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
  整个 Flask + PyMuPDF 的冷启动；更要紧的是 `doctor` 本该是「装坏了怎么查」
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
  sidecar 日志路径由 `handoff.sidecar_log_path()` 按 `brand.DESKTOP_BUNDLE_ID`
  推导（与 tauri 的 app_log_dir 同源）。看护 `tests/test_desktop_launch.py`。
- **前端交接三条纪律**（`applyOpenRequest`）：① 同项目**绝不**调
  `projectStore.open`（那条路 switchDocument 成空白文档，用户排的版当场没）；
  ② 必须重扫素材（交接的图刚写到磁盘，实例手里那份 panels 是旧的）；
  ③ 找不到就说找不到，绝不退而求其次选别的面板。重复交接同一张只选中，不叠第二份。
