# ADR 0106：脚本要读的数据找不到时，请用户指认一次，按项目记住

日期：2026-09-28 · 状态：**Accepted**（§裁决 A–D 四项由用户 2026-09-28 拍板）
相关：[0047 在脚本目录里运行](0047-safe-profile-project-workdir.md)、
[0057 首开的一次确认](0057-first-open-environment-and-workdir.md)（本 ADR 兑现它「不做的事」里留给 X01 的
「任意绝对路径的重新定位」中**用户显式指认**的那一片，以及登记用例 FO08 的 `guided` 合同）、
[0070 执行回执与数据绑定](0070-execution-receipt-and-manifest-identities.md)、
[0084 探路调用是首开证据](0084-probe-calls-are-first-open-evidence.md)、
[0094 写回脚本](0094-script-writeback.md)、[0099 脚本 input() 在界面上作答](0099-script-input-bridge.md)

## 问题

用户问（2026-09-28）：只把 `.py` 拖进 Tavotto 时，脚本引用的外部数据能不能找到？

数据还在原处时，现有机制基本够用：拖放打开的是脚本所在目录（ADR 0092，不复制脚本）；沙盒 cwd 下的
相对读由只读回退改指到脚本目录（`figcapture`，四个打开入口）；`glob` / `exists` / 只在项目根的数据由首开
确认把用户带到「在脚本目录 / 项目根运行」（ADR 0057 / 0084）。

数据**不在**原处时，产品只剩一段报错：

| 形状 | 今天 |
| --- | --- |
| 脚本被单独复制出来，`pd.read_csv("data/x.csv")` 相对路径落空 | 首开证据「一处都找不到」→ 不问（ADR 0084 §二）；运行后 `FileNotFoundError` 被 `worker.ensure_built` 一律归 `script_error`：「脚本执行失败: [Errno 2] No such file or directory: 'data/x.csv'」+ traceback |
| 脚本写死 `/Users/a/proj/data/x.csv`，数据被挪走 / 换了电脑 | 同上；而且规则明文「沙盒**之外**的绝对路径一个都不碰」（`figcapture` 模块头） |
| 脚本先 `exists()` / `glob` 判空再自己 `exit("找不到数据")` | `no_figures_captured`，界面只有脚本自己打印的那句 |

这对会写脚本、不熟文件系统的科研用户是死路：他要读懂 traceback、判断是路径问题、再去改脚本——而
**脚本永远不改**是产品承诺。登记用例 FO08（「项目移动与外部数据失联」）要的产品结果正是 `guided`：
「准确说明失联并允许重新定位；不复用旧绑定假报成功」，至今 `planned` 未兑现。

1.0 收敛纪律：这是兼容性缺口（同一脚本 `python fig.py` 在原机器能跑、换位置后 Tavotto 里只能报错），
且 FO08 属统一实施包 U03 的登记范围，不是新扩能力。

## 裁决

一句话：**认出「脚本要读的输入不存在」→ 弹窗请用户指认那个文件或它所在的文件夹 → 按项目记一张只读的
改指表 → 重跑一次。**不改脚本、不搜同名、不放松写入边界。

### 一、把「输入不存在」认出来：新错误码 `missing_input`

主语：**safe worker 进程**里、**这一次 build** 期间、**脚本（含它调用的库）发起的只读打开**。

1. **记下落空的读**：四个打开入口（`builtins.open` / `io.open` / 3.10 的 `Path.open` / numpy
   `DataSource.open`）在「只读 + 按真正会用的路径判确实不存在」时——不论回退救没救回来——
   把**原始实参**（脚本写的那一串，不是 realpath 后的）与**当时的 cwd** 记进这次 build 的
   `missed_reads`（有上限，去重）。这是记账，不改变任何读的结果。
2. **build 失败时分类**：`ensure_built` 捕获到的异常若是 `FileNotFoundError`（或 `OSError` 且
   `errno == ENOENT`），按下列顺序定「缺的是哪个」：`exc.filename` → `missed_reads` 的最后一条 →
   说不出。说得出就报 **`missing_input`**（`retryable=False`，traceback 原样带着），`extra` 带
   `requested`（脚本写的串）、`kind ∈ {relative, absolute}`、`cwd`、`script_dir`、`static_missing`
   （见 3）；说不出仍是今天的 `script_error`，一字不变。
   * **只在异常与记账能对上时才说「缺的是 X」**：`exc.filename` 为空（numpy 的 `"d.txt not found."`、
     h5py 的 C 层消息）时，用 `missed_reads` 最后一条；两者都没有就不判（判不出就别判）。
