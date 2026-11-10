# ADR 0179：`.ipynb` 抽成同名 `.py` 再打开——确定性抽取，不经 AI、不联网，笔记本一个字节不动

- 状态：已接受（2026-11-10，产品负责人裁决；1.0 豁免见 `docs/1.0-release-readiness.md` §1.8，原话「豁免，都开始做」）
- 相关：[0176 拖 / 粘贴文件进画布](0176-canvas-file-intake.md)（入口位置与失败出口）、[0092 主页拖放](0092-home-native-file-drop.md)（壳 `drop_paths::classify` 与同源对）、
  [0143 生成可编辑版本](0143-convert-to-editable.md)（R / MATLAB 入口：**那是 AI 转写，和本 ADR 不是一回事**，只借它的入口位置与同源对做法）、[0008 会话认证](0008-unified-local-session-auth.md)、
  `docs/rules/repo/same-origin-pairs.md`

## 背景

产品负责人 2026-11-10 的裁决：用户都是小白，**任何失败出口只能落三种结局**——① 静默做完并说一句；② 一句话 + 一个主按钮，按钮直接完成用户原来想做的事；③ 真做不了，主按钮「发送问题反馈」。

科研用户大量用 Jupyter。此前把 `.ipynb` 拖进主页只得到「打不开」，拖进画布只得到「放不进画布」；项目文件夹里只有 `.ipynb` 没有 `.py` 时，素材库的脚本区是空的。

## 决定

**`.ipynb` 进来 → 确定性地抽成一份同名 `.py` 放在它旁边 → 像普通脚本一样「打开并运行」。** 不经 AI、不联网、不执行笔记本里的任何东西；只读笔记本的 JSON，只写它旁边的一个 `.py`。

### 一、终态表（每个入口、每种结果 → 结局）

| 入口 / 情形 | 结局类 | 做什么、说什么 |
| --- | --- | --- |
| 脚本库「Jupyter 笔记本」组里一行，点「转成脚本并运行」 | ① | 后端抽成同名 `.py` → 走现有「打开并运行」；说「已从「x.ipynb」生成「x.py」，正在运行它」。行消失（见 §四），`.py` 成为上面的普通脚本 |
| 拖进画布：项目里的笔记本（拿到真实路径） | ① | 同上；图放进**这张**画布、落在放下的位置（与项目里的 `.py` 同一个意图 `placeIn`） |
| 拖进画布：项目外的笔记本（拿到路径） | ② | 「「x.ipynb」不在当前项目里，还没有运行。」主按钮「打开它所在的项目」：打开其所在文件夹，接着抽取并运行（与项目外 `.py` 同一个主按钮、同一条 `openItsProject`） |
| 拖进画布：项目外的笔记本，主按钮打开项目失败 | ③ | 「没能打开「x」所在的项目。」+ 发送问题反馈（沿用 0176） |
| 拖进画布：拿不到路径（浏览器；桌面壳这次没交来路径） | ① | 页面读 `File` 的内容上传（`POST /api/notebook/import`），后端抽成**当前项目根**里的 `.py`（笔记本本身不存进项目）并运行；不弹选择器、不再说「放不进画布」 |
| 拖进画布：没有项目（playground / MCP 画布） | ③（无按钮） | 不发请求；「「x」没能转成脚本。这里没有项目文件夹可放。」（那里没有反馈入口，不画按钮） |
| 拖进主页：壳交来真实路径 | ① | 打开其所在文件夹为项目，接着抽取并运行（与 `.py` 同一条「打开文件夹 → 运行」） |
| 拖进主页：页面带 `file://` 路径 | ① | 同上 |
| 拖进主页：拿不到路径（浏览器；壳没交来） | ② | 与 `.py` / R·MATLAB 同一条：说「系统没有告诉 Tavotto「x.ipynb」在哪个文件夹」，桌面弹原生选择器（现在也认 `.ipynb`），浏览器弹目录浏览器选它所在的文件夹（核「这个文件夹里确有这个文件」，选错一句话说没找到）；选中后打开并抽取运行 |
| 拖进主页：文件夹没打开成功 | —— | 沿用主页既有行为（项目没打开就什么都不接着做） |
| 一次拖多个：`.py` 与 `.ipynb` 同在 | ① | **`.py` 优先**；`.ipynb` 这次不处理，说「其余 N 个文件这次没有处理」 |
| 一次拖多个笔记本 | ① | 处理第一个，同上说一声 |
| 抽取失败：不是笔记本 / 读不出 / 不是 JSON（`notebook_unreadable`） | ③ | 「「x.ipynb」没能转成脚本。这个文件不是能打开的笔记本。」+ 发送问题反馈 |
| 抽取失败：版本太旧（`notebook_unsupported_version`，nbformat 3 及更早） | ③ | 「…这是很旧版本的笔记本，暂时转不了。」+ 发送问题反馈 |
| 抽取失败：没有可运行的代码（`notebook_no_code`） | ③ | 「…这个笔记本里没有可以运行的代码。」+ 发送问题反馈 |
| 抽取失败：语法核验不过（`notebook_syntax_error`，参数 `cell` / `line`，不含内容） | ③ | 「…笔记本里第 N 段代码有看不懂的写法，转成脚本后运行不了。」+ 发送问题反馈；一个字节不落盘 |
| 抽取失败：太大（`notebook_too_large`，上限 50 MiB）/ 空文件 / 找不到 / 路径在项目外 / 不是 `.ipynb` | ③ | 各自一句原因 + 发送问题反馈 |
| 写不进去：只读 / 无权限 / 磁盘满 / 其它写失败 / 候选名用尽 | ③ | 复用 `asset_import_not_writable` / `_disk_full` / `_write_failed` / `_name_exhausted` 的原句 + 发送问题反馈；项目里一个字节不留 |
| 笔记本之前转过、`.py` 还在且内容相同 | ① | 复用，不重写；照样运行，说同一句 |
| 笔记本更新了、之前生成的 `.py` 没被人改过 | ① | 原子替换那份生成的 `.py`（见 §三规则 3），照样运行 |
| 笔记本更新了、之前生成的 `.py` 被改过 / 有用户自己的同名 `.py` | ① | 另存 `名 (notebook).py`，不碰那份；说的那句里写的就是新文件名 |
| 抽取 / 上传期间换了项目 | —— | 整件作废，什么都不运行、不说（A 项目的响应绝不落进 B） |
| 反馈入口被关 | —— | 句子照说，不画按钮（沿用 0176） |

