# ADR 0111：私有 Python 归档随桌面安装包附带；pip 网络类失败时改用 PyPI 镜像重试一次

日期：2026-09-29 · 状态：**Accepted**（用户 2026-09-29 拍板三条：随包附带、镜像只在网络失败且无自配源时启用一次、本 PR 不翻 `enabled`）

相关：[0063 私有 Python 供应](0063-private-python-provisioning.md)（本 ADR 修订 §一 / §六 / §十）、
[0064 无系统 Python 目标资格](0064-no-system-python-target-qualification.md)（`enabled` 的翻转仍按 §四）、
[0061 联合依赖准备](0061-joint-dependency-preparation.md)（§四 安装器接入规则：仍是 pip）、
[0019 受控依赖修复](0019-controlled-dependency-repair.md)（§八：用户 pip 配置照常生效）；
`packaging/AGENTS.md`「内置渲染 runtime」、`docs/rules/backend/private-python.md`、
`docs/rules/backend/dependency-repair-and-packages.md`。

## 背景

没有合格 Python 的机器（只有 3.9 的 Mac、没装 Python 的 Windows）缺包时，一键修复要先准备私有 Python（ADR 0063）。
那条路有两处都要连国外的服务，而国内网络常常连不上：

1. **私有 Python 归档**只从锁里的 GitHub release 地址下载（25–47 MB），ADR 0063 §六 / §十 明确「来源就是锁里那一个
   https 地址」「私有镜像不在本轮」。连不上就是 `private_python_offline`，用户走不下去。
2. **装包**时 pip 用官方 PyPI（或用户自己的配置）。受管环境是 Tavotto 新建的，用户多半没为它配过源；PyPI 连不上时
   pip 重试几轮后报「找不到版本」，我们归成 `dependency_network_unavailable`，用户同样走不下去。

而桌面安装包本来就带着 CPython：macOS 的内置渲染 runtime 与私有 Python 是**同一份** pbs install_only 归档（同源对），
构建时已经下到构建机上了。

## 决策

### 一、归档的来源按序三处，信任只有一条：整份 sha256 等于锁（修订 ADR 0063 §一 / §六）

`privatepython._download` 取归档的顺序：

1. **安装包附带的归档**：`runtime.private_python_bundle_dirs()`（冻结产物的 `_internal/private-python/`，与内置
   runtime 同一套布局判断 `_MEIPASS` → exe 同级 → `exe/_internal`；`TAVOTTO_PRIVATE_PYTHON_BUNDLE` 是工程 / 冒烟的
   排他覆盖，与 `TAVOTTO_RUNTIME_DIR` 同一语义）里名为本目标归档名的文件；
2. 数据目录 `downloads/` 里校验过的缓存（ADR 0063 原样）；
3. 锁里的 URL（ADR 0063 原样：urllib、平台原生证书校验、有界重试）。

**信任**：包内那份只在**此刻整份 sha256 等于锁**时才用（判据的主语是包内那个文件此刻的字节，不信文件名、不信大小）；
对不上（截断 / 被改 / 另一目标的归档）就当它不在，记 WARNING，往下一处走——不删（安装目录只读，也不该由运行时去改）。
它与缓存、下载拿到的是**同一种东西**：一份校验过的本地归档。之后的成员校验 → 解到 staging → 真起一次 → `os.replace`
是 ADR 0063 §四 那一条链，一个字节不改；**不造第二条解包路径**。包内归档**不复制**进 `downloads/`（那是 25–47 MB 的
第二份字节，且没有任何一步需要它在那儿）。

对外：`offer_payload()` 多一个字段 **`origin`**（`bundled` / `cached` / `download`，闭集 `privatepython.ORIGINS`），
bundled / cached 时 `download_bytes=0`、`network_required=false`；`cached` 保留原义「有校验过的本地归档、不联网」，所以
bundled 时它也是 true（旧界面不会把它说成要下载）。供应进度（`downloading_python`）的 `private_python` 段带同名字段；
账（`ledger.json`）每条记 `origin`。`present_payload()`（已就位）不带 `origin`——那时不需要任何归档。

「优先包内」的理由：它是随签名安装包一起到用户手里的字节，零网络；缓存在它之后是因为两者等价（都校验过），而包内那份
永远在、缓存可能被清。

### 二、哪些平台带、怎么带

