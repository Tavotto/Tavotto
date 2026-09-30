# 验证链（按层）：CompatBench / 等价性矩阵 / 不变式 / 冒烟 / nightly / E2E / 性能基线

> 原文出自 `.github/AGENTS.md`「验证链（按层）」（2026-09-18 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **Matplotlib CompatBench**（`tests/compat/` + `scripts/ci/compat_matrix.py`，
  完整说明 `docs/ci/matplotlib-compatibility.md`）：与 `tests/acceptance/`
  **问的不是同一个问题**——那边比「Tavotto 今天 vs 昨天」（抓不到「我们从
  第一版起就一直改错某个 artist」），这边比「**原生 matplotlib** vs Tavotto
  零 override」，并沿九级漏斗（discover → execute → capture → open →
  semantic → edit → replay → export → fidelity）量化「外部 matplotlib 世界
  我们兼容多少」。两套 corpus **不许合并**，合了就再也分不清「我们退步了」
  和「我们本来就不支持」。
  * 结果分六类（`full_support` / `partial_support` / `unsupported_by_design` /
    `environment_dependency` / `product_bug` / `invalid_fixture`），
    **清单里没有声明过的失败一律记成 `product_bug`**；想声明某一级不该过
    要具体到阶段（`expected.<stage>=false` + `expected_false_reasons`），
    而 `execute` / `capture` / `open` **任何档位都不许声明成 false**。
  * 基线 `tests/compat/baseline.json` 与视觉基线同一套纪律（缺失 = FAIL、
    CI 绝不自动更新、`CI=true` 时 `--update-baseline` 被硬拒）；另加两条：
    非 full_support 必须写 reason、`product_bug` 还必须写 follow_up、
    **Tier 1 不许存在 product_bug**（schema 层面挡住）。**基线不是豁免名单。**
  * 判据一律复用产品自己的：重放比对走 `app._compare_manifests`（与写回放行/
    阻断同一把尺），像素比对走 `scripts/ci/pixelcompare.py`（与 golden 视觉
    回归**同一份算法**，从 `visual_regression.py` 提取出来的，不许再写第二份）。
  * artist 普查是**诊断**不是门禁：真正的 pass/fail 一律走生产路径的 worker。
  * 跑法：`--smoke`（PR，2~4 分钟）/ `--all` / `--target bundled|minimum|browser`
    / `--case <id>` / `--gate pr|main|nightly|release`。
- **四路等价性矩阵**（`tests/test_equivalence_matrix.py`，引擎的最终验收物）：
  `hot_apply(patches) == 清空后全量重放 == 全新 worker 重放 == 写回文件后全新
  worker 重放`，六个场景 × 十组 patch，判据直接复用 `app._compare_manifests`。
  四条腿各起独立 worker，核心场景在 workerd 控制面再走一遍。缺 matplotlib /
  缺 CJK 字体各自 skip 并注明理由。
- **五条结构性不变式**（`tests/test_invariants_engine.py` +
  `tests/support/engine_invariant_probe.py`）：能力真实 / 逐字还原 /
  热态==全量重放（含**删除**）/ 不许静默消失 / 单一权威。它们与
  `tests/acceptance/` 和 CompatBench 问的不是同一个问题，**三者不能互相替代**。
  能力真实那条**用像素说话**（`preview_png` 状态中立、6ms 一张、逐字节确定）。
  它们的推广——**带种子的随机操作序列**（`tests/test_override_sequences.py`，每步
  HOT == CLEAR+REPLAY、末尾 HOT == FRESH）——默认 8 条种子跟着常规套件走，lab 的
  nightly 及以上再以 `TAVOTTO_SEQ_SEEDS=32` 单跑一遍（同机 4 片，`TAVOTTO_SEQ_SHARD=K/N`
  按种子取模切片；`docs/ci/release-qualification.md`）：随机负责发现，发现了最小化后
  钉进 `FIXED` / `KNOWN`，PR 档不加时长（第四族 #423 是 24 条才碰上的）。
