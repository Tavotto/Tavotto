# ADR 0154：脚本自带的私有依赖文件夹是为别的 Python 版本装的——一键「跳过这个文件夹」

日期：2026-10-31 · 状态：**Proposed**（反馈包 TVD-ZCR4，0.19.2 Windows；落地需维护者确认）
相关：[0153 跳过读不了的 import 位置](0153-skip-unreadable-import-locations.md)（同一类「脚本自己塞进 `sys.path` 的目录」）、
[0014 safe / native 执行档](0014-safe-native-execution-profiles.md)、[0149 修复工具箱](0149-fix-toolbox.md)；
规则全文 `docs/rules/backend/execution-entries.md`「跳过外来 ABI 的私有依赖文件夹」。

## 动机

脚本开头把项目里的 `.kegg_python`（一个 `pip install --target` 出来的私有依赖目录，里面是 **cp312** 编译的 matplotlib 等）插到
`sys.path` 最前；Tavotto 选中的是受管环境 Python 3.14，环境里 matplotlib 完好。导入被那个目录盖住：`matplotlib/__init__.py` 里
`from . import _c_internal_utils` 找不到 `_c_internal_utils.cp312-win_amd64.pyd` 对 3.14 可用的版本，以 `ImportError` 收场。
AI 修复会话两轮都去 `change_python` 要 3.12（私有 Python 锁里只有一个版本）→ `python_change_unavailable` → 回滚。方向错：
只要这个文件夹对这个项目不生效就能出图。**用户的脚本和那个文件夹都不改。**

## 决定

### A1. 识别：以文件系统事实为主

`bridgeboot.foreign_abi_facts(exc, …)`（safe worker 与 native bridge 共用，纯标准库）。全部成立才算：

1. 异常链里有 `ImportError`（含 `ModuleNotFoundError`）；
2. 链上的文件（`ImportError.path` 与各层 traceback 的文件，内层优先）落在某个 `sys.path` 目录 D 之下；
3. D 不在解释器自己的前缀 / stdlib / 包目录之下，也不是脚本所在目录、项目根或它们的祖先（那是用户自己的模块，忽略它们会让用户的同名模块失效）；
4. D 下该文件所属的**顶层包**（或单文件扩展）里有 ABI 标签与**当前解释器**不符的编译扩展：`.cpXY-*.pyd` / `.cpython-XY-*.so`。
   `abi3`、无标签的 `.so` / `.pyd`、等于当前小版本的一律算兼容。

不解析英文报错文字；证据是 D 里的文件名。失败码 `import_folder_abi_mismatch`（事实：显示名——项目内是相对项目根的 POSIX 路径，项目外只有目录名——
与 D 里扩展的版本、当前版本）。完整路径只在私有键 `foreign_abi_folder_private`，`pool.foreign_abi_error` 把它换成服务端令牌就地丢弃，
不进 `err.extra` / 公开投影 / 遥测 / 日志；敏感运行的白名单不放行这两个键。

### A2. 补救：按项目记住的忽略名单 + 同一个查找器

* 名单存项目设置 `ignored_import_paths`（`engine/pathignore`，与 `workdir` / `input_remap` 同处）。项目里的存相对路径，项目外的存绝对路径。
  项目根与它的祖先永远不收。
* 经 `ExecutionSpec.ignored_paths` → `--ignore-paths`（`worker_argv` 与 `bridge_argv` 同一个参数）交给 worker / bridge；它进 argv，
  所以 workerd 的 spawn 规格哈希、池里的热会话都跟着变（改名单即 `shutdown_all` 这个项目的会话）。
* `bridgeboot.IgnoredImportPaths`（`install_ignored_import_paths`，两条入口各调一次）挂在 `sys.meta_path`
  **紧挨读不了查找器（没有就是 `PathFinder`）之前**——内置 / 冻结模块的优先级不变。只管顶层导入（`path is None`；
  子模块随包的 `__path__`，自然不进 D）：被忽略的目录里没有这个名字则不接手（常规路径一字不变）；有则用去掉这些目录的 `sys.path` 副本找，
  找不到抛 `ModuleNotFoundError`（不能返回 None，否则后面的 `PathFinder` 会把 D 再翻一遍）。`sys.path` 本身不改；用户的同名模块（不在名单里）照旧赢。
* 留痕：worker.log 的 `[import-ignore] {json}`（safe）；native 不写 stderr。

与 ADR 0153 一样，这是对 native「与 `python fig.py` 一致」的有意偏离，且只在名单里的目录生效、只在用户点了按钮之后生效。
`tavotto run`（terminal 拥有的 CLI）不读名单：那是用户自己敲的命令。

### A3. 卡片

失败卡一句话：「脚本自带的“{folder}”文件夹是为 Python {abi} 装的，和当前环境（Python {current}）不兼容」，主按钮「跳过这个文件夹重跑」。
按钮只把不透明令牌交给 `POST /api/engine/path-ignore`（要 `_page_authenticated()`、令牌绑项目、一次性、会过期），成功后沿用「再试一次」同一个动作重跑。
不摆「使用你的 AI 来修复」：换 Python 版本救不了。

### B. AI 修复不再走错方向

修复会话的运行报告带同一条失败码与事实；系统提示 3h：导入来自脚本自己插进 `sys.path` 的私有目录且 ABI 不符时**不要换 Python 版本**，
用 `ignore_import_folder` 工具（`tavotto` 档，走与令牌端点相同的 `pathignore.remember`）；`pyversionfix` 的失败码闭集不含新码，
不会因它尝试换版本。

## 否决的备选

* 在 `sys.path` 上直接删除 D：改了用户进程里的 `sys.path`，脚本之后的 `sys.path` 读写会与终端里不同；查找器只改「怎么找」。
* 自动忽略、不问用户：这是对 native 一致性的偏离，必须是用户点过的决定，可逆（`pathignore.forget`）。
* 换成 D 的 ABI 对应的 Python：私有 Python 锁里只有一个版本，且用户的环境是他选的。
