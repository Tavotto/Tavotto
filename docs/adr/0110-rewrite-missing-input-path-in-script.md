# ADR 0110：改指表救不回的数据路径，经确认改写脚本里的那串常量

日期：2026-09-29 · 状态：**Accepted**（§用户拍板 1–5 由用户 2026-09-29 拍板）
相关：[0106 数据找不到时请用户指认](0106-missing-input-relink.md)（本 ADR 兑现它 §用户拍板 B，接在它 §四之后）、
[0094 写回脚本](0094-script-writeback.md)（§六 备份与恢复、§七 明显提示、§八 安全边界——本 ADR 先把其中**单文件**那部分
落成共享底座，0094 之后直接复用）、[0084 探路调用是首开证据](0084-probe-calls-are-first-open-evidence.md)、
[0047 在脚本目录里运行](0047-safe-profile-project-workdir.md)、[0008 会话认证](0008-unified-local-session-auth.md)、
`docs/rules/backend/ai-agent-bridge.md`（「修改前快照、之后又变过就不静默覆盖」的先例）。

## 问题

ADR 0106 的改指表只在四个打开入口（`open` / `io.open` / `Path.open` / numpy `DataSource.open`）里起作用。下面三类
救不回，#716 的对话框对它们只说「请把数据放回脚本写的位置」：

| 形状 | 例子 | 今天 |
| --- | --- | --- |
| 探路 | `if not os.path.exists("data/x.csv"): sys.exit(...)`、`glob("data/*.csv")`、`os.listdir("data")` | `no_figures_captured*`，对话框只给说明 |
| C++ 读取器 | `h5py.File("run/x.h5")`、`netCDF4.Dataset(...)`、`xr.open_dataset(...)` | **根本不弹窗**：不经打开入口 → 没有落空记账 → `missing_input_of` 回 None → 仍是 `script_error` |
| 目录常量 + 拼接 | `DATA = "/Users/a/proj/data"`，之后 `h5py.File(os.path.join(DATA, "x.h5"))` | 同上 |

C++ 读取器那一行是实测（h5py 3.x / netCDF4 / xarray，2026-09-29，throwaway 环境）——三种异常形态：

| 库 | 异常 | `filename` | 路径在哪 |
| --- | --- | --- | --- |
| netCDF4 | `FileNotFoundError` errno 2 | `'nope/x.nc'`（脚本写的原串） | `filename` |
| xarray | `FileNotFoundError` errno 2（`__context__` 是 `KeyError`） | cwd 拼出来的**绝对**路径 | `filename`，要按 cwd 换回来 |
| h5py | `FileNotFoundError` errno 2 | **None** | 只在消息里：`unable to open file: name = 'nope/x.h5'` |

## 零、与「永远不改用户脚本」的关系

与 0094 §零 同一条线：原则不变——Tavotto 为了兼容、在用户没要求时绝不动脚本。这里是**用户亲手发起、看过逐行 diff、
勾选确认**的一次改动，意图来自用户，Tavotto 只是执行者，并且能完整撤回。默认出口仍是「不改脚本」：`via = open` 的条目
走 0106 的改指表，本 ADR 的按钮只是 `via ≠ open` 条目的**次级**出口。

1.0 纪律：0094 §十三 判「写用户文件」的新入口不属于 1.0 例外。本 ADR 的范围更窄（只换数据路径常量、不改画图逻辑），
理由是兼容性（FO08：同一脚本换位置后在 Tavotto 里只能报错）；用户 2026-09-29 决定现在就建共享底座（§用户拍板 1）。

## 裁决

一句话：**对话框里 `via ≠ open` 的条目改走「改写脚本」这条出口 → 用户指认数据位置 → 服务端生成只换字符串常量的
逐行 diff 并静态自检 → 用户勾选确认 → 两处备份后原子替换 → 重跑；随时能一键复原。**

### 一、C++ 读取器也要弹窗：`via = native`

worker 在 build 失败、`missing_input_of` 回 None（没有对得上的落空只读打开）、异常链里又确实有 `FileNotFoundError` /
ENOENT 的 `OSError` 时，给 `script_error` 的 `extra` 多挂一份事实 `enoent = {filename, named, cwd}`：

- `filename`：`exc.filename`（str / bytes / PathLike 都 `fsdecode`），没有就 None；
- `named`：`filename` 为 None 时从消息里取**引号里的那一串**（`name = '…'` 优先，其次第一个 `'…'`），取不出就 None；
- `cwd`：当时的工作目录（与 `InputMisses` 同一个取法）。

