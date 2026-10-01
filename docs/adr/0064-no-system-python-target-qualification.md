# ADR 0064：统一实施包 U05——无系统 Python 目标资格（NO_SYSTEM_PYTHON）：三档证据、验证矩阵与 release lane 的接法

日期：2026-09-21 · 状态：**Accepted**（U05 PR C；`enabled` 的翻转按 §四 另开 PR）

相关：[0063 私有 Python 供应](0063-private-python-provisioning.md)（§九：能力默认关）、ADR 0056 runtime_spike（U02 分支）、
[0061 U04 联合依赖准备](0061-joint-dependency-preparation.md)；实施包 03 §3 / §4 / §5 / §9、05 §4 / §7、06 §2、
`phases/U05_private_python.md`「目标验证」、registry FO23 / FO-022 / FO-024、enrollment 台账。

## 背景

06 §2：「NO_SYSTEM_PYTHON 资格只能来自真实可验证目标，不来自清 PATH 和 mock 版本」。U05 的代码可以在任何一台有 Python
的开发机上写完、测完（ADR 0063），但「干净机器上能不能靠产品自己准备出一个 Python」这件事，只有在**没有** Python 的机器上
才量得到；而托管 runner 三个平台全都预装了 Python。本 ADR 把「什么证据算什么」定下来，免得工程验证被写成资格。

## 决策

### 一、三档证据，互不替代

| 档 | 量什么 | 在哪跑 | 前提 | 能推出什么 |
|---|---|---|---|---|
| **机制**（pr lane，每个 PR） | 供应器状态机、事务接入、隔离、去重 / 取消 / 退役 | `tests/test_private_python.py`、`tests/test_private_python_transaction.py`（本地供应服务 + 假归档；真 venv / 真 pip） | 宿主有 Python；替身归档 | 代码按 ADR 0063 的合同工作；**不是**资格 |
| **工程**（每周 schedule / dispatch / 本能力文件变动且 base 是 main 的 PR，具名非 required job `private-python-targets.yml`；三条腿各真下载 30–120 MB 归档，按周刷新） | ① 真 pbs 归档经**产品代码**走完整链（公网供应探活单列一步）；② Linux：供应好的 runtime 只读挂进**没有 Python 的空镜像** `ubuntu:24.04`（`--network none`）真起 → venv → 离线装科学栈 → 出图；③ Windows：注册表 `Software\Python` / 用户与系统 `Path` / USERPROFILE 顶层前后快照 | 三个托管 runner；「没有基础解释器」= 发现链末端置空（`bootstrap.find_base_python → None`） | 归档、runtime、事务、隔离在真实目标平台上成立；**空镜像那一条**证明 runtime 在只有 glibc 的目标上可用。仍不是资格：宿主有系统 Python，产品的**入口**没有在无 Python 的机器上跑过 |
| **目标**（release lane，U11） | 真实无系统 Python 的目标 + **冻结产物**（Windows NSIS / macOS 签名 app）+ 正常入口（打开项目 → 一次授权 → 私有 Python → 环境 → 出图） | Windows 干净 VM / 容器（无 Python、无 py launcher）、macOS 干净用户（无 Homebrew / python.org / Conda）；Linux 没有桌面产物，只有 ② 那一档 | **资格**（FO23 / FO-022） |

三档的判据各自独立：第一档红是代码坏了；第二档红分「公网供应失败」（单列那一步）与「产品 / 归档 / 平台」两类，
都不进 Gate；第三档红是资格没取得。**不能拿低一档的绿冒充高一档**（05 §7）。

### 二、验证矩阵（按目标，`enabled` 的翻转以此为据）

| 目标 | 机制 | 工程 ① 产品链 | 工程 ② 空镜像 | 工程 ③ 注册表 | 目标（资格） | `enabled` |
|---|---|---|---|---|---|---|
| macos-arm64 | pr lane | 周腿 + 本机（evidence/u05） | 不适用（macOS 没有空镜像） | 不适用 | **已取得（2026-09-30）**：tart 全新 macOS 26.6.2 VM + 冻结 .app，冻结 sidecar + 浏览器界面驱动（非桌面壳窗口本身）；证据 `evidence/u05/target-macos-arm64-2026-09-30.md` | true |
| macos-x86_64 | 同上（不分架构） | 无 Intel runner | 不适用 | 不适用 | **未取得，维护者拍板例外开启（2026-10-01）**：没有 Intel 机器；与 arm64 同一归档族但不是同一目标。§四 的例外，见修订记录 | true（例外） |
| linux-x86_64 | pr lane | 周腿 | 周腿（2026-09-30 本分支 dispatch run 36772138253 绿） | 不适用 | 无桌面产物：以 ② 为该平台的最高档 | true |
| linux-arm64 | 同上 | 无 arm64 runner | 本机 docker（evidence/u05/empty-image-linux-arm64.json，2026-10-01 重跑） | 不适用 | 同上 | true |
| windows-x86_64 | pr lane | 周腿 | 不适用（Windows 容器另议） | 周腿 | **已取得（2026-10-01）**：阿里云干净 VM + nightly 冻结 NSIS（run 36771293696），经界面一键修复出图、包内归档零下载；证据贴在翻开关的 PR 评论（tavotto-51） | true |

「不适用」是事前支持矩阵里的真实不适用，不是 skip（03 §5）。

### 三、台账

