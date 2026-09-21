# ADR 0063：统一实施包 U05——私有完整 Python 的供应：来源锁、按内容命名的目录、租约 GC、离线、授权与代理

日期：2026-09-21 · 状态：**Accepted**（U05 阶段；分叠栈 PR 落地，§八 记每一节落在哪个 PR；目标资格见 ADR 0064）

相关：[0019 受控依赖修复](0019-controlled-dependency-repair.md)、[0021 tavotto run 产品契约](0021-tavotto-run-product-contract.md)（§6 envlease 一张表）、
[0044 系统解释器候选](0044-system-interpreter-as-repair-candidate.md)、[0053 U01 合同](0053-foundation-contracts-and-preparation.md)、
ADR 0056 runtime_spike（U02，分支 `foundation/u02-spikes`；合入后补链接）、[0057 U03 首开](0057-first-open-environment-and-workdir.md)、
[0061 U04 联合依赖准备](0061-joint-dependency-preparation.md)（§四 安装器接入规则、§五 代目录）；实施包 D04 / D05 / D11 / D16、
`phases/U05_private_python.md`、registry FO-022 ~ FO-028、FO23 / FO24 / FO25 / FO26；`packaging/runtime-lock.json` 与
`packaging/AGENTS.md`「内置渲染 runtime」。

## 背景

ADR 0019 §一 把安装目标定成两种：用户的项目 venv、Tavotto 受管环境。受管环境要一个**基础解释器**来 `python -m venv`，
它来自 `bootstrap.find_base_python()`（系统 Python / Conda / python.org 落点，版本在支持区间内）。一台**没有**合格
Python 的机器（干净的 Windows、只带 3.9 的 macOS、Linux 服务器）在这里就停下：`managed_env_unavailable`，用户被引导
去装 Python——而 Tavotto 桌面版明明自带一份完整的 CPython 当渲染 runtime，只是那份是**不可污染**的
（`packaging/AGENTS.md`：它是「重装就能修」的前提；Windows 那份还是 embeddable，没有 venv / ensurepip）。

U02 的 runtime_spike（ADR 0056）证明了一条路：python-build-standalone 的 `install_only` 发行版可重定位、自带
venv / ensurepip / pip，下载 → 校验 → 解到私有目录 → 真起 → 原子改名 → 建 venv → 离线装包，三平台都跑通。
本 ADR 把它接进产品，**只补基础解释器的来源**，不动 U04 的事务（D05：不造第二套安装器）。

## 决策

### 一、来源：pbs install_only，锁文件在包内；两个 macOS 目标与 runtime-lock.json 同源

`src/tavotto/resources/private_python_lock.json`（schema 1）是 `engine/privatepython.py` 的**唯一输入**：一个
`python` 块（版本 / release / flavor / archive_root / SHA256SUMS 的地址与它自己的 sha256）+ 按目标
（`<os>-<arch>`，arch 经 `runtime.normalize_arch`：`macos-arm64` / `macos-x86_64` / `linux-x86_64` / `linux-arm64` /
`windows-x86_64`）分层的 URL / sha256 / size / `python_rel` / `enabled`。放在 `resources/` 而不是 `packaging/`：
产品运行时要读它（wheel 随包收进、冻结产物经 `tavotto.spec` 的 `resources/` datas），`packaging/` 不进包。

**同源对**：两个 macOS 目标的 version / release / triple / url / sha256 / size / archive_root **逐字段等于**
`packaging/runtime-lock.json` 的 `macos-*` 目标——桌面版内置渲染 runtime 与私有 Python 是同一份字节，不是两处各钉一遍
（`tests/test_private_python.py::TestLock::test_macos_entries_are_the_same_origin_as_the_runtime_lock` 看护，
`docs/rules/repo/same-origin-pairs.md` 登记）。Linux / Windows 的 sha256 进锁之前从该 release 的 SHA256SUMS 重新核过
（2026-09-21；那份文件的 sha256 `0e27ff80…7584966` 记在锁里）。

**Windows 用 pbs 而不是内置 runtime 那份 embeddable**：embeddable 没有 venv / ensurepip（U02 在 windows-latest 上真跑
`python.exe -m venv` 退出 1）。两者分工：embeddable 仍是桌面版的渲染 runtime（不可污染），pbs 是受管环境的 base。

**版本不自动追最新**：换版本 = 改锁（新 sha256 → 新 id → 新目录）+ 每个目标重新取得资格（ADR 0064）。

### 二、安装器不变：pip；uv 不进产品路径

ADR 0061 §四 的裁决在这里兑现：pbs install_only **自带 pip**，`create_generation_venv`（`python -m venv`）与
`pip_install_joint_argv` 一个字节不改，`privatepython` 交出去的只是一条解释器路径。U02 用 uv 建 venv / 装 wheel 只是
spike 里「一条 resolver」的选择；产品里再下载一个 37 MB 的 uv 二进制没有任何它才能做的事，还会成为第二个安装器
（D05）。**uv 不下载、不进锁**；ADR 0056 记着的 uv 钉法留作「base 没有 pip 时」的备选——今天没有这样的 base。

