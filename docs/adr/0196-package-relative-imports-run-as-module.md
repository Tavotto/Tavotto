# ADR 0196：包里的脚本用相对导入时，按包里的模块来运行（等同 `python -m 包.子包.脚本`）

日期：2026-11-19 · 状态：**Accepted**（维护者 2026-11-19 裁决）
相关：[0014 safe / native 执行档](0014-safe-native-execution-profiles.md)、[0020 native bridge](0020-native-matplotlib-bridge.md)、[0127 执行策略与回退](0127-execution-policy-and-unified-retry-guard.md)、
[0188 库模块入口](0188-library-module-entry.md)（§二「只有按 `python -m` 才解析得到的不当入口」的前提随本 ADR 改变，见「后续」）；
规则全文：`docs/rules/backend/execution-entries.md`「包里的脚本按模块运行（ADR 0196）」。

## 问题

用户的项目里有一个包：`plots/__init__.py`、`plots/helpers.py`、`plots/fig.py`（`from .helpers import tidy`）。直接当脚本跑
`python plots/fig.py` 必然 `ImportError: attempted relative import with no known parent package`；按 `python -m plots.fig`
（工作目录在包的上一级）就能跑。Tavotto 以前同样失败（`script_error`），内置 AI 在评测里把它误判成产品缺陷。

## 决定

### 一、判据：`engine/pkgentry.resolve`（唯一出处，AST 零执行，判不出就不改）

全部成立才按模块运行：

1. 脚本目标的登记入口是 `__main__`（桌面 native 由准备计划传入登记入口，函数入口同样不改；回退路径不知道登记入口时按整份脚本算）——函数入口走 `import_module`，不在此列；
2. 脚本顶层的**直接语句**里有相对导入（`ImportFrom.level > 0`）；嵌在 `try` / `if` / `def` 里的不算（`try: from .h import x / except ImportError: from h import x`
   今天能跑，按模块跑会丢掉脚本目录这个 `sys.path[0]`）；所有顶层相对导入的 `level` 都不超过包的层数；
3. 从脚本所在目录往上每一级都有 `__init__.py`；包根 = 最上面那个带 `__init__.py` 的目录；包根的上一级（= `sys.path[0]`）仍在项目根之内
   （项目文件夹本身就是包 = 越界，不改）；
4. 路径上没有符号链接 / junction，包名与脚本 stem 都是合法标识符（`my-plots/`、`fig.v2.py` 不改），stem 不是 `__init__` / `__main__`；
5. 命名空间包（没有 `__init__.py`）、读不了、语法错误、影子脚本（生成可编辑版本的校验运行）一律不改。

### 二、执行：两条入口同一个语义

`__name__ == "__main__"`、`__package__` 正确、`sys.path[0]` 是包根的上一级，与 `python -m` 一致；**cwd 规则不变**（safe 沙盒 / 桌面的脚本目录或项目根）。

- native bridge：身份参数仍是 `--target-kind script --target <脚本>`（描述符 / 资产 id / 指纹从一开始就不变），另加 `--as-module`，执行那一步复用现成的 `runpy.run_module`，`sys.path[0]` 由脚本路径往上数层得出；
- safe worker：`--as-module`，在原来 `runpy.run_path` 的位置调 `runpy.run_module(alter_sys=True)`，所有守卫（savefig 拦截、写入绊线、字节码不写项目目录、输入观察……）
  照旧；入口脚本的字节只从源码编译并记摘要（`_capture_entry_sha`，不读 `.pyc`）；`sys.path` 是 包根的上一级 + 项目根（追加在末尾，与以前一致）；
  子进程里再核一遍「模块名与脚本路径对得上、顶层包名没被别的模块占用」，对不上就照旧按脚本运行，绝不猜；
- 子进程只收模块名（worker 与 bridge 同名参数 `--as-module`）；`sys.path[0]` 由脚本路径推（`bridgeboot.package_path_root`，两侧同一个）。

### 三、身份不变（硬要求）

按模块运行**不是**新的目标：`ExecutionSpec` 的 `target_kind` / `target`（脚本 + 项目相对路径）与 `stable_payload()` 一字不变，只多一个正交字段 `module_name`
（不进稳定载荷）。所以描述符、资产 id（`runtime:plots/fig.py#fig`）、修订、注册表条目与「不按模块运行」时完全一致，面板不换身份。

### 四、范围

装配点在 `execspec.safe_spec`（safe）与 `execspec.desktop_native_spec`（桌面拥有的 native）两个构造函数里，所以三条 spawn 路径、桌面准备会话（`preparation.plan_for`）、
MCP 打开图、Safe→Native 回退同源。**终端 `tavotto run fig.py` 不改**（那是用户自己敲的命令；`native_spec` 结构上拿不到 `module_name`，`ExecutionSpec` 构造时拒绝）。

## 为什么这不违背「与 `python fig.py` 一致」

那条原则的意思是「别让脚本在 Tavotto 里的行为与用户自己在终端敲的命令不一样」。本改动只在 `python fig.py` **本来就必然失败**（顶层直接语句里的相对导入、没有父包）的形状上生效：
原样跑只会 `ImportError`，没有任何「一致」可言；改成按模块运行后得到的正是用户本该敲的那条命令（`python -m 包.模块`）的结果。凡是原样跑得通的写法
（相对导入包在 `try` / 函数里、没有相对导入、函数入口）判据一律不触发，行为一字不变。

## 不做

- 不改用户脚本、不替用户加 `__init__.py`、不猜命名空间包；
- 不放松任何守卫；`sys.path[0]` 不越出项目根；
- 不改终端 `tavotto run`；不改函数入口（`import_module`）的跑法。

## 看护

`tests/test_package_entry.py`（判据逐种形状 + spec / argv 装配 + 终端不带）、`tests/test_package_entry_parity.py`（真 `python -m` / bridge / safe worker 逐字段对拍；safe 不写 `__pycache__`、
savefig 仍被拦；普通子目录脚本 argv 逐字节不变）、`tests/test_package_entry_e2e.py`（真 sidecar：`completed`、图身份是 `plots/fig.py`、原文件不变；safe 被拦后回退 native 也按模块运行；命名空间包照旧报错）。

## 后续（本 ADR 不做）

- 0188 §二：`libmodule.find_entries` 把「只有按 `python -m` 才解析得到的（包里用相对导入调用它的）」排除出入口，理由是「safe worker 按脚本路径跑、推荐了会 ImportError」。
  本 ADR 之后这类入口在 safe / 桌面 native 下能跑，该前提需要另行复核；
- 静态依赖模型（`importscan`）仍把脚本当「脚本目录在 `sys.path[0]`」解析；按模块运行时 `sys.path[0]` 是包根的上一级，两者对包内相对导入无差别，
  但包内的**绝对**同级导入（`import helpers`）会随之失效（与 `python -m` 一致）。
