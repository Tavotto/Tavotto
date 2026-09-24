# ADR 0061：统一实施包 U04——一次准备多个依赖：无损声明、按上下文分类的需要、联合求解、代目录事务

日期：2026-09-21 · 状态：**Accepted**（U04 阶段；分三个叠栈 PR 落地，§八 记每一节落在哪个 PR；后续阶段按 §九 修订）

相关：[0019 受控依赖修复](0019-controlled-dependency-repair.md)（本 ADR 修订它的 §二 / §五 / §九 / §十）、
[0021 tavotto run 产品契约](0021-tavotto-run-product-contract.md)（§6 envlease 一张表）、[0038 设置外壳与包管理](0038-settings-shell-agents-packages.md)、
[0044 系统解释器候选](0044-system-interpreter-as-repair-candidate.md)、[0053 U01 合同](0053-foundation-contracts-and-preparation.md)、
ADR 0056 runtime_spike（uv + 不可变目录 + 指针；在 U02 的分支 `foundation/u02-spikes` 上，合入后再补链接）、[0057 U03 首开](0057-first-open-environment-and-workdir.md)；
实施包 D05 / D10 / D11 / D14，`phases/U04_dependencies.md`，registry FO-029 ~ FO-040、FO13 / FO18 / FO20 / FO21 / FO22 / FO27 / FO28 / FO29 / FO31。

## 背景

U00 实测（`U00_BASELINE.md` §1.3 / §4.3）：`depresolve` 的声明读法把 marker 整段丢掉、extras 剥掉、同名取第一条、
`constraints.txt` 根本不读。U01（ADR 0053）加了第二个读法 `DependencyIntent`，但它是手写的正则：extras 靠 `[...]`
截，marker 靠 `;` 切，`-r` 一律 unknown，pyproject 只认 PEP 621 主依赖与 optional。U03 把项目 venv 的发现前移到首次
执行之前，于是「项目里有 venv 但缺什么」第一次成了准备计划里的事实（`plan.environment.discovery.rejected`）。

缺的是四件事，registry 与 32 个首开场景各有对应：

1. **声明不是无损的**（FO-029 / FO20）：marker 要按**目标解释器**求值，extras 要跟着装，`-r` / `-c` 要在项目内跟进，
   PEP 723 / PEP 735 要认，认不出的形状（Poetry `^`、pixi 锁、URL、`-e .`）要**显式说不做**，不能剥掉 `^` 或 marker
   偷偷继续（D14）。
2. **「需要」与「声明」是两回事**（FO-031 / FO-034 / FO18）：项目声明了 30 个包，这张图的脚本可能只用 3 个；脚本 import
   的 `lab_utils` 是本地模块、`h5py` 在 `try:` 里、`typing_only` 在 `if TYPE_CHECKING:` 里——ADR 0019 §十 说得对：
   AST 扫出 14 个就装 14 个是错的。错的是**不分上下文**，不是静态扫描本身。
3. **一次授权、联合求解**（FO-032 / FO18 / FO21 / FO-033）：今天缺三个包要用户点三次，每次装一个、每次重跑脚本；
   声明之间的矛盾（`six==1.16` 与 `six==1.17`）与 worker 侧约束（matplotlib 的支持区间）也没有进同一次求解。
4. **受管环境是原地改写的**（FO-036 / FO-037 / FO28）：`<data_dir>/environments/<项目>/venv` 只有一份，装到一半
   失败 / 取消就只能标 `incomplete` 下次重建；「安装失败旧环境仍可用、未完成环境不激活」在这个布局上做不到。

## 决策

### 一、声明的无损读法交给 `packaging`；认不出的显式 `unsupported`

`packaging`（PEP 508 / 440 / 685 的参考实现，pip 与 uv 认的就是它）进 `pyproject.toml` 运行时依赖
（`packaging>=24,<27`）。它纯 Python、零依赖、`Apache-2.0 OR BSD-2-Clause`（`COMMERCIALIZATION_DEPENDENCY_AUDIT.md`
已列 GREEN；桌面 runtime 闭包里本来就有——matplotlib 依赖它），不是科学栈。**只在 `engine/depresolve.py` 里延后
import**：Flask 侧「纯标准库」那条边界（`process-boundaries.md`）指的是不把 matplotlib / numpy 拖进父进程，
`packaging` 与 Flask 自己的 Werkzeug / Jinja2 同一档——这是本 ADR 对该细则的一处明确修订，写进细则。
旧安装路径（`parse_requirement` 的窄语法、`resolve()`、单包 `create_plan`）**一个字节不用它**。