### 三、目录：按内容命名、不可变；账不是指针

```
<data_dir>/private-python/
    runtimes/<id>/                 id = cpython-<版本>-<sha256 前 12 位>；解开的 install_only（bin/python3 或 python.exe）
    runtimes/.staging-<id>-<pid>/  解包 + 成员校验 + 真起一次 的临时目录；从不叫最终名字
    downloads/<归档名>             校验过的归档（离线可复用，FO24）；`.part` 是还没校验的
    ledger.json                    账：供应过哪些 id、来源 / 字节 / 时间 / last_used
```

* 同一份字节永远落在同一个目录：已在就复用（不重下、不重解、不重起）；不同字节是另一个目录。**不 rmtree 正在
  被用的目录**（ADR 0056 §2 Codex 指出的形状）。
* 「现在该用哪一份」= 锁文件那份的 id（`source_for()` → `runtime_dir()`），**不设第二个指针文件**（与 ADR 0061 §五
  「manifest 的 `active` 是唯一指针」同一条纪律：同一件事记两处迟早不一致）。`ledger.json` 是账，删了也不影响判
  「在不在」。
* 提交点 = `os.replace(staging/<root>, runtimes/<id>)`：同一文件系统内的 rename，要么在要么不在。`python_of()` 只看
  最终目录里的解释器文件（且可执行），不起子进程——staging 里的东西对它不存在，所以应用中途被杀留下的半个解包
  **不可能**被当成可用 runtime；下一次供应时按 pid 存活 / 时限清掉。

### 四、校验先于一切执行

顺序固定：整份归档 sha256 与锁一致 → `.part` 改成正式名 → 逐成员校验（绝对路径 / `..` / 不在 `archive_root/` 下 /
软链接指出根目录 / 硬链接 / 设备文件一律 `private_python_invalid_archive`，**不靠** `tarfile` 的默认行为；有 `data`
过滤器就再叠一层）→ 落点 realpath 在 `data_dir` 之下 → 解释器是常规文件且有可执行位 → **真起一次**
（`-I -c` 自报 version / prefix / executable，版本必须等于锁的版本）→ 才 `os.replace`。

坏 hash / 截断 / 错期望值的路上：解释器执行计数为 0、`runtimes/` 里没有新目录、`.part` 当场删掉、账不动（FO26；
`tests/test_private_python.py::TestRefusals`）。hash 不符**绝不重试**——对不上的文件是拒绝的对象，不是再下一次的对象
（`build_worker_runtime.download` 同一条）；传输层失败才有界重试（`DOWNLOAD_ATTEMPTS`）。缓存里躺着一份对不上的
归档同样不是复用对象（删掉、按无缓存处置）。

### 五、离线：有缓存零请求；无缓存有界 safe_stop；来源没了不是离线

* 归档缓存在且 sha256 一致 → 不联网（用例的判据是本地供应服务的请求日志为空 + 死代理 + 指向没人听的端口）——FO24 的
  automatic。
* 无缓存 + 连不上 → `private_python_offline`：有界（socket 超时 × 有界重试），不建目录、不留 `.part`，`python_of()`
  仍是 None——FO25 的 safe_stop，**不计入自动兼容成功**。
* 来源回 4xx / 5xx → `private_python_source_unavailable`：钉死的地址上没有这个文件，用户的下一步是升级 Tavotto，
  不是重试；与离线分开报。

### 六、联网只有一条路：urllib，与检查更新同一张脸

`urllib.request.urlopen`：TLS 校验默认开（源码里没有 `ssl` 的 import——用例按 AST 钉）、代理只从
`HTTP(S)_PROXY` / `NO_PROXY` 环境变量来（`updater` / pip 都是这一套）、`User-Agent: Tavotto/<版本>`、不带任何身份。
**不读** pip.conf / uv 配置 / 项目设置 / 用户配置（对 `config` 只调 `data_path` / `data_dir`，AST 钉住）——下载器
不能自行使用任意项目设置（任务书原话）。凭据 / 私有镜像不在本轮：来源就是锁里那一个 https 地址。

### 七、授权、去重、取消、租约、GC、配额

* **授权绑在计划上**：没有合格基础解释器且本目标提供私有 Python 时，`create_plan` / `create_joint_plan` 不再抛
  `managed_env_unavailable`，而是在计划上挂 `private_python` 载荷（`offer_payload()`：id / 版本 / 目标 /
  `download_bytes` / `cached` / `network_required`，没有机器路径）；`offer()` 的受管目标同样带它。界面必须把
  `download_bytes` 说出口（PR B 的一次授权对话框）。执行端按 `_GenerationJob.provision_private`（= 计划里有这段）
  决定要不要在**锁内、建 venv 之前**先供应；**重建 / 包管理首装没有明示过下载**，没有基础解释器就照旧
  `managed_env_unavailable`，一个字节不下。
* **基础解释器的优先级**：`managedenv.base_python()` = 系统合格 base（`bootstrap.find_base_python(accept=区间)`，
  不变）→ **已供应的**私有 Python（`privatepython.python_of()`，只看磁盘、不联网）。私有 Python 不压过用户已有的
  合格 Python：没必要下载时不下载。