### 二、抽取规则（`engine/notebookscript.extract`，纯函数，逐条）

1. **只认 nbformat 4**：不是 UTF-8 / 不是 JSON / 顶层不是对象 / 没有整数 `nbformat` / 没有 `cells` 列表 → `notebook_unreadable`；`nbformat != 4`（3 及更早有 `worksheets`、没有顶层 `cells`；5 还不存在）→ `notebook_unsupported_version`（参数 `version`）。UTF-8 BOM 容忍。
2. **只取单元的 `source`**（字符串或字符串列表，CRLF / CR 统一成 LF）。输出、执行计数、元数据、内嵌图片、附件一律不带。`raw` 单元与不认识的类型丢弃。只读、不执行。
3. **顺序拼接**，单元之间一个空行，每个单元以 `# %%` 起头（VS Code / Spyder 认得的单元分隔，用户以后自己看时一眼分得清）。每个单元去掉结尾空白；全空白的单元不带。
4. **Markdown 单元保留为 `# ` 注释**，起头写 `# %% [markdown]`（同一套约定）。理由：科研笔记本里的说明（实验条件、参数含义、结论）对用户重读脚本有用，注释不影响运行；丢弃是不可逆的信息损失，保留的代价只是几行注释。
5. **IPython 专有语法一律改成注释并保留原文**（`# [notebook] %matplotlib inline`，缩进保留），不执行、不猜等价物：
   * 行魔法（`%matplotlib inline`、`%load_ext …`、`%autoreload 2`）、`!` shell 转义（`!pip install …`，行尾反斜杠续行的后续行一并处理）、`?` / `??` 帮助、赋值右边是 `!` / `%` 的（`files = !ls`、`out = %timeit -o f()`）。
   * 判定方法：**让 Python 自己指出哪一行不合法**（`ast.parse`，逐个单元），那一行像 IPython 语法才注释、再试。因此以 `%` 开头的**续行**（括号里的取模 `x = (7\n % 3)`）本来就合法，不会被误伤；不靠「行首是不是 `%`」的猜测。
   * 单元魔法（第一个非空行以 `%%` 开头）：后面跟 **Python** 的（`%%time`、`%%timeit`、`%%capture`、`%%prun`、`%%python`、`%%debug`）只注释第一行，代码照留——`%%time` 之后的绘图代码绝不能丢；其余（`%%bash`、`%%html`、`%%javascript`、`%%writefile`、不认识的……）**整个单元**不是 Python，整单元注释掉。
6. **有可运行的代码才算数**：至少一个单元在处理完之后还剩真实语句（只有 `%matplotlib inline` 的单元不算）；否则 `notebook_no_code`。
7. **语法核验**：每个单元处理完仍不合法（注释不掉的真语法错误）→ `notebook_syntax_error`，参数 `{cell, line}`（第几个单元、单元内第几行；只给位置，不把内容放进错误 / 诊断）；整份拼完再 `ast.parse` 一次，不过同样不落盘。
8. **文件头三行**：中文一句 + 英文一句（来源笔记本名、「这是自动生成的副本，改它不会改动笔记本」，产品名取 `brand.PRODUCT_NAME`），第三行是本功能自己的标记 `# <DIST_NAME>-notebook: <笔记本文件名> sha256=<正文哈希>`。笔记本文件名里的控制字符换 `_`（POSIX 文件名可含换行，不许撑破头）。
9. 上限 50 MiB（整份在内存里解析，必须有界；与联系作者附件同一量级）；上传路径的上限卡在读取本身（`_read_body_capped`）。

