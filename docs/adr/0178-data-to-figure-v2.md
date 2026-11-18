# ADR 0178：从数据到图 v2——文件夹、多备选、闭集配方，仍不执行模型写的代码

日期：2026-11-10 · 状态：**Accepted**（2026-11-10 用户拍板五项；1.0 豁免已登记在 `docs/1.0-release-readiness.md` §1.8）
相关：[0138 TAF](0138-tavotto-ai-foundation.md)（§三 默认只改表现形式、§四 执行面不扩大、§五 1.0 登记）、
[0147 内置执行器](0147-builtin-agent-runner.md) §一（`data_to_figure` 的工具集没有写文件工具）、
[0143 生成可编辑版本](0143-convert-to-editable.md)。实施包：`docs/implementation/ai-foundation/02-architecture.md` §四。
细则：`docs/rules/backend/ai-agent-bridge.md`「数据出图 v2 的画法配方」。

## 问题

v1（ADR 0138 Phase 5）只读**一个 CSV**，只会折线 / 柱两种图，列映射由确定性规则或模型建议给出。用户要的是：选一个**数据文件夹**
（CSV / TSV / XLSX），与改图助手多轮对话，助手按数据结构与科学问题给 2–4 个**备选画法**（用真数据渲染的小图），选中一个才新建脚本；
出来的是一张普通的 Tavotto 图，后续照常编辑。用户原话：「能够画的图要足够丰富，要能够智能地给用户推荐最合适的图，要让模型思考科学意义来呈现，
例如什么时候应该用折线图，什么时候应该用柱状图。」

难点有三：**图要丰富**（但被执行的代码不能交给模型）、**推荐要对**（折线 / 柱 / 原始点的取舍是科学问题，不是审美）、**表里的文字不可信**
（列名与单元格能影响模型）。

## 决定（2026-11-10，用户拍板）

1. **被执行的画图脚本由 Tavotto 模板生成，模型只填「配方」。** 配方是闭集结构（`engine/ai_data_recipe.FigureRecipe`）：族（十二类科学问题）、
   1–4 层图元（约 18 种）、汇总方式、坐标、列角色、`why`（给用户看的一句话，只显示、不进脚本）。**不执行模型写的代码**——ADR 0138 §四原样保持。
   图型有限，但数据可核、结构必然可编辑；「模板画不了时让模型写」是以后单独立项的另一条路，本 ADR 不放开。
2. **Agent 可只读整个数据文件**（范围钉在用户选的文件夹内，单次读取有大小上限），没有写文件、shell、网络工具（0147 §一）。
3. **新脚本放数据文件夹下的 `tavotto_figures/`**；用户的数据文件永不改写、不移动、不复制进项目。
4. **第一版不做显著性检验**：图上只放描述性汇总（均值 ± SD / SEM / 95% CI、中位数 IQR、拟合线）；助手不替用户下统计结论，不引入 scipy。
5. **全部在实验开关 `ai.taf.enabled` 后面，默认关**；打开默认另行决定。界面不出现「配方」「图族」「执行器」这些词，用户只看到提问、备选、图。

## 配方与资格判定（本 PR：`ai_data_recipe.py`）

纯标准库、父进程、**不执行、不写文件、不渲染**。唯一权威是三张表，其余全是它们的消费者：

* `MARK_RULES`：每种图元要不要汇总（误差线 / 误差带 / 汇总线**必须写明** SD / SEM / 95% CI / IQR；原始值图元不许带汇总）、是否露出原始点、一句含义；
* `REJECTIONS`：每个拒绝条件一个稳定码 + 一句话；`ERROR_CODES` 就是它的键（照 `DataSourceError`：构造时校验已登记，前端中英文文案、`test_error_codes.py`）；
* `FAMILIES`：每类科学问题的列角色与类型要求、允许的图元、批次（1 / 2 / 3）、专属拒绝码、「什么时候用 / 什么时候不用」。

`validate_recipe` 逐项核对列名真实存在、类型对得上角色、图元组合在该族允许范围内、`unit` / `paired` 只认 `Confirmed`（用户在对话里点选确认过的设计与个体编号列；
`id_like` / `repeats` 只是线索，**绝不**当成已确认）、规模上限（分面 ≤ 6、颜色分组 ≤ 8、矩阵 ≤ 60 × 40……）。取值类规则（每组 < 5 个值不出箱 / 小提琴、柱要求非负且轴含 0、
同一 x 有重复 y 且无已确认个体编号则不许逐点连线、构成各部分非负合计为正、dose > 0、不支持双 y 轴……）需要带行数据的摘要，**没给就不判、也不假装判过**
（`eligible()` 把它们列在 `unchecked`）。`enforce_order` 在模型排序之后再强制一遍：能露出原始点时，只有「柱 + 误差线」的备选不得排在含原始点的备选前面。

### 选图准则（科学含义）

`ai_prompt_pack.data_figure_guidance()` 是给模型的准则文字：固定的「先回答三件事」（读者要得出什么结论；一个数据点是什么、独立还是同一对象的重复；
横轴有没有顺序、中间的值有没有意义）+ **由三张表渲染**的各族用法与拒绝条件 + 输出要求（2–4 个备选并排序、每个一句「它让读者看出什么」、对落选的常见画法一句理由、
拿不准第 2、3 件事就用 `ask_user` 问一个选择题而不是猜）。折线 = 横轴有序连续且同一对象被连续跟踪，连线暗示可内插，无序类别不连线；柱 = 从 0 起算才有意义的量，
轴必须含 0，比较均值而每组值不多时优先原始点；误差线必须写明 SD / SEM / 95% CI。不许出现第二份规则清单，测试钉住准则与表一一对应。

## 模板与期望指纹（批次 1：comparison、trend；`ai_data_template.py`）

**脚本形态：短脚本 + 旁边一个公共文件**（用户 2026-11-11 选定；此前的方案是把约 380 行运行时整段内嵌进每个脚本，被否）。
`render_recipe_script` 返回 `Rendered(script, runtime_name, runtime)`：脚本只剩 docstring、来源注释（文件名 + sha256 + 配方 JSON，ASCII、单行、**不含 `why`**）、
`import tavotto_plot_xxxxxxxx as tp`、常量和无参 `main()`（matplotlib 调用保持直白，用户与改图助手在这里改），数据路径相对脚本，输出名静态。
读表、汇总、抖动、箱、核密度、拟合等数值函数是模板模块里的普通函数；公共模块 = 这批函数的源码（`inspect.getsource`）+ `xlsxread` 的源码原文，父进程算期望值调用同一批函数——
不是「两处各抄一份、靠测试对拍」。公共模块对所有图逐字节相同、**不含任何数据**（数据文字只以 `repr()` 字面量出现在脚本里），一个模块覆盖三种数据源。
文件名按内容寻址 `tavotto_plot_<sha256 前 8 位>.py`（合法的模块名）：内容变了名字就变。**落盘规则**（`tavotto_figures/` 落盘是下一个 PR 的事，本 PR 不写任何用户文件）：
脚本与公共模块都只新建、永不覆盖；公共模块已存在且字节相同 → 复用；存在但字节不同 → 按损坏报错、不覆盖；老脚本继续 import 它们当时的那一版，新旧版本的公共模块并存。
脚本不改 `sys.path`：safe worker 与 native bridge 都把脚本目录放在 `sys.path[0]`。静态扫描（`discover` / `projscan`）不会把公共模块当成一张图（没有存图调用、没有 `main`），
前缀 `tavotto_plot_` 在 `discover.SKIP_PREFIXES` 里（与 `paper_style` 同类）：脚本清单把它标成 `infrastructure`，不当成「没出图的脚本」。
期望指纹只对父进程**能逐字节算准**、并且验证器**能按 label 匹配**的层声明：散点、Line2D、柱组可核；误差线、误差带、箱、小提琴不核（manifest 里没有 label 字段 / 由 matplotlib 拼成），
`fingerprint_coverage` 如实列出，没有可核层就报告「这张图的数据没法核」，绝不折算成 verified。图上的诚实：不画星号与 P 值；**图里不写任何脚注**（见下「图里不写脚注」；各格 n 不同时 n 仍写在刻度标签里，那是坐标轴文字），
柱的轴含 0，不画星号与 P 值，抖动是确定性的。样式数字来自出版规范 profile，分类色用公开的 Okabe–Ito 色盲安全色板。

