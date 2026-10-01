# U05 目标档证据：macos-arm64（2026-09-30 晚，tart 全新 macOS VM + 冻结 .app）

ADR 0064 §一 第三档 / §四：FO23 `required_evidence` = 目标系统清单、进程树、下载与安装目标、artifact SHA、编辑结果。
本文件是那份证据的仓库内副本（日志摘录 + 指向本机原始件的路径；**不提交截图**）。

## 验收方式与一处必须说清的限度

* VM：tart 从 `ghcr.io/cirruslabs/macos-tahoe-vanilla` 克隆出的全新虚拟机，验收前没有装过任何东西。
* 产物：本分支 `b31e13822` 在本机打的冻结 `.app`（未重新打包；验收用的就是 `src-tauri/target` 下那份）。
* 驱动：在 VM 里跑 `.app` 自带的**冻结 sidecar**（浏览器模式），经 `ssh -L` 由主机上的 Playwright 按用户的点法驱动真实界面。
* **限度（如实）**：目标档要求的是「正常入口」，这次驱动的是 `.app` 里的冻结 sidecar + 浏览器界面，**不是 Tauri 桌面壳窗口本身**。
  界面代码与引擎是同一份冻结产物，但桌面壳窗口没有被点过。这一格在未验证范围里保留，不算取得。

## 目标系统清单

来源：`/Volumes/Projects/tart/evidence/r3/target-system.txt`

| 项 | 值 |
|---|---|
| 系统 | macOS 26.6.2（25G83），arm64 |
| Python.framework（python.org） | 无（`/Library/Frameworks/Python.framework` 不存在） |
| Homebrew | 无（`/opt/homebrew` 不存在） |
| pyenv / Conda | 无（`~/.pyenv`、`~/miniconda3` 不存在） |
| Xcode Command Line Tools | 无（`xcode-select` 报无 active developer directory） |
| `/usr/bin/python3` | 仅占位 shim（没有 CLT，调用只会弹安装提示），不是解释器 |

## 产物 SHA

| 物件 | sha256 |
|---|---|
| 冻结 `.app` 的 zip | `38019988d2f3420ef67dbddf879f9c0ca9aa1514338ad6cee8f81babfda116a8` |
| 包内私有 Python 归档 `cpython-3.13.15-7d50bb42813a` | `7d50bb42813a5644db7c40d3ad79361d0b724bb29d25a91fab1048c2c5c6a8c5`（与 `private_python_lock.json` 的 `macos-arm64` 一致） |
| 源码 | `b31e13822ef3e9c0de264c27a39d5718ef8d3557`（`feat/private-python-enable`） |

codesign minos 扫描 rc 0。

## 用户操作时间线

来源：`drive.log`（UTC，2026-09-30）

| 时间 | 事件 |
|---|---|
| 20:20:31.551 | 项目已打开（`/Users/admin/Desktop/figproj`，1 个脚本 `plot_incidence.py`，读 .xlsx） |
| 20:20:31.644 | 点运行按钮：重新运行 `plot_incidence.py` |
| 20:20:45.033 | 缺包卡片出现：「这个脚本还缺 pandas 和 openpyxl，点一下自动装好。」 |
| 20:20:45.211 | 点「一键修复」（唯一一次点击） |
| 20:20:45.235 | 进度：正在准备 Python… |
| 20:20:48.263 | 进度：正在准备 Python 环境…（2/4） |
| 20:20:51.315 | 进度：正在安装 pandas 和 openpyxl…（3/4） |
| 20:21:09.394 | 进度：正在验证…（3/4） |
| 20:21:24.445 | ✓ 已发现 1 张图（自动重跑出图） |

点击到出图 39 秒（20:20:45 → 20:21:24）。

## sidecar 日志关键行

来源：`sidecar.log`

```
20:20:31,173 INFO tavotto.engine: 渲染解释器: …/Tavotto.app/Contents/Resources/sidecar/Tavotto/_internal/runtime/bin/python3.13（来源 bundled）
20:20:41,632 INFO tavotto.probe: 探测失败 plot_incidence.py [entry=__main__]: 脚本用到的 openpyxl 在当前渲染环境里没有。…
20:20:41,664 INFO tavotto.deprepair: 依赖修复计划: pandas, openpyxl → tavotto_managed（plot_incidence.py）
20:20:46,242 INFO tavotto.privatepython: 私有 Python 就位: cpython-3.13.15-7d50bb42813a（3.13.15，归档来自 bundled）
20:20:48,518 INFO tavotto.deprepair: pip install：包源 pypi
20:21:08,027 INFO tavotto.deprepair: pip install 完成：包源 pypi
20:21:22,050 INFO tavotto.deprepair: 受管环境换代: /Users/admin/Desktop/figproj → gb39a56894a69（装 4 条）
20:21:22,051 INFO tavotto.deprepair: 依赖修复成功: openpyxl 3.1.5 → tavotto_managed
20:21:22,214 INFO tavotto.engine: workerd 会话打开: plot_incidence.py（entry=__main__，解释器来源=managed_project_env）
20:21:23,671 INFO tavotto.probe: 探测成功 plot_incidence.py [entry=__main__] → ['incidence']
```

读法：

* 私有 Python 取自包内归档（`归档来自 bundled`），`downloads/` 为空——**零下载**（ADR 0111 要求的那一格）。
* 受管环境一代装 4 条，pip 包源 PyPI（没有触发国内镜像慢速回退）。
* 换代后 worker 的解释器来源是 `managed_project_env`，探测成功、出图。
* 进程树：sidecar 冻结进程 → 私有 Python（包内 runtime）→ 受管 venv 的 pip / worker；系统里没有其他解释器参与（目标清单见上）。

## 截图清单（本机路径，不提交图片）

目录 `/Volumes/Projects/tart/evidence/r3/`：`01-opened.png`、`02-card.png`、`02-window.png`、`03-progress-01.png`、
`03-progress-02.png`、`03-progress-03.png`、`03-progress-04.png`、`04-done.png`。另有 `drive.log`（时间线）、
`drive.out`、`sidecar.log`、`target-system.txt`。

## 此前两轮（附录）

* r1（`/Volumes/Projects/tart/evidence/vm-r1/`、`…/evidence/` 顶层的 `05-stuck-details.png` 等）：暴露「缺 pandas 时一键修复卡死」——修在 #760。
* r2（`/Volumes/Projects/tart/evidence/r2/`）：#760 修复后的复测。
* r3 = 本文件，取自本分支 `b31e13822` 的冻结包。

## 结论与适用范围

* FO23 实例：`macos-arm64` = **pass**（限度见开头：冻结 sidecar + 浏览器界面，非桌面壳窗口）。
* 不覆盖：`macos-x86_64`（没有 Intel 机器；与 arm64 同一归档族不算取得，ADR 0064 §四 不许拿另一目标的证据替代，锁里保持 `enabled: false`）；
  `windows-x86_64`（待阿里云目标档证据）；桌面壳窗口本身；国内网络下 pip 慢速回退（本次 PyPI 直连，未触发）。