归因在父进程（`inputremap.native_miss`，与 `static_missing` 同一个模块、同一份静态证据）：候选只来自脚本里**以常量出现**、
此刻哪儿都找不到的路径（`static_missing` 的全集，含下面 §二 的目录常量）。`filename`（或 `named`）先换回脚本写法那一侧
（cwd 之内的绝对路径换回相对——xarray），再与候选按路径段比：候选与它整串相等或是它的前缀（目录常量拼出来的），多条都对上
取路径段最长的那条（更具体的赢，与改指表同一条规则）。一条都对不上——不判，一字不变地仍是 `script_error`。认下的话错误码仍是 `script_error`（traceback
原样、不改分类语义），但挂上 0106 的弹窗载荷，主条目 `requested = 候选`、`via = "native"`。

顺带收紧 0106 的一处：`missing_input_of` 在 `filename` 为 None 时用「最近一条落空」——h5py 失败前若有过一次被 `try` 吞掉的
可选读（`open("local.cfg")`），会指错文件。改为先按 `named` 对账，对不上才退回最近一条。

### 二、改哪些常量（§用户拍板 2：等于或前缀）

`engine/scriptedit.py`（纯标准库）用 `tokenize` 找 STRING token、用 `ast` 判语境。一个常量成为**候选**要同时满足：

1. **值**：等于某条缺失路径 `m`，或按路径段恰好是 `m` 的前缀（`"/Users/a/proj/data"` 之于 `/Users/a/proj/data/x.h5`；
   相对的 `"data"` 之于 `data/x.csv`）；并且这个常量**自己**此刻也不存在（相对的在脚本目录与项目根都不存在）——
   存在的路径一个都不改。
2. **写法**：单个普通字符串字面量（可带 `r` / `u` 前缀、单双引号）；**不改** f-string、隐式拼接（`"a" "b"`）、bytes、
   三引号、docstring、`%` / `.format` 的格式串。
3. **语境：只改证得出只喂给读取的值**（`scriptedit._InputOnly`；Codex 评 #730 同族三次之后从「排除会流到写出的」
   换成「只认正面证据」）。从常量往外穿过拼路径的写法（`Path(…)`、`/`、`+`、`os.path.join`、路径方法）直到碰上调用：
   读取（`READ_FUNCS` / `read_*`，带模式的只认读模式）或探路（exists / listdir / glob）才算；存图 / 写出 / 写模式打开 /
   建目录不算，传进不认得的函数也不算（追不清）。赋给名字的：名字只赋值过一次、**每一处**读取都证得出（链式赋值接着追）；
   参数默认值按函数体里的每一处读取追。
   - **读写混用**（同一个 `DATA_DIR` 既读又写）：整条不进改写候选。读取那一侧由改指规则兜住——改指只影响读取，
     不会动输出；改写脚本却会把输出目的地一起换掉。
   - 追不清的（容器、下标、比较、`return`、`for`、重复赋值、属性、不认得的函数）一律不改，逐行报 `SKIP_CONTEXT`。
   - 唯一例外：用户正在处理的那一条（`requested`）整串直接做不认得的函数的实参——那一条本身就是「脚本读它失败」的证据，
     C++ 读取器（§一 `via = native`）又常经包装函数打开。
4. **改完能落地**：把它换成新值后，`m` 按同一换法落到一个存在的目标上（文件 / 文件夹；glob 至少匹配一个）。

「缺失路径」的集合是这次载荷里 `requested` 加 `others` 的全部——**包括 `via = open` 的那几条**（§用户拍板 4）。同一条推导
规则能对上的候选都放进**同一份** diff，确认一次（与 0106「按同样规律能找到的会一并修好」一致）。一个候选都没有（路径是 f-string
拼的、从命令行参数来的……）→ 409 `script_edit_nothing_to_change`，逐条说出没改的原因与行号，对话框回到「请把数据放回原处」。

### 三、新值怎么写

- **规则**：沿用 `inputremap.derive`（最长公共后缀），扩两类目标：条目是文件夹（`listdir` / `exists` 问的是目录）时按文件夹比；
  glob 条目取它不含通配符的目录前缀去推，推完要求新模式至少匹配一个文件。判据仍只有一份 `figcapture.remap_target`。
- **一律写绝对路径**，分隔符一律 `/`（Windows 上 Python 同样认，免掉反斜杠转义）。理由：探路调用不经只读回退，相对路径在
  沙盒 cwd 下仍然落空；绝对路径在 Tavotto 的三种 cwd 模式与终端 `python fig.py` 里结果相同（§用户拍板 5）。
