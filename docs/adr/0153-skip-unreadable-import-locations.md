# ADR 0153：import 时跳过「找到了但读不了」的位置（safe 与 native 同装），并把残余失败说成一句话

日期：2026-10-30 · 状态：**Accepted**（用户 2026-10-30 裁决 A + B；反馈包 TVD-HZ0A，0.19.1 Windows）
相关：[0014 safe / native 执行档](0014-safe-native-execution-profiles.md)、[0020 native matplotlib bridge](0020-native-matplotlib-bridge.md)、
[0057 首开环境与工作目录](0057-first-open-environment-and-workdir.md)；规则全文 `docs/rules/backend/execution-entries.md`「跳过读不了的 import 位置」。

## 动机

用户脚本从脚本目录逐级往上找 `.work_sensor_plot_deps_<日期>`，找到就 `sys.path.insert(0, dep)`。这个文件夹在项目外面，是 Codex
沙箱账户建的，当前 Windows 账户没有读权限。`import pandas` 时要去 dep 里找 `tzdata`，读 `tzdata\__init__.py` 抛
`PermissionError: [Errno 13]`；同一个 worker 里第二次运行又报 `partially initialized module 'pandas'`。

CPython 的行为是：`sys.path` 某处**找到了**模块、却读不了它，就地失败，**不会接着往后找**（mac 上 `chmod 000` 可复现）。
AI 修复换了两代受管环境，一点用都没有——换环境改变不了这个脚本自己放进 `sys.path` 的目录。用户的脚本永远不改。

## 决定

### A. 查找器：只在「读不了」时换下一个位置

`bridgeboot.UnreadableImportSkipper`（纯标准库、3.10 可跑）挂在 `sys.meta_path` 里**紧挨 `PathFinder` 之前**（`BuiltinImporter` /
`FrozenImporter` 的优先级不变）。它替 `PathFinder` 做**同一次**查找：

* 命中的 spec 是文件加载器（源码 / 无源码字节码 / 扩展）、origin 打开抛 `PermissionError`、且它的缓存字节码也读不了
  （能读到有效缓存时 CPython 本来就能加载，不算读不了）→ 在候选的**副本**里去掉命中的那一条（包、单文件模块、子包都认；
  相对路径与大小写按 `normcase(abspath)` 比较），继续找；
* 后面找到了可读的 → 返回它，并记一条事件；
* 后面没有可读的了（或认不出命中的是哪一条）→ 返回**最初**命中的 spec，loader 原样抛最初的 `PermissionError`；
* 其余一切（能读的路径、找不到的模块、命名空间包、别的异常、`ImportError`）一字不变，不吞不改写；`sys.path` / `__path__` 本身从不修改。

装配点**只有一个**：`bridgeboot.install_unreadable_import_skipper()`。safe worker（`worker.py` 装好 `bridgeboot` 之后）与
native bridge（`bridge_runner.py` 同位置）各调用一次；`worker.py` 另传一个回调往 worker.log 写 `[import-skip] {json}`，native 不写 stderr
（那是用户的终端）。

**这是对 native「与 `python fig.py` 一致」契约的有意偏离**：`python fig.py` 在同样的机器上会失败。偏离只发生在「本来必然失败」
的那一刻，且换来的结果是脚本原来想要的那个模块——不会出现 `python fig.py` 成功而 Tavotto 失败的反向分歧，也不会把能读的位置换成别的。

### 留痕

每次成功跳过记 `{module, entry, error}`（同一模块同一位置一次，最多 64 条）：

* worker.log：`[import-skip] …`（safe；路径由诊断包统一脱敏）；
* 回执 `runtime.import_skips`（safe 与 native，经 `figsession.runtime_report(import_skips=…)`）。`receipt.project_import_skips` 是它进入
  公开面的唯一一处：公开只有模块名（点分标识符）与 `PermissionError`；被跳过的位置（机器路径）只在 `include_private`（诊断包）里。

### B. 跳过之后仍失败：失败卡点名，AI 修复不再换环境

worker / bridge 在脚本失败时判一次（`bridgeboot.unreadable_folder_fact`）：异常链里有带文件名的 `PermissionError`（errno 13）、
文件落在某个**脚本运行期间新加进 `sys.path`** 的目录下、该目录在项目根之外（且不包住项目）、也不在解释器自己的前缀 / 包目录里
（那种读不了换环境才可能有用）。成立则随错误带 `unreadable_folder: {folder: 目录名}`（只有名字，不带路径；敏感运行里这个键过不了白名单）。

父进程在错误收口（`pool._error_of` / `pool._worker_error`，native 在 `app.py` 的脚本错误出口，同一个 `pool.unreadable_folder_error`）
把它转成稳定码 `import_folder_unreadable`，**先于缺依赖判**；失败卡走既有的 `errors:backend.<code>`：默认只露一句话
（「脚本加载的项目外文件夹“X”读不了」+ 该做什么），主按钮沿用现有的。AI 修复：`fixsession.SYSTEM_PROMPT` 的 3g 一条写明这类失败
换环境 / 装包无效，运行报告的 `error` 带上 `code` 与 `params`。

## 后果

* 少数「本来就该失败」的 import 现在会成功——前提是后面确有另一个可读位置；读的永远是同一个模块名下 `sys.path` 顺序里下一个可读者。
* 每个未缓存的 import 多一次 `open(origin)`；热路径实测见 `tests/test_unreadable_import_skip.py::test_overhead_is_small`。
* 无新增遥测、无新增端点、无存储格式变化；回滚 = 删掉两处装配调用。
