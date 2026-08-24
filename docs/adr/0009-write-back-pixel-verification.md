# ADR 0009：写回验证的像素门——纯属性分歧的封口

日期：2026-08-24　状态：已采纳（issue #81，`severity:P1` / `release:blocker`）

## 背景

「写回原始文件」的 verify 阶段用 `app._compare_manifests(hot, fresh)` 比对
热态与干净重放，但它只量三样：gid 集合、每个元素的 bbox/anchor、figure 的
size_mm。**几何不变的纯属性差异**——颜色、线型、字体样式、透明度、hatch、
marker——一项都量不到。PR #49 已经出现过真实缺陷：`bar_series.facecolor`
的恢复顺序错误不改变任何几何，旧比较器报 0 处分歧，却可能把错误颜色静默
烙进用户原件。用户从成功提示上无从知道写回结果是错的——silent-wrong /
data-loss，1.0 的阻断项。

## 决策

在 verify 阶段加**第二道门：像素比对**（issue 验收标准里的方案 2），
几何比对不动、照旧先跑：

1. 前提与 manifest 比对完全一致——只有 `_hot_manifest` 判定「热会话最后
   应用的正是这组 patches」时才比（历史恢复、跨面板同步等热态不可比的入口
   如实回 `fresh_only`，**不装比过**）。
2. 两侧各出一张探针图：热会话 `worker.render_png(stem, 1000)`（画**当前**
   live figure，不重新 apply——渲染的正是「用户此刻所见」），重放会话在
   `fresh.override(stem, patches)` 之后同样 `render_png`（「重开项目后重放
   出来的」）。协议命令既有，两条控制面（Python 池 / workerd）同语义，
   协议不升版。
3. 逐字节相同（期望的通过态）直接放行；不同则经
   `pdfbackend.compare_png` 解码成灰度、扣底噪 3 后算三指标
   （`changed_pixel_ratio` / `mean_abs_diff` / `max_abs_diff`，语义与
   `scripts/ci/pixelcompare.py` 逐条相同），任一越过 `app.REPLAY_PIXEL_TOL`
   即分歧：追加一条 `{gid: "", field: "pixels", metrics, exceeded,
   tolerance}` 进分歧清单，走既有的 409 `replay_divergence`（code 前端已
   双语翻译；metrics/exceeded/tolerance 是可成文的 params）。
4. 探针渲染失败**让异常冒出去**（写回失败、原件零改动）：查不了 ≠ 查过，
   静默降级会把这道门慢慢变成空转的门禁。唯一的降级是 workerd 在探针中途
   **透明重开**了热会话（`unknown_session` → `_open()` → 重试）：重开后的
   会话画的是脚本原样、不再是「用户所见」的基准，拿它比只会误报——这一支
   回 `verification.pixels = "hot_rebuilt"`，不阻断也不装比过。
5. 通过时成功响应 `verification.pixels = "ok"` 如实记账；`fresh_only` 时
   该键不出现。

## 为什么是像素，不是逐属性比 manifest

* manifest 的 `editable` 值是 getter 的**表示**，热态与重放可能以不同但等价
  的形式出现（`"#ff0000"` vs RGBA 元组、numpy 标量 vs float），逐属性稳定
  比较要为每类 prop 再写一份规范化——那是第二个 patchspec，而且 manifest
  没暴露的属性（hatch 细节、marker 路径）永远比不到。
* 像素是所有可见属性最终兑现的地方：**不用枚举属性**，连「没登记进能力表」
  的差异都逃不掉。不变式套件已经确立了先例——「能力真实」那条就用
  `preview_png` 的字节相等断言（状态中立、逐字节确定）。
* 代价可忽略：两张 1000px 探针合计几十毫秒，而写回本来就要重跑一遍脚本
  （heavy 的分钟级）。

## 为什么不误伤（抗锯齿 / 平台噪声）

热态与重放出自**同一台机器、同一个解释器、同一版 matplotlib、同一 dpi**，
通过态本就该逐字节相同；跨平台字体 / 抗锯齿差异根本不进入比较。底噪 3 +
三阈值（0.001 / 0.5 / 64）只为万一的解码抖动兜底，且刻意远低于最小的真实
信号（虚线化一条曲线 ≈ 0.2% 变化像素）——比 CompatBench 的跨版本保真阈值
（0.004 / 1.2 / 140）严一个量级，因为这边比的是同一个世界里的同一张图。

## 第二份像素实现的对拍纪律

`scripts/ci/pixelcompare.py` 是 CI 侧唯一算法（numpy + Pillow），但 Flask
父进程的依赖边界是 flask + pymupdf（wheel 不带科学栈、import 不到
scripts/），所以 `pdfbackend.compare_png` 是一份**受对拍看护的镜像**：
`tests/test_pixel_compare.py` 在同一组图上逐指标断言两份实现输出相等
（与 patchspec ↔ Rust、telemetry 客户端 ↔ 代理同一套纪律）。改任一侧必须
同步另一侧。

## 看护

* `tests/test_worker_roundtrip.py` 写回一节（真 matplotlib + 真一次性重放，
  CI 的 backend 矩阵在 ubuntu / macos / windows 三平台跑）：
  - 负例：几何完全一致、只有颜色 / 线型 / 透明度不同的热态（绕过 pool 记账
    的 override 模拟增量应用残留）→ 必须 409 `replay_divergence` 且带
    `pixels` 分歧项。修复前这三条真的红过（写回回 200），证据在 PR。
  - 正例：无 override、走正门的属性修改、纯几何修改（FigS3 组合）照常通过，
    且 `verification.pixels == "ok"`。
* `tests/test_write_back.py`（假 worker）：像素分歧阻断与清理、底噪内抖动
  放行、热态不可比时探针一次都不跑。
* `tests/test_pixel_compare.py`：比较器语义 + 与 CI 实现的对拍。

## 记录在案的限制

* 热态不可比（`fresh_only`）时像素门与几何门一样没有基准，写回内容本就出自
  干净重放，响应如实标注。
* 阈值之下的差异按定义与噪声不可区分；真实通过态是逐字节相同，容差只朝
  「不误报」方向留，不朝「放过信号」方向留。