### relationship 与 distribution（批次 1 的后两族）

四个批次 1 的族现在都有模板。**规则各只有一份文字定义 + 一份实现**（公共模块里的纯 Python 函数，父进程与脚本同源）：直方图分箱 = Freedman–Diaconis（箱宽 `2 × IQR × n^(-1/3)`，箱数 1–50，IQR 为 0 退到 Sturges，各组共用同一组箱边）；
密度带宽 = Scott（`SD × n^(-1/5)`，网格取数据范围两侧各 3 个带宽）；ECDF = 排序后的逐点阶梯；拟合线 = 最小二乘直线，**在画出来的坐标里拟合**（对数轴那一维先取 log10；只有一维是对数时图上是曲线，取 50 个点）。
这些规则只在这里与代码里是一份定义，**不再写进图**（2026-11-17 起，见「图里不写脚注」）；不报显著性、不放 R²。
distribution 有两种形态：值在横轴（hist / ecdf / density，不带类别 x、按颜色分组）与值在纵轴（box / violin / jitter / points，类别 x 可选）；两类不能叠在同一张图，ECDF 不与直方图 / 密度叠（纵轴含义不同），
值在横轴的图元不用对数轴——这些组合由 `validate_recipe` 拒绝（`mark_not_allowed`），模板对同样的组合抛 `TemplateError`。可核：hist 的柱高、density 曲线、ecdf 阶梯、散点、拟合线；不核：box、violin（同 comparison）。

## 出备选、候选目录、落盘与端点（不调模型；`ai_data_recipe.default_candidates` / `ai_data_propose.py` / `ai_data_task.py` 下半 / `/api/ai/data-figure/*`）

本节是「没连接 AI 也能走通」的整条流水，也是模型路径的底座（模型工具集与多轮提问复用它，只是把「确定性备选」换成「模型给的配方」，其余校验、渲染、落盘一字不换）。

**确定性备选**。`default_candidates(profile, table=, sheet=, confirmed=, hint=, values=, picks=)` 对选定的那张表给 2–4 个现在有模板能画（`RENDERABLE`）的配方，全部经
`validate_recipe` + `enforce_order`。选表与列角色的规则各只有一处文字定义（`pick_table` / `_resolve_roles` 的文档字符串），保守：能由结构或用户点名唯一确定就确定，
有歧义就返回 `NeedsChoice`（2–4 个选项 + 可选的「不确定」，每个问题的「不确定」都有安全默认：y = 最后一个数值列、分组 = 最左的分组列、横轴 = 最左的日期 / 数值列、重复测量 = 当独立数据画、不带编号）。
`hint` 只做按词点名列 / 文件（`ai_data_figure._mentioned`），不做语义猜测；`unit` / `paired` 只来自 `Confirmed`。`id_like` 列永不当 y / x / 分组；有编号列、编号重复、用户没表态且能画趋势时问一次
「同一对象重复测量吗」，没确认就绝不出逐个体的线（配对 / 轨迹两族本来就没有模板）。每个备选带 `title_code` / `why_code`（`CANDIDATE_CODES`，后端不写界面文案，前端 `ai:dataFigureV2.candidate.<码>.title|why`
中英文都补）；选择题与备选为空的原因同理（`QUESTION_CODES`、`NOTHING_REASONS`、`ai_data_propose.DROP_REASONS`）。数值列里混着文字（`<0.1`）按「不猜」处理：该列不当数值列，整表没有数值列时原因码是 `value_column_mixed`，请用户先整理。

**候选渲染与候选目录**。每个备选 `render_recipe_script` 出脚本 + 公共模块，写进 `data_dir()/cache/ai_data_candidates/<session>/`（一个**独立的小项目**：`tavotto_figures/<stem>.py`、公共模块、选中那一张数据文件的**字节副本**，
布局与落盘后相同，脚本读 `../<数据相对路径>`），用 safe worker 以这个小项目为 `figures_dir` 真渲染 PNG 缩略图与 manifest，**写入根绊线只开候选目录**。与 `ai_convert.run_validation` 的先例对照：先例是影子脚本（候选字节在候选目录、
worker 以项目里的显示路径为 `__file__`），这里不能照搬——模板脚本要 `import tavotto_plot_*`，而显示目录 `<数据文件夹>/tavotto_figures/` 此刻在用户磁盘上不存在，公共模块无处可 import；所以取先例的另两半
（独立候选目录 + 写入根绊线），用户文件夹里一个字节都不写、不建目录。解释器：先用项目渲染本来会用的那个（只读已定下的结论、不触发检测与依赖流程），起不来 / 缺 matplotlib 就静默回退默认链条再画一次，结果里留 `interpreter_fallback` 标记；两条都不行才算这个备选失败。候选脚本算出的数据指纹与 `expected_fingerprints` 对拍，对不上的剔除并记
`candidate_fingerprint_mismatch`；单个备选失败（渲染失败 / 超时 / 缩略图取不到 / 被绊线拦下）只少一张卡，全失败返回结构化的 `{kind:"nothing", reasons}`，不是 500。并发 `MAX_PARALLEL = 2`，超时沿用 worker 池的请求超时，
渲染后一律 `pool.invalidate(force=True)` 退役 worker。生命周期：会话在内存、TTL 30 分钟（读 / 提交重新计时）、至多 6 个、`discard` 即删、**提交后不删**（可接着出同一批里的别的备选；同一备选重复提交靠 `client_request_id` 幂等，换请求号 = 新建 `_2`；数据 sha 变了照旧 409）、同一会话里数据副本只有一份（≤ 5 MiB）、每次出备选 / 提交前 `sweep()` 清过期与孤儿目录；删除不用 `ignore_errors`，
删不掉记日志留给下一次。会话属于项目，进程重启 = 过期（`propose_session_expired`），前端据此重新出备选。

**选中落盘（任务类型 `data_to_figure_v2`，v1 的 CSV 同目录路径与用例逐字不变）**。提交只回传 `{session, id}`——配方在服务端的会话里，不在契约里。落点 `<数据文件夹>/tavotto_figures/<stem>.py`
（stem 规则沿用 v1：避开已登记的、同名脚本、同名产物，casefold 比较）+ 同目录 `tavotto_plot_<8 位>.py`；两个文件都只经 `scriptlock.create_script`（`O_EXCL`、永不覆盖、不跟随链接、必须持有该路径的 `script_guard`，
对非脚本的公共模块同样合适，不需要第二套写法）。顺序在项目锁内固定：目录（不存在才建）→ 公共模块（不存在新建 / 字节相同复用 / 字节不同或不是普通文件 → `common_module_conflict`，**什么都不写**）→ 脚本 → `discover.register` →
锁外统一刷新 → 提交后 safe 渲染 → `figureverify.data_matches_expected`。提交前 L1 已经判过一遍公共模块与目录状态（冲突则任务 `failed`、零写入），锁内再判一遍防竞态。登记失败：删掉刚建的脚本；公共模块与目录若是**本次新建**
且字节未被动过也一并撤回，复用的不删。数据路径一律 `contained_path`，`tavotto_figures` 是链接则拒绝（`figures_dir_unusable`）。

**写不进数据文件夹**：建目录 / 写公共模块 / 写脚本时的 `OSError` 里，权限不够与只读文件系统（`EACCES` / `EPERM` / `EROFS`，含 `PermissionError`）→ `task.error = data_folder_not_writable`，磁盘满（`ENOSPC` / `EDQUOT`）→ `disk_full`（`ai_data_task.write_failure`；文案 `ai:dataFigure.reason.<码>` 中英两份）。其余 `OSError` 不猜，仍是 `executor_error:<类型名>`。事务沿用上一段：脚本写到一半失败时 `create_script` 已删掉半截文件，随后撤回本次新建的公共模块与目录，所以**公共模块与脚本要么都在要么都不在**；复用的公共模块、别人的文件不碰。错误详情只带 errno 名，响应里没有路径；成功路径的行为不变。HTTP 仍是 200（与其它落盘失败同口径，结局在 `task.outcome = "failed"`），前端不必再靠项目的 `writable === false` 猜。

