# .github/ — CI、发布与验证链规则

仓库级路由与不变量在根 `AGENTS.md`。

## CI 分层（1.0 稳定化，2026-08-21 起）

CI 按**发生时机**分工（`.github/workflows/ci.yml` 抬头有全图，2026-08-25
Merge Queue 定版）：PR = 快速反馈（python-lint / invariants / backend-fast /
frontend / workerd / **desktop-shell** / compat-smoke / CodeQL）；merge_group = 完整合并资格的唯一常规执行
点（backend-platforms / package ×3 / 两个真产物冒烟，Merge Queue 对「最新
main + 前序 PR + 当前 PR」的组合提交验证）；`full-ci` 标签 = 在 PR 自己的
SHA 上提前跑全套；push main = 轻量落地审计（main-landing-audit，不重复打
包）；nightly / lab / release 照旧。**覆盖面一条没减，改的是时机**。ruleset
的 required checks 只有三个稳定 Gate（CI fast gate / CI integration gate /
CodeQL gate），判定收敛在 `scripts/ci/aggregate_gate.py`——普通 PR 上
integration gate 显式 deferred，merge_group 与 full-ci 永远不许 deferred。
迁移顺序与 Ruleset 工具见 `docs/ci/merge-queue-rollout.md`；受管生成物
（canvas.html 等）的冲突域治理与 stack / train 协作见
`docs/ci/parallel-prs.md` + `.github/conflict-domains.json`。ci.yml 与
codeql.yml 的 `cancel-in-progress` **只对 PR 开**：merge_group 候选与 main
的唯一验证记录都不许被取消，tag / release 链路不在分组里。

四条 workflow 的顶层 env 钉 `TAVOTTO_NO_TELEMETRY=1`——**CI 绝不产生真实的
产品事件**（细节见 `src/tavotto/AGENTS.md` 的遥测一节）。

## 门禁纪律

- **反空门禁纪律**：新增的核心不变式测试，提交前必须手工反证一次——把修复
  拿掉，确认它真的红，并把结论写进 PR。「读的人会以为它在挡什么，而它什么
  都没挡」比没有更坏。豁免表要写得出理由，并区分「豁免」（本来就不画）与
  「使能」（画在一个关着的通道上，开了就必须变）。反证是**事后**那一半；
  事前那一半（写判据之前先说出主语：谁的、哪个进程、哪个时刻、哪个维度）
  的唯一出处是根 `AGENTS.md` 的「判据的主语」一节。
- **空转的门禁比没有门禁更坏**：nightly 曾对早已退役删除的 `packaging/tavotto.iss`
  报平安；macOS 冒烟曾借 worker-env 让「runtime 没打进去」全绿。判「最近
  跑过没有」要数**有结论的 run**。
- **Ruff（`python-lint`）是快线里最便宜的一格，不是别的门禁的替代品**：
  它只回答「这份 Python 在语法与名字层面立得住吗」（F821/F401/F841 那一类），
  语义问题仍归不变式 / 等价性矩阵 / CompatBench。它经 `CI fast gate` 参与合并
  资格（`needs` 与 `--required` 闭集里都有它，红了或 skipped 都会让 Gate 红），
  **没有新增 required context**。规则集与豁免的唯一出处是 `pyproject.toml` 的
  `[tool.ruff]`——workflow 命令行上不许再写一份，CI 也不许 `--fix`。
  当前状态：**lint、import 排序（`I`）、formatter 三项均已启用**。
  `python-lint` 这一格里 `ruff check` 与 `ruff format --check` 是**两个独立结论**
  （format 那步带 `if: always()`），一次 push 就把要修的都告诉你；CI 只检查、
  永不写回。**没有因此新增第四个 required context**——两条命令在同一个 job 里。
  first-party 靠 `[tool.ruff]` 的 `src` **按目录**判（那几个目录就是运行时真的
  被注入 sys.path 的），不靠一张会漂的名字清单——**但新增一处 sys.path 源码根
  时必须回来审查 `src`**，在已有源码根下加模块则不用动。
  理由与后续见 `docs/ci/ruff.md`。
