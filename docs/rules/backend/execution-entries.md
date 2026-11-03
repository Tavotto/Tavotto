# 两条执行入口：safe worker 与 native bridge（ADR 0014 / 0020）

> 原文出自 `src/tavotto/AGENTS.md`「两条执行入口：safe worker 与 native bridge（ADR 0014 / 0020）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`src/tavotto/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_worker_roundtrip.py`、`tests/test_install_dir_bytecode_free.py`、`tests/bridge/test_bridge_namespace.py`、`tests/bridge/test_bridge_thread_model.py`、`tests/test_import_architecture.py`、`tests/test_foundation_first_open.py`、`tests/test_unreadable_import_skip.py`、`tests/test_foreign_abi_path.py`

<!-- agents-route
sheet: src/tavotto/AGENTS.md
order: 180
改到: `figsession.py`、`wireproto.py`、`bridge_runner.py`、`bridgeboot.py`、`worker.py` 的装载形态
主题: 两条执行入口：safe worker 与 native bridge → `execution-entries.md`
必守要点: 编辑语义只有一份；用户的同名模块永远赢；native 侧不起后台线程
-->

**Figure 到手之后的编辑语义只有一份**（总纲原则 1）。两条入口的分工：

| | safe worker（`worker.py`） | native bridge（`bridge_runner.py`） |
|---|---|---|
| 解释器 | Tavotto 挑（`pool` 五级优先） | **用户 invocation 里那一个**，绝不静默替换 |
| cwd | 沙盒（写入边界） | **用户的原样**（继承） |
| argv | `[脚本自身]` | **用户的原样** |
| env | bundled 时 `child_env()` 清洗 | **原样继承**；只额外注入 `TAVOTTO_BRIDGE_TOKEN`，且子进程一起来就摘掉 |
| savefig | 吞掉（不写盘）+ 捕获 | **透传**（照常写文件）+ 捕获 |
| 写/删守卫 | 有 | **无**（脚本拥有用户的全部权限——文案必须与此一致，绝不声称沙盒） |
| stdout | 重定向到 stderr | **原样是用户的** |
| 控制通道 | stdin/stdout 行协议 | 127.0.0.1 loopback + 一次性 token |
| 编辑语义 | `figsession.LiveFigureSession` | **同一个** |
| 协议信封 | `wireproto`（worker v1） | **同一个**（只多一个 `continue`） |

改动纪律：

- **`figsession` / `wireproto` 是两条入口共用的。** 改它们等于同时改两条
  入口——先跑 `tests/test_worker_roundtrip.py` 与 `tests/bridge/` 两套。
- **safe worker 与 native bridge 用同一份 `bridgeboot` 装引擎模块**（issue #447 / FO19，
  ADR 0057 §四）：`worker.py` 摘掉 CPython 塞进 `sys.path[0]` 的 engine 目录、按文件路径装
  `bridgeboot`、`load_engine_modules(HERE, _ENGINE_MODULES)` 一次装完整条平铺 import 闭包进
  `tavotto_bridge_runtime.*`，顶层 `manifest` / `overrides` / … 还给用户。`_ENGINE_MODULES`
  必须等于闭包：`tests/test_runtime_build.py` 从它反推 spec 的 datas，
  `tests/test_import_architecture.py` 要求它逐条登记成 worker.py 的 `extra_edges`。
- **用户脚本的 `sys.path` 顺序（safe 与 native 同一语义）**：脚本所在目录在 `sys.path[0]`，与 `python 脚本.py`
  逐字相同；safe worker 在此之外把项目根（图库根）**追加到末尾**（site-packages 之后，
  `worker._put_project_on_path`），所以项目根里的 `paper_style.py` 之类帮手模块照样 import 得到，但项目根里恰好有
  同名文件夹（整份库源码 `celerite/`）**盖不住**环境里装好的同名发行包——没装时才退回项目根那一份。**绝不要把项目根
  `insert(0)`。** 静态侧是同一个模型，三处要一起改：`importscan._search_roots`（项目根排在标准库之后，只有项目根里的
  `random.py` 遮蔽不了标准库）、`importshadow.detect(late_roots=…)`（项目根里的同名包要有 traceback 帧证据才算遮蔽，
  且说明改为「装同名发行包能修」）、`diagdetail` 的遮挡扫描（不再列项目根）。看护 `tests/test_syspath_order.py`。