FO23（无系统 Python / uv / pip 冷启动）由 planned → **observing**：具名任务 = `private-python-targets.yml`（三条腿的
`TestRealChain` + 空镜像 + 注册表快照；每周一次 + dispatch + 本能力文件变动且 base 是 main 的 PR——叠栈里只在链头跑），`lane: release`（它的资格档在 release lane），`expected_product_outcome: guided`
不变。它不进任何 Gate 的闭集；红保留在 workflow 结论与工件里。**observing 的用例一条都不许 skip**：`TestRealChain`
缺 wheelhouse 才 skip，而三条腿都先建 wheelhouse——skip 就是基础设施红。FO-022（真桌面产物）保持 planned 到 U11。
FO24 / FO25 / FO26 的机制面用例在 pr lane 已有（ADR 0063），经真实入口的场景在 PR B 提升。

### 四、翻 `enabled` 的 PR 必须带什么

某目标 `enabled: true` 的 PR = 第三档证据（目标系统清单、进程树、下载与安装目标、artifact SHA、编辑结果——
`archive/firstopen_cases.json` FO23 的 `required_evidence`）+ 该目标**当天手动 dispatch 一次** `private-python-targets.yml`
的腿绿（周 schedule 的证据可能已过期一周）+ 台账里 FO23 该目标的实例 `pass` + 06 §2 要求的升级与回退方案。翻回 false 不需要证据（关能力永远安全）。**不允许**：拿另一平台的证据替代、
拿清 PATH 的托管 runner 冒充干净目标、拿 spike 的 report 当产品证据。

### 五、不做的事

* 不在每个 PR 上造全部目标 VM（`phases/U05_private_python.md`「目标验证」）；工程档只在本能力文件变动时随 PR 跑。
* 不把 `private-python-targets.yml` 接进 merge_group / 任何 Gate（03 §2；`tests/test_merge_queue_workflows.py` 的信任区
  与事件闭集守着它）。
* 不为 Windows 造空容器（Windows 容器镜像与 runner 的 Hyper-V 隔离另议）；Windows 的第三档在 U11 的干净 VM。

## 修订记录

### 2026-10-01：翻 `enabled` 的 PR——五个目标全开，其中 macos-x86_64 是例外

* `macos-arm64` 取得第三档证据（见 §二 矩阵与 `evidence/u05/target-macos-arm64-2026-09-30.md`），FO23 台账记该目标实例 `pass`，
  当天手动 dispatch `private-python-targets.yml`（run 36772138253，2026-09-30 20:22Z = 北京时间 10-01 04:22，三条腿绿，与取证同一晚）的结论记在翻开关的 PR 里。**限度**：驱动的是冻结 sidecar +
  浏览器界面，不是 Tauri 桌面壳窗口；这一格不因本次取得而关闭。
* `windows-x86_64` 取得第三档证据：阿里云干净 VM（无 Python、无代理、不开逃生门）装 nightly 冻结 NSIS（run 36771293696），
  全程经界面：脚本行「还缺 adjusttext」+ 一键修复 → 3 分钟出图，私有 Python 来自包内归档、GitHub 零下载，缺 ISRG / USERTrust
  根证书不影响；用户已有 3.12 + adjustText 时 5 秒内自动切换、无弹窗。完整证据由 tavotto-51 贴在翻开关的 PR 评论。
* Linux 两个目标按 §二 以第二档（空镜像）为最高档翻 true：`linux-x86_64` 用上面同一次 dispatch 的 ubuntu 腿；`linux-arm64`
  2026-10-01 在本机 docker 的 `ubuntu:24.04` arm64 空镜像里重跑（`--network none`，锁里同一归档 sha256 303efcce…，venv +
  离线装 matplotlib 3.11.2 / numpy 2.5.3 出图，`evidence/u05/empty-image-linux-arm64.json`）。
* **例外：`macos-x86_64` 没有任何目标档证据，由维护者 2026-10-01 拍板照开。** 这是 §四「不许拿另一目标的证据替代」的明示例外，
  不是取得资格：手上没有 Intel 机器（Apple 芯片上的虚拟机跑不了 Intel 版 macOS），Intel 的 dyld / 签名 / Rosetta / 包内归档
  面都没有量过；理由是它与 arm64 同一归档族、同一份产品代码，且关掉的代价（Intel 用户没有合格 Python 时仍是死路）被判断为大于
  风险。FO23 台账该目标记 `not_run`（不是 `pass`）。取得 Intel 目标档证据后补在本节；出问题按下一条回退。
* 回退：把某目标 `enabled` 翻回 false，不需要证据。已供应到用户机器上的私有 Python 不主动删：关闭后探测链末级不再把它当
  base（`tests/test_private_python.py::TestBaseChain` 钉着），磁盘上那份留着；已经以它为 base 的受管环境世代继续可用
  （venv 挪不走 base，ADR 0063 §七：`retire_unused` 只删「任一世代都没记着、也没有会话租用」的旧 runtime，不会把在用的一份删掉）。
  用户要回收空间，走数据目录清理；产品不在回退时自动删。

## 后果

* 加了：`.github/workflows/private-python-targets.yml`、`scripts/ci/private_python_empty_image.sh`、
  `scripts/ci/private_python_windows_snapshot.py`、`tests/test_private_python_transaction.py::TestRealChain`、
  台账 FO23 → observing、`evidence/u05/empty-image-linux-arm64.json`。
* U11 拿走：§二 的矩阵与 §四 的翻转条件；release lane 里 FO23 的实例按 `foundation_harness` 的绑定（artifact = 安装包 sha256）记。