### 三、落盘规则（`engine/notebookscript.place`）

落在**笔记本同目录、同基名**：`名.py`。基名过 `assetimport.clean_name`（非法字符、Windows 保留设备名、过长、过不了 `exportreq.check_filename` 的回退）。候选名依次是 `名.py`、`名 (notebook).py`、`名 (notebook 2).py` …（上限 99）。对每个候选：

1. **不存在** → 新写这里（不是第一个候选就是 `renamed`）。
2. **已存在、正文与这次抽取相同**（不管文件头怎样）→ **复用**，不写。
3. **已存在、有本功能的标记、标记指的是这个笔记本、且现有正文的 sha256 等于标记里记的**（= 自生成以来一个字没被改过）→ 笔记本更新了，**原子替换**它。这是「覆盖自己生成的、没被人碰过的文件」：可以重新生成出一模一样的，没有任何东西会丢；若每次更新都另存 `(2)`，反复改笔记本的用户项目里会堆出一串副本、画布上关联的还是最旧那一份。
4. **其余**（用户自己的同名脚本；标记对不上；正文被改过；目录 / 符号链接 / 读不动 / 不是 UTF-8）→ **绝不碰**，看下一个候选。**判断不了有没有被用户改过，就当改过。**

名字比较不分大小写、NFC 归一（大小写不敏感的文件系统上 `FIG.PY` 与 `fig.py` 是同一个）。符号链接一律当用户的、不跟随。写入：先写同目录隐藏临时文件（`.tavotto-import-*`，扫描与 watcher 不认隐藏文件），再 `place_exclusive`（硬链接，目标存在由内核拒绝）或 `atomicio.publish_file`（替换，仅规则 3）；同一把进程内锁（`assetimport.LOCK`）串行化「挑名字 → 落盘」。
路径只经 `projectenv.contained_path` 钉回项目内（`..` / 软链接 / 项目外绝对路径都在那一步现形），不新造守卫。

### 四、脚本库里列哪些笔记本（`/api/registry` 的 `notebook_scripts`）

后端报告（`discover.iter_notebooks` = `_iter_py` 换后缀，同一套剪枝；隐藏目录含 `.ipynb_checkpoints` 不下探），前端不按后缀自己判。只看文件名与文件头，**不解析笔记本**（几十 MB 的笔记本每次刷新都解析不起）；坏笔记本也列，点了如实说清。
**已转过的不重复列**：同目录里有一份由它生成的 `.py`（标记指名这个笔记本）、且 `.py` 的 mtime 不早于笔记本——那份 `.py` 已经是脚本库里的普通脚本；笔记本之后又被改过（比 `.py` 新）就再列出来，点一下更新那份。用户自己的同名 `.py` 不算「已转过」，笔记本照列。独立于 `all_scripts`：不进探测、依赖准备、可运行脚本判据，也不被静态起草 / 注册表认领。

### 五、端点（会话认证走全局 guard，无旁路）

```
POST /api/notebook/extract            {"notebook": "<项目相对路径>"}        → 200 {notebook, script, status}
POST /api/notebook/import?name=<文件名>  body = 笔记本原始字节              → 200 {notebook:"", script, status}
status: created | updated | reused | renamed
```

`script` 是项目相对 POSIX 路径（与 `/api/registry` 的脚本键同一种写法）。`import` 只写项目根、`name` 只取 basename 并清洗。错误码 `notebook_*` 在 `notebookscript.ERRORS`（`tests/test_error_codes.py` 读 `ERROR_CODES`）与两份 errors.json 同时出现；写入失败复用 `asset_import_*` 四个码。

### 六、前端与壳

* `lib/notebookRun.ts::runNotebook`：所有入口共用（脚本库、画布、主页）；`extractNotebook` / `importNotebook` → 刷新脚本库 → `projectPreparationStore.open(scriptTarget(script,{reuse:true}),{run:true, placeIn})`，**不另造运行路径**；失败一律 `sayNotebookFailed`（一句话 + `feedbackAction()`）。
* `canvasFileIntake`：`IntakeKind` 新增 `notebook`；`useCanvasScriptDrop`：优先级 `.py` > 笔记本 > R/MATLAB；`HomeView`：新增 `openNotebook`，选择器的「待定」从 `ForeignLanguage` 扩成 `ForeignLanguage | 'notebook'`。
* 壳 `drop_paths.rs`：`DropTarget::Notebook { folder, notebook, name, ignored }`，优先级 **`.py` > `.ipynb` > R/MATLAB > 文件夹**。后缀判据：恰为小写 `ipynb`、`.ipynb` 本身（隐藏文件）不算、目录不算。

