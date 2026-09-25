# 两条执行入口：safe worker 与 native bridge（ADR 0014 / 0020）

> 原文出自 `src/tavotto/AGENTS.md`「两条执行入口：safe worker 与 native bridge（ADR 0014 / 0020）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

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
  签过名的 `.app` 里），用户解释器没有 `-B`，而我们也不给它加（会关掉用户整个进程的缓存；
  native 档明令不加标志）。所以 `worker.py` / `bridge_runner.py` 按文件装 `bridgeboot` 的那一下、
  `load_engine_modules` 的装载窗口、`projectenv._PROBE_SRC` 执行 `worker.py` 那一段各自把
  `sys.dont_write_bytecode` 打开、finally 还原；一次性的 `discover.TARGET_PARSE_ARGS` 直接带 `-B`。
  新增「用户解释器执行安装目录里的引擎代码」的入口，要在 `tests/test_install_dir_bytecode_free.py`
  加一条（它两条边都钉：安装目录零 .pyc、用户模块照常缓存）。
- **`bridge_runner` / `bridgeboot` 启动阶段不许 import matplotlib**，
  钩子挂在 `sys.meta_path` 的后置 import 回调上。
- **native 侧不许起后台线程**：Figure 归主线程，`LiveFigureSession` 有线程
  身份断言兜底（源码判据在 `test_bridge_thread_model.py`）。
- **spike 不是产品**：`python -m tavotto.engine.bridge_spike` 没有稳定契约、
  没有接进 `tavotto` CLI，别在文档 / 官网 / release notes 里提它。
  **产品入口是 `tavotto run`**（`docs/rules/backend/tavotto-run-control-plane.md`）。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 两条入口都经 `bridgeboot` 把引擎模块装进私有包（safe 的清单 `_ENGINE_MODULES`、native 的 `_PHASE1/_PHASE2`），用户的同名模块永远赢
- 兄弟模块只在模块层平铺 import 并登记（函数体内裸 import 会命中用户文件）
- native 侧不起后台线程