**数值列里夹着少量文字**（`<0.1`、`n/a`、`12.5*`；2026-11-12 用户拍板：不许静默只画数值行，要先问）：被这次画图用到的数值列里，非数字的非空格子 > 0 且占比 ≤ 20%、数值格子 ≥ 3 个（`numeric_like`）时，propose 返回 `{kind:"ask", question_code:"mixed_cells", columns:[{column, non_numeric, rows, preview, more, options}]}`，**不出图**。`preview` 最多 5 行：文件行号（表头第 1 行；CSV 的空行占行号，XLSX 是表内行号）、格子原文（截 40 字）、同行其它用到的列的值、选「取数字」后会得到的值（取不出 = null）；多个用到的列一次问完。三个选项：`skip`（忽略：该格当缺失，不画、不计 n，折线在那里断开，同空格）、`extract`（取第一个数：`12.5*`→12.5、`<0.5`→0.5、`3.2 mg`→3.2，取不出的忽略）、`describe`（`free_text`，前端显示输入框）。回答写进 `answers.cells`：`{列名: {"mode": "skip"} | {"mode": "extract"} | {"mode": "map", "map": {原文: 数 | null}, "rest": "skip" | "extract"}}`，声明式、不含代码；`describe` 本身不可执行（要先由助手译成 `map`，模型那一段在下一个 PR），只带说明文字的请求回 409 `propose_cells_need_rule`；规则校验（键 ≤ 50、值有限数或 null、键必须真的在该列文字格里、未知 mode / 多余字段 / 列不是用到的夹文字数值列）失败回 400 `propose_cells_invalid`，`params.reason` 是拒绝码、`params.key` 点名哪一项。规则进配方（`FigureRecipe.cells`，不写时 `to_dict` 与旧版逐字相同）：生成的脚本读表后调用运行时函数 `apply_cells(header, rows, CELLS)` 套规则（只改内存副本，用户文件一个字节不动），图下方说明写清做了什么与各几行（英文，如 `value: number taken from 1 text cell, 1 non-numeric cell ignored` / `ND = 0`），父进程的期望指纹、取值类规则（每组 n）、`unplotted_rows`、提交结果里的 `cells` 计数都用**同一份** `ai_data_figure.apply_cells` 源码，所以数据核对按处理后的数据算，而且用户已明确决定的格子不再是 `partial` 的理由（只剩横轴缺值的行仍报告）。没有规则的配方里这样的列仍是 `column_type_mismatch`（`eligible` 结构资格放宽，`validate_recipe` 不放宽）。超过阈值的列不当数值列，整表没有可用列才是 `nothing`（`value_column_mixed`）。幂等键 = (项目, 请求号, 请求内容摘要)：同一请求号换了处理方式必须重新出图。公共模块多了 `apply_cells`，其内容哈希（文件名 `tavotto_plot_<8 位>`）因此变化；老脚本继续 import 它们当时的那一版，新旧并存。提交后的渲染与核对是对**原件**（脚本读 `../<数据>`）做的，不沿用候选副本的结论。

结局口径（沿用 v1 并扩展）：全部可核层对上 → `completed` + `data_check: "verified"`；有行画不出（横轴空 / 非数）→ `partial` 并逐行报告；指纹对不上 → `failed` + `data_check: "mismatch"` + `can_delete_script`（脚本已落盘，由用户决定）；
**没有任何可核层（只有箱 / 小提琴 / 误差线）→ `data.verifiable` 记 unknown（`data_not_verifiable`），`data_check: "unverifiable"`，结局最高 `partial`，绝不写成 verified**。`ai_tasks.run_task` 只为「确定性执行器只新建文件」放行写入的规则原样保持，没有放宽。

**端点**（都在 `ai.taf.enabled` 后面，关着 404 `taf_disabled`；都过会话认证 guard；项目指名不存在 409；路径只经 `normalize_rel`（字符串层，反斜杠 / 盘符 / `..` 拒绝）与 `projectenv.contained_path`）：
`POST /api/ai/data-figure/propose`（`{folder?, table?, sheet?, hint?, answers?, client_request_id?}` → `ask` / `candidates` / `nothing` 三选一）、`GET /api/ai/data-figure/thumbnail/<session>/<id>`（只读缓存里我们自己写的 PNG，
id 是服务端登记的不透明串）、`POST /api/ai/data-figure/commit`（`{session, id, client_request_id?}`）、`POST /api/ai/data-figure/discard`（放弃会话，幂等）；`/api/ai/data-files` 扩到
`.csv / .tsv / .tab / .xlsx`，XLSX 带 `sheets: [{name, hidden}]`。旧的 `POST /api/ai/data-figure`（v1）不变。遥测不新增事件（本 PR 不改 `CONSENT_VERSION`）。

## 修订（2026-11-16）：文字先到的懒渲染、不再提问（`propose(render="lazy")` / `ensure_thumbnail`）

前端改成「点一个数据文件，立刻出现候选画法的名字和说明，缩略图一张张补上；不再有选择题和弹窗，一律先按默认画出来，右侧面板显示横轴 / 纵轴 / 分组用了哪一列并允许改」。上面那条流水的**判据一字不动**（配方校验、模板出码、指纹对拍、候选目录隔离、写入根绊线、提交只新建），只换了「什么时候画」与「问题怎么处理」；旧请求（不带 `render`）行为与响应逐字不变。

* **请求** `POST /api/ai/data-figure/propose` 增加可选 `render: "lazy"`（别的值 400 `propose_input_invalid`）。懒模式**永不返回 `ask`**：选表 = 发现遍历的第一张；列角色的问题用各自的「不确定」默认（y = 最后一个数值列、横轴 / 分组 / 重复测量同前）；夹着文字格的数值列 = 不画那几行（`skip`）；表头行 = 读表猜的那一行。每个替用户做的决定都写进响应：`defaults: [{question_code, answer?}]`、`mixed_cells: [{column, non_numeric, rows, mode:"skip"}]`、`header_row`（文本表；XLSX 为 null）、`table` / `sheet`。用户在面板上改了就重新出：列角色用**既有的** `answers.y / x / group`（列名；新增值 `none` = 不要有序横轴 / 不要分组）、文字格用既有的 `answers.cells`、表头行用既有的 `header_rows`——没有另造一套参数。`render` 进幂等摘要。
* **响应的 `roles`**（懒模式在 `candidates` 与 `nothing` 里都有）：`{x, y: [..], group, facet, unit, options: {x: [..], y: [..], group: [..]}}`，`options` 是每个角色此刻可换成的列，供下拉。列角色只有一处定义（`_resolve_roles`），`options` 由它出，不在 propose 里另算。
* **缩略图懒渲染**：懒模式只写脚本与公共模块（毫秒级），不渲染；每项 `thumbnail_status: "pending"`（旧模式一律 `"ready"`）。渲染在 `GET .../thumbnail/<session>/<id>` 时做：该项没画就画并等它画完再回图（200）；**同一项并发取图只画一次**（项上的锁），画好的不重画；全进程同时在画的缩略图受 `MAX_PARALLEL = 2` 限（与旧模式同一上限，池的存活 worker 数有上限，再多会互相回收），先到先画，前端先取推荐那张。画不出（渲染失败 / 超时 / 缩略图取不到 / 被绊线拦下 / 指纹对不上）→ **422 `propose_thumbnail_failed`**，`params.reason` 是 `DROP_REASONS` 里的码，结果记在项上、不重画；唯一的重试：`candidate_render_failed`（池在内存吃紧时回收刚起的 worker 也落在这个码里，可能是瞬时的）自动再画一次。「渲染失败的备选不进列表」的保证在懒模式下落成两条：该项取图 422、**提交该项 404 `propose_candidate_unknown`**（失败的不许落盘）。列表顺序稳定（推荐标记永远在第一项），推荐落在失败项上时由前端把推荐挪给下一张可用的。模板不认的配方（`candidate_template_unsupported`）仍在 `dropped`，不进列表。