`DependencyIntent` 的 kind 由三档变四档：`requirement` / `constraint` / `unknown`（认不出）/ **`unsupported`**（认得出、
Tavotto 不替用户做）+ 闭集 `reason`（`depresolve.UNSUPPORTED_REASONS`，15 条：直接 URL / VCS、`-e`、本地路径 / 归档、
选项行、每需求选项（`--hash` 认）、include 缺失 / 越界 / 环 / 超限、Poetry 约束、管理器锁文件、TOML 解析器缺席、
TOML 坏、环境变量展开、声明文件读不了 / 超过上限）。两档都保留原文、都**不是空依赖**。

读法的范围（各自按元数据规范，不多认）：

| 来源 | 认什么 | 不认（→ unsupported） |
|---|---|---|
| `requirements*.txt`、`requirements/*.txt` | PEP 508 全形、`--hash=`、续行、注释；`-r` / `-c` **在项目根内**有界跟进（同一次 walk 最多 `MAX_DECL_FILES` 个文件；被 include 的条目归引用它的组；同一文件在**同一组、同一 kind** 下只读一次，换组 / 换成约束再 include 是另一份条目） | 选项行、`-e`、URL / 路径、`${VAR}`；include 缺失 / 越界（含软链接跳出）/ 环 / 超限各是一条 unsupported **留在原位**；读不了 / 超过 `MAX_DECL_BYTES` 的声明文件是 `unreadable`（不是空） |
| `constraints.txt`、`-c` 引到的 | 同上，`kind=constraint`（永远生效，不分组） | 同上 |
| `pyproject.toml` | `[project.dependencies]`、`[project.optional-dependencies.<名>]`、`[dependency-groups.<名>]`（PEP 735，`include-group` 展开到外层组，环 / 缺失 / 超限各一条）、`[tool.poetry.dependencies]` 里 **PEP 440 形态**的字符串（`"2.13.0"` = `==2.13.0`、`">=1,<2"`、`"~=1.4"`、`"*"`） | Poetry 的 `^` / `~` / 表 / 列表（不翻译）；3.10 且没有 `tomllib` / `tomli` → 整份记 `toml_parser_unavailable`（**不是没有依赖**） |
| 脚本自己的 PEP 723 块 | `dependencies`；组名 `pep723:<脚本>` | 多个块 / 坏 TOML → `toml_invalid` |
| `poetry.lock` / `pixi.*` / `environment.yml` / `conda-lock.yml` | 只登记存在（`manager_lock`），X01 做适配 | — |

**组**：文件组的 id 是相对项目根的路径（`requirements.txt`、`scripts/requirements-dev.txt`），pyproject 的是
`pyproject.toml:project.dependencies` / `…:optional-dependencies.<名>` / `…:dependency-groups.<名>` /
`…:tool.poetry.dependencies`。**默认选中**只有三种（`depresolve.default_group`）：任何层级的 `requirements.txt`、
pyproject 主依赖、脚本自己的 PEP 723；其余组（`requirements-*.txt`、optional、dependency-groups）要用户在项目设置
`dependency_groups` 里点名（FO20：未选的 dev / 训练组不装）。约束不分组。

`conflicts()` 改用 `SpecifierSet` 判**确定**矛盾（两个不同的精确 pin、pin 不满足另一条、下界 ≥ 上界），带理由；判不出
的不报——真正的求解交给安装器，它的 `ResolutionImpossible` 同样停在切 active 之前（FO21 两条路都是 safe_stop）。

交给安装器的每个字符串由 `requirement_string()` **从解析后的结构重新序列化**（名字 PEP 503、extras PEP 685、
specifier 规范串），原文一个字节不进 argv / 需求文件——与 ADR 0038 `argv_package_name` 的重拼是同一条纪律，
也是 ADR 0019 §三「包名语法是安全边界」在宽语法上的延续。

### 二、「需要」按 import 的上下文判（修订 ADR 0019 §十）

新模块 `engine/importscan.py`：脚本（及其本地模块，有界跟进）的每个顶级 import 分四个桶 × 六种上下文。

| 桶 | 判据 | 处置 |
|---|---|---|
| stdlib | 在**目标解释器**的 `sys.stdlib_module_names`（宿主的只是默认） | 不装 |
| local | 脚本目录或项目根下有同名 `.py` / 包目录 / 命名空间目录 / 扩展模块（`_within` 项目根） | **永远不装**（FO-031 / FO19）；跟进它 import 的东西 |
| third_party | 能经可信解析映射到 distribution：项目声明（经 curated 或同名对上）> curated | 进联合计划 |
| unknown | 都不是 | **不装、不猜同名**（FO-034）；列出来让用户指定（走既有 `user_specified`） |