3. **静态补全**：`databinding` 已经认得的相对字面量 / 探路目标里，在脚本目录与项目根都 `missing` 的
   那些，连同脚本里**以常量出现的绝对路径**中不存在的那些，一并作为 `static_missing` 带上——让一个弹窗
   尽量一次问完，而不是「跑错 → 指认 → 再跑 → 下一个文件又错」。`no_figures_captured` 在
   `static_missing` 非空时同样挂上它（覆盖「先 `exists()` 再自己 exit」的形状）。
   绝对路径常量**只 stat、不读、不列目录**；项目外的相对目标仍按 ADR 0057 记 `outside` 不看。

### 二、弹窗：说清缺什么，请用户指认

前端对 `missing_input`（以及带 `static_missing` 的 `no_figures_captured`）弹一个对话框（与首开的
工作目录确认框同一族样式，三类入口同一个结构化载荷：桌面对话框 / MCP `recovery` / HTTP）：

> 脚本要读取 **`data/x.csv`**，在 ○○ 里没有找到。数据可能被移动了，或脚本是单独复制出来的。
> 【找到这个文件…】【选择数据所在的文件夹…】 ‹查看报错详情›

- 文件选择器由用户操作，Tavotto **不按同名搜索、不预选候选**（ADR 0057 / FO08：旁边可能有同名不同值的文件）。
- 列出 `static_missing` 里的其余路径，让用户知道这次指认会顺带修好哪些。
- 选完先显示改指规则（见 §三）让用户确认，再重跑；重跑后若仍缺（别的文件），弹窗只针对新的那一个，
  同一个 `requested` 在一次重跑链里只问一次——不打转。

### 三、改指表：从一次指认推出规则，只读，按项目记住

用户指认后，按**最长公共后缀**推出一条规则：

| 脚本要的 | 用户指认 | 推出的规则 |
| --- | --- | --- |
| `data/run1/x.csv`（相对） | `/Volumes/B/proj/data/run1/x.csv` | 相对读的额外根目录 `/Volumes/B/proj` |
| `/Users/a/proj/data/x.csv` | `/Volumes/B/proj/data/x.csv` | 前缀 `/Users/a/proj` → `/Volumes/B/proj` |
| `/Users/a/proj/x.csv` | `/Volumes/B/y.csv`（改了名） | 只改指这一个文件 |
| 指认一个文件夹 | — | 相对：该文件夹当额外根；绝对：以 `requested` 的父目录 → 该文件夹 |

规则生效的条件与今天的只读回退逐条对齐：**只读模式** + **按真正的 open 会用的那条路径判确实不存在** +
**命中规则的前缀** → 改指到规则的目标；目标也不存在就放行给原来的 open 报它本来的错。写 / 改 / 删 /
重命名一个字节都不经过这里；原路径存在时永远读原路径（数据回来了，规则自动失效）。

- **生效入口**：与只读回退相同的四个打开入口（同一个 `_fallback_path` 判据之后多查一步改指表）。
- **身份**：改指来的读照样经 `InputObserver` 按实际打开的文件记，数据身份与绑定比对（ADR 0070）自然跟着
  真文件走。改指表本身进 `ExecutionSpec` 的 `STABLE_FIELDS`（新字段 `input_remap`，与 `cwd_mode` 同一条
  理由：它改变脚本看到的世界），三条 spawn 路径（Python 池 / `_spawn_spec` / `one_shot`）从同一个出处取——
  写回的重放必须和热态读同一份数据。空表时 argv / stable payload 逐字节不变（golden 钉着）。
- **native（`tavotto run`）不改指**：那是用户自己的 `python fig.py`（ADR 0020）。浏览器 playground 不适用
  （单文件，`missing_file` 维持现状）。
- **设置界面**：设置 → 渲染环境多一节「数据位置」，列出本项目的每条规则，可删；删了就是回到报错。

### 四、C++ 读取器与探路调用：诚实说做不到的部分

ovito `import_file`、h5py / netCDF 的原生打开、`exists` / `glob` / `listdir` 不经四个打开入口，
**改指表救不回它们**（与 ADR 0047「不扩回退到 `exists` / `glob`」同一个理由：扩了只会让脚本「以为」数据在，
C++ 读取器照样读不到）。对这些形状（`requested` 来自探路证据或 `import_file`，或重跑后同一路径仍缺）：

