# ADR 0084：探路调用是首开证据——`glob` / `listdir` / `exists` 只在脚本目录找得到时，先问运行目录

日期：2026-09-26 · 状态：**Accepted**
相关：[0047 safe 档的项目级工作目录](0047-safe-profile-project-workdir.md)（「不扩回退」「不自动切换」原样成立）、
[0057 首开的一次确认](0057-first-open-environment-and-workdir.md) §三（本 ADR 给它的证据表加一类、结论表加一档）

## 问题

用户实报（2026-09-26，Tavotto Beta 0.17.0）：打开一个分析脚本，界面只显示脚本自己打印的一句
「当前目录下找不到数据文件」，而脚本同目录下确实有它要找的几个轨迹文件。用户以为「Tavotto 不懂通配符」。
（下文的文件名是保留形状的中性替身：相对 glob、带通配符与字符类，形如 `run-*-*[Ll]ongrun.traj`。）

根因不在通配符：脚本里的 `glob.glob('run-*-*[Ll]ongrun.traj')` 是**相对 cwd** 的，
safe worker 默认的 cwd 是会话沙盒（空目录），glob 回空列表，脚本按自己的逻辑 `exit()`。相对路径只读
回退只包了四个**打开**入口（`builtins.open` / `io.open` / 3.10 的 `Path.open` / numpy `DataSource.open`），
ADR 0047 明确不把它扩到 `exists` / `glob`（救不回 C++ 读取器——这个脚本接下来正是交给 ovito 的
`import_file`）。根治是「在脚本目录里运行」（ADR 0047），首开的确认（ADR 0057 §三）本来就是把用户
带到这个开关前的那一道门——但它的证据只认「像相对数据路径的字符串常量」，**glob 模式被明文排除**
（「动态的一律不算」），于是这个脚本的证据是 `none`，首开不问，沙盒里跑，一张图都不画。

同一家族（在沙盒 cwd 下同样看到空目录、同样没有证据或证据被误判）：

| 调用 | 旧证据 | 沙盒里 |
| --- | --- | --- |
| `glob.glob("x-*.csv")` / `iglob` / `Path("d").glob` / `rglob` | 不算（glob 模式） | 空列表 |
| `os.listdir()` / `os.scandir(".")` / `os.walk("runs")` / `Path().iterdir()` | 不算（没有字面量 / 不像数据路径） | 空或 `FileNotFoundError` |
| `os.path.exists("1/run.traj")` / `isfile` / `isdir` / `getsize` / `Path("x").exists()` | 字面量在脚本目录找得到 → `default_ok`（**误判**：回退救得回 `open`，救不回 `exists`） | `False` |
| `import_file("x.traj")`（ovito，C++ 读取器） | 同上，`default_ok`（误判） | 读不到 |

ADR 0047 背景里那九个 ovito 脚本（`exists` + `glob` + `import_file`）正是后两行——它们在首开时也不会被问。

## 裁决

### 一、探路调用算证据（`databinding.probe_literals`）

静态识别这几类调用、**只认字符串常量**的相对目标（绝对路径、`~`、f-string / `%` 模板、变量、
`Path(__file__)` 起算的一律不算——说不出话）：

* `path`：`exists` / `lexists` / `isfile` / `isdir` / `getsize` / `getmtime` / `stat` / `lstat` / `import_file` 的第一个实参；
  `Path(<常量>)` 上的 `exists()` / `is_file()` / `is_dir()` / `stat()` / `lstat()`；
* `dir`：`listdir` / `scandir` / `walk`（不带实参 = `.`）；`Path(<常量>).iterdir()`；
* `glob`：`glob.glob` / `glob.iglob` / `from glob import glob`（不认别的对象的 `.glob()`）；`Path(<常量>).glob` / `rglob`
  （拼成 `d/*.x` / `d/**/*.x`）；`Path.cwd()` / `Path()` 起算的等价于 `.`。