**横轴列夹着少量文字格（2026-11-17）**：有序横轴与纵轴同一套规则——`numeric_like`（非数字占比 ≤ 20%、数值格 ≥ 3）的列也能进横轴候选（`_resolve_roles` 的 `xs`、`roles.options.x`）；用了就和 y 列一样进 `mixed_cells`（懒模式默认 `skip`，`answers.cells` 的 `extract` / `map` 同样生效），规则进配方并随脚本读表套用；被跳过的行计入 `unplotted`（取数字部分取出了数的行不算），统计在 `cells`。文字占多数的列仍不进横轴候选。没有混文字格的表输出逐字不变。

## 修订（2026-11-17，用户决定）：图里不写脚注

生成的图（缩略图与提交后的正式脚本）里**不写任何自动加的脚注**：没画的行（`day: 2 non-numeric cells ignored`）、汇总定义（`mean ± SD`、`bars: … error bars: …`）、画法规则（分箱 / 带宽 / 箱线须 / `dash-dot line: least-squares fit (descriptive)` / 整系列水平线）、样本数（`n = …`、`n per group in tick labels`）一概不进图。画法本身不变（拟合线照画、汇总层照画），图幅也不再为脚注留白——收尾只剩 `fig.tight_layout()`。这些信息改由响应字段（`unplotted`、`mixed_cells`、`cells`、`coverage`）给前端，界面右栏展示；各格 n 不同时 n 仍在刻度标签里（坐标轴文字，不是脚注）。

实现：`ai_data_template._footer` 不再出说明；只给脚注用的运行时函数（`n_note` / `n_phrase` / `trend_point_counts` / `trend_series_counts`）从公共模块删掉，公共模块文件名哈希由 `3c540192` 变为 `86dcfebb`（`tests/test_ai_data_encodings.py::PINNED_RUNTIME_NAME` 同步）；`apply_cells` 的返回值不变（说明列表脚本里不再使用）。期望指纹只核数据、不含脚注，不变。已生成的老脚本带着自己的公共模块与脚注，不受影响。看护：`tests/test_ai_data_template.py::test_generated_scripts_write_no_footnote_of_any_kind` 与各形状用例的 `texts == []`。

## 文本表格：`.txt` / `.dat` / 嗅探 / 表头行覆盖 / 千分位（`ai_data_figure.read_text_table`）

第一版只做文本格式（`.mat` / `.npy` / HDF5 等二进制以后再说；`.xlsx` 读法不动）。整件事在 `ai.taf.enabled` 后面；v1（`inspect_data_source` 默认分支、v1 模板的 `_cell`）**逐字不变**——新读法只在 `v2=True` 时启用。

**读法 = `(分隔符, 表头物理行号)`，只有一份解析实现。** 分隔符 `,` / `\t` / `;` / `ws`（连续空白）；行号是 1 起的**物理行**（与给用户看的原文预览同一口径，说明行与空行都占号）。解析（`delimited_records` / `resolve_header` / `table_from_text`）是运行时函数：父进程算摘要与期望指纹、生成的脚本里的 `read_delimited(DATA, 分隔符[, 表头行])` 调用**同一份源码**（`inspect.getsource` 进公共模块）；嗅探（`_detect`）只在父进程，读法以字面量写进脚本，脚本不嗅探。干净的 CSV / TSV 读法仍是 `read_delimited(DATA, ',')`，与此前生成的脚本逐字相同（公共模块多了函数，哈希随之换名，新旧并存）。表头行显式时：它之前的行不读；表头首格的 `#` 标记去掉；`ws` 切出的列数对不上数据时改按「两个以上空白」切（`Time (s)   Voltage (V)`）；表头之后以 `#` 开头**且列数与表头不同**的行是注释、丢掉（列数相同的 `#` 行是数据）。

**嗅探规则**：`.csv` / `.tsv` / `.tab` 先只试自己的分隔符（干净老文件不变），读不成才试别的（分号 CSV）；`.txt` / `.dat` / `.asc` 四种都试。数据块 = 文件末尾连续列数一致（≥ 2 列、≥ 2 条记录）的一段；块大者胜，相同则「像数」的格子占比高者胜（欧式小数 `1,5` 也算像数，仅用于挑分隔符），再相同按 制表 / 分号 / 逗号 / 空白。`.txt` 等必须有数值列（README、清单不是表）。嗅探只看开头 256 KiB（解码后的文本）；编码判定看整份文件；整份文件仍按 `MAX_BYTES`（5 MiB）/ `MAX_ROWS` / `MAX_COLUMNS` 读。表头：块里第一条「数值列有数」的记录之前的记录是候选；1 条 = 认得准，≥ 2 条（标题 + 列名、列名 + 单位）= 猜第一条像名字的、没认准；0 条时看块外紧挨着的 `#` 行或带空格的列名行，都没有 = 没有表头、猜第一行、没认准。猜的表头读出来列名为空 / 重复也是「没认准」（不是读不了），用户点另一行即可。

**表头选择与单位行（2026-11-17 修订）**。表头候选 ≥ 2 条时，取**最后一条**像名字的（紧挨单位行 / 数据），不再取第一条——仪器导出常有等宽的说明行（`operator: lab`）排在列名前面。表头记录的**下一条**若正好是第一条数据行之前的最后一条、且整行都是裸写的单位（`nm  a.u.`，`UNITS` 键或 `_UNITS_ROW_EXTRA`，没有一格像数、不带括号）就是**单位行**：读表时切掉，既不当数据也不当第二个表头。括号包着的（`(a) (b)`）仍交给用户在两行里点，不自动切。读法多一项 `units_row`（`TextLayout.units` → `DataSummary.units_row` → 脚本里 `read_delimited(DATA, 'ws', 3, True)`）：**只在读的就是嗅探认出的那一行表头时才切**（用户用 `header_rows` 另指一行 = 读法由他定，不切）；没有单位行的文件，读法与生成的脚本逐字不变。切掉后数据行的物理行号不变、被切的行不计入非数字格。`table_from_text` / `read_delimited` 是内嵌进公共模块的运行时函数，加参数使公共模块文件名哈希由 `1d525a3a` 变为 `3c540192`（`tests/test_ai_data_encodings.py::PINNED_RUNTIME_NAME` 同步）；已生成的老脚本带着自己的公共模块副本，不受影响。单位文字暂不用于标注坐标轴。

**畸形行规则**（表头定下之后，数据区里列数不符的行一律拒绝，`data_ragged`，不丢表头和前面的观测、不把后缀当成新表）：数据块 = 文件末尾连续等宽的一段；块**之前**还有等宽的非注释记录就说明块不是整张表（第一行数据 / 中间 / 开头有一行畸形），整份文件报 `data_ragged`；块之前只有列数不同的行 = 仪器导出的说明行，正常。**尾部**：空行忽略、`#` 开头的行忽略（注释 / footer）；其它列数不符的尾行（如 `Total rows 3`）一律算畸形，不静默丢弃——分不清 footer 与坏行，宁可让用户看见。带引号的字段按 `csv` 规则算一个格子，引号里的逗号不是列。畸形文件的 `table` 仍是 `unreadable` + `data_ragged`，但带 `header_row`（猜的表头行）与 `preview`，用户点对表头行后按 `header_rows` 读；指定行**之后**的畸形行同样拒绝，指定行之前的说明行不管列数。已知代价：与表头等宽、但被列数不同的行隔开的说明行（如 `Temperature,25` 后面隔着一行三列说明再接表）会被当成畸形，要用户指定表头行。

**状态与接口**。`read_text_table(path, header_row=None) -> TextTable`（**不抛**）：`status` 三选一 `ok` 认得准 / `unsure` 没认准 / `unreadable` 读不了（稳定码：`data_binary`（NUL 或控制字符 ≥ 2%）、`data_decode_error`（不是 UTF-8 / 有 BOM 的 UTF-16 / GB18030，或解出来不合理）、`data_not_table`（没有成列的数据）、`data_empty`、`data_too_large`、`data_too_long`、`data_too_wide`、`data_ragged`、`data_header_row_invalid`…）。`GET /api/ai/data-files?formats=all` 给每个文本文件加 `table: {status, code, delimiter: comma|tab|semicolon|space, header_row, columns, rows, preview}`；默认（只列 CSV）的响应**不变**、没有该字段。`preview: [{line, text}]` 只在 `unsure` 时有：前 30 行（猜的行更靠后则多给到它后 5 行），最多 100 行、每行最多 200 字符、控制字符换空格（表里的文字不可信，只显示）。最多读 100 个文件、合计 64 MiB，超出的 `table` 是 `null`（不是「读不了」）。`.txt` 等散文（`data_not_table`）不进 `profile_folder` 的 `skipped`。

