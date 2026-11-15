# ADR 0188：只定义了画图函数的库模块——运行前认出来，反查调用它的入口脚本

日期：2026-11-15 · 状态：**Accepted**（用户 2026-11-15 裁决：选方案 D 并加「谁 import 了它」的反查；1.0 登记例外，见
`docs/1.0-release-readiness.md` §1.8）
相关：[0053 准备计划与回执](0053-foundation-contracts-and-preparation.md)、[0143 生成可编辑版本](0143-convert-to-editable.md)（`rasterhint` 的「说出原因、不摆 AI 修复」先例）、
[0148 智能修复路由](0148-fix-router.md)、[0184 脚本即图片](0184-script-is-figure.md)（§三 带着脚本进来的视图范围）、#1823（只复制现成图片的脚本）。

## 问题

一份用户反馈（TVD-Y5JF，Windows 0.21.2）：用户把一个代码仓库里的**函数库模块**当成画图脚本运行。这个文件只有 import、常量和
几个要传入数据的画图函数，没有任何调用、没有 `__main__`；数据准备与调用在仓库里的另一个入口脚本里，那个脚本不在项目文件夹里。

- 产品先为它装了几分钟依赖，然后运行，结局是 `no_figures_captured_silent`；
- 卡片只说「跑完了，没有出图」，智能修复推荐 `ai_fix`——AI 手上没有这些函数要的数据，只能编一份去调用它们，产出不可信的图。

跑完没图本身是正确的；错在产品既没在运行之前认出来，也没给出真正的出路（入口脚本）。

## 决定

### 一、判据：`engine/libmodule.is_library`（AST，零执行）

主语是「产品此刻会怎么跑它」。全部成立才算库模块（正面形式；判不出就不说）：

- 注册表登记的 entry 不是函数；
- 没有 `if __name__` 守卫、`discover._toplevel_reaches_plot` 为假、`discover.entry_candidates_of` 只剩兜底的 `__main__`——与产品挑入口**同一份**算法；
- 至少一个顶层函数够得着画图调用，或把画图交给同一个包里的本地模块；
- 顶层只有 docstring、import（无 `import *`）、def / class、赋值、表达式调用，以及只包着这些的 `try` 和 `if TYPE_CHECKING` / `if sys.version_info|platform`；
  每个顶层调用（含装饰器、默认值、基类）都**不**指向本文件的函数 / 类、不指向本地模块 / 相对导入的名字、最后一段不在 `PLOT_FUNCS`；
  顶层代码里**提到**本文件的函数 / 类或本地导入的名字也不算（`map(plot_one, FILES)`、`fire.Fire(plot)`、`DF.groupby(k).apply(plot_group)`、`Plotter().run()` 都是把它交出去跑）。

顶层调用按正面清单放行：标准库与常见数据包（`SAFE_PACKAGES`）的调用；画图库家族（`PLOT_FAMILY`）只认配置与常量构造（`CONFIG_CALLS`：`matplotlib.use`、
`rcParams.update`、`LinearSegmentedColormap.from_list`……，改名导入按原名判，`from matplotlib.pyplot import subplots as make_canvas` 不算）；其余第三方一律当判不出。
类体在定义时执行、没有延迟注解时注解也在定义时求值——两者照顶层同样检查。`for` / `with` / 普通 `if` / 顶层调本地函数一律不算。

### 二、反查：`engine/libmodule.find_entries`

- **范围**：从脚本目录往上找最近的仓库标记（`.git` / `pyproject.toml` / `setup.py` / `setup.cfg`，最多 8 层）；标记目录在项目根之外、不是主目录或其祖先、
  不是盘符 / 文件系统根时用它，否则只在项目根里找。用户 2026-11-15 同意往项目外**只读**源码。
- **遍历与读取**只经现有的有界入口：`discover._iter_py`（同一套剪枝与深度；符号链接 / junction 不跟进）、`scanbudget.read_regular_text(no_follow=True)`
  （占位文件不读）；遍历与每一次正向解析共用同一个 2 s 截止（每次 `importscan.scan` 都带只剩到截止为止的 `scanbudget.Budget`），最多 2000 个 `.py`、
  最多 40 次正向解析，超了 `complete=false`。