- 每个「只在别人电脑上发生」的 bug 先变成 `tests/test_windows_regressions.py`
  的用例再谈修（cp936 编码、文件占用、盘符/反斜杠/中文路径、端口占用、
  CLI 只有 .cmd、解释器探测）。

## 验证链（按层）

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
  sidecar 冒烟 → 覆盖安装（升级）再冒一次 → 静默卸载。这条链路只有真装一遍
  才知道，而且必须挂在**在发的那个发行形态**上。
- **黄金路径 E2E**：`cd web && pnpm e2e`（Playwright，`TAVOTTO_EXE` 指打包产物、
  缺省用 `python -m tavotto`）。跑之前先 `python scripts/build_frontend.py`——
  包内 `src/tavotto/web/` 优先于 `web/dist`，只跑 `pnpm build` 测的还是旧界面。
- **性能基线**：`python scripts/bench_render.py --python .venv/bin/python`。
  结论与前后对照都写进 `docs/perf-baseline.md`——**改性能前先在那儿指出一个
  数字**。它**默认不隔离 HOME**（重置 HOME 会让每次冷启动多出 9 秒字体缓存
  重建；要量首次体验用 `--fresh-home`）。
- 后端冒烟（示例项目）：`tavotto --figures examples/figures --no-browser
  --insecure-no-auth` 后 `curl -X POST /api/engine/render
  -d '{"id":"Fig1_kinetics.pdf","patches":[]}'`（不带 `--insecure-no-auth` 时
  curl 要加 `X-Tavotto-Auth` 头，见 ADR 0008）。
- 导出保真：导出 PDF 用 pymupdf `get_text()` 验证矢量文字。

## 发布链

- **`desktop-shell`（2026-09-04，issue #275）**：`src-tauri` 的
  `cargo fmt --check` / `clippy -D warnings` / `cargo test`，与 `workerd` 同一条
  纪律（都不做 paths 过滤）。它原先只在 `desktop-tauri.yml` 里跑，而那个工作流
  只在打 tag / dispatch 时跑、`cargo test` 还收在 build 矩阵的 macOS 那条腿上
  ——改了壳的 PR 因此一路全绿，Rust 侧判据合并前一次都不执行。
  **`tauri.conf.json` 的 `bundle.resources` 指向 `../dist/Tavotto`，空目录就够**
  （`mkdir -p dist/Tavotto`），所以这一格不必挂在完整打包之后，几十秒回来。
  已知边界：`main.rs` 里 `#[cfg(target_os = "macos")]` 的应用菜单分支在这条
  Linux 腿上不参与编译，那部分仍由 desktop-tauri.yml 的 macOS 腿覆盖。
- **「在 Gate 的闭集里」≠「在 PR 上会跑」**：重型那几档接在 integration gate 里，
  普通 PR 上整体 skipped 而 Gate 判 deferred（绿）。把一个 fast 档的 job 改成
  重型条件，Gate 依旧全绿而它守的东西合并前一次都不验——
  `tests/test_merge_queue_workflows.py::test_every_fast_lane_job_actually_runs_on_a_plain_pull_request`
  逐个比死条件看住这一位。
- release.yml 生成插件更新清单（`make_plugin_manifest.py` → `out/codex-plugin.json`），
  **不能挪进 desktop-tauri.yml 的 updater-manifest**（那个 job 没配 minisign
  私钥就整个跳过，插件更新通道会悄悄停而且全绿）。
- 桌面更新清单 `latest.json` 由 `scripts/make_updater_manifest.py` 在两条
  matrix 腿都跑完后合成；macOS 更新包必须在签名/公证之后重做
  （见 `src-tauri/AGENTS.md`）。
- 遥测部署顺序：先发代理 → 验 PostHog 收得到 → 配采集器 → 再发客户端
  （反过来新事件被静默 400 而且全绿）。发行量采集器失败必须让 workflow 红。