| 上下文 | 例 | 跑前装？ |
|---|---|---|
| unconditional | 模块层裸 `import x`；`if __name__ == "__main__":` 体内；`with open(...)` 体内 | **是**（`needed`） |
| conditional | `if os.name == "nt":`、`for` / `while` / `match` 体内、`except` 分支 | 否 |
| deferred | `def` / `class` 体内 | 否 |
| optional | `try:` 体内、`with suppress(ImportError):` 体内 | 否 |
| type_checking | `if TYPE_CHECKING:` 体内（`else:` 是运行时那一半） | 否 |
| dynamic | `importlib.import_module(变量)` / `__import__(变量)`（字面量按普通 import） | 否 |

同一个名字多处 import 取最强的上下文；经本地模块的 import 取两处里**较弱**的（脚本在 `try:` 里 import `lab`，`lab` 无条件
import `h5py` → 对脚本来说 h5py 仍是可选）。相对 import 不是依赖。

ADR 0019 §十「不做静态扫描后批量安装」据此修订为：**不做不分上下文的批量安装**。跑前只装「无条件 import 且目标环境
没有」的第三方包，且仍然只在用户明确授权之后；其余五种上下文的缺包仍由真实的 `ModuleNotFoundError` 触发（执行权威
不变），轮次上限 `MAX_DEPENDENCY_REPAIR_ROUNDS` 不变。「打开项目不联网」（§十一）不变：跑前的判断只读本机
（目标解释器里 `importlib.metadata`），联网的只有安装、只由点击触发。

**2026-09-24 修订：import 了、却从未用到的缺包不挡图。** 用户实测：脚本第 2 行 `import sympy as smp`、全文件一次没读
`smp`，内置 runtime 没有 sympy——门要求先装 sympy，不装就在 import 那一行 `ModuleNotFoundError`，一个根本没用到的包
挡住了整张图。永远不改用户脚本，所以从产品侧解决，两半用**同一份**判据 `figcapture.unused_imports`（纯标准库，
Flask 父进程与 worker 都已加载它）：

* **判据只收 AST 能证明的**：这个模块在脚本里出现的每一处都是**起了别名**、不带点的 `import X as Y`、都不在
  `try` / `with` 里（**裸 `import X` 一律不收**：没读过的裸 import 常常是为了副作用——`import scienceplots` 之后
  `plt.style.use("science")`、`import cmocean` 注册色图；占位会把「请装 scienceplots」换成看不懂的「样式不存在」。
  起了别名 = 写的人打算用那个名字，一次没用才是遗留）；**X 在实测过的无副作用名单 `figcapture.SIDE_EFFECT_FREE_IMPORTS`
  里**（评审 #555 P1：别名同样可以只为副作用而起——`import scienceplots as _sp` / `import cmocean as cm`；「绑定没读」
  证明不了「import 没用」）；绑定名在别处**一次都不出现**（Name 的读写删、形参、global、函数 / 类 / except / match 捕获名、
  别的 import 的绑定、属性名、关键字名——宁可多判「用到了」）；X 与绑定名都不以字符串常量出现（`sys.modules["X"]`、
  `import_module("X")`、`getattr(m, "Y")`、`__all__`）；脚本里出现 `globals` / `vars` / `locals` / `eval` / `exec` /
  `compile` / `__import__` / `__dict__` 任一个就整份放弃（读不清）。只看脚本自己：本地模块也 import 了它时照旧按上下文判。
* **计划**：`importscan` 给这类名字标 `unused`，`needed` 不含它（因而不进 `missing` / `unknown`、门不问、ADR 0079 的
  用户环境发现也不为它起），`JointPlan.unused` 列出来（诊断可见，不装）。
* **执行**：safe worker 在脚本开跑前按同一份判据装 `figcapture.install_unused_import_placeholders`——包一层
  `builtins.__import__`，只在「发起者是脚本自己（globals 的 `__file__`）+ `level == 0` 无 fromlist + 名字在名单里 +
  真 import 抛的 `ModuleNotFoundError` 缺的正是 X 本身 + **import 系统确实找不到它**（`importlib.util.find_spec(X) is None`，
  评审 #555 P2：`exc.name == X` 只说明异常这么写着——项目里的同名 `sympy.py` 执行到一半自己抛
  `ModuleNotFoundError(name="sympy")` 时它找得到，已经执行的副作用不会因占位撤销；`find_spec` 自己抛错按判不清处理）」
  时回一个占位模块；占位**不进 `sys.modules`**（库里的
  `try: import X except ImportError` 照旧看到失败），读它任何非 dunder 属性抛与原来逐字相同的
  `ModuleNotFoundError: No module named 'X'`——判据若错，失败形状不变，运行后的缺包修复照旧接手。装了的包照常 import。