- **两条入口里都绝不能出现裸的兄弟模块 import。** engine 目录在 bridge 里是
  **临时**上 `sys.path` 的（装完就收回），用户项目里完全可能有同名的
  `manifest.py` / `overrides.py` / `config.py`。兄弟模块一律在**模块层**平铺 import
  （装载期解析，那一刻 engine 目录在 `sys.path[0]`、用户同名模块已从 `sys.modules`
  摘走），函数体内的裸兄弟 import 在用户代码之后才执行、命中的是用户的文件；新平铺
  进来的模块要登记进 `bridge_runner._PHASE2` / `bridgeboot._TOPLEVEL_TO_RESTORE`
  （`overrides._sibling(...)` 那条延后访问器 2026-09-17 随 manifest ↔ overrides 环一起删掉；
  结构性守卫：`tests/bridge/test_bridge_namespace.py::test_no_bare_sibling_import_survives_in_overrides`，
  装载清单与 `tests/import_architecture_baseline.json` 的 `extra_edges` 对拍在
  `tests/test_import_architecture.py`）。
- **装载引擎代码的那一段不写字节码**（QA REL-01-B1）：engine 目录就是安装目录（macOS 上在
  签过名的 `.app` 里），用户解释器不一路带 `-B`（会关掉用户整个进程的缓存；native 档明令不加标志；
  safe worker 只在解释器启动期带，见下一条）。所以 `worker.py` / `bridge_runner.py` 按文件装 `bridgeboot` 的那一下、
  `load_engine_modules` 的装载窗口、`projectenv._PROBE_SRC` 执行 `worker.py` 那一段各自把
  `sys.dont_write_bytecode` 打开、finally 还原；一次性的 `discover.TARGET_PARSE_ARGS` 直接带 `-B`。
  新增「用户解释器执行安装目录里的引擎代码」的入口，要在 `tests/test_install_dir_bytecode_free.py`
  加一条（它两条边都钉：安装目录零 .pyc、项目外的用户模块照常缓存）。
- **safe worker 不往用户项目目录里写字节码**（2026-09-28 Windows 实测：entry=main 按模块 import 脚本，
  项目里多出 `__pycache__/fig_data.cpython-312.pyc`，而 `python fig_data.py` 从不写它）：`worker.build`
  在第一次 import 用户代码之前装 `_suppress_project_bytecode`——`SourceFileLoader.set_data` 对落在项目根 /
  脚本目录之下的 `.pyc` 不写，**但当前解释器的包目录（`sysconfig` 的 stdlib / purelib / platlib）之下的照写**
  （项目根里的 `.venv` 是用户的环境）；只认包目录、不认整个前缀——前缀包住项目或就是项目根（`python -m venv .`）时
  项目源码照样不写。只挡项目目录、不是 `-B`：项目外的包照常缓存。native 档不装（它的契约是与 `python fig.py`
  逐字相同）。看护 `tests/test_install_dir_bytecode_free.py`（两种 entry + 项目内 `.venv` + 前缀包住项目 + venv 就在项目根）。
  **守卫在解释器启动之前就生效**（#736）：项目本来就在 PYTHONPATH 上、里面有 `sitecustomize.py`（或 `.pth` import
  项目模块）时，那些代码在 site 初始化阶段就被 import，早于 `build()`。所以非内置解释器以
  `runtime.worker_args(bundled=False)` = `-B -X tavotto_project_bytecode` 起（Python 池、workerd 规格、
  `deprepair.worker_self_test` 三条路径同一个出处）：启动期整个进程不写，worker.py 第一段
  `_startup_bytecode_guard` 看到标记就按命令行上的项目根装守卫、再把字节码写入还给用户（他自己设了
  `PYTHONDONTWRITEBYTECODE` 就保持不写）；之后 import 的包照常缓存。代价只在启动期（site 初始化时 import 的
  site-packages 小模块不写缓存）；`-B` 会经 `sys.flags` 传给用户脚本用 multiprocessing 起的子进程（它们不写字节码，
  结果不变）。内置 runtime 没有标记，`-B` 一路有效。看护同一文件的
  `test_project_code_imported_at_interpreter_startup_writes_no_bytecode`（项目里的 sitecustomize 当传感器、先证明它活着）。