`listdir()` / `listdir(".")` / `Path().iterdir()` 列的是 cwd 本身、不指名任何东西，在哪个候选下都「存在」：
它只记在脚本目录那档（产品的默认假设，与只读回退、终端里 `python fig.py` 同一个），否则任何不在项目根上的
脚本 `listdir()` 一下就成了歧义（QA PATH-06 的形状）。其余每个目标在「脚本目录」与「项目根」下各判一次 `found` / `missing` / `outside`：路径是否存在、目录是否存在、
glob 是否至少匹配一个**项目内**的条目（`iglob(root_dir=…)`，最多看 `MAX_GLOB_MATCHES` 个匹配）。
**项目外不看**：目标（glob 取第一个通配段之前的固定前缀）按 realpath 落到项目根之外时直接记 `outside`，
不列目录、不 stat——与 `_lookup` 同一条纪律（Codex 评 #459 P1）。

### 二、结论多一档 `script_parent`，首开多一种理由 `script_dir_evidence`

探路目标只在脚本目录找得到 → `script_parent`；只在项目根 → `project_root`；两处都有 → `ambiguous`；
一处都找不到 → **不说话**，结论与没有探路调用时逐字相同。与打开类字面量的结论合并：同指一个目录
（或打开类没说话）听探路的；指向不同目录 → `ambiguous`（机器不挑）。脚本就在项目根（`same_dir`）
时两个候选是同一个目录，结论是 `script_parent`。

`workdir.decision_for` 对 `script_parent` 抛同一个 `workdir_confirmation_required`，`reason =
script_dir_evidence`、`recommended = project`（脚本所在目录）。选项的 `found` 列上探路目标（glob 模式原样），
**沙盒那档不列**——回退救不回它们，列上就是在确认框里说假话。载荷多一个 `probes` 字段（老前端忽略）。
桌面确认框、MCP 的 `recovery`、HTTP 的 `PATCH /api/engine/workdir` 都是既有的同一条路，只多一句文案。

## 不做的事

* **不扩回退到 `exists` / `glob` / `listdir`**（ADR 0047 原样）：C++ 读取器照样读不到，只会让脚本「以为」数据在。
* **不自动切到脚本目录**（ADR 0047 / 0057 原样）：那是用户项目里的写入边界，问一次、记一次；用户选「继续沙盒」
  就在沙盒里跑，盲区如实保留（`no_figures_captured` 那条既有路径仍然可见）。
* 不执行脚本、不猜拼出来的路径（`os.path.join("d", "x")`、f-string）——那时仍是 `none` / `unknown`，与今天一样。
* 探路目标**不进数据绑定修订**（`binding_for`，ADR 0070）：glob 匹配到的文件集合变了不会让准备计划作废；
  数据身份仍由执行时的输入观察器按实际打开的文件记（`InputObserver`）。
* 不处理脚本里的 `input()`：这次实报的脚本在 glob 之后还会 `input()` 要用户选文件编号——那是另一个问题
  （worker 没有交互终端），不在本 ADR。

## 代价

「宽」的代价是多问一次：`exists` / `glob` 同名的别的调用（`import_file` 若是别的库的纯 Python 读取器）会让
这个项目首开多一个确认框，选错了也不会画错数据（只是 cwd 不同）。「窄」的代价是本次实报：一张图都画不出来、
界面只有脚本自己的一句「找不到文件」。

## 看护

`tests/test_databinding.py`（探路识别的正反例表、实报形状在中文空格路径下判 `script_parent`、脚本在项目根、
`exists` 不再误判 `default_ok`、glob 匹配位置决定结论与「哪儿都没有就不说话」、与打开类字面量冲突即歧义、
项目外不列目录）；`tests/test_first_open_workdir.py`（`pool.get()` 不起进程就回 `script_dir_evidence` + 推荐
`project`、沙盒档不列探路目标；真 worker：先问 → 选沙盒仍零张图 → 选推荐档出图、savefig 不落盘）；
`tests/test_workdir_mode.py` / `tests/test_zero_capture.py` 的沙盒盲区用例改为「决定了沙盒之后」；
`tests/test_first_open_paths_d1.py` 的 PATH-06（先探路再读）从「沙盒里零张图」改为「先问 → 选沙盒仍零张图 →
选推荐档全序列 = 真值」；`web/src/components/WorkdirConfirmDialog.test.tsx`
（新理由的文案与预选）。