**表头行覆盖**。`POST /api/ai/data-figure/propose` 新增 `header_rows: {表路径（相对 folder，同 table）: 物理行号}`：形状校验（≤ 64 项、整数、不收布尔）后只与遍历结果（`discover.walk_files`）**逐字比对**，不拿来拼路径；`profile_folder` / `with_explicit_table` / `_table_for_values` 都按它读，实际用的行号记进 `Session.header_line` → `Selection.header_line`，提交按同一读法重读、不重新嗅探；它进幂等摘要（同请求号不同表头行不会拿到旧答案）。按指定行读不出来（`data_header_row_invalid` / `data_ragged`…）→ 400 + 稳定码。

**千分位**（`parse_cell`，v2 与脚本共用；v1 走 `legacy_parse_cell`）：整格是「首组 1–3 位且不以 0 开头、其后每组恰好 3 位」的逗号分组整数，可带小数——`1,000` → 1000、`-12,345.5` → -12345.5。不认：`1,2` / `0,5` / `12,75`（欧式小数）、`1,000,5` / `1,00` / `1,0000` / `0,123` / `01,000`（乱格式）。纯千分位的整列是数值列，不触发「混了文字」；千分位列里夹 `1,5` 仍是混列、先问用户。`apply_cells` 的「取数字」同规则：`<1,000` → 1000；不是千分位写法的仍取逗号前的数（`1,2` → 1）。已知歧义与防护（2026-11-15 用户定：科研数据被静默读错比多问一次严重）：三位小数的欧式写法（`1,234` = 1.234）与千分位分不开，所以**分号分隔的表里带逗号的格子一律不自动认**——`table_from_text` 给这些格子打 `SemicolonCell` 标记（运行时类，进公共模块），`parse_cell` 对它整格当文字（不按千分位、也不猜成小数），该列于是是「混了文字」（先问用户，走既有的 `mixed_cells`）或「没有数值」（出不了图，`nothing`），绝不静默读成 1000 倍。同一列里出现过不合千分位写法的逗号数（`1,5`）时该列同样是混列、先问，`1,234` 不会被悄悄认成数。逗号 / 制表 / 空白分隔的表维持 `1,000` → 1000。`.csv` 嗅探另加一条：数据块没覆盖全部非注释行时，自己的分隔符要和别的一起比（否则分号 CSV 里带逗号的数会被当成逗号表）。XLSX 的文字格同样认千分位（读法不动）。

**编码**（`ai_data_figure.decode_text`，父进程唯一的解码入口；纯标准库、不探测、不打分）。国内仪器与 Excel「另存为 CSV」常给 GBK，Windows 上的工具常给带 BOM 的 UTF-16，所以认三类，判定顺序**确定且可解释**：
① 看 BOM——`EF BB BF` → `utf-8-bom`，`FF FE` → `utf-16le`，`FE FF` → `utf-16be`（`FF FE 00 00` / `00 00 FE FF` 是 UTF-32，不支持）；② 没有 BOM：**严格**按 UTF-8 解，成功 → `utf-8`（纯 ASCII 也是它）；③ 失败再**严格**按 GB18030 解（覆盖 GBK / GB2312），成功且通过合理性检查 → `gb18030`；④ 都不行 = `data_decode_error`。
合理性检查：GB18030 解出的文字里有私用区 / 未分配 / 代理码位的，多半是别的编码被硬解，同样算读不了。没有 BOM 的 UTF-16 不认（里面的 NUL 字节在无 BOM 时按二进制拒绝，`data_binary`）；有 BOM 的 UTF-16 先解码再查 NUL，文本里真有 NUL 仍是 `data_binary`。
**为什么不加 Big5 / Shift-JIS / Latin-1 / Windows-1252**：Big5 与 Shift-JIS 的字节大多也是合法的 GB18030，谁先谁后都会把一种编码的文字悄悄解成另一种的乱码；Latin-1 什么字节都解得开，不能当兜底。科研数据被静默读错比说「读不了」严重——用户另存为 UTF-8 / GBK 即可（`data_decode_error` 的文案就是这么说的）。已知局限：Big5 / Shift-JIS 文件若恰好是合法 GB18030 且不含私用区码位，仍会被当成 GB18030 解成乱码；这是不探测、不加字典所接受的边界，列名与分组值显示出来用户能看到。
**同源**：编码和分隔符、表头行一样在父进程定下，**以字面量写进脚本，脚本不探测**。摘要 `DataSummary.encoding`、`TextTable.encoding` 记它（`TEXT_ENCODINGS`：`utf-8` / `utf-8-bom` / `utf-16le` / `utf-16be` / `gb18030`；XLSX / v1 是 `None`）。UTF-8 与 UTF-8 BOM 的脚本仍写 `read_delimited(DATA, …)`（`utf-8-sig`），**与此前生成的脚本逐字相同，公共模块没有变，文件名里的哈希不换**。GB18030 / UTF-16 的脚本写 `header, rows = table_from_text(DATA.read_bytes().decode('gb18030'), ',')[:2]`（UTF-16 的 codec 名是 `utf-16`，BOM 决定字节序）：用 `bytes.decode` 而不是 `open(...)`，是为了不经平台换行转换，读出的文本与父进程解码的是同一段字节；`table_from_text` 本来就在公共模块里，所以不必给公共模块加函数。提交时按同一份字节重新解码（编码是字节的纯函数，`data_sha256` 没变就必然同一个编码）。
**上界**：编码判定看整份文件（≤ `MAX_BYTES`），不靠开头一段猜，所以 256 KiB 的嗅探窗口切在多字节字符中间不会造成误判（窗口只用于分隔符与表头嗅探，作用在已解码的文本上）。
**接口**：`GET /api/ai/data-files?formats=all` 的 `table` 与 `GET /api/ai/data-overview` 都带 `encoding`（上面的稳定名；解码之前就读不了的、XLSX 是 `null`），给前端在预览标题旁显示。表里的文字仍是不可信输入：预览的控制字符处理不变。

**没做**：无 BOM 的 UTF-16、UTF-32、Big5 / Shift-JIS / Latin-1 等其它编码、无表头文件自动编列名（`header_row = 0`）、`.mat` / `.npy` / HDF5。

## 数据概览与脚本来源（`ai_data_overview.py`，都只读、都在 `ai.taf.enabled` 后面）

前端数据页要三样 propose 给不了的东西：侧栏里每个工作表的行列数、点开一个文件看的预览（列名 / 类型 / 前几行 / 被跳过的说明行）、没认准时点某一行当「每列的名字」后**立刻**看到按那一行重读的结果（此时用户还没点「开始」，拿不到 propose 的结果）。再加上素材库要分「从数据生成」组、数据页要给画过的表标「已画」，需要知道一张脚本是从哪份数据生成的。本节两件事都**不写任何文件、不调模型、不执行任何代码、不发遥测**。

**`GET /api/ai/data-overview?path=<项目相对路径>[&sheet=<工作表名>][&header_row=<物理行号>]`**。选 GET：纯读、参数全是短标量，与同族的 `/api/ai/data-files` 一致。开关关着 404 `taf_disabled`；过会话认证 guard。返回（所有键恒在，读不到的是 `null` / `[]`）：

* `path`（遍历结果里的规范路径）、`sheet`（XLSX 实际读的工作表名，文本表 null）、`status`（`ok` / `unsure` / `unreadable`，XLSX 没有 `unsure`）、`code`（`unreadable` 时的稳定码）；
* `delimiter`（`comma` / `tab` / `semicolon` / `space`，XLSX null）、`encoding`（文本表实际用的编码，见「文本表格」的编码一段；XLSX / 读不了的 null）、`header_row`（实际用的表头**物理行号**，与 `/api/ai/data-files` 的 `table.header_row` 同口径；XLSX 的表头永远是第一条非空行，null）、`rows`（数据行数，不含表头）、`columns`（列数）、`layout`；
* `fields: [{name, kind, numeric, text, empty, nan, id_like}]`——`kind` 就是 `ColumnSummary.kind`（`numeric` / `date` / `text` / `mixed`），不另造分类；
* `preview_rows: [{line, cells}]`（前 20 行，`line` 是物理行号 / 工作表行号，`cells` 是单元格原文）、`truncated`（是否还有更多行）；
* `lead_lines: [{line, text}]`——表头行**之前**被跳过的说明行（空行不给，最多 20 行）；`raw_lines: [{line, text}]`——`unsure` 时文件开头的原文行（同 `/api/ai/data-files` 的 `preview`），给用户点「哪一行是每列的名字」。