- **`bridge_runner` / `bridgeboot` 启动阶段不许 import matplotlib**，
  钩子挂在 `sys.meta_path` 的后置 import 回调上。
- **native 侧不许起后台线程**：Figure 归主线程，`LiveFigureSession` 有线程
  身份断言兜底（源码判据在 `test_bridge_thread_model.py`）。
- **spike 不是产品**：`python -m tavotto.engine.bridge_spike` 没有稳定契约、
  没有接进 `tavotto` CLI，别在文档 / 官网 / release notes 里提它。
  **产品入口是 `tavotto run`**（`docs/rules/backend/tavotto-run-control-plane.md`）。

## 跳过读不了的 import 位置（ADR 0153）

**safe 与 native 同装一个查找器，这是对 native「与 `python fig.py` 一致」的有意偏离**（用户 2026-10-30 裁决，反馈包 TVD-HZ0A）：
`sys.path` 里某处「找到了模块、origin 打开却抛 `PermissionError`」时，CPython 就地失败、不往后找；用户脚本往 `sys.path` 塞了当前账户读不了的
项目外目录（别的账户建的依赖夹）时，整个 `import pandas` 就挂在上面。

- **唯一装配点** `bridgeboot.install_unreadable_import_skipper()`：`worker.py`（装好 `bridgeboot` 之后，传回调写 worker.log）与 `bridge_runner.py`
  （不写 stderr）各调一次，别另写第二份。查找器 `bridgeboot.UnreadableImportSkipper` 挂在 `sys.meta_path` **紧挨 `PathFinder` 之前**，
  不抢内置 / 冻结模块；常规路径与 `PathFinder` 那一次查找逐字相同，只多一次 `open(origin)`。
- **只在读不了时生效**：文件加载器 + origin 抛 `PermissionError` + 缓存字节码也读不了；去掉候选只在副本上做，`sys.path` / `__path__` 不动；
  全部读不了 → 返回最初的 spec，由 loader 原样抛最初的 `PermissionError`；不吞别的异常、不改写 `ImportError`。
- **留痕**：worker.log 的 `[import-skip] {json}`（safe）+ 回执 `runtime.import_skips`（两条入口；公开面只有模块名，路径只在诊断包的私有回执里，
  唯一投影 `receipt.project_import_skips`）。
- **残余失败先走一键安装，兜底才是一句话**：`bridgeboot.unreadable_folder_fact`（项目外、脚本运行期间新加进 `sys.path`、不在解释器前缀 / 包目录下，且文件是查找器登记过的「命中但读不了」的模块文件——数据文件的权限错误不算）→ 错误带
  `unreadable_folder.{folder, module}`（只有名字）→ `pool.unreadable_folder_error`：模块能映射到 PyPI 包名（`depresolve` 现有的表）且这个项目里还没装过它（记账只在安装成功提交后，`deprepair._run_install` → `pool.note_unreadable_install`，按项目区分） →
  **现有的 `missing_dependency` 错误**（一键装进受管环境，下次运行查找器跳过读不了的那一处）；`app.py` 的可信解析给不出安装名、或装过一次后同一处仍读不了 →
  稳定码 `import_folder_unreadable`（一句话卡；Windows 上主按钮是「修复权限」，见下）。AI 修复提示词 3g：装进受管环境有效、换解释器无效、不改脚本不动文件夹。
