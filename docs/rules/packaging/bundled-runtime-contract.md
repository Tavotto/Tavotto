# 内置渲染 runtime：定位、优先级与不污染

> 原文出自 `packaging/AGENTS.md`「内置渲染 runtime（Windows 2026-08-17；macOS 2026-08-18）」（2026-10-24 迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`packaging/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

<!-- agents-route
sheet: packaging/AGENTS.md
order: 20
改到: `packaging/runtime-lock.json`、`engine/runtime.py`、`pool._prioritized_candidates()`、`child_env()` / `child_args()`、`bundled_runtime_*` code
主题: 内置渲染 runtime：版本锁、路径判断、解释器优先级、不往安装目录写 → `bundled-runtime-contract.md`
必守要点: 锁是唯一输入且三个目标闭包逐字相同；`engine/runtime.py` 全程 os.path；`TAVOTTO_RUNTIME_DIR` 排他；`-B` + 摘掉 `PYTHON*`，macOS 往 .app 写 `__pycache__` 会破坏签名；缺失报专用 code、绝不自动 pip install
-->

**每个桌面安装包都自带一套 Tavotto 私有的 Python 渲染环境**，用户不需要先装
Python，首次渲染也不联网：

    Windows: Tavotto.exe → _internal\runtime\python.exe    → engine/worker.py → 用户的脚本
    macOS:   Tavotto.app → …/_internal/runtime/bin/python3.13 → engine/worker.py → 用户的脚本

- **上游发行版按平台分，理由不同**：Windows 用官方 embeddable（Python 官方就把它
  定位成「应用私有的运行时」）；macOS 用 **python-build-standalone 的 install_only**
  ——官方 macOS 安装器装的是 `/Library/Frameworks` 下的固定路径、**不可重定位**，
  嵌不进 `.app`，而 Homebrew/Conda 是用户的环境，不碰。pbs 的 prefix 由解释器
  自身路径推导，且是逐个可 codesign 的普通 Mach-O（公证要求每个嵌套二进制都签到）。
- **版本锁 `packaging/runtime-lock.json`（schema 2）是唯一输入**，**按目标分层**
  （`windows-amd64` / `macos-arm64` / `macos-x86_64`）：CPython 下载地址 + SHA-256，
  以及科学栈的**完整传递闭包**（精确版本，不允许范围/latest）。分层不是洁癖——
  一个平台的 wheel 绝不能被另一个平台复用。**三个目标的闭包刻意保持逐字相同**：
  同版本的 matplotlib/numpy 才能让同一个脚本在两个平台画出同一张图
  （`test_all_targets_pin_the_same_versions` 看护）。构建脚本
  `scripts/build_worker_runtime.py` 只执行、不做版本决策（`--resolve` 是维护者
  更新锁文件时才跑的那一档）。**别手写闭包**——漏掉的传递依赖会在用户机器上以
  ModuleNotFoundError 出现。产物在仓库根的 `runtime/`，进 .gitignore，并在
  pyproject 里显式 exclude（wheel/sdist 绝不能被它污染）。
- **架构范围如实记录**（ADR 0076）：macOS **按架构各发一个 dmg**——arm64 与
  x86_64 都 `shipped: true`，Intel 那份只在原生 Intel runner（`macos-26-intel`）上
  构建与冒烟。不产出 universal2——科学栈 wheel 分架构发布，硬拼没验证过。
  shipped 的 macOS 目标 == 发行矩阵里的 macOS 腿架构（`test_every_shipped_macos_target_has_a_native_desktop_leg`）。
- **`engine/runtime.py` 是路径判断的唯一出处**（frozen 的 `_MEIPASS` / exe 同级 /
  源码树 / `TAVOTTO_RUNTIME_DIR` 覆盖）。这一段**全程 os.path 拼字符串，一个
  pathlib 都不用**：`Path()` 按 `os.name` 分派，在别的平台上构造另一半直接抛
  UnsupportedOperation，连在 macOS 上单测 Windows 分支都做不到
  （test_runtime_path_logic_never_instantiates_a_foreign_pathlib 看护）。
  两种布局（`python.exe` / `bin/python3*`）都要认——构建机会交叉产出另一平台的
  runtime，只认本平台那种会误报「不完整」。**版本化实体名按 glob 找，不写死
  3.13**：写死的话升到 CPython 3.14 会突然「找不到解释器」，而提示是
  「安装文件不完整」——与真实原因毫不相干。
- **`TAVOTTO_RUNTIME_DIR` 覆盖是排他的**：指了就只认这一个，指到空处即等于
  「没有」。「覆盖了却被别处那份悄悄顶掉」是最难查的一种——你以为在验刚构建的
  产物，实际验的是上一次留下的，两边日志一模一样。
- **manifest schema 2 会校验平台/架构**（`platform_mismatch()`）：装错架构的包
  启动时就报 `bundled_runtime_invalid`，而不是等第一次渲染甩一句
  "incompatible architecture"。宿主平台经 `host_os()`/`host_arch()` 取，
  做成函数是为了能在任何一台机器上单测另一台的分支。
- **探测解释器要和真起 worker 用同一套 env/args**（`_has_matplotlib(bundled=)` →
  `child_env()`/`child_args()`）：macOS 上没有 `._pth` 挡着，用户 shell 里的
  `PYTHONHOME`/`PYTHONPATH` 会让探测那句 `import matplotlib` 失败，一个好用的
  内置 runtime 被判成不可用（只在「从终端启动」时复现）。
- **解释器优先级（`pool._prioritized_candidates()` 是唯一出处）**：
  `TAVOTTO_WORKER_PYTHON` → 用户在设置里指定的 → **内置 runtime** → 自身
  （非 frozen）→ 系统 Python/Conda 探测。用户显式指定的永远优先；
  第 5 条是兼容回退，不是摆设（脚本要 rdkit 这类内置环境没有的包时靠它）。
  来源标签 `env_override/configured/managed_venv/bundled/current_process/system`
  经环境状态 API、诊断包与冒烟断言一路暴露出来。
- **不往安装目录写任何东西**：`child_args()` 的 `-B` 是硬保证，
  `child_env()` 再注入 `MPLCONFIGDIR`（改道到数据目录）+ `PYTHONNOUSERSITE`。
  **刻意不设 `PYTHONPYCACHEPREFIX`**——它连**读**的位置一起改道，而 `-B` 又
  禁止写，两条合起来让随包发的预编译字节码一份都用不上，每次冷启动重编
  整个科学栈（只在 macOS 上发作：Windows 的 `._pth` 忽略环境变量）。Windows 上安装目录可能在 Program Files（没写权限）；
  **macOS 上后果更硬——`.app` 是签过名的，往里写一个 `__pycache__` 当场破坏
  代码签名，用户下次启动看到「应用已损坏」**。非内置解释器（项目 .venv / 系统 /
  `TAVOTTO_WORKER_PYTHON`）不加 `-B`，由引擎装载窗口里的 `sys.dont_write_bytecode` 挡
  （`docs/rules/backend/execution-entries.md`，看护 `tests/test_install_dir_bytecode_free.py`）。
- **`child_env()` 还要摘掉 `PYTHONHOME`/`PYTHONPATH`/`PYTHONSTARTUP`/
  `PYTHONUSERBASE`**：Windows 上 `._pth` 的隔离模式顺手挡住了它们，
  **macOS 上没有任何东西挡**。用户从终端启动 Tavotto 时，shell 里为 Conda 或
  自家项目设的那几个会原样传给内置解释器——轻则 import 到别的 numpy，重则
  解释器起不来；而且只在「从终端启动」时复现，Finder 双击一切正常。
- **缺失/损坏/架构不符报专用 code**（`bundled_runtime_missing` /
  `bundled_runtime_invalid`），提示「安装文件不完整，请重新安装」——**不是**
  「请先安装 Python」。那时 `can_install` 必须为 false：embeddable 里连 pip 都
  没有，现场建 venv 只是把包装问题伪装成用户的环境问题。pip / 源码 / Linux
  不带 runtime，那里 runtime 缺失是正常状态，两个 code 都不给
  （`ships_bundled_runtime()` 是这条判断的唯一出处）。
- **本阶段不做包管理**：脚本缺包时报结构化的 `missing_dependency` + 包名，
  引导用户换成自己的环境；**绝不按 ModuleNotFoundError 自动 pip install**——
  那会让内置环境不再可复现，也让「重装就能修」这条退路失效。
  内置环境覆盖的是常用科学栈，不承诺覆盖任意用户脚本的依赖。