**预览 = 之后出图读到的那张表。** 文本表走 `read_text_table(path, header_row=…)`、XLSX 走 `inspect_data_source(v2=True)`，就是 propose 的 `profile_folder` / `_table_for_values` 用的那两个读表函数；`header_row` 经 `ai_data_propose.parse_header_row`（`parse_header_rows` 也用它，一份判据：1 到 `2 × MAX_ROWS + 1000` 的整数、不收布尔，否则 400 `propose_input_invalid`），XLSX 与 propose 一样不使用它。用例把概览的行列数 / 列名 / 类型 / 表头行 / 前 N 行与 propose 那条链逐项对拍。**「读不了但可以让用户点一行试试」的信号 = `raw_lines` 非空**（不另设布尔）：`read_text_table` 对可以靠指定表头行补救的读不了（仪器说明行触发的 `data_ragged`；用户指定的行读不成表：`data_header_row_invalid` / `data_header_invalid` / 指定行之后仍有畸形行的 `data_ragged`）会带着原文行与猜的 / 用的行号，概览原样带出（同样的上界与控制字符处理），`header_row` 是那个猜的 / 用的行号；真二进制、散文、编码错、空文件、太大等没有补救办法的 `raw_lines` 为空，XLSX 恒为空。用例覆盖 Codex 的仪器例子（无覆盖 → unreadable + `raw_lines` + 猜的行号；`header_row=3` → ok）。读不了**不是** HTTP 错误：200 + `status: "unreadable"` + 稳定码（含 `data_header_row_invalid`：指定的行读不出表，propose 对同一情形给 400 + 同一个码）；请求本身不对才是 400：路径出界 `data_path_outside_project`、文件不在（含链接、隐藏项、剪枝目录、非表格后缀、`~$` 锁文件、目录）`data_not_found`、工作表不存在 `data_sheet_not_found`、参数形状 `propose_input_invalid`。**没有新增错误码**（全是已登记的码）。

**路径**：只经 `normalize_rel`（字符串层）→ `projectenv.contained_path`（realpath 后钉在项目内）→ `discover.walk_files` 遍历项目并与结果**逐字比对**，打开的是遍历结果里的那一项，不拿请求字符串拼路径（与 `with_explicit_table` 同做法）。

**上界**（表里的文字是不可信输入，只显示）：前 20 行、列数 ≤ `MAX_COLUMNS`（64）、每格 ≤ 80 字符、列名 ≤ 200 字符、说明行 ≤ 20 条且每条 ≤ 200 字符、`raw_lines` 沿用 `PREVIEW_*`；单元格 / 列名里的控制字符（含制表符、换行、C1）换成空格，超长截断加 `…`。**XLSX 数值格的预览文字取 15 位有效数字**（与 Excel 显示一致：`0.6840000000000001` → `0.684`）：只对带小数点 / 指数、有效数字 > 15 位的十进制字面量改写，去尾零；绝对值在 [1e-9, 1e15) 用普通小数、更大 / 更小才用科学计数法（`1.5e-12`）；整数、日期、布尔、错误值、位数不多的原样。它只改 `preview_rows` 里给人看的文字，读表、类型判定、资格判定与出图用的数值不变（用例对拍 `inspect_data_source` 原文）；文本表的格子永远是原文。已知局限：读表层不区分「数值格」与「长得像浮点数的文字格」，XLSX 里一个有 16 位以上有效数字的文字格在预览里也会被取整。注意截断只在这个展示里：列名要回传给 propose 时用 propose 自己给的（`ask` 选项里的 `answer`），不要用概览里被截断的名字。

**XLSX 的行列数：放进 `/api/ai/data-files?formats=all` 的 `sheets` 每一项**（`{name, hidden, rows, columns, status, code}`），而不是让前端逐表调概览。理由：侧栏要一次拿齐，逐表调用 = 每个工作表打开一次 zip；列表本来就开一次工作簿。有预算，做法对齐文本表的 `table`：最多 30 个工作簿、合计 32 MiB、每个工作簿前 20 张表（`SHEET_COUNT_MAX`），超出的 `rows` / `columns` / `status` / `code` 是 `null`（= 没读，不是读不了）；读不了的那张表 `status: "unreadable"` + 稳定码。数字与概览端点同一份读法（`_summarize_book` + `parse_cell`），用例互相对拍。这改了 `sheets` 每项的键（原先只有 `name` / `hidden`），只在开关后面的 `formats=all` 里，开关关着响应逐字不变。

**脚本来源 `generated_from`**。v2 生成的脚本头有 `# tavotto-source: <JSON>`（`ai_data_template._source_comment`）。解析与生产在同一个模块（`SOURCE_COMMENT_PREFIX` / `SOURCE_GENERATOR` 两个常量共用，`parse_source_comment` 与 `_source_comment` 互为往返用例）：只看脚本开头 64 行 / 16 KiB；必须是 tokenize 认得的**真注释**（字符串 / docstring 里长得一样的一行不算）、第 0 列起、整行纯 ASCII 且 ≤ 4096 字符；**只认第一条**，它不合格就是没有；JSON 对象的键恰好是 `data` / `sha256` / `recipe` / `generator`，`generator` 必须是 `data_to_figure/v2`，`sha256` 是 64 位小写十六进制，`recipe.sheet` 缺省 / null / 1–255 字符无控制字符，`data` 是 1–1024 字符无 NUL / 反斜杠。格式不对一律当没有；**只外传** `{data, sheet}`，不给配方 JSON、不给哈希。`data` 在注释里是相对脚本目录的（`../sub/a.csv`），`ai_data_overview._data_path` 换算成**项目相对 POSIX 路径**（纯字符串：绝对路径 / 盘符 / 逃出项目 / 非表格后缀都当没有），**不核对数据文件还在不在**。

出口：`GET /api/registry` 的 `all_scripts[]`（素材库 / 脚本库本来就按脚本名 join 它；面板条目带 `script`，前端用它找到来源）。**只在认出来源的条目上加** `generated_from: {data, sheet}`，其余条目一个键都不加。开关关着时整个不读、不加，`/api/registry` 的响应与没有这个功能时**逐字相同**（用例比较开 / 关两份响应去掉该键后相等）。读脚本只读开头 16 KiB、不跟随链接、钉在项目内、最多读 2000 个脚本、基础设施脚本（`tavotto_plot_*`）不读；伪造的注释最坏结果是「某张脚本被标成来自某个项目内的表格路径」，路径只作前端的匹配键、从不被后端打开。

## 前端：首页入口与数据页（第一个前端 PR：一张表的整条链；`web/src/components/datastudio/`、`store/dataFigureStore.ts`）

整件事在 `ai.taf.enabled` 后面；**开关关着时首页、左轨与全部文案逐字不变**（`HomeView.taf.test.tsx` 把关着时的 DOM 与合入前主干上取的摘要逐字对拍）。前端只翻译后端的话，不判断、不编造读取进度 / 步骤。