* **native 会话不做**：`tavotto run` 跑在用户自己的解释器里、语义就是 `python script.py`（CLI 拥有用户的 Python），
  门也只在 `pool._new_worker` 上；那条路上缺包照旧由用户自己的环境决定。
* **名单怎么来的**（评审 #555 两条 P1）：别名同样可以只为副作用而写——注册样式 / 色图（`import cmocean as cm`），
  也可以是与 matplotlib 无关的进程级改动（`import requests as _r` 改 warnings 过滤器、装 logging handler）；占位不执行
  这些，之后的行为就悄悄变了。所以进名单的判据是一份**进程级副作用快照**（唯一出处
  `tests/support/import_side_effects.py`，名单现量用例与实测脚本共用）：全新解释器 `-I` 里 import 前后比——matplotlib
  是否进 `sys.modules`、`os.environ` 整体、`warnings.filters`、logging（root 与已有 logger 的 handlers / level /
  propagate / disabled，新出现且带 handler 的 logger，`NullHandler` 也算）、`sys.path` / `meta_path` / `path_hooks`、
  全部信号处理器、`sys.excepthook` / `displayhook` / `threading.excepthook`、atexit 注册数、builtins 的名字与身份、
  `codecs.register` / `locale.setlocale` 调用与当前 locale、递归上限 / 线程切换间隔 / 存活线程数 / gc / trace /
  标准流身份 / cwd——**任何一项变了就不进**。唯一的豁免：新加的 warnings 过滤器只管这个包**自己定义的**警告类
  （包不在，这个类就不存在，谁也发不出）。
  实测（Python 3.13 / matplotlib 3.11.2，2026-09-24，16 个候选）：**只剩 sympy / tqdm / statsmodels / networkx /
  tabulate / yaml 六个**（sympy 唯一的变化是给自己的 `SymPyDeprecationWarning` 加 `once`，落在豁免里）。剔掉的：
  requests（warnings + logging handler + atexit）、astropy（warnings + logging handler）、sklearn（环境变量 + warnings +
  logging handler + meta_path + atexit）、numba / h5py / openpyxl（warnings + atexit）、joblib / netCDF4（环境变量 +
  warnings + atexit）、xarray（warnings + meta_path + atexit）、numexpr（warnings）；cmocean / scienceplots / colorcet /
  cmasher / seaborn / lmfit 还会装 matplotlib。表外的名字（含一切不认识的）照旧准备；扩名单要用同一份快照实测。
* **为什么不做「发过占位的运行失败就按缺包报」**：它兜得住样式 / 色图查不到的**响亮**失败，兜不住静默的（rcParams、
  warnings、logging 悄悄不同，图照常出来）；还要在两条控制面的错误出口各加一层归因。名单是按构造就对的那一边，1.0
  收敛期选它。代价：名单外、确实没用到的包仍会被要求安装（和修订前一样，不会更差）——名单只有六个，这个修订实际
  覆盖的是「sympy 这类纯计算库的遗留别名 import」。
* **残余风险**：快照看不见的副作用（改了别的第三方模块的全局、写磁盘、开网络连接、只在某个平台或某个版本上才有的
  import 期动作）仍是盲区；名单是**某个版本、某个平台**上的实测，将来某一版开始在 import 时改动进程状态，判据不会自己
  知道。看护用例在 worker 解释器里对名单上装了的那些用同一份快照现量一遍（装了多少量多少，一个都没装就 skip）；快照
  自己是活的由合成模块逐类钉着（每一类副作用各造一个模块必须被看见、什么都不改的必须量出空、豁免只放行自己的警告类）。
* 看护：`tests/test_unused_missing_import.py`（判据的每一条「不收」、计划、真子进程里的占位、真 worker 出图与反向用例、
  名单现量）。

### 三、联合计划：要装的、约束的、adapter 的，与四种明确停下

`engine/depplan.py` 把三个权威合成一份 `JointPlan`（不装任何东西）：