| 目标 | 带什么 | 安装包增加 | 构建时从哪来 |
|---|---|---|---|
| macos-arm64 | `cpython-3.13.15+20260814-aarch64-apple-darwin-install_only.tar.gz` | 25,304,407 字节 | 与内置渲染 runtime 同一份归档：`build/runtime-cache/` 缓存命中 |
| macos-x86_64 | `…-x86_64-apple-darwin-install_only.tar.gz` | 25,053,138 字节 | 同上 |
| windows-x86_64 | `…-x86_64-pc-windows-msvc-install_only.tar.gz` | 47,131,996 字节 | 另下（内置渲染 runtime 是 embeddable，不是这份） |

（tar.gz 本身已压缩，安装器几乎压不动它，增量约等于归档大小。）

链路：`scripts/stage_private_python.py` 按**产品运行时读的同一份锁**（`privatepython.source_for()`）取本目标归档，经
`build_worker_runtime.download`（同一个下载函数、同一个缓存目录，sha256 不符当场失败）放进 `build/private-python-bundle/`
（目录里只许有这一份）；`build_desktop.py` 在 PyInstaller 之前调它（`--skip-private-python` 是开发态省时开关）；
`packaging/tavotto.spec` 用同一把尺 `check_bundle()`（恰好一个文件、名字是本目标的、sha256 与大小等于锁）把它作为 datas
放进 `private-python/`；`TAVOTTO_REQUIRE_PRIVATE_PYTHON=1`（发行工作流 `desktop-tauri.yml` 打开）时缺了就拒绝打包。
合并队列里那几条 exe / app 冒烟腿直接跑 PyInstaller、不备料——它们量的是渲染链路，归档缺席时 spec 只打一行提示。

**wheel / sdist 绝不带它**：产物只在 `build/` 下（`.gitignore` 挡），pyproject 的 `exclude` 再显式挡
`build/private-python-bundle/**`（与 `runtime/`、`build/runtime-cache/` 同一条理由），`tests/test_private_python_bundle.py`
看护。pip / 源码安装没有包内归档，行为与 ADR 0063 逐字相同。

**Windows 为什么带 pbs 而不是复用 embeddable**：ADR 0063 §一 原样成立——embeddable 没有 venv / ensurepip（U02 在
windows-latest 上 `python.exe -m venv` 退出 1），当不了受管环境的基础解释器；它也是「重装就能修」的不可污染渲染 runtime。
所以 Windows 包里是两份 CPython：embeddable 渲染、pbs 归档（未解开）当 base 的来源。

**macOS 签名**：归档是一份数据文件（非 Mach-O），随 `.app` 的资源封条封进签名；里面的 Mach-O 解开后落在用户数据目录，
不在 Gatekeeper 的范围（ADR 0063 §十 原样）。公证服务会不会深入 `.app` 内的 `.tar.gz` 检查嵌套二进制，本 PR **没有**
真打包验证过——由第一次带它的发行构建的公证结果回答；若被拒，出口是换容器格式或在构建时对归档内 Mach-O 逐个签名，
另开 PR 决定。

### 三、PyPI 镜像回退：只在网络类失败、且用户没自配源时，改用固定镜像重试一次（修订 ADR 0063 §十「不做镜像」）

执行器 `deprepair._run_pip_install` 是四条装包路径（单包修复 / 包管理 / 联合准备原地 / 受管环境换代）的唯一入口，两条
argv 出处 `pip_install_argv` / `pip_install_joint_argv` 各多一个只在这里传的 `index_url`：

1. 先按官方 / 用户自己的配置跑（argv 一个字节不变）；
2. 失败且 `mirror_retry_warranted(code, user_package_source(python))` 为真时，带 `--index-url PYPI_MIRROR_URL` **再跑一次**；
   再失败就如实报那一次的 code，不再换。

**判据（纯函数，`tests/test_pypi_mirror_fallback.py` 钉）**：`code == dependency_network_unavailable` 且用户源判为
`False`。`dependency_network_unavailable` 只在 pip **退出码非零**且输出带网络特征（`_NETWORK_MARKERS`：DNS 失败 /
连不上 / 读超时 / 代理错误 / pip 自己的 `Retrying (Retry`）时才会出现；退出码 0 哪怕输出里有过 Retrying 也是成功。
取消、我们的总超时、冲突、找不到、要编译、hash 不符都不是换源能解决的，一律不重试。