- 保留原来的前缀与引号；非 raw 串按需转义 `\` 与引号字符；raw 串遇到新值含同种引号 → 这一处不改、进报告。
- 编码按 PEP 263（`tokenize.detect_encoding`），新值用文件自己的编码写；编码不下 → 这一处不改、进报告。

### 四、静态自检：写之前证明「只换了这几串」

改路径不是渲染等价性问题，不跑 0094 §五 的 verify 重跑；改为四条静态判据，任一不过 409、原脚本零改动：

1. 新字节 `ast.parse` 通过；
2. 新旧两棵 AST 把被换的那几个 `Constant` 的值对调回去之后 `ast.dump` 逐字相等（别的节点一个没动）；
3. 每处替换的**字节区间之外**，新旧字节逐字节相等（BOM、换行风格、混合换行、结尾无换行全部原样）；
4. §二.4 的每个目标此刻存在。

提交后界面照常重跑；如果仍然失败（比如数据文件本身坏了），新的错误照常显示，旁边多一个「恢复原脚本」。

### 五、共享底座 `engine/scriptbackup.py`（§用户拍板 1）

按 0094 §六、§八 的**单文件**部分实现，接口从一开始就按 0094 的用法设计（`kind` 区分来源）：

- **两处备份**：项目内 `tavottofile/script-backups/<脚本相对路径 slug>/<月日_时分秒>/`（路径只从 `project_layout_dir()` 取）
  + `<data_dir>/script_backups/<项目id>/<slug>/<时间戳>/`。各放 `original.py`（原字节）与 `meta.json`：`kind`（本 ADR 是
  `input_path`，0094 将是 `adjust`）、脚本相对 / 绝对路径、前后 sha256 / 大小 / mtime_ns / 权限位、编码 / 换行 / BOM、Tavotto
  版本、时间、每处改动 `{line, col, before, after}`、git 状态。任何一处写不进 → 409 `script_backup_failed`，原件未动。
- **保留**：某脚本的第一条标 `pristine`、永不自动清理；其余留最近 20 条。
- **原子替换**：复用 `atomicio.write_bytes`（同目录临时文件 → fsync → 设回原来的权限位 → `os.replace` → fsync 目录；新增的
  `mode=` 参数），孤儿临时文件走 `reap_orphan_tmps`。替换是提交点；替换前再核一次磁盘 sha256（`script_changed_since_preview`）。
- **崩溃一致性**：本 ADR 只动一份文件（不动项目文档、不删 override），所以不需要 0094 §五.7 的跨文件日志：备份在替换之前
  落盘；崩在两者之间只多一份备份，历史列表按「磁盘此刻 sha256 等不等于 meta 里的 after」标出它从未生效。0094 PR3 在这个库
  上加日志。
- **拒绝**（预览阶段就判，按钮置灰并说明）：脚本或它在项目内的任一父目录是符号链接（`script_is_symlink`）、硬链接
  `st_nlink > 1`（`script_hardlinked`）、没有写权限或只读卷（`script_readonly`）、`runtime:` 资产、同一脚本有进行中的 AI 会话
  （`script_busy`）。写哪个文件由服务端按项目 + 脚本相对路径经 `projectenv.contained_path` 决定，请求里不收绝对路径。

### 六、端点与确认令牌

| 端点 | 作用 |
| --- | --- |
| `POST /api/script-edit/input-path/preview` `{script, entry, chosen, chosen_kind}` | 推规则 → 选候选 → 生成新字节 → §四 自检；回逐行 diff（后端生成）、改了哪些 / 没改哪些及原因、两处备份位置、git 状态、同目录校验清单提示、一次性令牌 |
| `POST /api/script-edit/commit` `{token}` | 重读磁盘：脚本变了 → 409 `script_changed_since_preview`；重算一遍新字节与预览时不同（数据又挪了）→ 409 `script_edit_preview_stale`；否则备份 → 替换 → `shutdown_all(root)` + 刷新 |
| `GET /api/script-backups?script=` | 某脚本的备份历史（0094 共用） |
| `POST /api/script-backups/restore` `{backup_id, mode}` | 复原（§七） |

令牌：Flask 进程内存里，绑定 ADR 0008 的**浏览器会话 cookie**、写前 sha256、新字节 sha256，单次、10 分钟。只凭本机进程凭据
（MCP / CLI 那条请求头）的请求在预览 / 提交 / 复原上一律 403 `script_edit_needs_ui`。令牌**不出现在任何 MCP 结果里**；
AST 门禁（0094 §七.4 同一条）：`codex-plugin/`、`engine/ai_*`、`specfix`、样式与刷新代码里不许出现 `script-edit/commit` 与
`script-backups/restore`。MCP 的 `recovery` 只说「可以在 Tavotto 窗口里改写脚本里的路径」。

### 七、复原

- 磁盘此刻 sha256 == 备份的 after：先把当前版本备份成一条「复原前」，再原子替换回 `original.py`。
- 不相等（之后用户又改过脚本）：**绝不静默覆盖**。默认「只撤销这几处路径」——每处 `after` 字面量在原位置（或全文唯一一处）
  仍原样存在才可选，逐处换回 `before`，其余你的修改保留；否则只给「整份恢复到改写前（当前版本先另存一份）」。
  「可选」由后端判：备份列表每条带 `undoable`，与复原端点 `undo_edits` **同一个**判据（`scriptedit.undoable` /
  `undo_edits_of`），界面只在它为真时给「只撤销那几处」——不给点了才 `script_restore_conflict` 的按钮。
- 备份记录绑定在它所在的目录上：`meta.id` 必须就是请求的 `<slug>/<时间戳>`、`meta.script` 的 slug 必须就是目录的
  slug，`original.py` 必须是记录里的改前哈希；任一不符当不存在（`script_backup_unknown`），列表也不列——被改过的
  记录不能把一份备份写到另一个脚本上。
- 入口：改写成功后的结果区「恢复原脚本」；设置 →「环境诊断」里与改指表并列的「脚本改写备份」列表。

### 八、界面

对话框与 0106 同一个「一句话 + 一个主按钮，其余折叠」（用户 2026-09-29）：默认只有标题、一句话（只有文件名）、主按钮与
「稍后」。主条目是 `via ∈ {probe, glob, native}` 时，「可以让 Tavotto 把脚本里写的这个路径改成数据现在的位置……」与原因
一起在「详情」里；主按钮随 `probe_kind` 变——只接受文件夹的（glob、`listdir` / `iterdir` / `isdir`……）主按钮本身是
「找到这个文件夹…」，其余是「找到这个文件…」（改选文件夹在「详情」里）；浏览器模式粘贴后按钮是「预览要改的地方」，
`chosen_kind: "auto"`，
但选完不记改指、而是去预览（`others` 里的这几档在改指把 `open` 的条目救回来、重跑之后成为主条目）→ 确认页：

- 醒目的警告条「这会修改你的脚本文件」+ 完整绝对路径；逐行 diff（只渲染后端给的行）；「将改 N 处 / 没改 M 处」逐条原因；
  两处备份位置；git 状态；同目录有 `SHA256SUMS` 之类清单列着这个脚本时提示校验值会变。
- 必须勾选「我已查看以上改动，知道这会修改我的脚本」；按钮「修改脚本」不是默认焦点、回车不触发。
- 成功：关框、画布上的失败面板与素材库里的脚本各自重跑；toast 带「恢复原脚本」。失败：409 的原因 +「脚本没有被修改」。

`via = open` 的条目不走改写（§用户拍板 3），仍是 0106 的指认（主按钮「找到这个文件…」，改选文件夹在「详情」里）。

## 用户拍板（2026-09-29）

1. **底座**：B 现在就按 0094 §六 建共享的 `scriptbackup`（单文件部分），0094 之后复用；不等 0094、也不做一次性的临时实现。
2. **改写范围**：与缺失路径相等**或按路径段是其前缀**的字符串常量都改（覆盖 `DATA = "…"` 再拼接的写法）；f-string、拼接字面量、
   bytes 一律不改。
3. **覆盖面**：探路（`exists` / `glob` / `listdir`）与 C++ 读取器（§一 的 `via = native`，把弹窗扩到对得上常量的 ENOENT
   `script_error`）；`via = open` 的条目不给改写按钮。
4. **同一份 diff 连带改 `via = open` 的常量**：脚本先 `exists("data/x.csv")` 再 `read_csv("data/x.csv")` 时，只改前一处会得到
   一份半新半旧的脚本——在 Tavotto 里靠改指表碰巧能跑，在终端里仍然读旧位置。连带的几行同样逐行出现在 diff 里、同一次确认。
5. **新值一律写绝对路径**（§三 的理由）。代价：脚本换到另一台机器仍要再指认一次——但原来的写法在那里本来就失效。

## 前置修正（已在 #716 上修）

1. `inputremap._absolute_literals` 曾把脚本里**所有**绝对路径常量都标成 `via = open`，包括 `os.path.exists("/Users/a/x.csv")`
   里的那个——没出图 → 对话框给文件选择器 → 用户指认、存下规则 → 重跑时 `exists()` 不经改指表照样 False → 又弹同一个框。
   现在按常量所在的调用判（与相对路径同一张表：`PATH_PROBE_FUNCS` / `DIR_PROBE_FUNCS` / `GLOB_FUNCS` / `Path(...)` 上的方法）。
2. 0106 §一.3 说「没出图」的静态载荷覆盖「先 `exists()` 判空再自己 exit」，实际上最常见的 `sys.exit("找不到数据")` 走的是
   `script_exited`、不挂载荷。现在 `script_exited` 与「没出图」一样挂静态那部分，试运行也带出。
3. 同一个死循环的第二种写法：`DATA = "/Users/a/x.csv"` 再 `if not os.path.exists(DATA)`——常量不直接在调用里，仍被
   标成 `open`。现在只赋值过一次的名字跟到它的常量（赋值不止一次的不跟：说不清探的是哪个值）。更远的数据流
   （`os.path.join(DATA_DIR, name)` 之后再探、函数参数传进去再探）不做，那些仍按 `open` 给选择器——已知边界。

## 不做的事

* 不改 f-string / `os.path.join(…)` 拼出来的整条路径里的变量部分，不改 `sys.argv` / 配置文件 / 环境变量来的路径。
* 不在常量之外动任何一个字节；不重排 import、不格式化、不加注释标记（与 0094 的钩子块不同，这里没有「删掉整段即恢复」，
  复原只靠备份与记下的逐处改动）。
* 不自动改：没有「记住、下次自动改写」的选项；每次改写都要一份新的预览与勾选。
* 不包 C++ 读取器、不把改指扩到探路调用（0106 §不做的事 原样）。
* MCP / Codex 插件 / 编码 Agent 桥 / `tavotto run` / 浏览器 playground 不开放。

## 分阶段实施

1. **#716 上的前置修正**（上一节三条）。
2. **本 ADR 的实现，一个 PR（叠在 #716 上）**：`engine/scriptbackup.py`、`engine/scriptedit.py`、worker 的 `enoent` 事实与
   `inputremap.native_miss`、四个端点与令牌、AST 门禁、`docs/rules/backend/script-edits.md` 与速查行、错误码两种语言的文案；
   前端的确认页、设置里的备份与复原、真浏览器 e2e（功能登记 `assets.missing-input-rewrite`）。原计划前后端分两个 PR，
   但后端那一半本身就要带 `errors.json` 的文案与重新生成的 `resources.d.ts`，拆开要按 hunk 切同一个文件，合成一个。

## 看护（每条落地时做一次变异反证）

- `tests/test_script_edit.py`：候选判据（相等 / 路径段前缀 / `"dat"` 不是 `"data/x"` 的前缀 / 存在的路径不改 / 下标与字典键不改 /
  存图实参不改 / f-string、隐式拼接、bytes、三引号拒绝并报原因）；新值写法（引号、`r` 前缀、转义、Windows 盘符、编码不下）；
  §四 四条自检各自有一条「故意坏掉」的用例；字节矩阵沿用 0094 spike 的六种编码 / 换行 + 混合换行 + 结尾无换行 + BOM。
- `tests/test_script_backup.py`：两处备份任一失败 → 原件逐字节不变、无残留临时文件；pristine 永不清理、其余留 20；符号链接 /
  硬链接 / 只读目录 / 项目外路径拒绝；崩在备份与替换之间只多一份「未生效」备份；复原两种情形（未外改 / 外改后逐处撤销 / 只能整份）。
- 真 worker：`exists` 判空退出、`glob`、`h5py.File` / `netCDF4` / xarray 三种异常形态、`DATA` 目录常量拼接——各自「缺失 → 弹窗载荷
  `via` 正确 → 预览 → 提交 → 重跑出图，图的值等于那份数据的真值」；项目里放同名诱饵不影响；`filename` 为 None 且之前有一次被吞掉的
  可选读 → 仍指对 h5py 那一串。
- 端点：未认证 401；令牌跨会话 / 重放 / 超时 / 预览后脚本被改 → 拒且原件不变；`runtime:` 与 AI 忙时拒。
- 反证清单（每条必须红）：自检 2 只比 `ast.parse` 成功 → 「顺手改了别的常量」用例；替换时统一换行 → 混合换行用例；先替换后备份 →
  备份失败用例；前缀判据按字符不按路径段 → `"dat"` 用例；`native_miss` 允许对上多条 → 歧义用例；commit 不重算字节 → 预览后改脚本用例；
  复原不先备份当前版本 → 外改用例。