- 看护 `tests/test_unreadable_import_skip.py`（含两条入口各一条真 worker 端到端、变异：删掉装配调用即红）。
- **一键修复权限（Windows，ADR 0153 修订 2026-10-30）**：`engine/folderrepair.py`（Flask 侧，纯标准库）。完整路径只在失败事实的私有键
  `unreadable_folder_private`（worker / bridge 同一份 `bridgeboot.unreadable_folder_facts`；敏感运行的白名单不放行），
  `pool.unreadable_folder_error` 换成服务端令牌就地丢弃（**不进 `err.extra` / 公开投影 / 遥测 / 日志**）；`params.repair_token` 是前端按钮的唯一入口。
  `POST /api/engine/folder-repair {token}`：只收令牌、要 `_page_authenticated()`、路径动手前 `validate_target` 对真实路径再验（每一条拒绝一个用例）、
  提权前 `scan_for_links` 不跟随地拒绝子孙里的重解析点 / 超限，一次 UAC 提权跑 `icacls <目录> /setowner *S-1-5-32-544 /T /C /L /Q` + `icacls <目录> /reset /T /C /L /Q`（argv 由 `build_commands` 唯一给出；不用 takeown，其确认值随语言变；提权脚本内先以备份语义（SeBackupPrivilege + `FILE_FLAG_BACKUP_SEMANTICS | OPEN_REPARSE_POINT`，`folderrepair._WALK_SOURCE`）遍历整棵树再动手：201 链接 / 203 超限 / 202 遍历失败；Windows 真机手动用例 `scripts/dev/verify_folder_repair_windows.ps1`）、事后复核仍读不了算失败。
  不放宽任何一条验证；加新的拒绝条件加在 `validate_target` 并配用例。 静态关卡：脚本会改 `sys.path`（`importscan` 的 `sys_path_modified`）时，表外无可信映射的未装模块不在运行前问包名，记入 `JointPlan.deferred_unverified` 先跑一次（`depplan.plan`）。看护 `tests/test_folder_repair.py`。

## 跳过外来 ABI 的私有依赖文件夹（ADR 0154）

脚本把 `pip install --target` 出来的私有目录插到 `sys.path` 最前，里面的编译扩展是为**别的** Python 小版本编的（TVD-ZCR4：cp312 的 `.kegg_python` 对 3.14 的环境）。
换 Python 版本救不了；让那个目录对这个项目不生效，**脚本与目录都不动**。

- **识别**只有一处 `bridgeboot.foreign_abi_facts`（worker `_foreign_abi_fact` 与 bridge `_foreign_abi_extra` 共用）：异常链里有 `ImportError`、证据文件（`ImportError.path` / traceback 帧）
  落在 `sys.path` 目录 D 下、D 不在解释器包目录 / 脚本目录 / 项目根（或其祖先）之下、D 下该顶层包里有 ABI 标签不符的扩展（`abi3` 与无标签算兼容）。不解析报错文字。
  `pool.foreign_abi_error` → 稳定码 `import_folder_abi_mismatch`（先于缺依赖与读不了判）；路径只在私有键，换成 `pathignore` 令牌就地丢弃。
- **补救**：名单存项目设置 `ignored_import_paths`（`engine/pathignore`，唯一出处；项目根与祖先永不收）；`ExecutionSpec.ignored_paths` → `--ignore-paths`（`worker_argv` / `bridge_argv` 同一个参数，
  三条 spawn 路径从 `pathignore.paths_for` 取，进 argv 所以会话身份随之变）；`bridgeboot.IgnoredImportPaths` 装在读不了查找器 / `PathFinder` 之前，只管顶层导入，**只对名单里的目录生效**，
  用户的同名模块照旧赢，`sys.path` 不动。改名单 = `pathignore.remember`（令牌端点 `POST /api/engine/path-ignore` 与 AI 修复的 `ignore_import_folder` 工具共用）+ 关这个项目的会话。
- 看护 `tests/test_foreign_abi_path.py`（识别的误判面、过滤器、名单与令牌、argv 同源、两条真入口端到端；变异：不装过滤器端到端必须红）。

## 写入根绊线：MCP 来源的 safe 运行（#898，ADR 0141）

**绊线，不是沙盒。** 只对 **MCP 来源**的 safe 运行装（桥 `_spawn_worker` 传 `write_roots=allowed_roots()`）；GUI / native / CLI 完全不动。
worker 在脚本 build 期间用 `sys.addaudithook` 的 `open` 事件（及 `os.rename|remove|mkdir…`、`shutil.copy*|move|rmtree`）按**事件发生时的真实 cwd** 解开符号链接后比对白名单，
根外 → `PermissionError`；脚本吞掉它，build 结束时也以稳定码 `write_outside_roots` 失败（越界的尝试发生过）。静态读源码证不了的 `os.chdir` / `contextlib.chdir` / 跨文件 helper 切目录再写，靠它在运行期兜住。
根外 `mkdir` 的目标已是目录时，钩子直接抛 `FileExistsError`，**不执行创建系统调用**，让 `exist_ok=True` 正常完成无写入操作；检查后目录消失也不会获准创建。新目录与其他写入仍拒绝。

