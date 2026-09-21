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
| macos-arm64 | pr lane | 周腿 + 本机（evidence/u05） | 不适用（macOS 没有空镜像） | 不适用 | **未取得**（U11：干净用户 + 签名 app） | false |
| macos-x86_64 | 同上（不分架构） | 无 Intel runner | 不适用 | 不适用 | 未取得 | false |
| linux-x86_64 | pr lane | 周腿 | 周腿 | 不适用 | 无桌面产物：以 ② 为该平台的最高档 | false |
| linux-arm64 | 同上 | 无 arm64 runner | 本机 docker（evidence/u05/empty-image-linux-arm64.json） | 不适用 | 同上 | false |
| windows-x86_64 | pr lane | 周腿 | 不适用（Windows 容器另议） | 周腿 | **未取得**（U11：干净 VM + NSIS） | false |

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

## 后果

* 加了：`.github/workflows/private-python-targets.yml`、`scripts/ci/private_python_empty_image.sh`、
  `scripts/ci/private_python_windows_snapshot.py`、`tests/test_private_python_transaction.py::TestRealChain`、
  台账 FO23 → observing、`evidence/u05/empty-image-linux-arm64.json`。
* U11 拿走：§二 的矩阵与 §四 的翻转条件；release lane 里 FO23 的实例按 `foundation_harness` 的绑定（artifact = 安装包 sha256）记。