* **目标事实** `target_facts(python)`：在目标解释器里量 PEP 508 marker 环境（与 `packaging.markers.default_environment()`
  同一套字段与取法，脚本不依赖 packaging——目标解释器不一定有它）、标准库名字表、已装 distribution 及版本。
  启动条件与 worker / `probe_environment` 对齐（不带 `-I`、env 原样继承、cwd 换空目录）：`pip install --user` 装的包
  worker 看得见，事实表就得看得见。**marker 按目标环境求值**，不按 Flask 进程的 `sys.platform`（判据的主语）。
  **两份事实**（Codex #461 P1）：缺什么按**此刻会跑脚本的**解释器量（门的主语：现在起会话缺不缺）；装什么、marker、
  stdlib 按**装到哪**量（`deprepair._facts_for`：用户 venv 是同一个；受管 = active 那一代，没有就是从 base 新建的一代——
  `fresh_venv_facts(base)`：marker 环境与 stdlib 是 base 的、已装为空）。选中项目 venv 而目标受管时，新的一代要装全
  needed，不只是 venv 里缺的那几个。
* **选择** `select()`：默认组 + 用户点名的组；marker 为假的进 `skipped_marker`（不装、不算缺）；目标事实拿不到时
  marker 不求值、全部按选中（宁可多列让用户看见）；选中组里的 unsupported / unknown 与所有约束来源里的这类行进
  `unsupported`。
* **计划**：`needed` = 无条件第三方 import；`missing` = needed 里目标没有的；`satisfied` 带「装的版本满不满足声明」
  （不满足只报告，**不改**——FO-038）；`requirements` = missing 那些 distribution 的**全部**选中声明（extras / specifier
  原样；没有声明、只有 curated 映射的给裸名——不替项目决定版本）；`constraints` = 其余选中声明的 specifier + 约束文件
  （不需要的包不装，但它们的版本约束照样管住求解，pip `-c` 语义）；`adapter` = `ADAPTER_REQUIREMENTS`
  （`matplotlib>=3.8,<3.12` / `numpy>=1.24,<3`，与 `pyproject.toml` 的 `worker` extra 同源，用例钉着），**只并入受管
  环境**——并进用户 venv 会让 pip 为了满足我们的区间去动用户已装的科学栈；`possible` = 其余上下文的第三方 import
  （只列，不装）；`unknown` = 无条件 import 却映射不到的名字（只列，不装）。
* **hash 模式**：任一条选中声明带 `--hash` 就是锁文件语义——整份选中集合就是闭包，全部 `--require-hashes` 装；
  有一条没 hash → `dependency_hashes_incomplete`（pip 的判据，提前说出来）。
* **状态**闭集：`nothing_needed` / `ready` / `blocked`——**blocked 优先**：选中的声明不完整时哪怕什么都不缺也是
  blocked（`missing` 为空只说明不用装）。blocked 的理由闭集四条，每条对应用户能做的下一步，
  **都不会「剥掉认不出的那行偷偷继续」**（FO-029 / FO-033）：

  | code | 含义 | 用户的下一步 |
  |---|---|---|
  | `dependency_declaration_unsupported` | 选中的声明里有 unsupported / unknown 的行 | 改用自己的环境（项目 venv / 指定解释器）、或按提示改写那几行、或用既有的手动指定装单个包 |
  | `dependency_conflict` | 声明之间确定矛盾 | 改声明；旧环境原样保留 |
  | `dependency_hashes_incomplete` | hash 模式下有条目没 hash；或受管目标下锁没有把 adapter（matplotlib / numpy）用 `==` 钉住（载荷 `adapter`） | 补 hash 或去掉所有 hash；把科学栈也锁进去 |
  | `dependency_target_unavailable` | 目标解释器起不来 / 量不出 | 走既有的环境选择出口 |

* **身份** `identity`：只由意图决定（要装什么 / 约束什么 / 目标类型 / 目标 Python 的 minor 与平台），不含任何机器路径
  （04 §3）；受管环境的代目录按它命名（§五）。

### 四、安装器：pip 留在 U04；uv 在 U05 经同一个事务接入，不出现两套安装器（D05）

裁决：**U04 的安装器仍是 pip**——`deprepair._run_pip` 那条流式执行器、`pip_install_argv` 的 `--only-binary=:all:` /
`--no-input` / 不带 `--upgrade`、`classify_pip_failure` 的四档闭集、两道脱敏，一个字节不变。理由：U04 的目标环境
（项目 venv、以及用 `python -m venv` 从合格 base 建出来的受管环境）**都自带 pip**；为它们再下载一个 37 MB 的 uv 二进制
是把 U05 的问题提前到没有它的机器上。联合安装多的只是**一个新的 argv 出处** `pip_install_joint_argv(python, requirements_file,
constraints_file, *, require_hashes)`：`-r` / `-c` 指向**我们自己从解析结构生成**的两份临时文件（不是用户的文件；`--hash`
只能出现在需求文件里，这是 pip 的规定），其余参数与单包路径逐字相同。