- **配置唯一出处** `execspec.worker_argv` 的 `--write-roots`（JSON 数组）；`safe_spec(write_roots=)` 是唯一构造入口，三条 spawn 路径（`EngineWorker` / `WorkerdWorker` / `_spawn_spec`）同源。
  绊线是 worker 的**身份**：workerd 规格哈希（`argv + env + cwd`）因 argv 不同而不同，Python 池键 / 缓存目录并入 `execspec.write_roots_key`——MCP 与 GUI 取同一脚本是两条会话。不用环境变量传。
- **白名单**：授权根（= MCP `RootAuthority` 快照里可信的根，与 `check_scope` 同一份）+ 会话沙盒与会话缓存目录 + 临时目录 + `MPLCONFIGDIR` / matplotlib 缓存与配置目录 +
  平台用户缓存目录 + seaborn / scikit-learn 数据目录（`writetripwire.infrastructure_roots`，每项带理由）。**不放行** `sys.prefix` 整体，只放行 `__pycache__` 里的 `.pyc` 形状；不放行整个用户数据目录或整个 `%LOCALAPPDATA%`。
- **窗口**：脚本 build（`arm(True)` … `finally` 里 `arm(False)`）。窗口外引擎自己的协议请求（导出到任意临时目录）不拦。
- **盲区（如实，用例断言「不挡」）**：ctypes、`subprocess` / `os.system`、C 层 `fopen`（h5py / netCDF / sqlite3 的 C 层）、`dir_fd` 相对打开、脚本自己关开关、build 窗口之外的线程 / `atexit`。**文案与回执里绝不称沙盒。**
- **回滚**：引擎能力 `write-root-tripwire`（`capabilities.FEATURES`）；去掉它桥就不传参。`write_outside_roots` **不在** `execpolicy.BLOCKED_CODES`，不触发换 native 重跑。
- 新增引擎模块 `writetripwire` 在 `worker._ENGINE_MODULES`（私有包闭包）与 `packaging/tavotto.spec`、`tests/import_architecture_baseline.json` 登记。看护 `tests/test_mcp_write_tripwire.py`。

### 改道模式：AI 转换 / 改图的校验运行（ADR 0143 修订 2026-10-30）

校验运行的 `write_roots` 由 `ai_convert.validation_roots(cand)` 给（唯一出处：登记键、探测、硬杀同一份）：候选目录 + 成对的 `redirect-project:` / `redirect-dir:` 条目
（`writetripwire.redirect_entries` / `split_roots`）。worker 见到它就在 `install(redirect=)` 里包装 Python 层写入口，**落在项目内的写**换到 `<候选目录>/redirected/<项目相对路径>`，
项目外且不在白名单的写仍 `write_outside_roots`。**MCP 来源的 `write_roots` 不含改道条目，绊线语义不变**——不要把改道扩到 MCP（那里绊线就是要失败）。
读回（打开读取与 `exists|isfile|isdir|getsize`）看改道区，追加 / 读写模式写时复制；限制与盲区（`stat|listdir|glob|Path.exists`、C 层写由校验后的项目指纹兜住）见 `writetripwire` 头注与 ADR 0143 同日修订。
候选目录指纹 `ai_figure_edit._tree` 不看根下 `redirected/`。看护 `tests/test_convert_shadow_validation.py`（真 worker）、`tests/test_write_redirect.py`。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 两条入口都经 `bridgeboot` 把引擎模块装进私有包（safe 的清单 `_ENGINE_MODULES`、native 的 `_PHASE1/_PHASE2`），用户的同名模块永远赢
- 兄弟模块只在模块层平铺 import 并登记（函数体内裸 import 会命中用户文件）
- native 侧不起后台线程
- 装载引擎代码那一段不写字节码（用户解释器不加 `-B`）