- **端到端冒烟**：`python scripts/smoke_app.py --python .venv/bin/python`
  （或 `--exe dist/Tavotto/Tavotto.exe`）。隔离用户目录 → 渲染环境自检 →
  打开项目 → 渲染 → 导出 → 覆盖导出 → 干净退出（走 `/api/shutdown`，需
  `TAVOTTO_ALLOW_SHUTDOWN`；退出后断言没有残留 worker 子进程）。
  `--expect-source bundled` / `--expect-packages numpy,pandas,…` 是 Windows 桌面版
  的核心验收：少了它，一台碰巧装着 matplotlib 的 CI 机器会让「内置 runtime 根本
  没打进去」全程绿灯。CI 的 windows-exe-smoke 与 nightly 共用它。
  验收项目在 `examples/runtime_check/`（一个把整套内置科学栈都用一遍的脚本）。
  `--expect-control-plane workerd` 同理盯另一件静默失灵：桌面产物必须自带
  Rust supervisor，少了它渲染回退到 Python 池——功能全在、只是慢、零报错。
  两条冒烟腿都**不设 `TAVOTTO_WORKERD`**：要验的正是自动发现。
- **nightly 的安装链路（`nightly.yml`，每晚一次）**：三档代表性环境
  （无 Python / 官方 Python / Conda）× 中文用户名 + 中文区域 + cp936。
  冒烟项目**按档给**——`examples/runtime_check` 要整套科学栈，只有内置 runtime
  满足；指向用户自己解释器的两档用 `examples/figures`（numpy + matplotlib），
  它们验的是解释器优先级与中文路径。「无 Python」那档还会现打一个 NSIS
  安装器，走**装一遍再冒烟**：静默安装 → 断言安装目录里有 sidecar + 内置
  runtime + workerd → 起真壳确认它能拉起 sidecar 且退出不留孤儿 → 对装出来的
  sidecar 冒烟 → 覆盖安装（升级）再冒一次 → 静默卸载；随后**真 GUI** 各装一遍
  默认位置与中文 + 空格的自选目录（点目录页、读注册表落点、自选那遍还跑装出来的
  CLI）。这条链路只有真装一遍才知道，而且必须挂在**在发的那个发行形态**上。
- **黄金路径 E2E**：`cd web && pnpm e2e`（Playwright，`TAVOTTO_EXE` 指打包产物、
  缺省用 `python -m tavotto`）。跑之前先 `python scripts/build_frontend.py`——
  包内 `src/tavotto/web/` 优先于 `web/dist`，只跑 `pnpm build` 测的还是旧界面。
