# ADR 0075：拖动性能探针——在用户自己的 Mac 上量出卡在哪

日期：2026-09-23 · 状态：**Accepted**
相关：[0016 诊断 V2](0016-diagnostics-v2-frontend-state-tracing.md)（同一条隐私线：只在用户点按钮时生成、不上传）、
`docs/perf-baseline.md`（后端渲染基线，本 ADR 补前端交互那一半）、
`docs/rules/frontend/fake-realtime-preview.md`（预览平面与取消语义）、
细则 `docs/rules/frontend/perf-probe.md`、使用说明 `docs/perf-probe-guide.md`。

## 背景

多位用户反馈「拖动子元素一卡一卡的」，而开发机（M4 Pro）上完全复现不了。
`docs/perf-baseline.md` 量的是后端渲染（串行、热态 17–28ms），拖动途中后端一次
都不被惊动（假实时预览），所以那份基线回答不了这个问题。卡顿发生在**用户机器上的
WKWebView 里**，而 WKWebView 的样式 / 布局 / 绘制代价与 Chromium 不同、与机器
（Intel / M1 Air / 低电量模式 / 外接 4K）强相关——只能在那台机器、那份图上量。

## 裁决摘要

| 问题 | 裁决 | 落点 / 证据 |
|---|---|---|
| 探针放在哪 | **产品内**，关着时零成本。外置工具进不了桌面壳的 WKWebView（没有可附着的调试端口），Playwright/Chromium 量的不是用户那台 WebKit | `web/src/perf/core.ts`（叶子，热路径只 import 它）；`perf/core.test.ts`「没在录制时」两条 |
| 入口 | 设置 → 诊断 → 性能分析 →「开始」：设置关掉，画布右上角挂出探针面板（录制中 / 自动测试 / 完成并保存 / 放弃） | `DiagnosticsSettings.tsx` 的 `PerfProbeSection`、`components/PerfProbeHud.tsx`、`e2e/perf-probe.spec.ts` |
| 量什么 | 每帧一行：rAF 间隔、主线程渲染（rAF 回调开始 → 渲染后的第一个 MessageChannel 任务）、pointermove 业务处理、React 同步重渲染（onMove 之后的微任务）、rAF 脚本、pointermove 数、输入延迟（`event.timeStamp` → 画出它的那一帧渲染结束）。另有插桩点耗时分布（`doc.txn_update` / `snap.compute` / `raf.preview_write`）、组件渲染次数、store 通知次数、片段开始时的 DOM / SVG / 文档规模、松手后 600ms 的尾巴、图内元素的 commit→权威 | `perf/core.ts` 头注释（量的主语）；`perf/session.ts` |
| 片段怎么切 | 一次拖动 = 一个片段，边界挂在 `interactionStore.begin / end`（所有拖动种类共用的一处），不在各个 `startXDrag` 里各写一遍 | `store/interactionStore.ts` |
| 自动测试 | 在用户点过的位置合成 pointerdown → 两轮 3 秒 8 字形 pointermove（`m1` 每帧 1 个 / `m3` 每帧 3 个）→ **`pointercancel` 收尾**。事件从命中元素派发、冒泡到 window，走的是真实的命中与起拖链；取消语义保证不写 override、不进历史、不渲染 | `perf/synthetic.ts`；`perf/synthetic.test.ts`（文档与撤销栈原样，反证：换成 pointerup 必红）；e2e 断言零后端请求、transform 复原 |
| 机器事实 | `GET /api/perf/system`：机型、CPU、核数、内存、系统版本、电源、低电量模式（新老两种 pmset 写法）、CPU 限速 / 热警告、Rosetta。不回主机名 / 用户名 / 路径 | `engine/perfprobe.py`；`tests/test_perf_probe.py` 的身份反查（反证：塞进 hostname 必红） |
| 隐私 | 报告只有数字与枚举；面板 id、文件名、图内文字一律不进。留在本机，用户自己决定发给谁，Tavotto 不上传、不进遥测 | `perf/session.ts` 头注释；e2e 断言报告不含文件名 |
| 报告落在哪 | `POST /api/perf/report` 写进 `data_dir()/perf-reports/`（文件名由后端生成、请求体不参与拼路径、读取处封顶 16MB），桌面版随即用既有的 `reveal_export` 在访达里选中它；浏览器模式另给一份下载。**不走 `<a download>`**：桌面壳没有注册下载处理器，wry 的 WKWebView 导航代理会把下载动作直接取消 | `engine/perfprobe.save_report`；`tests/test_perf_probe.py` 保存三条；e2e 断言数据目录里那份与下载的逐字节相同 |
| 结论在哪判 | **不在产品里**。报告是原始数字（`tavotto-perf-probe/1`）；「卡在哪、改哪行、怎么改」由 `scripts/perf_report.py` 判（标准库，单份 / 多台机器对照 / HTML / JSON）。判据随代码演进，改一条规则不必发一个版本；产品界面只给一句帧率与超时比例 | `scripts/perf_report.py`；`tests/test_perf_probe.py` 分析器用例 |

## 1. 为什么拆成三份（输入 / 渲染 / 未归因）而不是只报帧率

帧率只能说「卡」，不能说「为什么」。三种卡的改法完全不同：输入处理重要做的是合并
pointermove、收窄订阅；渲染重要做的是合成层 / 栅格化 / 减节点；未归因重要先去
Safari Web Inspector 录时间线。WebKit 不支持 `longtask` / LoAF 观察器，所以靠
两个可移植的时刻把一帧切开：rAF 回调开始、渲染结束后的第一个任务。**主线程之外**
（合成器 / GPU）的等待主线程看不见，只表现成「间隔长、各段都不大」——分析端把它
单独说成「未归因」，不假装归因。

**WebKit 的计时精度**：WebKit（含 WKWebView）把 `performance.now()` 粗化到 1ms，
单次不到 1ms 的 span 读出来是 0 或 1。单个值没有意义，但几百次的**平均**仍然无偏
（量化相位随机），分析器只用总和 / 次数与逐帧量级（慢的那几类本来就是几毫秒到几十毫秒）。
e2e 在 WebKit 腿上也跑一遍（`playwright.config.ts` 的 webkit 白名单）。

## 2. 为什么自动测试要两轮

120Hz 触控板 / 高回报率鼠标在 60Hz 屏上每帧会来 2–3 个 pointermove。预览平面
已经按 rAF 合并写 DOM，但 `trackPointer → onMove`（画布对象的 `txnUpdate`、
交互 store 的写入）是每个事件跑一次。`m1` 与 `m3` 同一条轨迹、只差输入频率：
两轮差得多，就是「每个 pointermove 的成本」在主导，合并到每帧一次就能省下来。

## 3. 首轮实测（开发机模拟慢机器，2026-09-23）

Chromium CPU ×6 限速 + 一张 2×2 × 2500 散点 + 160 条线的纯矢量图，拖子图标题：
超时帧平均 90ms = 输入 2.9 + **渲染 79.4** + 未归因 10.4；最大一张 SVG 10 847 个
节点。同一份图不限速时 59 帧/秒，但松手后有 150ms 长帧、commit→权威 420ms。
即：密集矢量图上，拖一个子元素让浏览器重排 / 重绘整张 SVG，是第一嫌疑；JS 不是。
这只是开发机上的模拟，**用户机器上的报告才是结论**。

## 不做什么

- 不自动上传、不进遥测、不开后台常驻录制。
- 不在产品里给「优化建议」：建议写在分析器里，跟着代码走。
- 不量 Windows 桌面壳的专有维度（WebView2 同样可用本探针，但机器事实只做了 macOS）。