* **开关怎么拿**：后端只有 `GET /api/ai/capabilities` 带 `taf_enabled`，它第一次调用要探测本机编码助手、可能很慢。首页不等它：`tafFlagStore` 先按本机记的上一次（`localStorage`，读写都不假设可用）画，后台问一次，回来不一样才换版；读不到当没变。助手面板已探测过就直接用它的结果。
* **首页**：开关开着时，大拖放卡拆成并排两张入口卡——左「只有数据，还没有图」只做点击（`useProjectEntry` 选一个文件夹，`entry:'folder'` 打开，成功后进数据页；打开期间 `view: 'entering'` 先占住数据页，不闪一帧画布），不收拖入（整页的拖放处理仍只认脚本 / 项目文件夹）；右卡就是原来的拖放 / 选脚本入口。老手版没有叙事句、示例脚本退成灰字链接；新手版同样换成两张卡（否则新用户没有入口）。
* **数据在左栏抽屉里，不是一个独立的页**：点左轨「数据」只开抽屉（`LeftTab` `'data'`，一棵文件树：`/api/ai/data-files?formats=all`，行尾 行数 / 没认准 / 读不了 / 没读；`table: null`、`sheets: null` 与工作表四项全空都是「没读」，不是「读不了」），画布与文档原样不动，没有任何东西盖在画布上。
* **点一个数据文件 = 在画布标签栏开（或复用）一个「临时标签」**（`DraftTabItem`，标题是文件名、斜体、带关闭）。它**不是画布**：不进 `documentStore.openTabs`、不进项目文件 / 导出 / 撤销栈，状态只在 `dataFigureStore`（`draftOpen` / `draftActive`），关掉即没；点真画布标签时它只是退到后台。内容区 `DataDraft` 盖在画布位置上（画布与文档不动，画布快捷键让位），右栏换成 `DataPanel`。点文件那一刻就读表并出候选，没有「开始」按钮；Esc 关临时标签，抽屉里 ↑↓ 换文件、临时标签跟着换。
* **一轮的流程**（`dataFigureStore`）：`propose` 带 `render: "lazy"`，**文字先到**——候选的名字 / 说明 / 推荐、列角色（`roles` + 每个角色可换成的列 `roles.options`）、实际用的表头行（`header_row`）、单位行（`units_row`，数据条里置灰）、非数字格（`mixed_cells`，默认不画）一次回来；懒模式不会返回 `ask`（万一返回，按失败处理，不替用户答）。四张缩略图由 `<img>` 各自取（后端取到才画，画不出 422，该项之后提交 404 `propose_candidate_unknown`）：到了淡入，422 的静默去掉、推荐标记挪给剩下的第一张，全部画不出走「这次没画出来」；图没到的磁贴不可点。右栏的横轴 / 纵轴 / 分组下拉与「N 格不是数字」下拉改任何一项都带着 `answers`（`y` / `x` / `group` 的列名或 `none`、`cells`）重新出候选，下拉不卸载；表头猜错就点原始行视图里对的那一行，带 `header_rows` 重来。后端与前端之间只隔一层 `lib/dataFigureAdapter.ts`。点一张 = 出图：被点的带扫光、其余淡掉，临时标签原地变成正式的图（`projectPreparationStore.open(scriptTarget(script), {run: true})`，即素材库里「打开并运行」那条现有路径，自动进单图快速编辑），画布工具条上方出一枚「用 X 画的 · 换个画法 · 撤销」（只在这一次会话里，`fresh` 不持久化）。
* **结局**：没落盘且项目 `writable === false`（或 `figures_dir_unusable`）是「换个文件夹」，其余「这次没画出来，数据没问题。」+「再试一次」；`mismatch` 留在临时标签里（脚本已落盘；Tavotto 不替用户删文件，写明脚本路径，桌面壳里给「在文件夹中显示」）；`unverifiable` / `unknown` / 缺字段（去重的回应）照样打开图，一句话走浮动通知（绝不写成已核对）；没画出来的行不再通知。`propose_session_expired` / `propose_data_changed` / `propose_candidate_unknown` 静默重新出候选、按 `title_code` 对号再提交一次，对不上就让用户看新的。一次点击一个 `client_request_id`，没收到结果的重试复用它。
* **store 规矩**：项目代际（`clear()` 在 `resetForNewProject` 里调，在飞请求按 `epoch` 作废）、每类请求一个序号、请求带发起那一刻的项目；键是 `tableKey({path, sheet})`（项目相对路径 + 工作表名的结构化键），`data-*` 里不放文件名（诊断轨迹会记 data 属性）。

## 修订（2026-11-18，用户决定）：一次选多份数据、各画一张（前端，`store/dataBatchStore.ts`）

用户定死的：不要勾选框；每份数据只取一种画法；一次出多张图后都进素材库的卡片、**不自动打开任何一张、不自动拼排版**；画完不提示少画行；点「数据」只开抽屉。