## 严格同源对

「什么算笔记本 / 打开哪个目录」：壳 `drop_paths.rs::is_notebook` ↔ 页面 `web/src/lib/scriptImport.ts::isNotebookName` ↔ 后端 `notebookscript.SUFFIX` / `discover.NOTEBOOK_SUFFIXES`（区分大小写）。壳与页面各与 `tests/golden/drop_script_rule.json`（新增 `notebook` 档与 7 条用例）比；`NativeFileDrop` 的 kind 闭集（`notebook` 并入）由 `tests/test_desktop_file_drop.py` 两侧相等看护。

## 隐私与安全

* **内容只在本机处理**：抽取在本机 Flask 进程里读 JSON、写 `.py`；不联网、不经 AI、不执行笔记本里的任何东西（`%%bash`、`!` 一律只是注释）；不增加遥测事件（白名单不变）。`docs/privacy.md` 登记一条。
* 路径守卫、会话认证、写入范围（只写笔记本同目录；上传只写项目根）、原子写、不覆盖用户文件都在上面。诊断参数不含笔记本内容（`notebook_syntax_error` 只有第几段、第几行）。
* **用户的笔记本永远不改**：`tests/test_notebook_script.py` 与 `e2e/notebook-to-script.spec.ts` 钉笔记本字节与 mtime 不变。

## 不做的事

* **不执行、不转换输出**：不带输出图片；不尝试等价物（`%matplotlib inline`、`!pip install` 都只是注释）；运行时缺依赖仍走现有的失败卡与修复。
* **不垫 IPython 运行时**：笔记本里调用 `display(...)`、`get_ipython()` 等 IPython 内置名的，抽出来的脚本运行会 `NameError`——由现有失败卡接住（不在本 ADR 里加 shim，见「后续」）。
* **不反向写回**：改 `.py` 不会改笔记本；不做 `.py` → `.ipynb`。
* 不认 nbformat 3 及更早（升级旧笔记本不是我们的事）；不认 `.IPYNB`（Jupyter 只生成小写）。
* 不新增界面控件形态：脚本区的行沿用现有 R / MATLAB 那一组的行样式；不加说明文字。

## 验证

* 后端 `tests/test_notebook_script.py`：抽取规则逐条（行魔法 / 单元魔法 / shell / `?` / 赋值形式 / 续行取模 / 反斜杠续行 / 中文 / BOM / CRLF / 空笔记本 / nbformat 3 与 5 / 坏 JSON / 语法核验失败 / 超限 / 不执行）；落盘规则逐条（同名各情形、不覆盖、被改过的生成件、他人标记、大小写撞名、目录与符号链接占位、候选用尽、并发）；端点（路径逃逸 / 软链接 / 项目外绝对路径 / 失败不留文件 / 写失败各码 / 上传 / 无项目 / 会话认证真服务器）；脚本库清单（已转过不重复列、笔记本更新后再列、用户同名脚本不遮蔽）。反证：取消「正文哈希等于标记」判断，「被改过的生成件不被覆盖」变红。
* 前端：`lib/notebookRun.test.ts`、`canvas/useCanvasScriptDrop.test.ts`（笔记本一组）、`components/home/HomeView.test.tsx`、`components/left/ScriptLibrary.notebook.test.tsx`、`lib/scriptImport.test.ts` / `dropScriptRule.golden.test.ts`；壳 `drop_paths.rs` 单测与 golden；`e2e/notebook-to-script.spec.ts`（脚本库一行转成脚本并运行 → 图出现、笔记本字节不变；浏览器拖到画布走内容上传）。
* `pnpm test && pnpm build && pnpm i18n:check`、`python scripts/build_browser_playground.py --check`、`cargo test` / `cargo fmt --check` / `cargo clippy`、`python scripts/dev/agents_budget.py --check`。

## 后续（建议，未做）

1. 抽出来的脚本在头部加一小段 shim（`display = print`-级别、`get_ipython` 空实现）让常见笔记本直接跑通——属于「猜」，需拍板。
2. 笔记本里的 `plt.show()` 之后的内联输出对应的图：当前靠脚本自己 `savefig` / 现有的运行捕获；没有 `savefig` 的笔记本是否默认存图交给现有「运行后发现图」。
3. 拖项目里的 `.py` 同款的「浏览器按文件名 + 内容哈希匹配项目里的笔记本」：浏览器拿不到路径时现在把笔记本内容抽到**项目根**（即使它在子目录里），可能与子目录里已转的那份重复——需要时再做哈希去重。
