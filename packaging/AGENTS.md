# packaging/ — 打包、内置渲染 runtime 与签名规则

仓库级路由与不变量在根 `AGENTS.md`。桌面壳与安装器在 `src-tauri/AGENTS.md`；
引擎侧在 `src/tavotto/AGENTS.md`。

## 打包与启动（src layout，2026-08-16）

- 代码在 `src/tavotto/`，`pyproject.toml`（hatchling）声明依赖与
  `tavotto = "tavotto.cli_entry:main"` 入口（**纯标准库的轻量入口**：
  `open`/`doctor` 要在 import Flask 之前分派掉，见 `src/tavotto/AGENTS.md`
  的「外部交接」）。`run.sh` = 自建 `.venv` + `pip install -e .` +
  `exec .venv/bin/tavotto`；**不要再写 `python app.py`**，
  根目录已无该文件（旧进程内存里的老路径正是「worker 进程崩溃（无响应）」的成因）。
- extras：`worker`（matplotlib/numpy，装了就用同解释器渲染）、`dev`（pytest/build）。
- 前端产物 `src/tavotto/web/` 由 `scripts/build_frontend.py` 从 `web/dist` 拷入，
  进 .gitignore；hatchling 默认跳过 VCS 忽略的文件，**必须靠 pyproject 的
  `[tool.hatch.build] artifacts` 收回**，否则 wheel 里没有界面（首页 404）。
  开发态包内无 `web/` 时 `app.py` 自动回退到 `web/dist`。
- CI 的 package job 看护这条链路：build_frontend → wheel → 断言含
  `tavotto/web/index.html` + entry point → 干净 venv 装 wheel 跑 `tavotto --help`。
- 打包卫生：wheel/sdist 不含 `src-tauri/`、`workerd/`、`codex-plugin/`、
  `.agents/`、`services/telemetry_proxy/`、仓库根 `runtime/`（pyproject 的
  exclude 是权威，多处 pytest 看护）。

## 内置渲染 runtime（Windows 2026-08-17；macOS 2026-08-18）

**两个桌面安装包都自带一套 Tavotto 私有的 Python 渲染环境**，用户不需要先装
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
- **架构范围如实记录**：目前只发 **macOS arm64**；`macos-x86_64` 标着
  `shipped: false`（锁着版本但**没构建过也没冒烟过**，CI 没有 Intel runner）。
  不产出 universal2——科学栈 wheel 分架构发布，硬拼没验证过。
  改这条之前不许在 README 里写「支持 Intel」。
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
  代码签名，用户下次启动看到「应用已损坏」**。
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
- **构建链的三道闸**（漏一道就会安静地发出「装完不能渲染」的包）：
  ① 构建脚本自己逐个 import + 画真图，不过就失败在构建机；
  ② `TAVOTTO_REQUIRE_RUNTIME=1` 时 `tavotto.spec` 经
  `build_worker_runtime.check_runtime_dir()`（**与 build_desktop.py 共用同一把尺**）
  确认 schema / 平台架构 / 冒烟状态；③ 打包后 `smoke_app.py
  --expect-source bundled --expect-runtime` 真启动真渲染。
- **macOS 签名**：内置 runtime 让嵌套 Mach-O 从几十个变成五百多个，且全在
  `Contents/Resources` 下——**`codesign --deep` 既签不到也验不出**（它们被当作
  *资源*封进签名，封条本身合法）。签名与验收统一走 `scripts/codesign_macos.py`
  （读魔数找 Mach-O、深度降序自内向外、只给可执行文件挂 entitlements、
  最后逐个 `--verify` 并核对架构）。
- **CLI 双入口**：`packaging/tavotto.spec` 从同一个 Analysis 产出 GUI 的
  `Tavotto` 与 `console=True` 的 `tavotto-cli`（共用 `_internal/`）。GUI exe
  不能当 CLI 调（无终端时 stdout 落日志），交接与安装清单都指 `tavotto-cli`
  （见 `src/tavotto/AGENTS.md` 的「外部交接」）。
- 验证：`tests/test_bundled_runtime.py`（定位/优先级/布局/架构/失败路径，
  **全部平台无关**）+ `tests/test_runtime_build.py`（锁文件分层、布局、
  `._pth`、构建判据、打包卫生，另有几条只在本机构建过 runtime 时才跑的
  **真 import + 真绘图**用例）+ CI 的 `windows-exe-smoke` 与 desktop-tauri 的
  两条腿（见 `.github/AGENTS.md`）。
- **别把「借一个解释器」加回冒烟**：macOS 这条腿一度现建 worker-env 再设
  `TAVOTTO_WORKER_PYTHON`，于是「runtime 根本没打进去」全程绿灯——空转的门禁比
  没有门禁更坏（`test_macos_ci_no_longer_fakes_a_worker_env` 看护）。
- **浏览器 playground 的运行时锁**：`packaging/playground-runtime.json`
  钉死 Pyodide 版本与包白名单（前端 JSON import + 构建脚本共读），
  细节见 `web/AGENTS.md` 的「浏览器 playground」。
