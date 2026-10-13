# macOS 整个应用窗口拖影：原生复现与取证

日期：2026-10-14。独立于 ADR 0075「拖动画布对象」：移动整个 Tauri 窗口由 macOS WindowServer / WKWebView 合成，浏览器 rAF 不是系统实际的呈现时间。

## 先复现再归因

在同一台 macOS 机器、同一显示器、相同缩放/刷新率和电源模式下比较：

| 组 | 窗口里是什么 | 用户动作 |
|---|---|---|
| A | 空白首页 | 拖动整个 App 窗口约 12 秒 |
| B | 普通图编辑页 | 同样拖动 |
| C | 密集 SVG / 大工程 | 同样拖动 |
| D | 一样尺寸的 Finder 或 Safari 窗口 | 作为系统与显示器对照 |

每组再分别在内屏/外屏、插电/低电量两种状态下做必要对照。记录：macOS 版本、机型、Tavotto 版本、外接显示器、缩放、刷新率、低电量模式、是否录屏时才出现。

记录前保持窗口静止，另录 idle。录制拖动时可让另一位测试者运行终端脚本，或先启动脚本立刻拖动：

```bash
python scripts/perf_macos_window.py record --stage idle --seconds 12 --output idle.json
python scripts/perf_macos_window.py record --stage drag --seconds 12 --output drag.json
python scripts/perf_macos_window.py compare idle.json drag.json
```

输出只含枚举、版本、数字样本；不包含 PID、用户名称、应用路径、图内数据。但 **RSS 求和可能重复算共享页；WindowServer CPU 是全局值，不能归因于 Tavotto；WKWebView/WebContent 进程可能不是 Tavotto 子进程，可能不在家族统计里**。无 Tavotto 进程时拒绝写报告。

## 真正能回答「拖影在哪里」的取证

1. 在 **Xcode Instruments** 中附着 Tavotto；Time Profiler + Hangs 用于查主线程/运行时阻塞。使用适用 macOS/Xcode 版本的 Animation Hitches / Core Animation 模板，检查合成和呈现（可用模板随系统版本不同）。
2. 必要时单独检查 WebKit WebContent 与 WindowServer；WebKit 子进程可能不属于 Tavotto 进程树。不能把 WindowServer 全局负载当成 Tavotto 的责任。
3. 通过开发版安全开启 WKWebView inspectable（正式版本不开），在 Safari Web Inspector 中查 JS、Layout、Paint；如果这些正常但窗口仍拖影，需要到原生层继续查。
4. 分别采集录屏与手机高速摄像的结果：窗口移动中的显示器像素残影可能无法从操作系统屏幕录制中看到；两者结论不同应注明。
5. 关闭高成本 CSS blur、复杂 SVG、页面动画做一次受控 A/B（仅测试构建，且验证回滚）。不要直接调整窗口透明模式、系统 GPU 偏好或更换桌面框架。

## 验收与下一轮

- 本 PR 只新增 macOS 现场采集工具、测试与清晰的归因边界，**不声称已定位拖影原因、不修改 Tauri 窗口构建**，避免与正在改原生标题栏的 PR 冲突。
- 下一轮必须有真实 `.app` + 至少两种复杂度 + Instruments trace 才能决定是哪层出错；若 WindowServer 与其他应用同时异常，先确认显示器/系统因素。
