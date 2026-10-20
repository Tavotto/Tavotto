# 平台后端线四片：2026-10-20 诊断与模型

## 问题与范围

PR #1161 head `209c59c80d64f3f3bb0af83e2eb88b0c0c1f2429` 的
[CI run 39416447642](https://github.com/Tavotto/Tavotto/actions/runs/39416447642)
在 Windows 平台第 2 片累计到 90 分钟取消。该片完成 5597/5652 条，尚有 55 条未完成；
最后位于 `test_worker_svg_canvas.py`，不是一条 90 分钟的断言或 CLA 服务卡死。
其他 Windows 两片分别成功于约 74、67 分钟。macOS 三片全部成功，原第 2 片为 69 分 39 秒。

本修复仅把 `backend-platforms` 的 macOS/Windows 文件分片由三片增为四片，并同步严格矩阵契约。
继续使用同一 `tests/support/shard.py`、原 JUnit 权重、原测试和 marker，不拆文件、不加 xdist。
job id、事件、needs、三个 required Gate、CodeQL、Merge Queue、权限及 90 分钟上限不变。
Gate 仍要求每个矩阵成员成功；新四片的真实平台资格须由新 head CI 验证。

## 文本证据和计时边界

Azure artifact 下载被拒后没有重试或换路下载；GitHub connector 的已完成 job 文本日志可读取。
取同一 run 的三个 Windows、三个 macOS 日志，按测试完成行时间戳做相邻差，差值记到当前完成节点所属文件。
这包含前一条 teardown、当前 setup/call、调度及日志开销；**不是精确 call duration，也不是 JUnit**。
初始 collection 和最终会话清理不在累计内，跨文件边界有归属误差。仅用于负载模型，不作资格门禁输入。

完成行计数与 collection 统计交叉检查：

| 平台 | 第 1 片 | 第 2 片 | 第 3 片 |
| --- | ---: | ---: | ---: |
| Windows 完成/选中 | 6463/6463 | 5597/5652 | 5838/5838 |
| Windows 相邻累计（分钟） | 72.17 | 88.09（下界） | 64.81 |
| macOS 完成/选中 | 6463/6463 | 5652/5652 | 5838/5838 |
| macOS 相邻累计（分钟） | 50.34 | 67.89 | 60.92 |

同 head 的 526 个文件与原算法分组一致；Windows 有 524 个完整文件，另有 canvas 的部分记录及未开始的 packaging。
GitHub 将某些参数标签掩为 `***`，导致 3 个不同用例显示同名；按完成行的独立出现计数，不能拿掩码标签假装 nodeid 集合。
真实 nodeid 的并集/不交自验仍由 pytest 进程执行。

[逐文件累计](evidence/platform-shards-20261020.csv)保留部分文件标记；
[来源、LF 规范化日志 SHA256 和回放结果](evidence/platform-shards-20261020.json)绑定具体 job。
原始文本在接续任务 `/tmp/tavotto-cla-readonly-audit/1161-{windows,macos}-shard{1,2,3}-text.log`；
分析入口 `analyze_windows_text.py`，回放记录 `1161-platform-load-scenarios.json`。

## 累计负载与旧权重

现役权重来自 2026-10-03 完整 Linux/Python 3.13 JUnit，共 348 文件；本 head 有 178 个新文件使用默认 8.61 秒。
旧模型把三片都估为 40.15 分钟，未反映当前跨平台累计成本。例如：

| 完整文件 | 原权重（秒） | Windows 相邻累计（秒） |
| --- | ---: | ---: |
| `test_dependency_transaction.py` | 236.58 | 588.64 |
| `test_hatchcolor_editing.py` | 8.61（默认） | 300.49 |
| `test_environment_autodetect.py` | 8.61（默认） | 232.19 |
| `test_scale_group_cmap.py` | 8.61（默认） | 198.71 |

不把文本近似伪装成新的 JUnit 测量值；`shard_weights.json` 不改。
对原权重及同一文件算法回放四片，得到文件数 `[128, 130, 130, 138]`，并集 526、互不重叠、无空片。

| 分组 | Windows 负载回放（分钟） | macOS 完整日志回放（分钟） |
| --- | --- | --- |
| 原三片 | 72.17 / 91.15* / 64.81 | 50.34 / 67.89 / 60.92 |
| 四片 | 47.06 / 63.71* / 55.89* / 61.47 | 34.23 / 50.67 / 41.80 / 52.45 |

星号涉及两份 Windows 未完整文件的模型，不是观测：canvas 用 macOS 完整文件累计乘共同已完成节点的最大 Windows/macOS 比率 1.46013，
得到 207.02 秒；packaging 同法得到 13.99 秒。Windows 其余 524 文件为完整文本记录。
本模型不保证未来 runner 速度，也不替代新 CI。它给出增加一片的证据与余量，而不是再次原样重跑。

## 验证要求

本地 222 条 CI/基线/分片/指导文档契约通过，Ruff check/format check 通过。
对真实 collection 的 17953 个节点、526 个文件分别构建三片和四片计划并调用既有自验：
两者并集均精确等于全集、两两不交。四片节点数为 `[5932, 4306, 3294, 4421]`。
只在工作副本删去矩阵第 4 片时，严格契约以 AssertionError/1 拒绝；恢复源码后正常验证通过。
临时采集脚本最初缺少仓库根导入路径而产生 collection 错误，修正该脚本后才得到上述完整成功记录。
静态契约严格要求新 `1..4`；不能以模型或本地 collection 成功替代真实 pytest 和最终 merge_group。