U05 接私有 Python 时怎么不出现两套安装器：事务（§五）的接口是 `install(target, requirement_set) → verdict`，安装器由**目标
环境的出处**决定（受管环境 manifest 记 `provisioner`）。pbs 的 `install_only` 构建自带 pip（U02 已验证 `python -m venv`
起得来），所以 U05 的第一选择是**继续用 pip**、uv 只负责取回解释器；只有 uv 建的、没有 pip 的 venv 才登记第二个 argv
出处 `uv pip install --offline --no-index --find-links --require-hashes`（ADR 0056 那三条子命令）——两个 argv 出处之上是
**同一个**事务、同一把锁、同一条验证链、同一份账。绝不允许的形状：第二个模块自己起子进程装包。

### 五、受管环境按代：最终目录里建、`incomplete` 到验证全过、原子切 `active`、旧代留到租约释放（落地：PR B）

`managedenv` 的布局从一份 `venv/` 变成**代**：

```
<data_dir>/environments/<项目指纹>/
    environment.json           manifest（schema 仍是 1：加可选字段不升）：generations{代号: state / requirements /
                               constraints / identity / created_at / python_version / provisioner} + active（代号）
    envs/g<身份前 12 位>[-N]/   每代一个 venv；目录名 = 计划身份（§三）——同一份意图同一个目录；同身份的那一代还在册
                               （active / 旧代有人用）时加序号（`fresh_generation`），身份字段不变
    plans/<代号>/              这一代交给 pip 的 requirements.txt / constraints.txt（我们生成的，可审计）
    venv/                      旧布局那一份，作为隐式的 `legacy` 一代继续认（第一次按代时登记进 generations）
    snapshots/
```

「哪一代是 active」只有 manifest 的 `active` 字段**一处**（manifest 本身 tmp + `os.replace` 原子写，从不半写）；
ADR 0056 的 spike 用了独立的 `active.json`，这里刻意不设第二个指针文件——同一件事记两处迟早不一致。

事务（`deprepair.prepare()`，**与单包修复共用**，单包只是 delta 只有一条的特例）：

1. 拿锁：`pool.mutating_environment(key, python, shutdown=False)` → `envlease.mutating`，**只有这一张表**（ADR 0021 §6）；
   受管环境的 key 是 `tavotto_managed:<项目指纹>` + active 那一代的解释器路径（序列化同一项目的两次换代，也挡住包管理的
   原地作业并发改 active 那一代），项目 venv 的 key 是解释器路径（既有，`shutdown=True`）。两个项目各自独立（FO29）。
   有活跃 native 会话的环境拒绝开始（`environment_in_use_by_native_session`），**不杀 native**；受管环境换代
   **不收掉旧代上的 worker**（`shutdown=False`）——旧代目录不动，它们跑完自然作废；换代期间新会话按既有语义
   `environment_mutating` 拒起。
2. **在最终目录建**：`envs/g<身份>/` 直接 `python -m venv`（`create_generation_venv`），manifest 先记这一代
   `state=incomplete`（`register_generation`）。不在 tmp 里建完再 rename（venv 不可移动，[W3]）；已经存在同名目录
   （同一份意图上次建到一半、已被 `retire_unused` 注销）先删掉重来——它从没 active 过。**在册的代不会被选作目录名**
   （`fresh_generation` 加序号；`register_generation` 拒绝重新登记 active / ready 的代）：重建两次同一份账是同一个身份，
   第二次不能把 active 那代删掉重来（Codex #461 P1）。
3. 装：`pip install -r <生成的需求文件> -c <生成的约束文件> [--require-hashes]`（`pip_install_joint_argv`，唯一出处），
   需求 = 这一代的完整集合（`generation_requirements`：adapter + 账上记过的 `dist==当时版本` + 这次的 delta，delta 里的
   同名让账上那条让位；**hash 模式只有锁本身**——adapter 与账上那些给不出 hash，锁必须已经钉住 adapter，计划期校验，
   §三），约束 = 计划的约束。两份文件由 `write_plan_files` 从解析结构生成（每行过 `parse_intent` 的
   形状关、`requirement_string` 重新序列化；`--hash` 只能在需求文件里——pip 的规定）。项目 venv 目标只装 delta、只用
   项目自己的约束（§三）。