**「用户自配了源」**：`user_package_source(python)`——环境变量 `PIP_INDEX_URL` / `PIP_EXTRA_INDEX_URL` / `PIP_NO_INDEX` /
`PIP_FIND_LINKS`，或这个解释器的 `pip config list` 里有 `index-url` / `extra-index-url` / `no-index` / `find-links`。
它是诊断用的 `custom_package_index`（只问 index）的超集：离线 wheelhouse 同样是「用户说过从哪装」，绕开它去联网违背
意思表示。问不出来（`pip config list` 失败）按「配过」处理——宁可不换源。受管环境里跑的 pip 用的是用户自己的 pip 配置
（`runtime.owned_env` 只改缓存位置，ADR 0019 §八），所以这一问对四条路径量的是同一个东西。

**镜像的信任面**：固定一个——清华 TUNA `https://pypi.tuna.tsinghua.edu.cn/simple`（PyPI 全量镜像，HTTPS，证书由 pip
按平台校验）。它改变的只是「从哪取 wheel」：`--only-binary=:all:`、约束、hash 模式（`--require-hashes` 下 hash 由
我们生成的需求文件钉住，镜像给错字节会被 pip 拒）一个不变；包的元数据与字节来自 PyPI 的镜像同步。代价是多信任一个
第三方分发点——所以它**只在官方源已经连不上**、且用户没表达过源偏好时才启用，而且说出口：日志里一行「连不上默认的
Python 包源，改用 PyPI 镜像 … 重试一次」，进度记录顶层 `pypi_mirror`（之后每个快照含终态都带）。不做镜像列表、测速、
轮换、记住上次用了镜像——每次安装都先试官方源。

**为什么只在网络失败时启用而不是默认用镜像**：默认改源会让海外用户与配过公司内网源的用户突然从另一个地方装包，
且让「用你自己的 pip 配置」（ADR 0019）这条承诺变成例外；网络失败时用户本来就走不下去，此时换源只可能变好。

### 四、`enabled` 本 PR 不翻

锁文件每个目标的 `enabled` 仍是 false（ADR 0063 §九）：随包附带与镜像回退都是「供应得出来」的前提，不是资格。翻开关是
后续单独的 PR，按 ADR 0064 §四 带第三档证据（干净目标 + 冻结产物 + 正常入口）；那份证据从此应当包括「包内归档命中、
零下载」这一格。

## 看护

* `tests/test_private_python.py::TestBundledArchive`：包内归档命中 → 下载函数（`_fetch`）调用 0 次、本地服务零请求、
  不复制进 `downloads/`、账记 `bundled`；截断 / 篡改的包内归档被拒 → offer 说 `download`、按锁 URL 下一次、安装目录原样；
  只有缓存时 `origin=cached`；定位与冻结布局一致、覆盖排他。
* `tests/test_pypi_mirror_fallback.py`：判据表、退出码 0 不算网络失败（真子进程）、只重试一次、四个环境变量与配置键各自
  挡住镜像、非网络失败连配置都不问、两条 argv 出处；`tests/test_dependency_repair.py::
  test_the_managed_generation_records_the_mirror_on_its_progress`（换代那条经联合 argv、进度带 `pypi_mirror`）。
* `tests/test_private_python_bundle.py`：`check_bundle` 的判据、目标闭集 == runtime-lock 的 shipped、spec 的 datas 目的地 ==
  `runtime.PRIVATE_PYTHON_BUNDLE_DIR_NAME`（同源对表登记）、`build_desktop.py` 在 PyInstaller 之前备料、发行工作流要求它、
  wheel / sdist 不带。
* 每条判据写完手工变异过一次（撤掉实现 → 对应用例红，结论看退出码）。

## 后果

* 加了：`scripts/stage_private_python.py`；`runtime.private_python_bundle_dirs()` / `PRIVATE_PYTHON_BUNDLE_*`；
  `privatepython.bundled_archive` / `archive_origin` / `ORIGINS`；`deprepair.PYPI_MIRROR_URL` / `user_package_source` /
  `mirror_retry_warranted` / `_run_pip_install`；`TAVOTTO_REQUIRE_PRIVATE_PYTHON`；桌面安装包 +25 MB（macOS）/ +47 MB（Windows）。
* 改了：ADR 0063 §一（来源多了包内一处）、§六（「联网只有一条路」仍成立，但不再是唯一来源）、§十（「私有镜像不在本轮」
  对 PyPI 不再成立；私有 Python 归档本身仍只有锁里那一个下载地址，不做镜像）。
* 没验证到：冻结产物真打包（PyInstaller / Tauri / NSIS / 公证）；以产品入口在国内网络上的端到端。