- 相对路径 + 用户指认的文件夹在项目内：提议既有的「在脚本目录 / 项目根运行」（ADR 0047 / 0057），若指认的
  文件夹正是那一档的 cwd；
- 其余：弹窗如实写「这个脚本用 ○○ 读取数据，Tavotto 无法替它改路径」，给两条出路——手动把数据放回原处，
  或（§用户拍板 B）经用户确认改写脚本里那一处路径常量。

## 用户拍板（2026-09-28）

四项均按建议裁决：**A 放行**、**B 提供改写脚本（次级、经确认）**、**C 存本机项目设置**、**D 运行失败后才问**。
下文保留各项的理由与取舍。

**A. 绝对路径改指：放行。** 现行规则：「沙盒之外的绝对路径一个都不碰——那是用户指名的位置，就近找一个能用的
在那里是越权」。本 ADR 的改指不是「就近找」，是用户在弹窗里亲手指认、确认过规则、按项目记住、可删的。
**建议放行**，并把 `figcapture` 模块头与 `docs/rules/backend/figure-capture-and-execution.md` 那句改成
「沙盒之外的绝对路径只按用户指认的改指表改道，不猜」。不放行则第二种形状（绝对路径失联）仍只能报错。

**B. C++ 读取器 / 探路调用的出路包含「改写脚本」。** ADR 0094 已有写回脚本的事务通道。**建议**：
只作为弹窗里的次级按钮，展示逐行 diff、用户确认后才写，且只替换那一个字符串常量（拼出来的路径不改）；
默认路径仍是「不改脚本」。若否，这类脚本只给「把数据放回原处」的说明。

**C. 改指表存本机项目设置。** 规则里全是本机绝对路径，换台机器就失效。**建议**存本机项目设置
（`config.project_settings(<项目>)["input_remap"]`，与 `workdir` 同处），不进项目里的 `tavottofile/`
（不随项目包/同步走、不把本机路径带给合作者）。

**D. 运行失败后才问，不在运行前问。** 静态证据里「哪里都找不到」的路径，可以在第一次运行前就弹同一个窗。
**建议不在运行前问**：脚本可能自己先生成那个文件、或数据是可选的（`if exists(...)`），运行前问会误报；
改为运行失败后一个弹窗带上全部 `static_missing`，一次问完。

## 不做的事

* 不按文件名在磁盘上搜索、不猜候选（FO08 的「旁边有同名不同值」）。
* 不把改指扩到 `exists` / `stat` / `glob` / `listdir`，不包 C++ 读取器。
* 不改写入边界：沙盒 cwd、`Path.unlink` / `write_text` 守卫、savefig 不落盘原样。
* 不拷贝、不软链接用户数据到任何地方。
* 不对 `os.path.join` / f-string 拼出来的路径做静态判断（运行时仍经 §一.1 的记账被认出）。
* native 与浏览器 playground 不在范围内。

## 代价

- worker 四个打开入口多一次查表与一次记账（只在「确实不存在」的分支上，命中路径零额外开销）。
- `ExecutionSpec` 多一个 stable 字段：改指表一变，会话重建、准备计划作废——这是对的（数据变了）。
- 一个新错误码与一个对话框；三类入口（桌面 / MCP / HTTP）各接一处结构化载荷，同源对表加一行。

## 看护（落地时）

- `tests/test_missing_input.py`：真 worker——相对 / 绝对 / 改名 / 文件夹四种指认各跑通且输出等于真值；
  `exc.filename` 为空（numpy `loadtxt`）时靠 `missed_reads` 认出；认不出时仍是 `script_error`；原路径存在时
  不改指；写模式不改指；改指目标不存在时报原错；one_shot 重放读同一份数据；旁边放一个同名不同值的文件，
  结论不受影响（FO08 的反例）。每条用例落地时做一次变异反证。
- `tests/test_execspec.py`：空表时 argv / stable payload golden 不变；非空时进 payload。
- `web/src/components/MissingInputDialog.test.tsx`：文案、`static_missing` 列表、同一路径不重复问、英文无中文泄漏。
- FO08 从 `planned` 升 `enrolled`，证据是上面那条真 worker 用例。
