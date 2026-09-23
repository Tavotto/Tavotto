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
- 导出保真：导出 PDF 用 pymupdf `get_text()` 验证矢量文字。