* **去重**：同一个 id 的并发请求（两个项目同时准备）只下一次——`_inflight` 表里一个下载线程、若干消费者各自等；
  跨进程靠「staging 带 pid + 最终目录已在就复用」（第二个进程 `os.replace` 失败时看见最终目录已在 → 复用）。
* **取消按消费者管理（D11）**：每个消费者只管自己的 `cancel_ev`；一个取消只是它自己以 `private_python_cancelled`
  退出（事务层收成 `cancelled`，这一代**没登记**），下载在最后一个消费者也放弃时才中止（`.part` / staging 清掉）；
  提交点之后取消无效——目录不可变，留下的永远是完整的一份。
* **租约与 GC**：`retire_unused(in_use=…)` 只删「不是锁文件当前那份、且 `in_use(id, python)` 为假」的旧 runtime；
  `deprepair._private_runtime_in_use` = 任一项目受管环境的任一代记着它为 base（`managedenv.referenced_base_runtimes()`
  ——venv 挪不走 base，base 一删那一代当场坏掉）**或** 池里 / envlease 上有会话用着它。事务提交后各试一次，删不掉
  留到下次。这是「旧运行版本有 lease 不被 GC」（FO-028）在这里的形状：lease 的出处仍是 envlease 那一张表 + 代的账。
* **配额**：下载前 `require_free_disk`（归档 × `EXTRACTED_FACTOR` + 余量；量不出来不拦）→ `private_python_disk_low`，
  计划阶段就查一次。

### 八、隔离：不改系统一个字节

写入只在 `data_dir/private-python/` 之下（落点 realpath 判）；不改 PATH、shell、注册表、默认 Python、用户的
`.python-version`——模块里没有任何一行写到那些地方（用例：HOME 指空目录跑完仍为空、`os.environ` 与 cwd 前后相同、
新文件全在私有目录下）。Windows 注册表的前后快照本机量不了，由 ADR 0064 的目标腿取。真起私有解释器时摘掉
`PYTHON*` / `VIRTUAL_ENV` / `CONDA_PREFIX`（宿主 shell 里的 PYTHONHOME 会让它起不来——`packaging/AGENTS.md` 的
同一条教训）。

### 九、能力默认关：`enabled` 全 false，逃生门只给工程与目标腿

06 §2：无系统 Python 的资格只能来自真实可验证目标。锁文件每个目标的 `enabled` 在资格取得前保持 `false`——代码在、
用例在、能力不默认开；那时没有合格 Python 的机器看到的仍是 `managed_env_unavailable`。`TAVOTTO_PRIVATE_PYTHON=1|0`
是工程 / CI 目标腿的逃生门（与 `TAVOTTO_RUNTIME_HOST_ARCH` 同一档），不是产品设置、不写进设置界面。取得某目标的
资格 = 那一条 `enabled` 改 true 的 PR（带 ADR 0064 的证据）。

### 十、不做的事（各有出口）

* 代理凭据 / 私有镜像 / 断点续传（X01 或后续：来源只有锁里那一个地址；断点续传对 25–120 MB 归档不值一个状态机）。
* 签名 / 公证（pbs 的 Mach-O 逐个 codesign）：私有 Python 不在 `.app` 内，落在用户数据目录——不在 Gatekeeper 的
  范围；发行资格在 U11。
* uv（§二）；`uv python install`（ADR 0056 已否）。
* 私有 Python 作为**渲染 runtime**：它只当受管环境的 base，`pool._prioritized_candidates()` 不变。

## 落地

| PR | 内容 | 本 ADR 的节 |
|---|---|---|
| A `foundation/u05-private-python` | 锁文件；`engine/privatepython.py`；`managedenv.base_python` 末级 + 代记 `base_runtime` + `referenced_base_runtimes`；`deprepair` 的计划载荷 / 事务里的供应步 / 退役；本地供应服务用例 + 真事务用例；文案 | §一–§九 |
| B `…-b` | 入口：`preparation.plan_for` 的 `needs_input` 投影、HTTP / MCP 的授权面、前端一次授权对话框里的「将下载 N MB」；FO24 / FO25 / FO26 经真实入口的场景与台账 | §七（授权） |
| C `…-c` | 目标验证腿（ADR 0064）：nightly 的具名非 required job 真下载真 pbs → 校验 → 起 → venv → 装 → 出图；Windows 注册表快照；Linux 空镜像 | — |

## 后果与修订

* 加了：`resources/private_python_lock.json`；`engine/privatepython.py`；代记录多 `base_source` / `base_runtime`；
  计划多 `private_python`；进度多 `downloading_python` 状态；`private_python_*` 九个 code（`engine.repairError` 文案）。
* 改了：ADR 0019 §一 的「基础解释器从哪来」多了末级；ADR 0061 §四 的接入规则按 §二 兑现（uv 不进产品）；
  `packaging/AGENTS.md`「内置渲染 runtime」多一段「私有 Python 与它的关系」。
* U09 / U11 拿走：`base_runtime` 进回执的环境身份；发行时的目标资格与 `enabled` 的翻转。