- **「import 了它」由 `importscan.scan` 的正向解析定**（包、`from pkg import mod`、跟进到的本地模块）：先用源码里出现模块名做便宜的预筛，再看目标文件在不在它读到的
  本地文件里；包里用相对导入的模块按 `python -m 包.模块` 的入口再解一次。顶层不做事的导入者（中间库模块）不是入口，沿它再追，最多 3 层；`test_*` / `conftest` 不当入口；只有按 `python -m` 才解析得到的
  （包里用相对导入调用它的）不当入口——产品的 safe worker 按脚本路径跑，推荐了会 ImportError。读不动 / 解析不了的候选让结果变成 `complete=false`。
- 排序：直接 import 的 → 在项目里的 → 有 `__main__` 守卫的 → 路径短的；最多 5 个。
- 在项目里、但只有以仓库根为搜索根才解析得到的入口（项目开在 `pkg/` 上、入口写 `from pkg.x import …`）按项目根再解一次（与前面共用同一个截止时间与解析次数），解不到或预算不够就按「打开仓库再运行」给。
- 结果按目标文件身份（路径 + mtime + 大小）与搜索根目录的 mtime 缓存 10 分钟：报告每秒都会被轮询，只有第一次付扫描的钱；在仓库根新建入口脚本会改目录 mtime，下一次检查就看得见。
- **已知局限**：仓库根没有任何标记（没有 `.git` / `pyproject.toml` / `setup.py` / `setup.cfg`）时只在项目里找，入口在项目外就找不到，卡片只说那一句。

### 三、准备会话：不阻塞的提示，不是拦截

`prepsession.requirements_of` 多一条 `{id: "entry", kind: "library_module", code: "script_is_library", blocking: false, payload: {entries, complete, scope}}`，
**不改 phase、不改 `derive` 与黄金向量、不撤任何动作**——判错的代价只是多点一下「仍然运行」，MCP / CLI 照旧能跑。没出图的终局上 `remedy` 为空（不摆 AI 修复，与 `copied_images` 同理）。

项目外入口的绝对路径只在 requirement 载荷里（本机报告，前端要用它打开那个文件夹）；不进检查项 detail、诊断包、日志与遥测。

### 四、卡片

前端 `libraryView` 排在 `ready_to_run` 的所有分支、`needsSomething` 的运行目录 / 装依赖之前，以及没出图的终局里：

- 找到入口：一句话「{{script}} 只定义了画图函数，真正画图的是 {{entry}}」（项目外的说「项目外的 {{entry}}」），主按钮「运行它」/「打开并运行」，次按钮「仍然运行」；
  其余入口与「没查全」在详情里。
- 没找到：「{{script}} 只定义了画图函数、没有调用，运行它不会出图」，主按钮仍是运行（改口「仍然运行」）；此刻没有可运行的动作时退到「知道了」，主按钮永不为空。
- 用户在这个会话里选过「仍然运行」（认领过运行意图——还在等、已执行，或因为要改他自己的环境被撤回到逐次确认；或已经跑过一次），或此刻有上一次尝试
  留下的事要他回答时，库模块卡让位给原来那张卡。可确认的依赖来源卡排在库模块卡前面；必填参数没填齐时同样先换成参数卡。
- 卡片状态不是 `ready`：「打开并运行」入口不会代用户认领，也就不会先为它装依赖。
- 「运行它」：项目里的入口走当前项目的「打开并运行」；项目外的先打开仓库文件夹（带着这个脚本进来，ADR 0184 §三）再「打开并运行」，与拖进项目外脚本时
  「打开它所在的项目」同一条路，点按钮本身就是确认（`store/libraryEntryRun`）。

## 不做

- 不自动替用户改跑入口脚本（静默换一个用户没选的脚本，等于替他运行另一份代码）。
- 不为库模块构造数据去调用其中的函数。
- 不往主目录、盘符根之上找，不跟符号链接。

## 看护

`tests/test_libmodule.py`（判定逐种写法、反查的仓库根 / 项目内 / 经中间模块 / 只提名字不算 / 测试文件 / 无标记 / 主目录 / 项目内标记 / 符号链接 / 预算）、
`tests/test_preparation_session.py::test_a_plot_helper_module_gets_a_non_blocking_hint_naming_the_entry_script`（零 build、detail 不带路径、没出图不摆 AI）、
`web/src/lib/preparationText.test.ts`（排在装依赖之前、不是 `ready`、没出图同一张卡、用户选了「仍然运行」后让位给要他回答的卡、主按钮永不为空）、
`web/src/store/libraryEntryRun.test.ts`、`web/e2e/library-module.spec.ts`（真后端：项目外入口打开仓库并只运行一次）。