* **选法**（`DataList` 的 `multi`）：⌘ / Ctrl + 点加进 / 移出，Shift + 点选一段（按当前可见的表行顺序，起点是最近点的那一行），⌘ / Ctrl + 空格切换，Esc 清掉。只有状态是 `table` 的行（含 xlsx 的工作表行）能进多选，别的行 ⌘ / Shift 点没有反应。**入口「多选」**（`dataFigureV2.batch.pick`）：「数据」抽屉标题行右侧、图钉按钮左边（`DrawerHeaderActions` 槽），是与图钉同高（28px）的次按钮（`secondary` / `sm`，标题行里别处的文字按钮同一套），至少有 2 份状态为 `table` 的表时才出现；点它进入**多选态**（`dataBatchStore.pickMode`），按钮文字换成「取消」（点它退出并清掉选择，Esc 同；整批进行中禁用，出口是「停止」）。多选态下**普通单击 / Enter / 空格 = 加选 / 取消、不开临时标签**（读不了的行照旧单击看说明、不动选择；整批进行中点行 = 开它的临时标签看候选，不再改选择；没画成的行单击 = 开它的临时标签，见下）；⌘ / Ctrl 点、Shift 点选中第一份时自动进入多选态——手势和按钮是同一个状态。多选态下单份流程选中的那一行不再亮（只有「已选」这一种高亮）。`aria-multiselectable` + `aria-selected`。
* **文件行**（不要勾选框、不要「＋」、不要带底色的状态块、不要左侧彩色竖线、不新增颜色字面量；行高永远是 `h-7`）：行尾灰字行数任何时候都在（多选态也在）；**已选行 = 白底浮起的卡片**（`bg-surface` + `inset-ring inset-ring-border`，圆角沿用行的 `rounded-md`，字重 medium、不是 `bg-selected` + 粗体——与「素材」抽屉里激活项「surface 底 + 细描边」同一种语言；`shadow-card` 只许 `ui/Card` 写，所以这里用细描边），行尾行数换成对勾；相邻已选行之间仍是原来的行间距（列表 `gap-0.5`），不糊成一块。开画之后行尾：排队的是灰字「排队中」、正在画的是小转圈（这两种仍是已选卡片）、画完的是「已完成」（回到普通行）；**没画成的行是普通行**（不是选中卡片），行尾「失败」用 `text-ink-2`（比灰字 ink-3 深一档）+ 描边款 `OctagonAlert`（16px，与灰字同一档重量，不用实心图标、红字红底），原因句放 title；**单击没画成的行 = 打开它的临时标签**（单份流程自己会说清楚为什么画不了、让用户点对的那一行），这是它的出路。**纯平铺**（清单里没有文件夹 / 工作簿）时文件行不留树形箭头的空占位（`paddingLeft` 4px，图标左沿 = 标题文字左沿 12px）；有文件夹 / 工作簿时保持原样。文件名继续中间省略。
* **动作区**（`DataBatchActions`，取代旧的底部浮条与清单底部留白）：多选态下在标题行正下方、清单上方出现一块**固定高度**（`h-[4.75rem]`）的区域，透明底、底部一条细分割线，左 12px / 右 6px 与标题文字、图钉右沿对齐；进入 / 退出多选态时整块走 `ui/Field` 的 `Reveal`（高度过渡 token，减少动态效果时全局兜底直接切），清单整体下移 / 上移一次，之后从「已选 0 份」到进行中、没画成、画完高度全程不变，点选过程中清单不跳。两行：一句灰字状态（`未选择` / `已选 N 个` / `作图中 d/t` / `已完成 N 张，M 张失败`）+ 两个等宽并排的按钮。≥ 2 份才可用：主「分别作图」、次「合并作图」（少于 2 份都禁用）；进行中换成一个次按钮「停止」（`stopRequested`：画完当前这一份就停，后面的不画、仍留在选择里，已画的留着；点在最后一份上 = 它画完就是全部画成）；有没画成的换成主「查看素材」（切到素材抽屉；一张都没画成时禁用）+ 次「重试」（`run('failed')`：只重做没画成的；停下留下的没轮到的不跟着画）；写不进文件夹整批停下时主按钮是「换个文件夹」、「重试」= 接着画整个选择（`run('all')`，含没轮到的）。
* **一份的做法**：`propose`（`render: "lazy"`，不带 `answers`；用户在单份流程里改过的表头行带 `header_rows`）→ 取第一张候选（**合同：推荐永远在第一项**，`ai_data_propose.item_to_dict(…, k == 0)`；它提交时渲染 / 核对不过回 404 `propose_candidate_unknown`，就依次试后面的）→ `commit` → `discard` 会话（后端同时至多 `MAX_SESSIONS = 6`）→ 刷新项目（`refreshProjectNow`，新脚本进素材清单，**不打开**）。随后只运行这份新脚本（`scriptRunStore.run`，与素材卡上的 ▷ 同一条路径，不打开、不放进画布），好让卡片有预览；运行也排进同一条串行队列，运行失败不算没画出来（脚本已在，卡片自己显示它的状态）。不取缩略图。逐份**顺序**做，不并发：后端每次提交重算可用文件名，并发会抢同一个名字；单份出图与批量共用一把串行锁（`lib/dataCommitQueue.ts`，一条 promise 队列），任何时刻只有一个提交在飞。批量进行中用户仍可点别的文件看候选、切画布标签。
* **状态**：批量有自己的一组（选了哪几份、每份的进行态、整批进度），不借单份流程的 `commitRid` / `drawingId` / `fresh`；每份的状态按 `tableKey` 存（清单重读时行对象会换，状态不能丢）；文件名不进 `data-*`。
* **终态**：全部画成（选择清空且没有没画成的）→ 清多选、退出多选态、左栏切到素材抽屉，不弹任何通知（「分别作图」全部成功的行为不变）；部分 / 全部没画成 → 留在数据抽屉，画成的「已完成」、没成的「失败」（原因句放 title：`nothing` 用 `dataFigureV2.reason.*`，数据与图对不上用 `result.mismatch`，后端给的错误用 `backendErrorMsg`）并保持选中；表没有可画的（`nothing` / 全是文字 / 表头没认准）算没画成；项目文件夹写不进（`figures_dir_unusable` / `common_module_conflict` / 项目只读）立刻停整批；中途切项目 / 关项目（`captureProjectEpoch` + store `epoch`）静默中止，已写出的就是普通脚本；脚本已落盘但数据与图对不上算没画成（脚本留着，Tavotto 不替用户删文件），核对没完成 / 没法逐项核对算画成、不弹提示；会话过期 / 数据变了静默重新出一次。新一轮开始（没有没画成的在等重试）时，上一批留下的「已完成」标记作废，免得「已完成 N 张」把它们算进来；取消选一份没画成的 = 撤掉它的「失败」。
* **合画一张**（把选中的几份画进同一张图，每份文件是一组）：点「合并作图」→ 退出多选态 → 像点单个文件一样开临时标签（标签名 / 右栏标题 `dataFigureV2.batch.groupTitle`：「N 个文件」），里面是候选画法网格，点一张 = 和单份一样生成脚本并在画布打开（不出「换个画法」胶囊：它只认单份）。`dataFigureStore` 加可选的 `group: TableRef[] | null`（`selected` 是其中第一份，数据条 / 概览认它；`select` / `closeDraft` / `clear` 复位，`select` 的「同一份已开着」捷径对组无效），`propose` 发请求时有 `group` 就发 `tables: [{path, sheet?}]`（≥ 2 份，其余字段同单份，`render: "lazy"`），不发 `table`；合画不做第一份的「全是文字 / 要点表头行」判断，交给后端。后端合同（`feat/d2f-merge-tables`）：成功响应与单份同形，分组角色默认是后端合成的那一列；几份表列名不一致回 400 `tables_columns_differ`，份数超上限（`MAX_MERGE_TABLES` = 8，前端只在 `lib/dataFigure.ts` 写一处）回 400 `tables_limit_exceeded`（`params.max`，文案在 errors 命名空间「一次最多合并 {{max}} 个文件。」）。选中超过 8 个时「合并作图」禁用、title 用这一句，「分别作图」不受限。**出路**：两个码各是一个 `DraftPhase`（`columnsDiffer` / `limitExceeded`），某份表不在了是 200 `nothing` + `table_not_found`，三者临时标签里都是一句话（`columnsDiffer`「列名不一致，无法合并作图。」，`limitExceeded` 是后端那句，`table_not_found` 沿用「找不到你选的那张表。」）+ 主按钮「分别作图」——点了关掉临时标签、恢复这几份的选择（`dataBatchStore.drawEach`）、回数据抽屉并直接开始各画一张；不给「再试一次」（点多少次都一样）。

## 注入分析：Agent 能读整个文件，表里的文字能影响模型

表里的文字（列名、单元格、工作表名）可以诱导模型，但**模型能交出的只有一份闭集配方**：未知键、未知取值、层数不对、`why` 含换行都在 `from_dict` 当场拒收；
列名只作为「列名」被后端按 `FolderProfile` 逐项核对（必须逐字存在），文字不会成为代码，也不进脚本；`why` 只显示。模型没有写文件、shell、网络工具，写不了文件、跑不了代码。
所以注入最坏的结果是「推荐了一个不合适的备选」，而它仍要通过同一套资格判定与排序护栏；用户的数据文件永不被改写。

## 从 v1 到 v2 的扩展点

v1 的 `plan_mapping` / `render_script` / `ai_data_task.py` / 端点 / 前端组件**逐字不变**。v2 在下面叠加：读表层（TSV / XLSX、整文件夹摘要，PR #1827）→ 本 ADR 的配方与资格 →
模板（批次 1：comparison / trend / relationship / distribution）→ 工具集（`data_to_figure` 的只读五工具）与端点 → 前端 → 模板批次 2 / 3（paired / matrix / composition / ranking；
interval / trajectories / dose_response / profile）。批次 2 / 3 在本 PR 已有完整的资格判定，但 `renderable()` 为假：**注册表只放真能跑的**（同 `AGENT_REGISTRY` 的纪律），模板逐族打开。
候选在缓存里的候选目录渲染，不写进用户文件夹；「换个画法」就是换配方重生成一份新脚本。

## PR 分批

1. 读表与文件夹摘要（#1827）；2. 本 PR：配方、资格判定、排序护栏、选图准则、本 ADR；3. 模板批次 1（渲染 + 数据指纹对拍）；4. 出备选 / 候选目录 / 落盘 / 端点（不调模型，上一节；`ai_tasks.run_task` 只为「确定性执行器只新建文件」放行写入），随后是模型工具集与多轮提问；
5. 前端（浅 / 深色截图先给用户看再开 PR）；6. 模板批次 2 / 3。FigureBench 将加一组「数据 + 提问 → 应排第一的画法 / 绝不该出现的画法」用例；真实模型跑分要花钱，跑之前先报预算。

## 参考仓库与许可证边界

* `figures4papers` 是 **CC BY-NC 4.0**：它的**代码与配色表一律不得进入本仓库**。只借三个想法：语义配色角色、图例单独占一格、灰度下可分的填充。样式取自出版规范与所选期刊。
* `polish-sci-figures` 是 **MIT**：借它的纪律——按数据结构路由、同图幅多备选、列名猜出的角色不算已声明、能露出原始分布时不默认柱图；**实现自写**，不搬代码。

## 不做的事

显著性检验与统计结论；模型写 matplotlib（桑基、网络、地图等画不了就如实说「目前画不了」并给最接近的画法）；双 y 轴（量纲不同的量分面）；
显微图像处理、多格拼版自动排版、联网查文献；遥测不收列名、路径、指令、图中文字（新事件攒到下次 `CONSENT_VERSION` 升版）。

## 后果

* 图的丰富度由「族 × 可叠图元」的组合决定，加一类图 = 在三张表里加条目 + 一个模板 + 测试，不扩大执行面。
* 推荐的科学纪律（原始点优先、轴含 0、不连无序类别、误差必须写明）既在准则里告诉模型，也在后端逐项强制——模型说错了不会漏到用户面前。
* 取值类规则要读行数据：调用方（工具集与端点）对选中的那张表 `inspect_data_source` 一次再传 `values=`；只用摘要的路径要知道「合格」只代表结构合格。