4. 验：`pip check`（依赖一致性 → `dependency_consistency_failed`）→ 关键 import（这次 needed 的每个 import 名 +
   matplotlib，一个子进程 `probe_imports` → `dependency_import_still_failed`）→ `worker_self_test`（真起一次 worker 跑通
   build → `dependency_worker_selftest_failed`）。**任一步不过就是 `incomplete`，不切 active**（FO22 / FO-037）。
   pip 的失败按既有 `classify_pip_failure` 分档，多一档 `dependency_hash_mismatch`（`--require-hashes` 不符）。
5. 切：manifest 的 `active` 原子指向新代（`managedenv.activate`）→ 装完的 freeze 快照 → 记账 →
   `projectenv.remember(trigger=dependency_repair)` → `pool.invalidate` 该脚本会话 → `depplan.reset_cache(python)`。
   这是**提交点**。
6. 收：旧代目录保留到没有 worker / native 租约再用它（`managedenv.retire_unused(in_use=…)`：`pool.safe_workers_using`
   与 `envlease.native_sessions_on` 都为零才删；active 永远不删；事务开始与提交后各试一次，删不掉的留到下次）。

四条路共用同一个 `_run_generation()`：联合准备（`prepare()`）、单包修复到受管环境（`install()`，delta 只有一条）、
重建（`rebuild_managed()`，delta 为空 = 按账重建一代）、包管理里「环境还不在」的首装（`_run_package_job`）。没有第二套
建环境 / 装包 / 验证的代码；包管理对**已有** active 那一代的原地 install / update / uninstall（ADR 0038）不变。

**用户的 venv 没有代**（我们不能克隆它）：仍是 ADR 0019 §八的原地安装——明确确认、只进不退、取消后如实说「可能已部分修改」。
这条不对称写在计划里（`modifies_user_environment`），界面上说出来。

### 六、计划绑定与有界重计划；取消的接受时刻（D11）（落地：PR B / PR C）

联合计划绑定：项目 / 脚本 / 完整需求集合（规范串）/ 约束 / hash / 目标类型 / 目标解释器指纹 / 目标事实 digest /
选中的组 / 授权（plan_id 是唯一凭据，请求体里不读任何别的字段——ADR 0019 §四不变）/ 有效期。执行前重算环境指纹与
事实 digest，不一致 → `repair_plan_stale`。**一次「准备并打开」覆盖计划里全部已知的缺口**；它不覆盖计划之后才发现的
东西：私有源、要构建的 sdist、用户在中途改了环境——这些各自以既有 code 停下。

**跑前的门**与 U03 的工作目录门同一处（`pool._new_worker` 起会话之前——`pool.SPAWN_GATES` 上登记的
`deprepair._spawn_gate`，登记而不是 import 是因为 `deprepair` import `pool`；准备接口在 `plan_for` 里同一份判据
`deprepair.gate`）：`JointPlan.status == ready` 时不起会话，抛 `dependency_preparation_required` 带计划载荷
（`preparation_offer`：计划 + 可选目标 + 轮次 + 是否已跳过；终局 `needs_input`，与 `workdir_confirmation_required`
同形；渲染端点 `_worker_error_payload`、素材库试运行 `probe._error_from_worker`、MCP `_bridge_error_from_worker`
三处同一份投影）；`blocked` / `nothing_needed` 时**放行**——blocked 的诊断挂在计划的 `dependency_preparation` 上，
脚本照跑（可能以 `missing_dependency` 收场，那时用户看到的是同一份诊断）。**门一直问到有答案**：答案是一次成功的
准备（之后计划就是 `nothing_needed`），或用户明确「不准备，直接运行」（`deprepair.skip_preparation`：
`POST /api/engine/dependencies/skip` / 授权框的那个按钮 / MCP `prepare_dependencies="skip"`；进程内按 (项目, 脚本) 记，
一次成功的准备清掉它）；没有轮次了也放行。**不**做「同一个动作第二次悄悄变成另一种行为」的「只问一次」。

公共入口：`GET /api/engine/dependencies?script=`（只读 offer + 选组 + 轮次）、`POST …/plan`（绑定；非 ready 409 +
`joint`）、`POST …/prepare`（只发 `plan_id`；进度走既有 SSE `engine.dependency`，`flow: "joint"`）、`POST …/cancel`
（`accepted / reason`）、`POST …/skip`、`PATCH /api/engine/dependencies`（选组，项目设置 `dependency_groups`）。
桌面端 `DependencyPrepareDialog`（与 `WorkdirConfirmDialog` 同形：只翻译载荷、目标默认后端算的、blocked 摆理由不装）；
MCP `tavotto_open_figure(prepare_dependencies=tavotto_managed | project_venv | skip)` 同步走同一个事务再开图。

