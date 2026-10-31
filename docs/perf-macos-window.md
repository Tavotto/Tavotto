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

输出只含枚举、版本、数字样本；不包含 PID、用户名称、应用路径、图内数据。但 **RSS 求和可能重复算共享页；WindowServer CPU 是全局值，不能归因于 Tavotto；WKWebView/WebContent 进程可能不是 Tavotto 子进程，可能不在家族统计里**。无 Tavotto 进程时拒绝写报告。录制中 Tavotto 进程消失或换了 pid（崩溃/重启）同样拒绝写报告；CPU 取相邻两次 `ps` 累计 CPU 时间之差除以墙钟间隔（`ps` 的 %cpu 是带衰减的长窗口均值，不用），分辨率约 10 ms，第一个样本没有 CPU 值。`ps` 看不到已退出的进程：某个区间内家族里有 pid 消失（子进程退出，样本字段 `family_exited` 只记数量）或 WindowServer 换了 pid，该区间 CPU 记为未知（null）而不是偏小的值，短命子进程因此也采不到；`compare` 要求两边各至少 5 个有效 CPU 样本，否则判 incomparable。

## 真正能回答「拖影在哪里」的取证

1. 在 **Xcode Instruments** 中附着 Tavotto；Time Profiler + Hangs 用于查主线程/运行时阻塞。使用适用 macOS/Xcode 版本的 Animation Hitches / Core Animation 模板，检查合成和呈现（可用模板随系统版本不同）。
2. 必要时单独检查 WebKit WebContent 与 WindowServer；WebKit 子进程可能不属于 Tavotto 进程树。不能把 WindowServer 全局负载当成 Tavotto 的责任。
3. 通过开发版安全开启 WKWebView inspectable（正式版本不开），在 Safari Web Inspector 中查 JS、Layout、Paint；如果这些正常但窗口仍拖影，需要到原生层继续查。
4. 分别采集录屏与手机高速摄像的结果：窗口移动中的显示器像素残影可能无法从操作系统屏幕录制中看到；两者结论不同应注明。
5. 关闭高成本 CSS blur、复杂 SVG、页面动画做一次受控 A/B（仅测试构建，且验证回滚）。不要直接调整窗口透明模式、系统 GPU 偏好或更换桌面框架。

## 验收与下一轮

- 本 PR 只新增 macOS 现场采集工具、测试与清晰的归因边界，**不声称已定位拖影原因、不修改 Tauri 窗口构建**，避免与正在改原生标题栏的 PR 冲突。
- 下一轮必须有真实 `.app` + 至少两种复杂度 + Instruments trace 才能决定是哪层出错；若 WindowServer 与其他应用同时异常，先确认显示器/系统因素。


## Agent 可解析的 macOS 资源差异

新增可选机器可读输出：

```bash
python scripts/perf_macos_window.py compare idle.json drag.json --json window-diagnostic.json
```

`window-diagnostic.json` 的 `status` 只可能是 `diagnostic_only` 或 `incomparable`，**绝无 `pass`**。相同 macOS 版本、架构、每组至少 5 个资源样本时，附上家族 CPU、RSS 求和与系统全局 WindowServer CPU 的 P95 差额；否则拒绝对照。该数据仅帮助 Agent 分流是否应获取 Instruments / Web Inspector 证据，不代表屏幕呈现延迟或 GPU 拖影测量。

原生完整自动点击/移动窗口需要具备 Accessibility 权限、实际图形会话和可验证的显示输出，当前采样器没有伪造这项能力。

## compare 的可比条件清单

全部集中在 `scripts/perf_macos_window.py` 的 `failed_conditions()` 一张表，所有条件缺信息一律不满足（fail-closed）。`compare` 总是执行这些检查并把状态与未满足条件打印到 stdout，`--json` 只决定是否另写文件；必须正好给两份报告。任一条不满足即 `incomparable`，`--json` 输出的 `failed_conditions` 列出未满足的条件名。加条件只改这张表并同步本清单。

| 条件名 | 含义 |
|---|---|
| `stages_idle_vs_active` | 第一份必须是 idle，第二份是 drag / resize / complex |
| `same_arch` | 两份 CPU 架构相同且非空 |
| `same_macos_version` | 两份 macOS 版本相同且非空 |
| `same_sample_interval` | 两份 `sample_interval_seconds` 都有效且相等（区间 CPU 的平滑程度取决于采样间隔） |
| `same_capture_environment` | 两份 `environment` 都完整（任一字段未知即不满足，两边都未知也不行）且逐项相等：机型标识符 `hw_model`、供电 `power_source`（ac/battery）、`low_power_mode`、各显示器分辨率/刷新率 `displays`、`tavotto_version`。不记主机名、序列号、显示器名称、路径 |
| `at_least_5_samples_each` | 每份至少 5 个样本 |
| `at_least_5_valid_cpu_samples_each` | 每份至少 5 个有效（非 null）家族 CPU 样本 |
| `single_tavotto_family_throughout` | 每个样本都是对象且带整数 `root_count`，并且都等于 1（缺字段、类型不对、非对象一律不满足，不按默认值放行）：正好一棵顶层 Tavotto 进程树（打包的 sidecar 也叫 Tavotto，但它是主进程后代，不算第二棵；同时开两个实例或有孤儿 Tavotto 进程则 `record` 直接拒绝） |

`record` 的 `environment` 取不到的字段记为 null（pmset/system_profiler 失败等），这样的报告无法通过 `same_capture_environment`；这些条件只能证明两次录制的环境事实一致，不能替代「同一台机器、同一显示器、同一电源模式」的人工操作约定。