- **真 Tauri 窗口用例**（issue #542，`tests/desktop_windows/`）：只有**真壳里的 WebView2**
  才量得到的那一段——Playwright 起的是浏览器，不是这个窗口，**不许拿它冒充**。
  * 腿：**只有** `nightly.yml` 的 `windows-install`「无 Python」档，在刚装好的 NSIS 产物上跑，
    带 `TAVOTTO_DESKTOP_WINDOW_REQUIRED=1`（缺前提即红）；junit 里任何 skip（含 xfail）判红、
    执行条数钉在实测值。别处整目录 skip 并点名这条腿（`tests/test_e2e_leg_topology.py::TestDesktopWindowLeg`）。
    不进 PR 快档。
  * 通道：HKLM 策略 `SOFTWARE\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments`
    （值名 = 壳的文件名）开调试端口，用完即删——`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` 会被
    Tauri 自带参数覆盖、HKCU 策略不生效；与本机 WebView2 Runtime 同版本的 msedgedriver（验
    Authenticode）以 `debuggerAddress` **附着**，不用 tauri-driver（没有预编译、也不需要）。
  * **不注入全局输入**：键鼠经 CDP 进这个 WebView 的渲染进程；窗口级动作按**句柄**投消息
    （`WM_CLOSE`、菜单 `WM_COMMAND`、对话框控件 `WM_SETTEXT` / `BM_CLICK`）；剪贴板由测试进程
    以「另一个应用」的身份读写。`SendInput` 一类名字不许出现在夹具里；muda 的预定义剪切 /
    复制 / 粘贴 / 全选在 Windows 上就是 `SendInput`，那几个菜单项一概不点。

  | #542 | 内容 | Windows 真窗口 | 盲点（判不出就不判） |
  | --- | --- | --- | --- |
  | 5 | 系统文件选择器打开 `.py` | `test_file_picker.py`：主页「导入我的脚本」→ 原生 `#32770` 对话框 → 中文 + 空格路径 → 项目打开、后端最近项目即此目录 | **拖入**：真 OLE 拖放要系统指针输入；Windows 壳也不旁听拖放（ADR 0092 §五），拖入后同样落到这个选择器。拖放区的 DOM 降级由 `web/e2e/home.spec.ts` 量 |
  | 6 | 中文 / 英文 IME | **不覆盖** | IME 组字（TSF / IMM32 候选窗、上屏）只有真键盘 + 真输入法才有；CDP 的 `imeSetComposition` 是渲染进程里的合成事件，不是输入法，拿它判「IME 能用」是冒充。靠人工清单 |
  | 7 | 剪贴板跨应用 | `test_clipboard.py`：⌃C 后系统剪贴板里是对象载荷、所有者在本实例进程树里；别的应用改写载荷后 ⌃V 粘出改写后的样子；普通文字 ⌃V 不产生对象（没有内存兜底） | 图片 / 富文本格式没有用例（产品只收 `text/plain` 载荷） |
  | 8 | 真 WebView 拖动与 undo/redo | `test_drag_undo.py`：CDP 指针拖动、⌃Z 逐像素回原位、⌃⇧Z 回拖后 | 触控板 / 高 DPI 缩放下的指针换算 |
  | 9 | 关掉再开恢复 | `test_restart_restore.py`：`WM_CLOSE` → 无参重开；必绿（#718 稳定端口合入后转绿，#751 去掉了 xfail），同源 `location.reload()` 对照为绿 | — |
  | 12 | 强杀后恢复 | 同上，`TerminateProcess` 壳、断言子进程跟着退；必绿（同上） | 「改动还没落盘就被杀」那一刻的崩溃副本：时序抢不稳，没有稳定的被测状态 |
  | 13 | 菜单 / 快捷键 / 焦点 | `test_menu_keyboard_focus.py`：真菜单栏上加速键逐条在位；菜单撤销 / 重做、⌃D 在输入框里让位、「设置」开关后焦点；纯键盘 Tab 到素材卡 → 放图 → 撤销 / 重做（#37 真机那一段） | 加速键表的**查表**（真按键 → 哪条命令）要真按键；这里投的是查表之后那条 `WM_COMMAND`，菜单栏上的加速键文字是它的镜像。#37 的完整键盘闭环仍在 `keyboard-golden-path.spec.ts`（chromium 与 WebView2 同引擎） |

  **macOS**：WKWebView 没有 WebDriver，**整张表都不覆盖**（包括 9 / 12 / 13）——壳里没有对外的
  自动化入口，AppleScript / System Events 只能发全局按键（落进用户前台的别的应用，禁用）。
  macOS 上这些格靠发版前的人工清单；将来若给壳加一个只在测试构建里编进去的驱动命令，再补。
  反证方法：`TAVOTTO_DW_INJECT_JS=<脚本>` 在每次界面就绪后注进一段把被测行为弄坏的脚本（只给
  反证用，报告头会标出来），每条的记录在 PR #542 的实现 PR 正文里。
- **性能基线**：`python scripts/bench_render.py --python .venv/bin/python`。
  结论与前后对照都写进 `docs/perf-baseline.md`——**改性能前先在那儿指出一个
  数字**。它**默认不隔离 HOME**（重置 HOME 会让每次冷启动多出 9 秒字体缓存
  重建；要量首次体验用 `--fresh-home`）。
- **拖动性能（前端，用户机器上）**：性能探针（ADR 0075）在用户自己的 Tavotto 里录拖动，
  报告用 `python scripts/perf_report.py <报告>.json --html out.html` 分析；用法见
  `docs/perf-probe-guide.md`。后端基线量不到拖动（拖动途中后端一次都不被惊动）。
- 后端冒烟（示例项目）：`tavotto --figures examples/figures --no-browser
  --insecure-no-auth` 后 `curl -X POST /api/engine/render
  -d '{"id":"Fig1_kinetics.pdf","patches":[]}'`（不带 `--insecure-no-auth` 时
  curl 要加 `X-Tavotto-Auth` 头，见 ADR 0008）。
- 导出保真：导出 PDF 用独立读取器（PDFium 文字层 / `tests/support/pdfread.py`）验证矢量文字。