**有界重计划**：运行后的 `missing_dependency`（条件 / 延后 / 动态 import、跟进不到的本地模块）走既有 `offer` →
`create_plan` 路，offer 多带一份按当时目标重算的 `JointPlan`（缺的那个名字并进 needed）；轮次仍是每 (项目, 脚本)
`MAX_DEPENDENCY_REPAIR_ROUNDS`，每一轮都要用户点一次、`rounds_remaining` 可见。`ValueError` / `TypeError` 等普通
异常不触发装包（`pool.should_try_project_env` 唯一判据，不变）。

**取消**的接受时刻：① 计划之后、拿锁之前（一个字节不写）；② 建 venv / pip 期间（kill pip，这一代标 `incomplete`，
目录留给下次同身份重建时清）；③ 验证期间（同②；`worker_self_test` 之后、切 active 之前再看一次事件——接受了的取消不能
照常提交）。**提交点**（切 active）之后拒绝取消（`accepted=False, reason=committed`）。
取消永远不产生假 ready、不动 active、不杀 native、不收别人的 worker。

### 七、不做的事（各有出口）

* Poetry `^` / `~` 与 pixi / conda 锁的转换、命名 Conda 环境的发现（X01）；`-e .` / 本地路径 / URL / VCS 的安装
  （X01：新环境里构建本地代码要另一档授权）；`${VAR}` 展开（pip 特性，涉及凭据，不做）。
* 不做 lockfile 级复现（ADR 0019 §九 不变）；不做 sdist 构建（`--only-binary=:all:` 不变）；不猜同名（FO-034）。
* 原生扩展的 ABI 资格（FO13）：纯 Python 包装成功不算 ABI 资格；真实二进制 wheel 的隔离证据留在 integration lane
  （台账 `planned`，理由写在 enrollment）。
* 不对项目根之外的文件求 include；不读用户 pip 配置里的 index（那是 pip 自己的事，我们只记 `custom_package_index: bool`）。

### 八、落地分三个 PR

| PR | 内容 | 本 ADR 的节 |
|---|---|---|
| A `foundation/u04-dependencies` | `packaging` 依赖；`depresolve` 无损读法与 unsupported 闭集；`importscan`；`depplan`（计划模型，不装） | §一 / §二 / §三 / §四（裁决） |
| B `…-b` | `managedenv` 代布局 + manifest `active`；`deprepair.create_joint_plan / prepare()` 事务、`pip_install_joint_argv` / `write_plan_files`、三层验证、取消（提交点后拒）、租约、退役；单包修复 / 重建 / 包管理首装走同一事务 | §五 / §六（绑定与取消） |
| C `…-c` | `pool.SPAWN_GATES` + `deprepair.gate / skip_preparation` 的门；`preparation.plan_for` 的 `dependency_preparation` / `needs_input`；`probe` 的投影；六个 HTTP 端点；前端 `DependencyPrepareDialog` + `depRepairStore.prepare`；MCP `prepare_dependencies=`；FO20 / 21 / 22 / 27 / 31 enforced（pr）、FO18 / FO05 observing（nightly，联网）、FO13 / 28 / 29 planned 写理由 | §六（门与重计划） |

### 九、后果与修订

* 加了：运行时依赖 `packaging`；模块 `engine/importscan.py`、`engine/depplan.py`；`DependencyIntent` 多 `hashes` / `reason`，
  kind 多 `unsupported`；组名带来源路径（`pyproject.toml:project.dependencies`）。
* 改了：ADR 0019 §二（import 名 → distribution 的判据多了「项目声明经 curated 对上」在 importscan 里的同一份实现）、
  §五（多一个 argv 出处）、§九（受管环境按代）、§十（按上下文而不是不做）；ADR 0053 的 DependencyIntent 由「第二个读法」
  升级为「唯一的无损读法」（旧安装路径的窄解析仍在，只服务 `user_specified` 与单包路径）。
* U05 拿走：§四的接入规则、§五的代目录与指针形状（与 ADR 0056 的 runtime 目录同一种纪律）。
* U09 拿走：`JointPlan` / 代身份进回执的语义身份（`plan_identity` 应包含 `identity`）。
