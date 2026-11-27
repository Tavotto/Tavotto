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

## 修订（2026-11-18）：认 GROMACS / Grace 的 `.xvg`（`ai_data_figure.xvg_text` / `xvg_label`）

**为什么**：分子动力学的 `gmx energy` / `gmx rms` / `gmx gyrate` … 全是 `.xvg`——`#` 开头是注释、`@` 开头是 Grace 指令、数据是空白分隔的数值列，**没有表头行**，列名写在指令里。用户要把它们直接拖进数据出图。

**做法：把 xvg 改写成「带表头行的制表符文本」，再走同一条读表流水。** `read_text_table` 对 `.xvg`（`XVG_SUFFIXES`，进 `TABLE_SUFFIXES`，所以发现遍历、数据概览、`/api/ai/data-files?formats=all`、propose、提交都认）解码之后先调 `xvg_text`，其余（嗅探、`table_from_text`、摘要、行号、编码、`header_rows` 覆盖）一字不动。`xvg_text` 输出**行数与原文一致**，所以物理行号照旧：

* **只读第一个数据集**。数据集在一行 `&` 处、或数据开始之后再出现 `@target` / `@type`（Grace 的新数据集指令）处结束，其后的行一律清空。**不把多个数据集当成多张表**：「表」在整条链路里是 `(path, sheet)`，多数据集要新造一种 sheet 标识并让配方 / 脚本 / 提交都认，不便宜；需要时用户可以先用 `gmx` 或脚本拆开。多条曲线（`gmx energy` 选了多项）本来就是一个数据集里的多列，不受影响。
* `#` 注释行与 `@` 指令行读完即清空（留着的话，列数碰巧与数据列数相同的注释会被嗅探当成表头）；这意味着 xvg 在数据概览里没有「表头前的说明行」。数据行按空白切开、用制表符连。
* **列名**：`@ xaxis label "…"` → 第 1 列；`@ s0 legend "…"`、`@ s1 legend "…"` … → 第 2、3 … 列；**只有两列**（x + 一条 y）且没有 `s0 legend` 时用 `@ yaxis label`；缺的列叫 `col<N>`（N 从 1 数）；一个名字都没有就**不写表头行**，交给既有的「没有表头」流程（猜第一行当表头、状态 `unsure`、用户点一下确认）。表头写在第一条数据行的**前一行**（原来那一行指令 / 注释被它替换）；数据从第 1 行起就没有地方放，同样不写表头。重名列沿用既有规则（`data_header_invalid`，不另起一套去重）。
* **Grace 标签清洗**（`xvg_label`，保守；规则逐条有用例）：去掉上 / 下标与回正常的标记 `\S` `\s` `\N` 和字体切换 `\f{…}`，**文字留着**（`nm\S2\N` → `nm2`，`H\s2\NO` → `H2O`）；`\x` 加一个字母按 Symbol 字体换成希腊字母（`\xl\f{}` → `λ`，`\xD\f{}` → `Δ`，小写 a b c d e f g h i k l m n o p q r s t u w x y z → α β χ δ ε φ γ η ι κ λ μ ν ο π θ ρ σ τ υ ω ξ ψ ζ，大写 D F G L P Q S U W X Y → Δ Φ Γ Λ Π Θ Σ Υ Ω Ξ Ψ）；认不出的（`\q`、`\x` 后不是字母）**原样保留**；空白折叠。只认文件里的单个反斜杠写法（`\S`），不猜双反斜杠。
* 读不了的稳定码都是既有的：空文件 `data_empty`、只有注释 / 指令 `data_not_table`、数据行列数不齐 `data_ragged`、含 NUL `data_binary`、重名列 `data_header_invalid`。

**同源**：`xvg_text` / `xvg_label` 是运行时函数，进 `RUNTIME_FUNCTIONS`、被内嵌进公共模块（与合画的 `merge_tables` / `group_key` 一起，公共模块哈希由 `86dcfebb` 变为 `8dccb037`（`tests/test_ai_data_encodings.py::PINNED_RUNTIME_NAME` 同步）；已生成的老脚本继续 import 它们当时的那一版）。生成的 xvg 脚本读表是 `table_from_text(xvg_text(DATA.read_bytes().decode('utf-8-sig')), '\t', <表头行>)[:2]`（编码非 UTF-8 时 codec 照旧写字面量），父进程读出来的是同一张表；其它格式的脚本除公共模块名外逐字不变。原文件只读。
## 修订（2026-11-18）：合画——几份列名相同的数据画进同一张图，每份文件是一组（`ai_data_merge.py`）

**动机**：重复实验常按条件各存一份文件（`ctrl.csv` / `drugA.csv` / `drugB.csv`，列名一样），用户要的是「这几份画进同一张图、一份一组」。不改配方的闭集，也不让用户先去手工合表：**只在内存里把几份拼成一张逻辑表**，再走原来的出备选流水。

**合同**（`POST /api/ai/data-figure/propose`；功能仍在 `ai.taf.enabled` 后面；单份请求的请求、响应、生成的脚本逐字不变——公共模块文件名里的哈希除外，见下「公共模块」）：

* 请求：把 `table` 换成 `tables: [{path, sheet?}]`，**2 到 8 份**（`path` 与 `table` 同口径：相对所选 `folder` 的 POSIX 路径；`sheet` 只对 XLSX）。`table` 与 `tables` 同时给 → 400 `propose_input_invalid`（都不给 = 原来的文件夹流程）；不是列表、不足 2 份、项形状不对、同一份（同文件同工作表）给了两次 → 400 `propose_input_invalid`；超过 8 份 → 400 `tables_limit_exceeded`（`params.max`）。其余字段（`render` / `answers` / `header_rows` / `hint` / `client_request_id`）同单份；`header_rows` 的键是各份的路径，**每份各管各的**。
* **上限取 8 的理由**：合成的文件列做颜色分组，配方里颜色分组上限是 `MAX_GROUP_LEVELS = 8`；实测 9 份起趋势 / 关系 / ECDF 的备选全部 `group_limit_exceeded` 被剔除，只剩分组比较。恰好 8 份时四个备选一个都不丢。
* **逐份读**：每份各按单份读法（`inspect_data_source(v2=True)`：编码 / 分隔符 / 表头行 / 单位行 / 千分位 / XLSX 工作表）。某份不是所选文件夹里发现遍历认得的表 → 200 `{kind:"nothing", reasons:["table_not_found"]}`（与单份指名一张不存在的表同形）；读不了 / 没认准表头 / 路径出界沿用单份对应的 400 稳定码。各份**列名集合与顺序**必须一致，否则 400 `tables_columns_differ`（`params.index` = 第一个与第一份不同的下标）。合计字节（同一个工作簿的几张表只算一次）超 `MAX_TOTAL_BYTES`（4 × 5 MiB = 20 MiB）→ 沿用 400 `data_too_large`（前端现有文案写「超过 5 MB」，对合计超限不准确：已知问题，本改动不动已上线的单份文案）；拼后行数 / 列数超限沿用 `data_too_long` / `data_too_wide`。
* **拼表**（**一份实现**：运行时函数 `ai_data_template.merge_tables`，脚本与父进程调同一个；拼好的摘要仍由 `adf._summarize` 判，没有第二套分类器）：按给定顺序逐份拼行，**表头末尾**加一列合成的分组列。列名 `file`，与现有列重名（不区分大小写）加数字后缀 `file_2` …（字母后缀会被当成单位后缀、`_id` 会被当成编号列）。值 = 文件标签：文件名去扩展名；同名时依次带上工作表名（同一工作簿的几张表：`book (Sheet1)`）、父文件夹（逐级往上，`exp1/data`）、完整文件名（`a.csv` / `a.tsv`），最后兜底 `#2`；标签是数 / 日期 / `nan`（`1.csv`、`2025-01-03.csv`）会让这一列不再是「分组」，这时整批改用完整文件名。**唯一性判据与分组键同源**：分组键是运行时函数 `group_key`（`cell.strip()`，区分大小写、不规范化——各族的 `*_data` 与摘要的取值集合都是这个写法），标签分配再收紧成 `unique_key`（`group_key` + NFC + 不区分大小写），每一级候选都与**全部**已定标签比较；最后的兜底 ` #n` 从 2 递增到与全部已占用标签（含别的文件本来的标签）都不撞为止（反例：`a.csv` / `A.csv` / `A.csv #2.csv`）。**硬保险**：`merge_tables`（脚本与父进程同一个函数）发现两个标签的分组键相同就 `ValueError`，父进程 `build()` 在拼好之后再按分组键数一遍、组数必须等于文件数，否则 400 `tables_labels_collide`（内部保险，正常到不了；绝不静默把不同文件并成一组）。
* **分组角色默认是这一列**：用户没在 `answers.group` 里指定时 `picks.group = 合成列`（用户换列或写 `none` 就用他的；这一列成不了分组——如每份只有一行——按原规则）。懒模式下，若原本要问「按哪一列分组」（文件里还有别的分组列），`defaults` 里记 `{question_code:"pick_group", answer:{group:<合成列>}}`；`roles.group` 即此列。
* **响应**与单份同形，另加：`sources: [{path, sheet, label, header_row, units_row}]`（`path` 与请求一致，`sheet` 是实际用的工作表，`header_row` 是文本表格实际用的表头物理行号）、`group_column`（合成列名）。`items[].values.table` = 第一份的 `path`；懒模式响应的 `table` 同。没画的行（`unplotted` 与提交结果里的 `rows`）与「夹着文字格」预览里的行号是**那一份自己的文件行号**，条目多一个 `file`（该份标签）；单份没有这个键。
* **夹着文字格的规则**在拼好之后套（`apply_cells` 逐格独立，等于各份各自套再拼；类型与 `numeric_like` 按拼好的表判）；单位行、用户指定的表头行都是「各份各自按单份规则处理后再拼」。
* **文件里原本就有分组列时**（颜色已经给了合成列）。按「在每个文件内有几个取值」分两类（`ai_data_merge.classify_groups`，不含合成列与编号列）：① **每个文件内最多一个取值**（与「文件」是同一件事）→ **忽略**：从分组列里去掉（不当颜色 / 分面 / 比较的横轴、也不在 `roles.options.group` 里），拆小图只会把每个文件重复一遍；② **某个文件内有多个取值** → 候选里**同时给两种**：现状的（按文件上色、再按那一列分面 / 比较）与**只按文件比较**的（把那一列当不存在重新出一遍备选，同一套校验与排序）。排序不改：现状的在前，推荐仍是它的第一张（沿用既有打分），每张后面紧跟它只按文件比较的那张；两种画法画出来一样的（配方除 `why` 外相同，如散点只用一个分组）只留一张、不加标记；只有「只按文件」才画得出的排在最后。这种响应里每个 `items[]` 多一个 `variant`：`{"kind":"split","column":列名}` / `{"kind":"by_file"}` / `null`（没有成对的另一张）；标题与说明仍是既有的 `title_code` / `why_code`（两张同码），前端在标题旁用 `ai:dataFigureV2.variant.split|by_file`（「按「{{column}}」再分开」/「只按文件比较」，两种语言）区分。用户在右栏指定了分组列（`answers.group`）时不成对。候选总数因此最多 8 个；单份响应与没有这种列的合画响应没有 `variant` 键。
* **缩略图 / 提交端点不变**（按 session）。候选目录里同一会话放这几份的字节副本（布局 = 落点相对路径；同一工作簿只复制一次），磁盘上界按 `MAX_TOTAL_BYTES` 算；`MAX_SESSIONS`、TTL、放弃即删不变。

**脚本与落点**：落在这几份的**最近公共父文件夹**（相对所选 `folder`、仍在项目内）下的 `tavotto_figures/`——几份在同一文件夹就是那个文件夹，不在同一文件夹就是它们共同的祖先（可能是 `folder` 本身）；这与单份不同（单份永远是所选 `folder` 下），因为合画的数据是用户逐份点选出来的、没有「所选文件夹」这个整体。名字 = `<文件名公共前缀>_<纵轴列名>_by_file`（`ai_data_merge.merged_stem`，**提交时按所选备选的配方定**：growth_control / growth_drugA / growth_drugB + `tumor_volume` → `growth_tumor_volume_by_file.py`）：公共前缀取各文件名（去扩展名）的最长公共前缀，按词边界截断（前缀以 `_` `-` 空格结尾，或与各文件名下一个字符是字母 / 数字的交界；切断了一个词 / 一串数字就先去尾部数字、再退到最后一个分隔符）、去掉尾部分隔符；没有或短于 3 个字符就用第一份的 stem；纵轴列名与前缀都按 `[A-Za-z0-9_-]` 安全化（与 `stem_for` 同一条字符规则），纵轴最多 40 字符、总长不超过 80；重名走现有的 `_2` 递增。**留给模型的接缝**：`ai_data_merge.STEM_SUGGESTER`（`(paths, y) -> str | None`）——以后接模型时由它建议名字，建议不合格 / 抛异常 / None 一律退回规则名；现在不接。脚本顶部 `DATAS = [Path(__file__).resolve().parent / '../<相对落点的路径>', …]`、`SHEETS`、`LABELS`、`FILE_COLUMN`，`main()` 里 `tp.merge_tables([每份各自的读表表达式…], LABELS, FILE_COLUMN)`——每份的读法（分隔符 / 表头行 / 单位行 / 编码 codec / 工作表）与单份脚本里同一个 `_reader_expr`，脚本不探测。只**新建**：脚本与公共模块都经 `scriptlock.create_script`；原件一字节不动。来源注释的 `generator` 是 `data_to_figure/v2-merge`，`parse_source_comment` 只认 `data_to_figure/v2`，所以合画脚本不会被数据概览当成「来自某一份数据」（不放宽严格解析器）。

**提交**：`Selection` 多一个 `merge`（各份、合成列、落点）；`prepare_v2` 对落点下每份各自钉在项目内（`contained_path`）、sha256 必须还是出备选时那一份（任何一份变了 → 409 `propose_data_changed`，什么都不写），再用同一组标签与列名重新拼；期望指纹与 `unplotted_rows` / `cell_stats` 用拼好的摘要（与脚本同源）。任务契约 `input_refs` 每份一条 `data_file`（1 到 8 条；单份仍 1 条），`plan` 与提交前 `project.data` 对每份各核一遍 sha256。

**公共模块**：`merge_tables` 进 `RUNTIME_FUNCTIONS`，内容哈希由 `86dcfebb` 变为 `642ce634`（`merge_tables` 与 `group_key`；`tests/test_ai_data_encodings.py::PINNED_RUNTIME_NAME` 同步）；已生成的老脚本继续 import 它们当时的那一版。单份脚本在改动前后逐字比对（从 origin/main 的源码树与本改动各生成一遍）：82 个（四族的备选 × CSV / TSV / XLSX / GB18030 / UTF-16LE × 多种列角色）与 16 个（`skip` / `extract` 两种夹文字格规则）全部只差 `tavotto_plot_<8 位>` 这个名字（XLSX 的来源注释里的 `sha256` 因测试夹具每次生成的 zip 时间戳不同，比对前已归一）；单位行没有进这批逐字比对（夹具没被嗅探成单位行），由 `test_ai_data_propose.py` 里切单位行的提交用例与 `test_ai_data_text_tables.py` 看护。

**错误码**：新增 `tables_columns_differ`、`tables_labels_collide`、`tables_limit_exceeded`（份数超限不叫 `tables_too_many`：`_many` 是 i18next 的复数后缀，`scripts/i18n-check.mjs` 会把 `errors:backend.tables_too_many` 当成 `tables_too` 的复数形态而拒绝；`ai_data_propose.ERROR_CODES`、`test_error_codes.py`、前端 `errors.json` 两种语言）。**没做**：合画脚本在脚本清单里不显示「来自哪份数据」；`generated_from` 对它返回 None。

## 修订（2026-11-18）：提交的请求号在耗时步骤之前先占位（`app._COMMIT_INFLIGHT`）

**窗口**：`/api/ai/data-figure/commit` 原先先取会话、`ensure_thumbnail`、`prepare_v2`，到 `run_task` 才把请求号登记进任务台账（ADR 0132 的 `engine_ai.find_request` 查的就是它）。客户端在 prepare 期间断线、放弃会话后用**同一请求号**重试：`find_request` 还查不到 → 去取会话 → `propose_session_expired`，客户端据此认为「什么都没写过」而换号重画；但原 handler 还在 prepare，随后照样写出脚本——两份。

**做法**：handler 校验完参数、**在任何可能耗时的步骤之前**，对 `(项目, 请求号)` 在进程内的在途表（带锁的集合）做原子占位。同一请求号的第二个请求进来发现在途，**不取会话、不 prepare**，回与「任务未完成」同形的响应：200 `{task: {task_id: null, outcome: null, final: false, deduped: true}}`（前端对这种响应本来就保留请求号并轮询）。占位在 `finally` 里清——成功登记 / 失败（`DataSourceError` 等 4xx）/ 未处理异常（500）都清，所以不存在「永远在途」；清了之后同一请求号再来：任务已登记 → `find_request` 去重回最终结果（`deduped: true`，带既有脚本）；没登记（原请求在登记前就失败了）→ 一次确定的新提交（会话还在就完成，不在就 `propose_session_expired`，此时原 handler 已结束、确实什么都没写）。不同请求号、不同项目互不影响；不带请求号的提交不占位（行为不变）。

**进程重启**：占位只在内存里，随进程消失；此时原 handler 的线程也没了、不会再写，同一请求号重来要么 `find_request` 查到已登记的任务（照旧去重），要么是一次全新的提交——没有重复写入的路径。

**没改**：v1 `POST /api/ai/data-figure`（`prepare` 先于 `run_task` 登记，同样有「两个 handler 同时 prepare」的窗口，但它没有候选会话，不会走到「会话已放弃 → 过期 → 换号重画」那条把重复变成必然的路径）与改图提交（ADR 0132）只在这里说明，不改。看护：`tests/test_ai_data_commit_inflight.py`（在 `prepare_v2` 上打桩卡住第一个请求：同号重试得到未完成、第二个请求不碰会话、放行后只有一份脚本、第三次查询得到最终结果；prepare 抛 `DataSourceError` / 未处理异常都清占位、同号再来得到确定答案；不同请求号不互相阻塞）。

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
* **结局**：没落盘且项目 `writable === false`（或 `figures_dir_unusable`）是「换个文件夹」，其余「这次没画出来，数据没问题。」+「再试一次」；`mismatch` 留在临时标签里（脚本已落盘；Tavotto 不替用户删文件，写明脚本路径，桌面壳里给「在文件夹中显示」）；`unverifiable` / `unknown` / 缺字段（去重的回应）照样打开图，一句话走浮动通知（绝不写成已核对）；没画出来的行不再通知。`propose_session_expired` / `propose_data_changed` / `propose_candidate_unknown` 静默重新出候选、按 `title_code` 对号再提交一次，对不上就让用户看新的（2026-11-27 收紧：除了画法身份，配方与误差线也要逐项相同——`dataFigureAdapter.sameDrawing`；身份相同但列 / 分组 / 误差换了是另一张图，不自动提交，主图停在这个画法上让用户看过再点）。一次点击一个 `client_request_id`，没收到结果的重试复用它。
* **store 规矩**：项目代际（`clear()` 在 `resetForNewProject` 里调，在飞请求按 `epoch` 作废）、每类请求一个序号、请求带发起那一刻的项目；键是 `tableKey({path, sheet})`（项目相对路径 + 工作表名的结构化键），`data-*` 里不放文件名（诊断轨迹会记 data 属性）。

## 修订（2026-11-18，用户决定）：一次选多份数据、各画一张（前端，`store/dataBatchStore.ts`）

用户定死的：不要勾选框；每份数据只取一种画法；一次出多张图后都进素材库的卡片、**不自动打开任何一张、不自动拼排版**；画完不提示少画行；点「数据」只开抽屉。

* **选法**（`DataList` 的 `multi`）：⌘ / Ctrl + 点加进 / 移出，Shift + 点选一段（按当前可见的表行顺序，起点是最近点的那一行），⌘ / Ctrl + 空格切换，Esc 清掉。只有状态是 `table` 的行（含 xlsx 的工作表行）能进多选，别的行 ⌘ / Shift 点没有反应。**入口「多选」**（`dataFigureV2.batch.pick`）：「数据」抽屉标题行右侧、图钉按钮左边（`DrawerHeaderActions` 槽），是与图钉同高（28px）的次按钮（`secondary` / `sm`，标题行里别处的文字按钮同一套），至少有 2 份状态为 `table` 的表时才出现；点它进入**多选态**（`dataBatchStore.pickMode`），按钮文字换成「取消」（点它退出并清掉选择，Esc 同；整批进行中禁用，出口是「停止」）。多选态下**普通单击 / Enter / 空格 = 加选 / 取消、不开临时标签**（读不了的行照旧单击看说明、不动选择；整批进行中点行 = 开它的临时标签看候选，不再改选择；没画成的行单击 = 开它的临时标签，见下）；⌘ / Ctrl 点、Shift 点选中第一份时自动进入多选态——手势和按钮是同一个状态。多选态下单份流程选中的那一行不再亮（只有「已选」这一种高亮）。`aria-multiselectable` + `aria-selected`。
* **文件行**（不要勾选框、不要「＋」、不要带底色的状态块、不要左侧彩色竖线、不新增颜色字面量；行高永远是 `h-7`）：行尾灰字行数任何时候都在（多选态也在）；**已选行 = 白底浮起的卡片**（`bg-surface` + `inset-ring inset-ring-border`，圆角沿用行的 `rounded-md`，字重 medium、不是 `bg-selected` + 粗体——与「素材」抽屉里激活项「surface 底 + 细描边」同一种语言；`shadow-card` 只许 `ui/Card` 写，所以这里用细描边），行尾行数换成对勾；相邻已选行之间仍是原来的行间距（列表 `gap-0.5`），不糊成一块。开画之后行尾：排队的是灰字「排队中」、正在画的是小转圈（这两种仍是已选卡片）、画完的是「已完成」（回到普通行）；**没画成的行是普通行**（不是选中卡片），行尾「失败」用 `text-ink-2`（比灰字 ink-3 深一档）+ 描边款 `OctagonAlert`（16px，与灰字同一档重量，不用实心图标、红字红底），原因句放 title；**单击没画成的行 = 打开它的临时标签**（单份流程自己会说清楚为什么画不了、让用户点对的那一行），这是它的出路。**纯平铺**（清单里没有文件夹 / 工作簿）时文件行不留树形箭头的空占位（`paddingLeft` 4px，图标左沿 = 标题文字左沿 12px）；有文件夹 / 工作簿时保持原样。文件名继续中间省略。
* **动作区**（`DataBatchActions`，取代旧的底部浮条与清单底部留白）：多选态下在标题行正下方、清单上方出现一块**固定高度**（`h-[4.75rem]`）的区域，透明底、底部一条细分割线，左 12px / 右 6px 与标题文字、图钉右沿对齐；进入 / 退出多选态时整块走 `ui/Field` 的 `Reveal`（高度过渡 token，减少动态效果时全局兜底直接切），清单整体下移 / 上移一次，之后从「已选 0 份」到进行中、没画成、画完高度全程不变，点选过程中清单不跳。两行：一句灰字状态（`未选择` / `已选 N 个` / `作图中 d/t` / `已完成 N 张，M 张失败`）+ 两个等宽并排的按钮。≥ 2 份才可用：主「分别作图」、次「合并作图」（少于 2 份都禁用）；进行中换成一个次按钮「停止」（`stopRequested`：画完当前这一份就停，后面的不画、仍留在选择里，已画的留着；点在最后一份上 = 它画完就是全部画成）；有没画成的换成主「查看素材」（切到素材抽屉；一张都没画成时禁用）+ 次「重试」（`run('failed')`：只重做没画成的；停下留下的没轮到的不跟着画）；写不进文件夹整批停下时主按钮是「换个文件夹」、「重试」= 接着画整个选择（`run('all')`，含没轮到的）。
* **一份的做法**：`propose`（`render: "lazy"`，不带 `answers`；用户在单份流程里改过的表头行带 `header_rows`）→ 取第一张候选（**合同：推荐永远在第一项**，`ai_data_propose.item_to_dict(…, k == 0)`；它提交时渲染 / 核对不过回 404 `propose_candidate_unknown`，就依次试后面的）→ `commit` → `discard` 会话（后端同时至多 `MAX_SESSIONS = 6`）→ 刷新项目（`refreshProjectNow`，新脚本进素材清单，**不打开**）。随后只运行这份新脚本（`scriptRunStore.run`，与素材卡上的 ▷ 同一条路径，不打开、不放进画布），好让卡片有预览；运行也排进同一条串行队列，运行失败不算没画出来（脚本已在，卡片自己显示它的状态）。不取缩略图。逐份**顺序**做，不并发：后端每次提交重算可用文件名，并发会抢同一个名字；单份出图与批量共用一把串行锁（`lib/dataCommitQueue.ts`，一条 promise 队列），任何时刻只有一个提交在飞。批量进行中用户仍可点别的文件看候选、切画布标签。
* **排队的回调与不确定的提交**：共用串行队列里的每个回调（批量的 propose / commit / 只运行，单份流程的 commit）轮到时先核发起那一刻的项目与代际（`captureProjectEpoch` / `owner` 快照在**排队之前**取），对不上就什么都不发——`runScript` 取的是此刻的项目，A 的相对路径不能在 B 里被运行。批量某份的 commit 结果不确定（断网 / 网关，后端可能已写出脚本）时不试下一张候选，**只留这一份的请求号**（连同重发要带的会话 / 备选 id 两个字符串，按 `tableKey` 存，换项目清掉），会话照常放弃——后端同时至多留 `MAX_SESSIONS = 6` 个候选会话，批量不为不确定的行占会话、不挤掉用户开着的临时标签。「重试」用同一个请求号重发 commit：后端 `/api/ai/data-figure/commit` 先按请求号去重、后取会话，首次提交已落盘而响应丢了时，会话不在也回原结果（只回既有任务的结局）；首次请求根本没到后端时没有记录，重发回 404 `propose_session_expired`（什么都没写过），就重新出候选、换新请求号；其余明确拒绝（`isDefiniteRunFailure`）同样作废请求号重新出。
* **同一请求号重发时原任务还在处理中**：后端去重分支回当前快照（200、`task.deduped === true`、`task.final === false`、`outcome === null`、没有 `script`）——既不算失败也不算画成：请求号保留，`lib/dataCommitPoll` 按有界间隔（1.5 s，最长 60 s，写在 `commitPollConfig` 一处）重问同一个请求号直到 `final`；超时仍未完成 = 这一份 / 这次标失败但请求号仍在，下次重试继续问同一个，不换号重画。批量与单份流程（`dataFigureStore.commit`）共用；换项目 / `clear()` 后停止重问。
* **状态**：批量有自己的一组（选了哪几份、每份的进行态、整批进度），不借单份流程的 `commitRid` / `drawingId` / `fresh`；每份的状态按 `tableKey` 存（清单重读时行对象会换，状态不能丢）；文件名不进 `data-*`。
* **终态**：全部画成（选择清空且没有没画成的）→ 清多选、退出多选态、左栏切到素材抽屉，不弹任何通知（「分别作图」全部成功的行为不变）；部分 / 全部没画成 → 留在数据抽屉，画成的「已完成」、没成的「失败」（原因句放 title：`nothing` 用 `dataFigureV2.reason.*`，数据与图对不上用 `result.mismatch`，后端给的错误用 `backendErrorMsg`）并保持选中；表没有可画的（`nothing` / 全是文字 / 表头没认准）算没画成；项目文件夹写不进（`figures_dir_unusable` / `common_module_conflict` / 项目只读）立刻停整批；中途切项目 / 关项目（`captureProjectEpoch` + store `epoch`）静默中止，已写出的就是普通脚本；脚本已落盘但数据与图对不上算没画成（脚本留着，Tavotto 不替用户删文件），核对没完成 / 没法逐项核对算画成、不弹提示；会话过期 / 数据变了静默重新出一次。新一轮开始（没有没画成的在等重试）时，上一批留下的「已完成」标记作废，免得「已完成 N 张」把它们算进来；取消选一份没画成的 = 撤掉它的「失败」。
* **合画一张**（把选中的几份画进同一张图，每份文件是一组）：点「合并作图」→ 退出多选态 → 像点单个文件一样开临时标签（标签名 / 右栏标题 `dataFigureV2.batch.groupTitle`：「N 个文件」），里面是候选画法网格，点一张 = 和单份一样生成脚本并在画布打开（不出「换个画法」胶囊：它只认单份）。`dataFigureStore` 加可选的 `group: TableRef[] | null`（`selected` 是其中第一份，数据条 / 概览认它；`select` / `closeDraft` / `clear` 复位，`select` 的「同一份已开着」捷径对组无效），`propose` 发请求时有 `group` 就发 `tables: [{path, sheet?}]`（≥ 2 份，其余字段同单份，`render: "lazy"`），不发 `table`；合画不做第一份的「全是文字 / 要点表头行」判断，交给后端。后端合同（`feat/d2f-merge-tables`）：成功响应与单份同形，分组角色默认是后端合成的那一列；几份表列名不一致回 400 `tables_columns_differ`，份数超上限（`MAX_MERGE_TABLES` = 8，前端只在 `lib/dataFigure.ts` 写一处）回 400 `tables_limit_exceeded`（`params.max`，文案在 errors 命名空间「一次最多合并 {{max}} 个文件。」）。选中超过 8 个时「合并作图」禁用、title 用这一句，「分别作图」不受限。**成对候选**：合画时文件里原本有分组列且文件内有多个取值，候选成对出现（同一个 `title_code` / `why_code`），响应 `items[].variant`：`{kind:"split", column}`（`dataFigureV2.variant.split`「按「{{column}}」再分开」）/ `{kind:"by_file"}`（`variant.by_file`「只按文件比较」）/ null；磁贴标题下一行小字（`data-candidate-variant` 只放 kind、不放列名）显示，列名是用户数据、只做文本渲染、不进 `data-*` 与遥测。候选最多 8 张：骨架仍固定 4 张，名称到了再按实际数量排，网格在内容区里滚动。**候选的身份**：成对候选 `title_code` 故意相同，会话过期 / 数据变了、刷新候选后按画法找回「原来点的那张」只许用 `candidateIdentity`（`title_code` + `variant` kind，`split` 还要比 `column`；单份没有 variant，与只比 `title_code` 等价）；刷新后找不到身份完全相同、且配方与误差线逐项相同（`sameDrawing`）的那张就不自动提交，回到候选让用户重选。**概览新鲜度**：每次新开合并标签（`selectGroup`），每个文件的概览至少重读一次（切到它时若本标签里还没读过就重读，先显示缓存；同一标签内来回切不重复）；磁盘事件（`invalidateFiles`）之后本标签里读过的都作废，当前显示的文件随 `overviewGen` 重读。**数据条**：合画时标题行里「数据」后面放一排文件切换标签（复用 `ui/Segmented`，文字取响应 `sources[].label`，没有就按路径去扩展名；数量多时这一排横向滚动，不换行、不撑高标题行；`data-*` 里不放文件名），默认第一个；点哪个，下面的表和「前 N 行，共 M 行 · K 列」就是哪个文件的（`ensureOverview` 按需读，读过的不重读），「展开」展开当前选中的；表头行 / 单位行各文件各管各的（在某个文件的预览里点一行指表头，只带它的 `header_rows` 重新出候选）。单份流程的数据条不变。**出路**：「换成分别作图就能画」的后端码是一处集合 `MERGE_FAILURE_CODES`（`lib/dataFigure.ts`：`tables_columns_differ` / `tables_limit_exceeded` / `tables_labels_collide`，加新码只改这里），都落到同一个 `DraftPhase` `mergeFailed`，临时标签里显示后端那一句（errors 命名空间）；某份表不在了是 200 `nothing` + `table_not_found`，沿用「找不到你选的那张表。」；两类临时标签里都是一句话 + 主按钮「分别作图」——点了关掉临时标签、恢复这几份的选择（`dataBatchStore.drawEach`）、回数据抽屉并直接开始各画一张；不给「再试一次」（点多少次都一样）。

## 修订（2026-11-19）：大表（上限 500 MiB；`adf.SMALL_*` / `MAX_*`，`ai_data_template.reduce_*`）

**起因**。用户决定「上限直接放到 500 MB」。实测（`scratchpad/d2f-size/report.md`，改前）：5 MiB / 10 万行的上限不是任意值——整条链对同一份数据做约 7 次纯 Python 逐格完整解析、内存约 20× 文件；21 MB 起
`manifest.FINGERPRINT_BUDGET` 超限、数据核对悄悄变 unknown；105 MB 一张缩略图 181 s、提交 386 s；525 MB 概览就要 152 s / 5.4 GB；逐点画 262 万点的图是一块纯色。所以不是改常量，而是改链路。

**两档，界线不变**。`SMALL_BYTES = 5 MiB` / `SMALL_ROWS = 10 万` 就是原来的上限：不超过它的文本表格走**原来的整份读法，脚本、期望指纹、摘要逐字不变**（改前 / 改后各 39 个用例的脚本文本 [公共模块名按 `tavotto_plot_[0-9a-f]{8}` 归一]、期望指纹、覆盖情况、摘要字典逐项相同；XLSX 夹具每次重新生成、zip 时间戳不同，sha256 不同，屏蔽 sha256 后相同）。
超过它的是**大表**，上限 `MAX_BYTES = 500 MiB`、`MAX_ROWS = 6000 万`（超出仍是 `data_too_large` / `data_too_long`，文案不动）。XLSX、v1、合画永远只认小表（`inspect_data_source` 对大表默认抛 `data_too_large`，只有传 `allow_large=True` 的出备选 / 提交路径认）。

**读表（流式，一份实现）**。`iter_records` / `iter_table` 是读法的唯一实现（行迭代器进、`(物理行号, 单元格)` 出）；原来的 `delimited_records` / `table_from_text` 变成它们的整份文本版（薄包装），所以小表与大表读出来的是同一张表
（`tests/test_ai_data_large.py` 用 `text_tables` 的整套语料 + 全部编码在两条路径上逐字段比对）。大表 `read_text_table`→`_read_large_text_table`：一遍校验（sha256、编码、二进制、行数，增量解码）+ 一遍解析统计；嗅探 / 预览 / 说明行只看开头 1 MiB，其余判定（同一个 `_detect`、同一组稳定码、「认得准 / 猜的」口径）与小表相同。
摘要 `rows == []`，`DataSummary.large` 带整表精确统计（行数、每列有限数值的个数 / 最小 / 最大）与前 40 行预览；结构线索（分组 / 编号 / 布局）用有界集合（`STRUCTURE_SET_CAP`，超过按保守结局：不断言「有重复」）。同一个文件的扫描结果按（路径、字节数、mtime、inode、表头行）缓存（`use_cache`，只给概览 / 出备选；提交前核 sha256 的读取不用缓存）。`.xvg` 要整份改写文本，大表不支持。

**画法（缩减，趋势与关系）**。逐点画对大数据本身没有意义，所以大表的脚本读 `read_reduced`：
* 趋势（折线 / 阶梯 / 点）：每个（分面, 分组）系列在 **x 值**上分箱（2 的幂宽、起点 = 该系列第一行的 x、箱数超过上限就翻倍并箱——一遍扫完，不用先知道 x 范围，未排序的序列照样对）；每个箱留 最左 / 最右、每个 y 列的最小 / 最大。所以**每个 y 的全局最小 / 最大、x 的两端都在图上**。**断开**（有折线 / 阶梯层时）：折线在 y 不是数的地方断开，所以每个系列每个 y 列里**每一段**缺失（夹在两个有限值之间的连续非数行）各留下它的第一行——留下的任意两个相邻点之间，只要原数据在它们之间断开过，就一定有一条断开标记（2026-11-19 Codex 复现：多段缺失落进同一个箱、只留第一条会把极值点跨缺口连起来）。每个系列每个 y 列至多留 200 段（`LARGE_GAP_RUNS_MAX`），更多就**不画折线**（`template_option_unsupported`，前端显示「这种画法暂时画不出来」），散点照常。
* 关系（散点）：每个系列 等距抽样（第 0, K, 2K… 个有效点，K 是 2 的幂）+ x、y 各自的最小 / 最大所在行（4 个极值点）。
缩减留下的是原文行，之后与小表**同一份画图代码**（`trend_data` / `relationship_data` 原样吃）；父进程 `large_reduce` 与脚本里的 `read_reduced` 是同一份源码（进公共模块）。点数按预算反推（`large_spec`：系列数 × 每系列点数 × y 列数 × 层数 × 2 ≤ 100 万，是 `FINGERPRINT_BUDGET` 的一半），所以任何大表画出的数据总量都在指纹预算以内，**核对不会因超预算变成 unknown**，`manifest.py` 一行没改。
大表只放行 `ai_data_recipe.LARGE_MARKS`（趋势的折线 / 阶梯 / 点、关系的散点）；误差线 / 带 / 柱 / 箱 / 小提琴 / 直方图 / 密度 / 拟合线 / 水平汇总线要对**全部**行算统计量，大表上还没有流式的精确实现，`validate_recipe` 一律 `mark_not_allowed`（复用既有稳定码）；分组比较与分布两族在大表上暂时没有备选（structured `nothing`，不是错误）。带 `unit`（个体）与「夹着文字格」的处理规则的配方在大表上也拒（逐行规则没有流式实现）。
**值规则不许「没有行就当判过」**：小表上读行数据判的规则，大表上逐条给出流式答案或拒绝——对数轴上的非正值读概览扫描的精确最小值（`log_nonpositive`）；折线的 x 重复 / 没排序（`line_duplicate_x_unconfirmed`；重复只数 x 与该 y 列都有限的相邻观测，与小表同口径）与系列的 x 个数（`trend_too_few_x`）在缩减那一遍判（`LargeRuleError`，码与小表同一条规则一致、前端已有文案）。`trend_too_few_x` 的判据是 `ai_data_recipe.trend_too_few_x(counts)` 这一个谓词，小表的 `_value_problems` 与大表的 `large_reduce` 共用（计数口径：x 与第一个 y 都有限的观测的不同 x 个数，没有这样观测的格子不计入）；x 没排序的序列拿不到折线，但散点照常。

**核对**。（1）画出的点：与小表同一条指纹链，期望值按缩减后的表算，真 worker 里的 manifest 指纹逐字节对拍；（2）对原始数据的汇总：缩减那一遍独立累计读到的行数与每个 y（及关系的 x）的（有限数个数, 最小, 最大），`large_reduce` 与概览扫描的整表统计**逐项对账**，对不上（文件在两遍之间变了 / 缩减丢了行）抛 `TemplateError`——两遍是独立的累计，不是「同一个函数在两边各算一次再相等」。（3）**断开**：`gap_audit` 独立于缩减（不复用缩减里的任何状态，只用留下的行序号）再读一遍文件（只在折线且有断开时），数「某个系列某个 y 列里一段断开一行都没被留下」的段数，大于 0 抛 `TemplateError`。反证：`test_counter_proof_*`（脚本侧箱数不同 → 候选剔除；缩减丢行 → 对账抛错；缩减丢一段断开 → `gap_audit` 抓住；对数轴规则读的是整表最小值）。

**磁盘与时间**。候选目录仍放选中那张表的**字节副本**（脚本读 `../<数据>`，所以候选脚本就是将来落盘的脚本）；副本是我们自己的另一个文件，不是链接（硬链接会让候选脚本写到用户文件的 inode，符号链接解析到用户文件，两者都不要），`tests/test_ai_data_large.py` 钉住 inode 不同、写副本不改原件。大表副本总量设上限 `LARGE_STAGE_BUDGET = 1 GiB`：新会话放不下就先丢最老的大表会话；磁盘满（ENOSPC / EDQUOT）回结构化的「没有备选」（`candidate_render_failed`），不是 500。脚本在 worker 里的整个读 + 缩减 + 画图算 build（`pool.BUILD_TIMEOUT = 900 s`），500 MB 宽表实测约 25 s，不需要改 worker 超时与沙盒。响应里大表备选与提交结果多一项 `reduced: {rows, kept}`（小表响应没有）。`unplotted.count` 是精确总数（来自扫描），`rows` 只给前 100 个例子。

**实测**（Apple 机、24 GB；进程树 RSS 峰值；合成长时间序列 `time_s` + 3 列数值，折线 + 点 / 散点两个备选；别的会话同时在跑测试，只看量级。改前数字来自 `report.md`）：

| 数据 | 概览 | propose(lazy) | 缩略图 #1 | 提交（含渲染 + 核对） | 峰值 RSS | 结局 |
|---|---|---|---|---|---|---|
| 21 MB 改前 | 3.2 s | 18 s | 37 s | 44 s | 0.9 GB | partial / 核对 unknown |
| 21 MB 改后 | 1.4 s | 2.0 s | 3.1 s | 5.2 s | 0.3 GB | completed / verified |
| 105 MB 改前 | 18 s | 85 s | 181 s | 386 s | 3.5 + 2.7 GB | partial / unknown |
| 105 MB 改后 | 5.6 s | 9.2 s | 7.3 s | 20.6 s | 0.36 GB | completed / verified |
| 525 MB 改前 | 152 s | > 900 s 没跑完 | — | — | ≥ 5.4 GB | 不可用 |
| 500 MB（499.5 MB、1446 万行）改后 | 24.3 s | 41.5 s | 23.2 s | 70.5 s | 0.76 GB | completed / verified |

**界面文案（未改，列给用户定）**：`data_too_large` 等已上线文案里写着「5 MB」的地方；大表上被拒的汇总类画法只显示既有的「这种画法暂时画不出来」，没有说「表太大」；前端没有「正在读一个大文件」的进度与取消，也没显示 `reduced`（「画的是 N 行里的 K 个点」）。

## 修订（2026-11-19）：大表的真实进度与取消（`engine/ai_data_progress.py`、`/api/ai/data-progress/<rid>`）

**起因**。概览 / 出备选 / 缩略图 / 提交都是同步长请求（500 MB 读表 25–35 s、出候选 40–80 s），前端的「取消」只是不再等，后端会读完再丢掉。

**进度**。请求带请求号（概览与缩略图的 query `request_id`；出备选与提交沿用 body 的 `client_request_id`），后端在读表循环里记「已读字节 / 总字节」：`_stream_check`（`validate`，sha256 + 增量解码两遍合计）、`_scan_summary`（`scan`，每 65536 行一个检查点）、候选副本复制（`stage`，8 MiB 一块）、
缩减（`reduce`，运行时函数 `reduce_file` 的可选 `progress` 回调经 `tick_rows` 每 65536 行调一次；脚本里不传，所以公共模块里多了 `tick_rows`、哈希随之换了名）。`GET /api/ai/data-progress/<rid>` 回 `{state, kind, phase, phase_done, phase_total, pass, passes, fraction, indeterminate, cancellable, elapsed_s}`；`fraction` 按每种请求的阶段权重折算、
**单调不减**（缩减要读几遍由候选的画法数定，`set_passes`）。缩略图与提交的 worker 阶段（读 + 缩减 + 画图）在另一个进程里、脚本不带回调，**没有字节进度**，如实标 `indeterminate: true`、`fraction: null`（提交前的重读校验仍有 `validate` / `scan` 字节进度）。
**只有大表有条目**：条目在请求开始时登记（取消要能赶在读表之前到），结束时若从没进过大表阶段就删掉，查询读作 `none`——小表的请求与响应逐字不变（响应里多出来的只有大表才有的 `large` / `reduced` 标记）。条目有界（至多 32 条、终态留 10 分钟），只有阶段名与数字，不含路径 / 列名，不进遥测。会话认证 guard 与项目绑定同其它 `/api/ai/*` 端点（键是 `(项目, 请求号)`，别的项目查不到）。

**取消**。选**取消端点** `POST /api/ai/data-progress/<rid>/cancel`，不用「请求断开即停」：Flask / werkzeug 的处理线程在读表期间不读 socket，断线检测在本仓库的 HTTP 栈里不可靠（桌面壳、本机代理、浏览器 `AbortController` 的行为也不一致）；显式端点语义确定、可幂等、可测试。
取消是协作式的：读表循环的检查点发现标志就抛 `ai_data_progress.Cancelled`（**`BaseException` 子类**，与 `asyncio.CancelledError` 同约定：各处 `except Exception` —— `profile_folder`、概览 —— 吞不掉，不会被当成「这个文件读不了」），原请求回 409 `propose_cancelled`；清理走原有的 `except BaseException` 路径：候选目录（含半份大表副本）整个删、没有会话、幂等表不记这次响应、扫描 / 缩减缓存只在成功后才写。
端点幂等：重复取消、取消已完成 / 不认识 / 别的项目的请求都是 200，`cancelled` 只在「这次真的把一个进行中的可取消请求标成了取消」时为真；取消与完成赛跑时**以原请求的响应为准**（最后一个检查点之后才到的取消不生效）。缩略图与提交**不可取消**（提交一旦开始就会落盘；缩略图在 worker 里画）。

**大表标记**。`/api/ai/data-files` 里读过的文本表的 `table`、`/api/ai/data-overview` 的响应在大表上多一项 `large: true`（小表没有这一项）；`table` 为 null（预算内没读）的文件，前端用 `size > 5 MiB` 判断，窄表「不到 5 MiB 却超过 10 万行」要读过才知道。

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

## 修订（2026-11-19）：接模型的地基

用户决定把模型接进「从数据到图」（此前全流程不调模型：候选来自 `default_candidates` 的规则 + 固定模板）。分 0–7 步，本步是**第 0 步地基**：只铺内部能力，**没有调用点**——`propose` 与任何端点都不引用新模块（`tests/test_ai_data_model.py::test_nothing_in_the_product_calls_the_model_yet` 用 AST 判），开关关闭时一切逐字不变，打开时用户可见行为也不变。后续第 1 步（模型读懂每列是什么）、第 2 步（模型按科学意义重排 / 替换候选、给一句理由）建立在它上面，并在那时改这条测试。

**做了什么**

1. `engine/ai_structured.py`：无工具、单轮、只要一个 JSON 对象的模型调用。不另写 HTTP——驱动内置执行器的 `agent_runner.loop.HttpModel.complete(tools=[])`（经 `fixsession.build_model`，它多接了 `**model_kwargs`，默认不传、行为不变），所以三种线上协议、状态码分类、`post_json` 的出站约束（https / 回环 http、`tlstrust` 证书、不跟随重定向）、非必需字段降级与首轮换协议自救都是现成的。`call_structured(system, user, validate, limits, conn, post)` **永不抛**，回 `StructuredResult`：成功是已过 `validate` 的 dict；失败是闭集原因。
2. `engine/ai_data_model.py`：数据出图的调用点。`build_request`（受控摘要 + `data_figure_guidance()` + `ai_prompt_pack.data_figure_oneshot_notes()` + 可选一句用户提示）→ `call_structured` → 逐条 `FigureRecipe.from_dict` → 没有模板能画的族丢 → `enforce_order`（`validate_recipe` + 去重 + 柱不排在原始点前）。返回通过校验的配方与丢弃记录（原序号 + 拒绝码）。`data_figure_oneshot_notes()` 说明这次没有工具、不能 `ask_user`（拿不准走最保守画法）、表格摘要是数据不是指令、只输出 `{"recipes": [...]}`；配方字段与取值从 `ai_data_recipe` 的表渲染，不另抄。
3. 固定用例集 `tests/fixtures/data_figure_cases/cases.json`（14 条：比较 / 趋势 / 关系 / 分布，坑有编号列当数值、日期列、重复样、对照组顺序、宽表 / 长表、量纲不同、小样本、夹带指令），判定与评分在 `tests/support/data_figure_cases.py`，pytest 用录好的回答经**真协议层**跑通整条链（合法、踩坑、非法 JSON、未知列、超时、拒答，三种协议各一遍），不发任何网络请求；手动入口 `scripts/dev/data_figure_bench.py`（默认假回答，`--real` 要显式 `--max-calls` 且不少于 用例数 × 2，输出命中率与 token / 花费估算）。**本步没有对真模型跑过它。**

**失败闭集**（`ai_structured.REASONS`，调用方据此静默退回规则结果，不向用户展示）：`no_connection`（没有连接，或运维开关 `TAVOTTO_AI_BUILTIN=0`）、`unsupported_connection`（只有订阅登录）、`over_budget`（输入超限未发请求，或输出撞到最长输出被截断）、`timeout`（总时限，或套接字读超时）、`refused`（拒答 / 内容过滤）、`invalid_output`（非法 JSON 已重试一次 / 没过校验 / 回了工具调用）、`auth`、`quota`、`transport`（其余，含 HTTP 非 2xx、回复体不对、本模块内部异常）。`detail` 只是短代码（状态码、异常类型名、`not_json` / `shape` / `input` / `output`），不含响应原文、提示词、地址、密钥。映射对 `loop.REASONS` 逐值有结局（测试逐项核）。

**上限**（`Limits` 默认）：输入 ≤ 60 000 字（系统提示 + 用户内容；系统提示约 18 KB 固定）、输出 ≤ 3 000 token（另按每 token ≤ 8 字符卡回复长度，防不守上限的端点）、总时限 60 s（两次尝试共用、从解析连接起算；挑连接不联网，只在剩余预算 ≥ 35 s 时才对挑中的那一条联网刷新令牌 / 核地址，不够就用现有记录——这两步没法传入时限，放进线程超时会留下继续写 provider 存储的半截副作用）、**最多重试 1 次且仅在回复不是合法 JSON 时**（换协议 / 字段降级的自救不算重试，所以一次调用可能发多个 HTTP 请求：`StructuredResult.requests` 在传输层数，`max_requests` 对它硬性生效，到顶回 `over_budget`）——传输失败 / 拒答 / 鉴权失败 / 形状不对都不重发。摘要超出输入上限时按序收缩：每列文字样本 20 → 8 → 3 → 0 条，再从末尾起少发表（至少留 1 张），数目记在 `ModelRecipes.tables_omitted` / `sample_cap`；最多发 8 张表、每条样本 ≤ 60 字、用户提示 ≤ 200 字；模型一次最多交 8 份配方，多出的记 `too_many_candidates`。

**订阅登录连接（Codex / Claude Code CLI，2026-11-21 补，原为 `unsupported_connection`）**：没有 Key 连接、只有订阅登录时，`resolve_connection` 返回 CLI 连接描述 `{"kind": "cli", "agent", "model", "conn_id"}`，`call_structured` 走 `_call_cli`：给 `AgentDefinition` 加了可选的 `build_structured_command` / `parse_structured_output`（默认不支持 = 该适配器仍是 `unsupported_connection`）。与 HTTP 路径同一套语义（同一个 `validate`、非 JSON 重试一次、总时限、永不抛、闭集失败码），每次尝试起一个新进程（不落会话）。命令：Codex = `codex exec --sandbox read-only --ephemeral --ignore-user-config --skip-git-repo-check -C <空临时目录> -m <型号> -c model_instructions_file=<我们的系统提示> -c skills.include_instructions=false [-c model_reasoning_effort=low] [--output-schema <文件>] --json -o <输出文件> -`；Claude Code = `claude -p --output-format json --system-prompt <我们的系统提示> --tools "" --no-session-persistence --setting-sources "" --strict-mcp-config --disable-slash-commands --model <型号> [--effort low] [--json-schema …]`（**不用 `--bare`**：它不读 OAuth，订阅登录会失效）。用户内容只走 stdin，argv 里没有用户内容 / 令牌 / `--dangerously-*` / 可写沙盒（Codex 没法真禁工具，靠只读沙盒 + 空目录）。**型号显式**：连接里已解析出的型号，没有就取该适配器型号清单第一项，都没有 = 不支持（不写死型号名）；**不读用户自己的 CLI 配置**；思考强度只在适配器目录明确列了该型号的档位时才带。失败码：CLI 自己的结构化字段（Codex `turn.failed` 的状态码、Claude `result.api_error_status`）→ `auth` / `quota`，其余非零退出 `transport`，起不来 `no_connection`，超时杀整个进程树 `timeout`，空 / 非 JSON / 不合格 `invalid_output`。CLI 拿不到 token / 花费：`usage_reported=False`、请求数（起的进程数）照计。同一时刻最多一个结构化 CLI 进程（排队不吃总时限）。stdout / stderr 不落盘（stderr 丢弃）、不进日志 / 遥测 / 诊断包。**真跑要订阅额度，不进 CI / 夜跑**；`scripts/dev/structured_cli_smoke.py` 只在维护者本机手工跑，下面这些**尚未在真 CLI 上核过**：Codex 的 `model_instructions_file` 是否整体换掉预置指令、`--ignore-user-config` 是否挡住 `$CODEX_HOME` 下的 AGENTS.md / 技能、严格 schema 是否被接受（本步 `ai_data_roles` 不带 schema，靠提示词 + 本地解析校验）；Claude 的 `--setting-sources ""` 空串、带 `--json-schema` 时结果在 `structured_output` 还是 `result`。「当前连接」= 连接视图里第一个内置执行器能直接用的 Key / 账号登录连接（`first_key_connection`，旧版选中的接口排在前，与 AI 修复同一口径），其次才是订阅登录；让用户在这条功能里单独选连接，是后续步骤的产品决定。

**不变的边界**：模型不执行任何代码、本步不新增工具 / 命令行 / 写文件能力；表里的文字只作 JSON 字符串进 `<data>` 块（`<` 转义，伪造不出块的结尾），配方里没有自由文本进脚本；密钥只在 `HttpModel` 内（`repr` 不含）；本模块不写日志、不发遥测（遥测仍不收列名、路径、指令、图里文字）；运行时公共模块（生成脚本内嵌的那份）一个字节未动。

## 修订（2026-11-19）：第 1 步「读懂数据」——模型判断每列是什么（`engine/ai_data_roles.py`，`ai.taf.enabled` 后面，不加子开关）

**目标**：让默认的横轴 / 纵轴 / 分组更常是对的（规则在有歧义处只能猜「最后一个数值列 / 最左一列」）。**模型只给「列判断」，不出配方**：判断被翻译成 `propose` 本来就接受的三种输入——`answers.y / x / group`（`none` = 不要）、`header_rows`、`answers.cells`——配方仍由规则 `default_candidates` 生成并过 `validate_recipe` / `enforce_order`，闭集配方校验一步没绕过。

**列角色闭集**：`time` / `ordered_x` / `measure` / `group` / `replicate_unit` / `row_id` / `error`（sd / sem 这类误差列：不当纵轴，2026-11-21 加）/ `unit_label` / `note` / `other`。**`replicate_unit` 永远不会变成用户确认的事实**（`Confirmed.unit_col` 只来自用户）：它只让规则别把这一列当 y / x / 分组；响应里的建议只含 `y` / `x` / `group` / `cells`，没有 `design` / `unit`。

**校验与降级（逐字段）**：形状层严格（未知键 / 类型不对整份拒收 = `invalid_output`，不重试）；取值层逐字段——列不存在、角色不在枚举、重复给同一列、角色与列的类型对不上（`measure` 必须是 `numeric_like`、`time` / `ordered_x` 必须是日期或数值、`group` 必须在分组列里）、选择与自己给的角色矛盾、多 y 列不满足下面的客观条件、处理规则的键对不上 / 列没夹文字 / 不是最终用到的列，各丢各的，只记丢弃个数，回到规则默认。**采纳策略**：规则会问的地方（选值 / 选分组 / 选横轴）用模型的选择；规则唯一确定了某个角色、但模型明确说那一列不能当这个轴（y 的角色不是 `measure`；x 是 `row_id` / `note` / `unit_label` / `replicate_unit` / `other`；分组不是 `group`）才推翻；其余保持规则结果。用户的回答永远优先：请求里已有 `answers.y / x / group / cells` 或 `header_rows` 就不起模型任务（也避开「采纳建议 → 再来一次 → 再起任务」的循环）。

**模型判断「够得到」规则的三处（2026-11-21，留出集 HO-16 / 17 / 19）**：没有模型（没有 `picks`）时规则逐字不变；下面三种采纳都要「模型明确判断 + 列客观上满足条件」，条件各只有一处实现（`ai_data_recipe`），`suggest`（采纳）与 `_resolve_roles`（落地）共用，不满足就丢该项回规则结果，从不报错。
* **多列纵轴**：`picks.y` 给 2–3 个列名（`MULTI_Y_MAX`，与宽表分支同上限）。条件 `multi_y_ok`：互不相同、都在表里、都是数值列（`numeric_like`）、没有 `id_like` 列、列名后缀认得出的单位至多一种（否则是双轴，`dual_axis_unsupported` 会把整组备选剔光）；再加模型自己的角色不能否决（每列只能是 `measure`）。落地：`answers.y` 可以是 1–3 个列名的数组（`parse_answers` 接受，别的键仍只认字符串；右栏下拉发的仍是字符串，`propose` 响应里 `roles.y` 本来就是数组）；规则不认的数组当没给。只在规则本来要问「选哪个值」（或规则选的那列被否决）时采纳，规则已落定的宽表多 y 不动。**有有序横轴时**：只有趋势类（`line_units` / `line_points` / `line_band`，每列一条线）画全部纵轴列（至多 3 列），分布 / 比较 / 散点类配方只用第一列；**没有横轴时**（模型说 `none`，或除这几列外没有日期 / 数值列）多列纵轴不再丢弃，改为下面的「把列当分组」（2026-11-21 起；此前的做法是当没给、计入丢弃个数，HO-17 因此在 `--fake good` 下不过——现在 46/46，标准答案没改）。`roles.y` 与候选实际画的列始终一致（`_resolve_roles` 与 `suggest` 各核一次）。
* **有分组时的横轴**：规则有分组列又有多个横轴候选时不问横轴（分组比较是主图），模型给的 `x` 从前被忽略。现在：模型判该列 `time` / `ordered_x`（只是 `measure` 不行）、它是规则此刻的横轴候选（日期 / 数值、不是 y、不是编号列）才采纳；`none` / `unsure` 不算。
* **编号样的文字列当分组**：规则把名字像编号（`sample` 一类）的文字列判成 `id_like`，永不当分组。现在：模型判 `group` 且这列在 `s.groups` 里（`id_group_ok`：文字、2–12 个取值、有取值重复出现——每行都不同的是标签不是分组；数字编号不在其中）才采纳；落地走 `answers.group`（只有点名才进，没点名仍永不当分组），`roles.options.group` 里带上它，并且它不再兼作「重复测量的个体编号」（所有编号样的列都被当了分组时，不再问「同一对象重复测量吗」）。
* **整数编码的数值列当分组（2026-11-21，HO-20；用户同意，维护者定的边界）**。`knockout` 取 0 / 1、`treatment` 取 1 / 2 / 3 这类整数编码的分组列，从前「分组列必须是文字列」贯穿多处而画不出分组比较。现在给配方加一条明确事实 **`FigureRecipe.numcat`**（列名数组，至多 `MAX_NUMCAT` = 3；空时 `to_dict` 与旧版逐字相同）：「这些数值列按类别用」，贯穿 配方 → `validate_recipe` → 模板 / 生成脚本 → 落盘再校验 → 指纹核对；**不扩大 `s.groups`，不改规则**。
  * **客观条件（本地可核，唯一实现 `ai_data_figure.numcat_levels`，结果放 `DataSummary.numcat` = 列 → 取值个数，不进 `to_dict`）**：列 kind 是 `numeric`（没有文字格、没有 `nan` 字面量）、非空值全是整数（`3.0` 算、`1.5` 不算、`|v| ≥ 1e15` 不算；**负数可以**）、不同取值（按整数值，`1` / `1.0` / `01` 同一个）2 到 `NUMCAT_MAX_LEVELS`（= 6）个、非空格子数 > 取值数（至少一个取值重复；每行都不同的是编号 / 测量值）、不是 `id_like` 列（`subject` 一类名字）。**空格子**不算取值、那一行不画（同文字分组）。6 而不是文字分组的 12：数值本身有大小意义，取值一多更像剂量 / 时间点 / 计数。只对小表、v2 读法算；大表与合画（`merge.build` 显式清空）恒为空。
  * **点名来源**：用户在右栏「分组」里选它（`answers.group`），或模型判它 `group` 并经 `ai_data_roles` 采纳（`_role_fits_column` / `_valid_pick` / `suggest` 都读同一个谓词 `numcat_pick_ok`）。规则**从不**自己把它当分组：没有人点名时配方与响应逐字不变，唯一的差别是 `roles.options.group` 多列出了满足条件的整数编码列（默认选择不变；**可回退**：`_resolve_roles` 末尾 `options["group"]` 那一行）。条件不满足 = 丢弃这项、回规则结果。点名的列同时从 y 候选与横轴候选里拿掉（否则单独点名分组会得到「x = 分组」的趋势图）；拿掉后没有数值列可当 y 就当没点名。
  * **各层怎么认**：`validate_recipe` 对 `numcat` 里的每一列要求在 `s.numcat`、不重复、真的处在分组 / 分面 / 类别横轴角色（有序横轴不行），然后用 `numcat_view` 把它们当 `groups` 看（取值上限、构成部分数、排名条目数、配对水平数都读同一处）；`eligible(…, numcat=)` 同理；`default_candidates` 在校验前给用到它的备选补上 `numcat`。取值规则、落盘再校验与 `expected_fingerprints` 都经 `with_cells_applied`（先 `cells` 再 `numcat`），与脚本读表后调用的是**同一个**运行时函数 `apply_numcat`。
  * **类别的显示与排序**：`apply_numcat` 把这些列的数值格换成 `NumCat`（`str` 子类，内容是规范整数写法 `0` / `1` / `-1`，没有 `0.0`；`strip` 返回自己所以标记不丢）；运行时 `distinct` 见整列都是 `NumCat` 就按数值升序，否则仍是首次出现的顺序（别的路径逐字不变）。图例 / 刻度标签因此是 `0`、`1`。`NumCat` / `apply_numcat` 进 `RUNTIME_FUNCTIONS`，公共模块哈希由 `98157f49` 变为 `f7aa140e`（`tests/test_ai_data_encodings.py::PINNED_RUNTIME_NAME`）；老脚本继续 import 它们当时的那一版。
  * **大表 / 合画 / `.xvg` / XLSX**：大表暂不支持（摘要里 `numcat` 为空 → 点名被丢；`_large_problems` 另对非空 `numcat` 报 `mark_not_allowed`，因为缩减的系列数上界读 `groups`）；合画暂不支持（合成的文件列才是主分组，`build` 把 `numcat` 清空）；`.xvg` 与 XLSX 走同一个 `_summarize`，客观条件自然适用（XLSX 里 `1.0` 与 `1` 同一取值）。测试 `tests/test_ai_data_numcat.py`（客观条件表、规则不变、点名 / 模型采纳、真 worker 渲染并 `verified`、回退）。
* **把列当分组（宽表转长表，2026-11-21，HO-17 / HO-25..30；用户要求，维护者定的边界）**。实验数据里常见的宽表：每列是一个条件或一次重复，没有横轴、没有分组列（`sample, rep1, rep2, rep3`；`ctrl, drugA, drugB`）。从前画不出「各列之间的比较」：分布 / 比较类配方只用一个 y 列。现在给配方加一条明确事实 **`FigureRecipe.stack`**（列名数组，2–`STACK_MAX_COLUMNS` 个；空时 `to_dict` 与旧版逐字相同；`from_dict` 写得不合格报 `recipe_malformed`）：「这几列在逻辑上转成长表——一列值、一列类别（取值 = 原列名原文）」，贯穿 配方 → `validate_recipe` → 模板 / 生成脚本 → 落盘再校验 → 指纹核对。
  * **转换只有一份实现**：运行时函数 `ai_data_figure.apply_stack(header, rows, cols, value_name, cat_name)`（进 `RUNTIME_FUNCTIONS`，公共模块哈希由 `f7aa140e` 变为 `3bf67213`，`tests/test_ai_data_encodings.py::PINNED_RUNTIME_NAME`）。**按列逐列**取（类别首次出现的顺序恒等于 `stack` 的顺序，与哪一行先有值无关）、只取数值格（空格 / `nan` 不是观测，那一格不产生行）、值保留原文、其余列不进结果。生成的脚本读表后 `header, rows = apply_stack(header, rows, STACK, *STACK_NAMES)`，父进程的 `ai_data_recipe.with_cells_applied`（校验、落盘再校验、`expected_fingerprints` 都经它）调的是同一个函数。转换后是只有两列的长表：值列 `value`（各列名后缀里的单位认得出且一致就接上，`value_mM`，轴标题 `value (mM)`）与类别列 `group`（`stack_names`）；`recipe.y = (值列,)`，类别列做比较的 `x` 或分布的 `group`；`validate_recipe` 对着 `stack_view`（转换后的长表摘要：值列 numeric、类别列 `groups` 里取值个数 = 列数）判，所以取值上限、每组 n（`box_violin_n_small` 等）、`dual_axis_unsupported` 都读同一处。
  * **客观条件（唯一实现 `ai_data_recipe.stack_ok`，本地可核）**：2 到 `STACK_MAX_COLUMNS` 列、互不相同、都在表里、**都是纯数值列**（`kind == "numeric"`；夹着文字的列不支持——要先有处理规则，这里干净地回到规则结果；`cells` / `numcat` / `unit` / `paired` 与它互斥）、不是 `id_like` 列（模型判 `error` / `replicate_unit` / `row_id` 的列另被 `_valid_pick` 的角色核否决）、每列至少 `STACK_MIN_N`（2）个数值格、列名后缀里认得出的单位至多一种（量纲不同的几列不放同一根值轴，`dual_axis_unsupported`）、小表、非矩阵。**上限 K = `STACK_MAX_COLUMNS` = 8**：这里数的是**类别个数**（每列一组，进颜色分组 / 类别横轴），不是同一根轴上的几条线，所以不沿用多列纵轴的 3；取颜色分组上限 `MAX_GROUP_LEVELS`（8，再多颜色分不清），这样每一种备选（含 ECDF 的颜色分组）都放得下，不会有「某个备选因类别太多被剔掉」的参差（类别横轴本来的上限 12 更宽，不是瓶颈）。
  * **谁能触发（规则臂逐字不变）**：① 模型：`picks.y` 给了多列（`_valid_pick`：`multi_y_ok` 或 `stack_ok`，且模型对每列的角色只能是 `measure`）、最终没有有序横轴——`suggest` 在规则已落定别的默认时也采纳（如没有样品名列的 `ctrl, drugA, drugB`，规则会把第一列当横轴）；模型把**另一列**判成 `time` / `ordered_x` 却又说没有横轴（自相矛盾）不采纳。② 用户：`answers.y` 为数组（1–`STACK_MAX_COLUMNS` 个列名，`parse_answers`；有横轴时仍至多 3 个）且没有有序横轴（`answers.x == "none"`，或除这几列外没有日期 / 数值列）。有有序横轴时仍是现有的每列一条线（不变）。③ 规则自己**不**主动用：没有 `picks` / `answers` 点名时，`--arm rules --cases all` 的逐条输出改前 / 改后逐字相同（另对 46 条 × 两种 picks 的 `default_candidates` 全部输出做了逐字比对）。落地在 `_stack_roles`（`_resolve_roles` 的第一步）。
  * **`roles` / `values` 的表达（给前端的约定）**：`roles.y` = 被当分组的列（表里的列顺序，与点名顺序无关）、`roles.x` = `null`、`roles.group` = `null`，**只在这时多一项 `roles.stack: true`**；每个备选的 `values` 同样 `y` = 那几列、`x` / `group` = `null`，多一项 `stack: [列…]`（配方里转换后的值 / 类别列是内部名字，不对外）。前端将来发：`answers = {y: [列…], x: "none"}`——与它收到的 `roles` 一一对应，原样回传就能重出同一批备选。这条路径下 `roles.options.group` 只列整数编码列、`options.x` 是这几列之外的日期 / 数值列。
  * **不支持（一律回规则结果，不出错也不出零候选；在列角色挑选这一步退回，不靠 `validate_recipe` 剔光）**：有文字分组列（「列当分组再分组」）、点名了分组列（含整数编码列）、合画、大表（`validate_recipe` 对非空 `stack` 报 `mark_not_allowed`，摘要的行数据不在内存）。`.xvg` 与 XLSX 走同一个 `_summarize` / 同一个读表表达式，转换在读表之后，自然适用（测试覆盖 XLSX）。支持的配方族：`comparison`（均值 + 原始点、箱线 / 小提琴 + 原始点、柱 + 误差线，类别 = 横轴）与 `distribution`（ECDF 等，类别 = 颜色分组）；其它族在 `_stack_problems` 里 `roles_invalid`（趋势 / 关系图要有序横轴）。
  * **提示词**：`picks.y` 的说明补了一句「每一列是一个条件或一次重复、没有横轴也没有分组列时，把这几列都放进 `y`（最多 8 个），`x` 给 `none`」（`PROMPT_VERSION` 因此变化，缓存自然作废）。留出集追加 HO-25..30 六条（每列一个处理组无样品名列 / 带样品名的四次重复 / 中文列名 / 八列接近上限 / 空格子且列名带同一单位 / 反例：身高与体重两种不同的量，规则本来就对）。测试 `tests/test_ai_data_stack.py`（客观条件表、转换的列顺序与缺失、规则逐字不变、用户 / 模型点名、有横轴仍每列一条线、回退、真 worker 渲染并 `verified`）。

**`roles.y` 必须等于每个备选实际画的列（2026-11-21）**：`default_candidates` 最终的 `ys` 多于一列时（规则自己的宽表多 y、模型点名、用户点名一视同仁），只返回画全这些列的备选——目前只有趋势类（每列一条线）；只用第一列的关系图 / 分组比较 / 分布被去掉，记进 `dropped`（原因码用现有的 `roles_invalid`，`detail` = `multi_y_not_drawn`，不加新码）。去完一个不剩（如量纲不同退到第一列）就回到单列（`ys[0]`）重算，不出零备选，`roles.y` 同步变成单列。这是 main 上规则宽表本来就有的不一致（`roles.y` 三列、`scatter_fit` 只画第一列），因此没有模型时宽表的备选列表也少了只画第一列的那几张（主集 / 留出集里 RC-12、HO-05、HO-15、HO-18 各少一张 `scatter_fit`）；默认的 x / y / 分组不变。

**表头 / 单位行**：只在读表 `status == "unsure"`（已有预览）且用户没指定表头行时多问一次，答案必须是预览里的行号、按它重读要读出合格的表（有数值列）、且与嗅探的不同，才采纳，之后对重读的表再问列角色。**单位行强制不做**（不动读表运行时，`PINNED_RUNTIME_NAME` 不变）。

**接法**：`propose(render="lazy")` 先照常返回文字候选；有可用连接（Key 连接 `first_key_connection(refresh=False)`，其次是适配器支持无工具单轮的订阅登录 CLI，只看已有的能力缓存、不探测；没连、运维关内置 = 没有）时响应多一个 `understanding: {id, state}`，起后台线程（缓存命中则 `ready` 内嵌）。`GET /api/ai/data-figure/understanding/<id>` 轮询（会话认证、项目绑定、只读不联网）：`pending` / `ready`（`answers`、`header_rows`、`roles`、`dropped`）/ `failed`（闭集 `reason`）/ `off`（不存在 / 过期 / 别的项目，不是错误）。前端 `ready` 后把 `answers` 与 `header_rows` 并进原请求再 `propose` 一次、换掉旧会话；没有可用连接时响应里**没有** `understanding` 键、与从前逐字相同（测试钉住，含「有连接时响应只多这一个键」）。不做 `data-overview` 预取，只在显式出图时触发。「重新判断」的后端参数留好（`start(refresh=True)` 忽略缓存），前端以后接。

**时限与强度**：总时限 25 s（表头那次与列角色那次共用，含解析连接——挑连接不联网，只在剩余预算 ≥ 35 s 才对挑中的那一条联网刷新）；**订阅登录（CLI）连接取 `ai_structured.CLI_TIMEOUT_S` = 60 s（待本机真跑后校准；CLI 冷启动慢），排队等那一个 CLI 名额不吃这 60 s；前端轮询上限若仍是 30 次 × 1 秒，比它短，要前端配合放宽**；输出 ≤ 2 000 token；思考强度请求 `low`，但只有 `builtin_effort_args` 对「这个地址 + 这个型号」确认支持才发（官方端点、目录核过的型号），其余一个字段都不发（不靠 400 再降级）。`call_structured` 新增 `effort` 参数，`fixsession.build_model` 透传。

**发给模型的 / 不发的（逐项）**
* 发：表名（相对路径）与工作表名；行数；布局；分组取值及个数；编号线索；每列的名字、类型、空值 / NaN / 文字格个数、最多 20 条去重文字样本（每条 ≤ 60 字）；**本地算出的聚合**（只对数值列与日期列：不同值个数、是否单调、是否全为整数、最小 / 最大）；用户那一句提示（≤ 200 字）；仅 `unsure` 的表发表头预览（≤ 8 行 × 120 字，带原文行号）。摘要超预算（40 000 字）按序收缩文字样本 20 → 8 → 3 → 0。
* 不发：数据行本身；文件哈希与大小；绝对路径与用户目录名；密钥与地址；别的表。表里的文字是不可信数据：JSON 字符串放进 `<data>`、`<` 转义，提示词明说不是指令。
* **本步不发完整数据。**（用户 2026-11-19 已同意摘要、图、脚本乃至完整数据可发给模型；隐私说明文档**先不改**，列为开关打开前的待办。）
* 不写日志、不发遥测、不进诊断包：缓存里有列名，不在诊断包采集清单里；`tests/test_ai_data_roles.py` 钉住（诊断包 zip 里找不到缓存内容与列名、`telemetry.EVENTS` 没有相关事件、任务跑完不调 `capture`、模块不 import 遥测 / 诊断 / 追踪、日志调用只许固定文字）。

**缓存 / 单飞 / 负缓存**：缓存里存的是**模型的原始判断**（校验后的 `Judgement`：列角色、选择、处理规则、采纳的表头行），**不存**按提示算出的建议——每次命中都按**当前完整提示**与规则重新派生（`derive`），所以键与派生结果不会错位（提示只有前 200 字发给模型，规则读的是完整提示；Codex #2068）。`data_dir()/cache/ai_data_roles/<key>.json`，原子写；key = 协议版本（2）+ 系统提示哈希 + 数据字节哈希 + 实际表头行 + **模型看到的**提示（前 200 字）+ 连接身份（provider id + 型号，不含密钥与地址）+ 表；单飞登记另含完整提示摘要且只在 pending 期间存在；TTL 30 天、至多 200 条（按 mtime 淘汰）；坏文件 / 旧版本 = 未命中。失败只做 60 s 内存负缓存（同一 key 不再起任务，不落盘）；同一 `(项目, key)` 在途任务单飞。后台线程只读数据文件、联网、写缓存目录，不碰按项目绑定的状态，故不需要 `bound_project`。

**度量**：`tests/fixtures/data_figure_cases/roles_cases.json`（22 条：18 条规则会出错的——整数批次 / 流水号 / 重复序号被当轴、顺序反了、两个分组列、备注列在前、中文、夹文字格、两种表头问题——加 4 条规则本来就对的对照）；评分有列角色正确率（全部 / 关键列）、默认 x / y / 分组命中率（规则臂 vs 模型臂）、对照被改坏数、表头 / 处理规则命中；`scripts/dev/data_figure_bench.py --suite roles [--arm rules|both] [--real --max-calls N]`，`--max-calls` 按**实际 HTTP 请求数**硬拦，报告含实际输入 / 输出 token 与按定价表估的花费。假回答下的模型臂是录好的理想回答，**只证明链路与护栏，不代表真模型的命中率**。**本步没有对真模型跑过。**

**不做 / 留给后面**：单位行强制；多表文件夹里让模型选表（选表仍是规则或用户的事）；前端轮询与「采纳建议」的交互（文案要用户签字）。

**修订（2026-11-21，维护者同意按实测改）：读懂数据默认用小型号 + 最低档，不行再兜底回用户的型号**（`ai_structured.small_plan` / `ai_catalog.json` 的 `structured_small`）。第 1 步是一问一答的小任务，在 46 条用例上重复跑（OpenAI 官方端点、Responses 协议；「改坏」= 规则本来对的被模型改错）：

| 型号 / reasoning.effort | 次数 | 完成率 | 改坏 | 每张表花费 |
|---|---|---|---|---|
| gpt-6-luna / none | 230 | 99.6% | 0 | ≈ $0.00005 |
| gpt-6-luna / low | 230 | 97.4% | 0 | ≈ $0.00008 |
| gpt-6.1-terra / none | 138 | 98.6% | 0 | ≈ $0.0004 |
| gpt-6.2-sol / low | 138 | 100% | 0 | ≈ $0.0034 |

逐型号问 API 的支持档位：`gpt-6-luna`、`gpt-6.1-terra` 认 none/low/medium/high/xhigh/max（不认 minimal）；`gpt-6.2-sol` 认 low/medium/high/xhigh/max（none、minimal 都不认）；gpt-5 / mini / nano 认 minimal。其余 GPT-6 / 5.6 型号没有逐个核过。发不支持的值服务端回 400。

* **策略表**放 `ai_catalog.json` 的顶层 `structured_small`（与 `effort_origins` 同一份目录、同一套 origin 匹配与出处注释习惯）：按「官方端点 origin + 协议」给 `(型号, effort)`，现在只登记 OpenAI 官方 + `openai-responses` → `gpt-6-luna` + `none`。**不登记 = 行为不变**：Anthropic 官方、第三方 / 自定义端点、账号登录（Bearer）、订阅登录 CLI。用户自选的型号已经是它、或连接自带 `effort`（手动基准显式给的）时不插手。
* **只有调用方显式开 `auto_small` 才生效**（`call_structured` 默认关）：只有实测过的读懂数据（`ai_data_roles`）开；配方排序（`ai_data_model`）没测过，不开。**不影响改图助手 / 智能修复**——它们的型号与档位仍由用户选择与目录决定。
* **兜底**：小型号这一次失败且属于「这把 Key / 这个端点用不了它」——`transport` 里 `http_400` / `http_404` / `http_422`、`auth` 里 `http_403`（`ai_structured._SMALL_UNUSABLE`；能区分到的就是 HTTP 状态，不读服务端错误体）——就**改用用户连接里原本的型号与原有 effort 再试一次**，共用同一个总时限与 `max_requests`（预算 / 时间不够就不试、如实返回小型号那次的失败）。401（密钥被拒，换型号也一样）、timeout、quota、refused、invalid_output、over_budget 都不换型号。进程内记「连接身份（地址 + provider + Key 摘要 + 小型号）不可用」10 分钟（`SMALL_UNAVAILABLE_TTL_S`），期内直接用原型号。
* **`none` 的出路**：wire 层 `RESPONSES_WIRE_EFFORTS = RESPONSES_EFFORTS + none`；`RESPONSES_EFFORTS`（用户可选档位）不含 none，改图 / 修复的校验与连接视图的过滤照旧，所以它们永远发不出 none。目录里 `gpt-6-luna` / `gpt-6.1-terra` / `gpt-6.2-sol` 在 `gpt-6` 前缀之前单列（前缀先中先得），界面档位仍是 low/medium/high；`none` 记在 `hidden_efforts`（`ai_catalog.verified_efforts`），因为前端的档位文案表（`effortLabel`）没有 none，露出来会显示成裸的 `none`。要在界面上露出 none，先让用户签字加文案。
* **缓存键**（`ai_data_roles._identity`）带**实际会用的型号与强度**（`ai_structured.effective_choice`）：换型号 = 不同的判断来源；记下「小型号不可用」之后按原型号算。兜底成功的那份判断仍存在小型号的键下（调用前就定键）。用量记录的 `model` 是最后实际用的那个，`fell_back` 标出换过；两次请求都计入 `requests` / token / 花费。
* 手动基准 `--effort` 加 `none`；`--real --key-file` 仍是「显式给什么型号 / 强度就用什么」，不自动换；`--auto-small`（只用于 `--suite roles --real`，与 `--effort` 互斥）验证自动策略，默认关。
* **只作用于 Key 连接**：订阅登录（CLI）连接走 `_call_cli`，`small_plan` / `effective_choice` 对 CLI 连接一律「不适用」，型号与强度仍由 CLI 连接自己决定，缓存身份仍是 `cli_identity`。

## 修订（2026-11-23，用户决定）：按画布的出版规范出图（`ai_data_propose.parse_spec`、`ai_data_template.style_from_profile`）

**起因**：模板的样式只读内置默认规范（`profiles.load()` 无参），不看画布选的期刊——同一个项目里编辑器按期刊检查图，数据出图却另起一套。用户原话：「是的，直接按照该期刊的图宽和字号出图，同时我也希望面板中就直接有修改的地方，不用到设置里就能更改，这样用户看起来更加直观」。

**决定**：

1. `propose` 多一个可选的 `spec`：前端按画布绑定解析好的出版规范（`lib/specBinding.resolveDocumentSpec`，期刊覆盖已并入、快照优先那套取舍只在前端一处）。后端过 `profiles.validate_spec` 后只取模板要的数字，**不在 `ai_data_*` 里重做合并**。不带 = 默认规范，样式、脚本、响应逐字不变。 **判据不是画法（2026-11-27 更正）**：`axis_policy.enclosed_spines` 为 False 只表示这份规范不要求四周边框（期刊底、自由排版），模板只在它为 True 时才取；False 时沿用具名默认（四周边框）——此前一选期刊就把上、右边框去掉，是把「不要求」读成了「去掉」。
2. **栏宽**：`widths_mm.single / double` 有就用；缺的那档按规范的唯一读法 `profiles.derive_page_columns` 取（最窄一档当单栏、最宽一档当双栏，只有一档就两处都用它）；一档都没规定才落到具名默认 `_STYLE_DEFAULTS`——期刊没写就是没写（ADR 0155 §2），不拿别家规范的数字补。
3. **字号**（同日用户再定，原话：「出图的时候，首要条件就是字号符合期刊的规定的字号范围，然后再在不干涉的情况下，选用字号范围内的最大值，同时还要保证相同对象的字号大小一致」）：
   * 请求另带 `font_range: {min_pt?, max_pt?}`——**期刊自己规定的**上下限（前端 `journalStyleTargets`，按 `journalSources` 认，不把中性底的兜底数字当成期刊的规定）。
   * 有上限：刻度、轴标题、图例都从上限起；只有下限：从默认 9 pt 与下限中较大的起。
   * 「不干涉」由生成的脚本自己保证：`tight_layout()` 之后调公共模块的 `fit_fonts(fig, 下限)`——相邻刻度数字相交、轴标题 / 图例伸出图外、坐标区里的图例占了坐标区宽或高的四成以上时，**同一类文字一起**退 0.5 pt，直到不干涉或退到下限（没规定下限时最多退到 8 pt）。三类各自一个字号，所有子图一致。
   * 没带 `font_range`（没选期刊 / 期刊没规定字号）：正文字号（默认 9 pt）夹进规范允许的范围、图例小一号，脚本里没有 `fit_fonts` 这一行，与从前逐字相同。下限为 0 = 没规定，不当数字用。
   * **面板标号不在这个范围里**：期刊的字号上限常常只管「其余文字」（Nature：分图标号 a / b / c 要 8 pt 粗体，其余文字最大 7 pt）。模板现在不画面板标号，图里只有刻度、轴标题（含分面的子图标题）、图例三类；以后模板要自己画标号，按期刊的 `info.panel_label` 单独处理，不并进这三类、不参与退字号。
   * 面板显示的是期刊的字号范围，不是某个默认字号（用户原话：「直接显示期刊的字号的上下限范围，不直接显示默认字号」）。
4. 候选会话记下这份样式（`Session.style`），提交按同一份出脚本：选中的预览与落盘的图同宽同字号。规范进幂等键（换期刊必须重新出图）。
5. 规范不合法（不是对象 / 过大 / 过不了校验）**不让出图失败**：退回默认规范，响应带 `spec_fallback: true`。规范有效时响应带 `figure_style`（实际用的栏宽、起始字号与接受了的期刊字号范围），面板如实显示、前端不另算。
6. 规范只在本机使用：不进「读懂数据」发给模型的内容，不进遥测。
7. 前端：数据页的草稿自带一份规范绑定，初值取打开数据页时那张画布的绑定（**期刊是按画布存的，不是按项目**）；右栏最上面「期刊」一行直接改（批量出图也读它），只改草稿、不动被盖住的那张画布；新图落到的新画布带上同一份绑定，编辑器按同一本期刊检查这张图。

8. **图例放哪**（同日用户再定，原话：「如果真实字形轮廓碰到曲线，可以挪到图外。我更希望这个过程更灵活，当足够在图内放置的时候就放图内，不够的时候就放图外」）：所有从数据生成的脚本在 `tight_layout()` 之后都调公共模块的 `fit_fonts(fig[, 下限])`，它先摆图例（`place_legends`）——现在的位置不压数据就不动；压了就把图内 8 个位置挨个试，第一个不压的就用；都不行才挪到坐标区正上方排成一行（宽度不超过坐标区，排不下换行），再排一次版让位。「压到」的判法（`legend_hits_data`）与预检的图例干涉检查同一口径（与 tavotto-e8 对过，将落在 `docs/sciviz/validation-spec.md` V-011）：图例一侧取真正挡住数据的东西——有不透明底框取整块框，无框只取每条文字和每个色块的外接框（各外扩 1 px，不做到字形轮廓）；数据一侧用真实几何（线段、点位加记号半径、填充路径、误差线的线段）。带期刊字号下限时，图外的图例比坐标区宽、或高过坐标区四分之一，图例这一类再往下退字号。

**没做**：配色仍是模板里的 Okabe–Ito（规范推荐的色表不随 matplotlib 提供）；线宽、字体在期刊没规定时仍是具名默认。

## 修订（2026-11-24）：画错了的先堵上——没画的行都要有数、空分组不废整张图、误差列不是测量值

**起因**：用户 11-23 说这条流程「科学性和严谨性的表现不足」；对照 nature-figure 的审计（#2128 §5）指出几处，逐条读码并用最小表复现后属实的先修（顺序用户定：先堵漏洞，再做「依据 + 需要你确认」）。

1. **没画的行都要有数（sciviz G-055）**。`unplotted_rows` 以前只数「横轴不是值」的行；脚本里其余几处跳过一行的地方（类别 / 分组 / 分面 / 个体那一格是空的；比较类、分布类、散点类里纵轴是空的或 NaN）都没有数。现在每一处都有对应的原因码（`category_empty` / `group_empty` / `facet_empty` / `unit_empty` / `y_missing`，加上原有的 `x_not_a_value` / `not_a_number`）。折线的纵轴空格仍是缺失（线在那里断开，看得见），不算没画。大表在缩减那一遍里一并计数（`key_empty` / `y_invalid`），总数精确、行号给前 100 个。界面不新增提示（用户 11-17：画完不提示少画行）——数是算准了放在响应与提交结果里。提交后的核对 `goal.rows_plotted` 只把「有数据却没画上」的行算作没达成；纵轴空着的行（`y_missing`）是缺失，不让任务因此变成「部分完成」，但照样列在结果里。
2. **一格空的分组不废掉整张图**。取值规则（每组 n、趋势的不同 x 个数、同 x 重复……）以前按整张表分格，分组那一格是空的行被当成「只有一个点的一组」，整张趋势图会被判成 `trend_too_few_x`、箱线图被判成 `box_violin_n_small`，而脚本其实根本不画那一行。现在取值规则只看这份配方真会画的行（`drawn_rows`），那一行照常进「没画的行」。
3. **误差列不是测量值（G-053）**。名字在说「我是误差量」的数值列（`adf.error_kind`，只看列名的词、不做子串匹配，**宁可认不出、不可认错**——认错 = 把一列真的测量值悄悄踢出候选：整个名字就是 `sd` / `stdev` / `stddev` / `stderr` / `err` / `error` 或含中文「标准差 / 标准误 / 置信区间 / 误差」才单凭名字算；其它含误差词的名字（`mass_sd`、单独的 `sem` / `se` / `std` / `ci`）要表里另有它修饰的那一列才算，所以 `SEM_depth_nm`（扫描电镜测的深度）、`Se`（硒）、`std_curve` 不算）规则不拿它当纵轴 / 横轴——以前 `dose, mean_response, sd` 会把 `sd` 画成第二条线，`group, mean, sd, n` 会把 `sd` 当横轴。用户自己点了它、或去掉后一个数值列都不剩时不去；右栏下拉里仍然列得出它。名字只说 `err` 的记 `unknown`，**绝不当成 SD**（给之后「误差线：需要你确认」留依据）。

4. **拟合线的系数可追溯**。以前斜率 / 截距只在脚本运行时算，哪里都查不到。现在 `tpl.fit_report` 用与画图同一个 `linear_fit`、同一份数据算出每条线的 `{y, group, panel, n, intercept, slope, log_x, log_y}`（对数轴那一维是 log10），候选响应与提交结果里各带一份（`fits`，没有拟合线就没有这一项），脚本的来源注释下面多一行 `# tavotto-fit: {…}`（一行 ASCII JSON，标明是生成那一刻的数；之后数据变了脚本仍然现算现画，这行不会跟着变）。没有拟合线的脚本逐字不变。

**还没做**（同一批审计里属实的）：已汇总的表（均值 + 误差 + n）还没有专门的画法（应画成均值 + 现成误差列的误差线，而不是把均值当原始观测）；某一组 n < 2 时柱照画、误差线不画，说明文字仍写「误差线是标准差」。这两条要动画法与文案，和「依据 + 需要你确认」一起做。

## 修订（2026-11-22）：第 2 步之一——不该连线的折线（准则 G-018 / G-052；`ai_data_recipe.default_candidates` / `ai_data_roles._x_measure` / `ai_data_propose.parse_answers`）

**问题**。第 2 步考卷（`tests/fixtures/data_figure_cases/recommend_cases.json`，41 条）的天花板测量里，规则给出的候选含 `forbidden` 画法 6 条，其中 3 条被标「推荐」（P03B、P04B、H01）——都是「两个独立测量值之间画了折线」。规则看到一个数值列能当横轴就把趋势类排在最前，只判到「列是数值」，没有判「有序自变量」（G-018：折线只用于时间、剂量、浓度这类有序横轴；两个独立测量的量之间没有 key，用散点、不连线）。

**不靠模型：判不了，规则臂不变**。在考卷 41 条 + 读懂数据 52 条里、规则会选出数值横轴的那些表上，逐列看数据事实（不同取值占比、是否按行序单调、是否带小数）：
* 带小数不是线索：`time_min`（HO-02）13 个互异、单调、全带小数，是时间；
* 行序单调不是线索：`height_cm`（RC-21）、`count`（RC-04）单调却是测量值；`height_cm`（HO-29）不单调、全带小数，标准答案却是有序量；
* 同名同量也不是线索：`grain_size_um` 在 HO-04 是有序量、在 P04B 是测量值；P08A（温度 4 / 25 / 37）与 P08B（批号 1 / 2 / 3）连整数列的形状都相同。

「互异（占比 ≥ 0.8）且行序不单调」在这些表上没有反例，但那是文件的行序、不是变量的性质（乱序的时间序列、按别的列排序的剂量曲线都会被误判），而误判的代价是把规则本来对的时间序列 / 剂量曲线改成散点。所以**不加启发式**：没有模型判断时，规则的结果与从前逐字相同（测试钉住）。

**靠模型的列角色：判得了**。模型把规则（含模型自己的选择）此刻选作横轴的那一列判成 `measure`（`time` / `ordered_x` / 没给角色 = 现状）→ 趋势类（`line_points` / `line_band` / `line_units`）不出，关系类（`scatter_fit`、`scatter`）排前，`roles` 与候选一致。实现：
* 事实由**已校验的列角色**在 `ai_data_roles.interpret` 里派生（`Understanding.x_measure`，不改提示词、`validate_roles_shape`、缓存格式；缓存只存原始判断，照旧现算），经 `to_payload` 进 `answers.x_measure`——值是**列名**。它不是对 x / y / group 下拉的改动（不进 `picks`、不算「用户动过下拉」）；`ai_data_propose` 把 `x_measure` 加进 `_ANSWER_KEYS`（否则被 `parse_answers` 静默丢掉、也进不了幂等摘要）、经 `confirmed_from` 进 `picks`，并算 `_user_took_over`（请求已带模型建议，不再起模型任务，避免「采纳 → 再来一次 → 再起任务」的循环）。
* **用户优先靠绑定列名**：`default_candidates` 只在 `roles.x_ord == picks.x_measure` 时生效；用户在右栏换了横轴、或请求只带 `x` 不带这个键，折线照常。`interpret(fixed={x: …})`（用户已定横轴）不派生。
* 不会出零候选：x 不是数值（日期）、或关系图资格不过时不拿掉趋势类；同一组里 `scatter_fit` 不合格退到 `scatter`，第二组的 `scatter` 不重复出。拿掉的趋势类记进 `dropped`，复用 `roles_invalid`（`detail=x_is_measure`，先例 `multi_y_not_drawn`），**不新增错误码、不新增界面文案**；理由码复用 `scatter_fit` / `scatter`。
* 备选理由里「两个量都是测量值」这一档（G-039 的 data_fact）目前没有专用的理由码：建议另议（见本次汇报，由维护者定文案）。

**G-052（同一个 x 有重复观测时不逐点连线）**：规则一直有——`line_duplicate_x_unconfirmed`（逐点连线按「分组 + x」查重复，没有确认的个体编号就剔除，只剩汇总线 + 误差带 + 原始点）。核对考卷 P09A / P09B / P10B / P08A 与 N03、P04B：P09B 的候选是 `line_band`、`scatter_fit`，`line_points` 在 `dropped`；没有需要改的代码，只补了看护。

**前端依赖（必须另做）**：现有前端（`web/src/lib/dataUnderstanding.ts`，origin/main 与 d2f-roles-ui 一致）的 `suggestionFrom` 只拷 `x` / `y` / `group` 三个键，`diffSuggestion` 在三项无差别时返回 null 不再出候选——P03B、P04B、N03、H01 正好是模型与规则的 x / y 一致、只有角色不同。所以本修订在**产品里还没有效果**：旧前端不会出错（多出的键被忽略），但要前端把 `answers.x_measure` 并回 `propose` 才生效（建议 `Suggestion` 多带这一个键，「与当前 `answers.x_measure` 不同」算一处差别，且不计入「用户动过下拉」）。

**度量**（`scripts/dev/data_figure_bench.py --suite recommend --arm rules [--ideal-roles]`；`--ideal-roles` 把标准答案里每列的角色当「理想模型」走真的 `interpret` 回放，只打印、不写回）：
| 数字 | 规则臂（不变） | 理想列角色 |
|---|---|---|
| ① 第一名在 `acceptable_top`（基线） | 25 / 41 | 27 / 41（+P04B、N03） |
| ② 只重排现有候选最多 | 28 / 41 | 28 / 41 |
| ③ 换进合法全集最多 | 29 / 41 | 29 / 41 |
| ④ 需新能力 | 12 / 41 | 12 / 41 |
| forbidden 被标推荐 / 出现在候选里 | 3 / 6 | **0** / 3（余下是 P02A / P02B / P07B 的 `bar_error`，不在本步范围） |

P03B 与 H01 的第一名仍不对：标准答案要 `scatter_identity`（等比例 + y = x）或配对连线，模板还没有（第 2 步之四）。

## 修订（2026-11-24，用户决定）：一张主图 + 依据 + 需要你确认（`components/datastudio/CandidateStage.tsx`、`DataPanel.tsx`、`ai_data_propose.candidate_facts`）

**起因**：用户 11-23 原话——「我希望从数据到图这个功能，同时具备好入手和科学性。就像一个老师或者科研助理在给建议，而不是直接画完了，结果画出来的图不够科学严谨。」四宫格是「替你画完」；严谨做了也看不见。顺序他定的是：先堵漏洞（上一条修订）→ 这一步 → 按科学任务推荐 → 补画法。

**决定**：

1. **一张主图 + 小图切换，点「创建可编辑图」才生成**。选中的候选显示成一张大图（默认第一张，推荐），下面一排小图 + 画法名；点小图 / 方向键只换预览，不发请求、不生成脚本；主按钮才调提交。候选图渲染宽度从 640 提到 1440 像素（主图在 2 倍屏上约 700 CSS 像素宽）。原来的引导句「点一张，放进画布接着改。」不再显示。
2. **依据句**：主图下面一行灰字，封闭句式、按后端给的数拼，不用模型的自由文本（没连 AI 时也有这句）。句式用户定：「依据：按 3 组各 8 个数值绘制；另有 2 行因缺值未计入。」其余形状类推（折线「……，横轴有 N 个不同取值」；散点「按 N 个点绘制，每行一个点」）。后端 `candidate_facts` 给 `{rows, drawn, groups, n_min, n_max, x_values, skipped_missing, skipped_other}`：数的是这份配方真会画的行；没画的行分两类如实说（那一格是空的 / NaN = 缺值，是文字 = 不是数值），两类之和等于 `unplotted.count`。这是 sciviz G-039 三档理由里的「数据事实」一档；「感知依据」仍是每种画法原有的那句说明，「目的」留给下一步（模型推断任务）。
3. **需要你确认**：右栏列角色下面一个小标题 + 与现有行同款的下拉，只放**会改变画法或统计含义、而系统没法自己确定**的项。确认只来自用户的点击：
   * 「误差线」（当前预览的候选带误差线 / 误差带、且用户没点过时出现）：标准差 (SD) / 标准误 (SEM) / 95% 置信区间。写 `answers.error`，后端把所有候选里「均值 ± 误差」的层换成点选的那一种（`with_error_summary`），响应带 `error_confirmed`。没点 = 模板默认的均值 ± 标准差，并留在「需要你确认」下面；任何一次点选（包括仍选 SD）都算确认。
   * 「重复测量」（懒模式替用户按「不确定」处理了 `pick_design` 时出现）：还不确定 / 不是，每行是不同的样本 / 是，同一个样本测了多次。写已有的 `answers.design`（`unsure` / `independent` / `repeated`）。
   * 已确认的项从小标题下移到列角色那一组里（仍可再改）；没有待确认项时不显示小标题。
   * 模型的建议永远写不出这两个键（`ai_data_roles._PICK_KEYS` 是闭集 `y / x / group`），前端自动套用建议的逻辑也不碰它们；改它们不算「用户动过列角色下拉」，不重起也不丢掉读懂数据的任务。
4. 右栏这一组行的标签列从 44 px 加宽到 56 px（放得下「重复测量」四个字），所有行同宽、控件左缘对齐。
5. 换了 `answers` 重新出候选后，预览尽量保持同一种画法（按画法身份认，不按候选 id），没有了回到第一张。

**文案**（全部由用户看过本地真页面后定，2026-11-24：「可以，继续推进」）：主按钮「创建可编辑图」；小标题「需要你确认」；标签「误差线」「重复测量」；选项见上。

**还没做**：图创建之后在问题面板里切换误差线类型（11-21 批的那一处，涉及改已有脚本）；已汇总的表画成均值 + 现成误差列的误差线；某组 n < 2 时的说明；「重复测量」选「是」而表里不止一个编号列时让用户选哪一列。

## 修订（2026-11-25，用户反馈）：切换数据文件时按表记住草稿（`web/src/store/dataFigureStore.ts`）

用户原话：「左侧这些文件我在来回切换的时候，每切换一次，这里的图都要重画，这个不应该是画了一次就可以保存吗」。原来数据页同一时刻只有一份草稿，`select` 一律清空重来（重读概览、重新 `propose`、缩略图全部重画），而后端的候选会话本来就留 30 分钟。

- **留存**（只在前端，模块内的列表，不进 zustand 状态）：切走时把**已完整**的草稿（候选出齐、缩略图都已到或已判失败、没在出图、没出过错）整份存下，不放弃它的后端会话；切回来按「表（合画是有序的一组表）+ 各表头行 + 出图用的期刊绑定」命中就整份放回，不读表、不出候选、不重画。半成品不存。
- **失效**：清单里文件 `size` 与**发出出候选请求那一刻**记下的不同（大小跟着候选走，不在切走时才记；出候选时清单还没读到大小的草稿不留存——没法验证就退回从前的行为）；从候选响应到达起超过 30 分钟（与后端 `SESSION_TTL_S` 同数，取响应到达时刻、偏保守）；期刊绑定不同；换项目（全丢并放弃会话）；条数上限 `MAX_SESSIONS − 1 = 5`（当前草稿占一个），超了丢最久没用的并放弃它的会话。
- **兜底**：还原后缩略图取不到（会话其实没了）→ 静默带着现有答案重出一次，不循环；提交回 `propose_session_expired` / `propose_data_changed` 走原有的静默重出。
- **会话去向**：当前草稿存不了（半成品、文件变了）时，无论要去的那张表命没命中留存，都先放弃它的后端会话（不留既不留存也不放弃的会话占后端名额）。
- **不变的**：关掉临时标签 = 这份草稿不要了（丢并放弃会话），别的表的留存保留；提交成功变成正式的图之后不再留存。
- **已知缺口**：大小没变但内容变了的文件比不出来，还原出的是旧图，到提交时后端回 `propose_data_changed` 才重出；浏览器图片缓存给出旧缩略图时同此。读完概览但没有候选的终态（全是文字 / 要点表头行等）不留存，切回会重读。

## 修订（2026-11-24）：已汇总的表——把现成的误差列画成误差线（`ai_data_recipe.paired_error` / `FigureRecipe.err`、`ai_data_template`、`ai_data_propose`）

**起因**：上一条修订把名字在说「我是误差量」的列从纵轴 / 横轴里踢了出去，但只是忽略它：`dose_uM, mean_response, sd` 只画了均值、误差没了；`group, mean, sd, n`（一行一个组）仍按「每个均值是一次原始观测」去算均值 ± 标准差、箱线、分布。用户批的顺序是「……再把现成的误差列画成误差线」，且「误差列类型不明时给『不确定』选项、绝不默认成 SD；名字写了 sd / sem 的就按名字」。依据：sciviz **G-053**（误差量不是观测，汇总过的数据不能再汇总）、**G-065**（误差线必须写明 SD / SEM / CI 以及 n 数的是什么）、G-055（没画的观测要有计数）。

**事实**（都由规则按列名与行数据判，模型 / 用户点名不写；不新增子开关，仍在 `ai.taf.enabled` 后面）：

1. **配对**：`paired_error(摘要, y, axes)` 回答「`y` 配着哪个误差列」。名字规则只有一份——`adf.error_match`（`error_kind` 是它的第一个返回值）：`mass_sd` ↔ `mass`、`response_sem` ↔ `mean_response`；光秃秃的 `sd` / `sem` / `err` / 中文词配「恰好一个测量列」（去掉误差列、样本量列、编号列与 `axes`——横轴等不是测量的列），多个时只认名字带 `mean` / `avg` / `average` 的那一列；**同一个测量列有不止一个误差列、或配对有歧义 = 不配**（宁可不画，不猜）。`SEM_depth_nm` 一类仍不是误差列（判据没放宽）。名字恰好是 `n` / `count`、表里又有误差列陪着的数值列是**样本量列**，规则不当它纵轴 / 横轴（用户自己点了照点）。
2. **配方**：`FigureRecipe.err`（误差列名，默认空）与 `err_kind`（闭集 `sd` / `sem` / `ci95` / `unknown`）。贯穿校验 → 脚本（来源注释里有配方、脚本多一行 `ERR = '…'`）→ 提交。空 = 与从前逐字相同（脚本、配方字典、期望指纹都对拍过）。校验：只许比较 / 趋势类、恰好一个 `y`；`err` 是另一列、纯数值列；与个体编号 / 配对 / 把列当分组互斥；图元只许点（比较）/ 点、线、阶梯线（趋势）且都不带汇总；画的行里（纵轴有值的）误差不能为负；每个位置恰好一行。`err_kind` 与 `err` 同有同无（G-065）。比较类带 `err` 时横轴可以是「每格都不同的文字列」（本来要有重复取值才算分组）——只对 `err` 配方放宽。模型的配方带 `err` / `err_kind` 一律丢掉（`recipe_unknown_key`）：模型说的「这是 SD」没有依据。
3. **备选**（`default_candidates`）：表是「已汇总」的形状——配得上误差列、且每个位置恰好一行（有序横轴：分组 × 分面 × 横轴取值；否则类别 × 分组；没有分组列时表里**恰好一个**每格都不同的文字列当类别轴）——才多出：`line_err`（趋势：点 + 线，误差线取自误差列）与 `points_err`（比较：一类一个点），排第一。这种表**不再给**均值 ± 误差、箱线、小提琴、ECDF、直方图、核密度、均值线（备选闭集里剔掉，`dropped` 记 `summarised_not_raw`，`detail` 是被剔的图元）；只画给定点的散点 / 普通折线仍可在 `*_err` 之后。有重复测量的表**行为不变**（误差列照旧被忽略）。`err_kind` 取自列名（`sd` / `sem` / `ci` → `ci95` / 只说「误差」→ `unknown`），用户的 `answers.error` 覆盖（列不变、`err_kind` 换成他点的）。
4. **画法**：`ax.errorbar(x, y, yerr=误差列的值, fmt='none', …)`，线宽 / 帽 / 颜色与「均值 ± 误差」的误差线同一套；误差格是空 / NaN（或运行时变成了负数 / 文字）的点照画、只是没有误差线。公共模块多了 `comparison_error_points` / `trend_error_points`，`comparison_data` / `trend_data` 多一个可选 `err`（与取值同一份行过滤，不另写一遍）。
5. **响应**：`error` 报 `sd` / `sem` / `ci95` / `unknown`；多 `error_source`（`column` 取自现成误差列 / `computed` 用重复值算的）、`error_column`、`error_stated: true`（种类是**列名自己说的**——不是 `unknown`、不是用户点的；界面据此写「取自列名」而不是「需要你确认」）。`error_confirmed` 语义不变：只有用户发了 `answers.error` 才是 true。`facts` 数的是画进去的行；误差格缺失的行是画了的、不算没画（`unplotted_rows` 不变）。
6. **算出来的误差线 n < 2**（同一批审计里的另一条，只给事实）：均值 ± 误差的 `errorbar` / `band` 层里，只有一个值的位置没有误差线 / 带——每个这样的备选多 `error_missing_for: [标签…]`（比较类是类别 / 分组 / 分面，趋势类是横轴取值；用的是脚本画图的同一批函数；至多 50 个；空则没有这一项）。不写文案、不改界面。

**核什么、不核什么**：点与线两层照旧按 label 对指纹（`expected_fingerprints` 与没有 `err` 的同一份配方逐项相同）；取自误差列的误差线与现有的误差线层一样**没有 label、核不了**——`fingerprint_coverage` 在最后多列一条 `{mark: errorbar, checked: false, reason: errorbar_has_no_label}`（序号在配方的层之后）。所以提交后 `data_check` 是 `verified` 的含义是「点与线对上了数据」，不含误差线的数；误差线的数由 `tests/test_ai_data_err_column.py` 里真执行脚本、量 `Axes.errorbar` 收到的 `yerr` 来保证。

**新增的闭集**：配方 `err` / `err_kind`（`ERR_KINDS`）；备选码 `line_err` / `points_err`；拒绝码 `summarised_not_raw` / `error_values_negative` / `error_cells_not_unique`（`REJECTIONS`，前端 `errors:backend.<码>` 文案由前端那一步补）；响应键 `error_source` / `error_column` / `error_stated` / `error_missing_for`。

**没做**：已汇总的表上「柱 + 现成误差线」；多个 y 列、有个体编号、合画、大表的现成误差列；误差类型 `unknown` 时右栏「需要你确认」里加「不确定」之外的选项文案（前端）；图创建之后切换误差线类型；n 数写进图注（图里仍不写任何自动脚注）。

**补充（2026-11-25，接手核对与界面）**

- 类别轴那一列：没有分组列、没有有序横轴时取「每格都不同的那一个文字列」，**名字像编号的列（`sample` / `id`）也算**——已汇总的表里它就是每一行的名字；没有误差列的表不受影响（仍不把它当类别轴）。这一列在响应的列角色里如实报成 `group`（右栏「分组」一行显示它），误差列不再列为横轴的备选（右栏不出现「横轴 / 不要横轴」一行）。
- 界面（`CandidateStage` / `DataPanel` / `lib/dataBasis.ts`）：两种画法的名字「点 + 误差线」「点 + 折线 + 误差线」；说明句不重复列名，列名只在依据句里（「依据：按表里的 N 行绘制，每行一个点；误差线取自「列」列」）；算出来的误差线缺位置时依据句末接「N 处只有 1 个数值，算不出误差线 / 误差带」，只有一处时直接点名，多处的位置在该行的 `title` 里；「误差线」一行——列名自己说明了种类的不进「需要你确认」，`unknown` 的进并显示「还不确定」。文案由用户 2026-11-25 过目同意（「这些文案都可以」）。
- 推荐考卷（`tests/support/data_figure_recommend.py`、`recommend_cases.json`）：写考卷时这种画法还没实现，记作 `missing` 的占位码 `errorbar_from_columns`；实现后按族落成 `points_err`（比较）与 `line_err`（趋势）两个 `coded` 码，三条用例（RC-P06A / RC-P12A / RC-H05）的 `acceptable_top` 相应从占位码换成这两个码——标准答案的含义（用表里现成的误差列画误差线）没变。规则第一名落在标准答案里：25 / 41 → 28 / 41。

## 修订（2026-11-25，用户决定）：「读懂数据」用哪个 AI，可以选、可以关（`ai_data_aichoice`、`GET|POST /api/ai/data-understanding`）

**起因**：用户原话——「这里应该需要有一个小图标，点击后可以打开列表，让用户可以选择进行分析的ai。也可以选择关闭。」（指「从数据到图」左栏数据抽屉；「分析」= 第 1 步「读懂数据」。）此前它自动用第一个可用连接，用户既不能选、也不能关。

**决定**：

1. **三种选择，存在后端**（应用级，不是按项目）：`ai.taf.understanding`——缺省 = `auto`（行为逐字不变）；`{"mode": "off"}`；`{"mode": "connection", "connection": "<连接 id>"}`。连接 id 沿用现有的：Key / 账号登录 = provider 记录 id，订阅登录 = `sub:codex` / `sub:claude`。**畸形 / 不认识的 mode 一律当 `off`**（宁可不发数据，也不把坏配置当成 `auto`）。
2. **判据只有一处**：`ai_data_roles._available_connection()` 经 `ai_data_aichoice.resolve`。`off` → 没有连接；指定连接 → **只找那一个**，此刻用不了（被删 / 未登录 / 不在能力缓存里 / 被判不可用）= 没有连接，**绝不悄悄换成别家**（那等于把用户的数据发给他没选的服务）。
3. **指定连接必须显式传下去**：以前 Key 连接只靠 `call_structured` 自己再挑一次「第一个 Key 连接」（并联网刷新令牌），只对订阅登录（CLI）连接才把挑中的传下去。指定连接时这样会把数据发给 A 而不是用户选的 B，所以现在 `start(...)` 在 `mode=connection` 时把解析出的连接显式传给 `judge`，并只刷新**被选中的这一条**（`ai_data_aichoice.refresh_key`，后台线程里；刷不动 = `no_connection`）。`auto` 下传给 `call_structured` 的参数与从前完全一致（`tests/test_ai_data_understanding_choice.py` 钉住）。
4. **出站入口清单**（数据出图里会去找连接 / 调模型的地方，全仓 grep）：只有两处——`ai_data_roles`（`start` → `_run` → `judge` → `ai_structured.call_structured`）与 `ai_data_model.propose_recipes`（产品里目前没有调用方）。两处都服从上面的判据：`off` 时一个请求都不发、也不读磁盘里旧连接的判断（`start` 在查缓存之前就返回）。其余 `ai_data_*` 模块没有任何模型 / 网络调用。
5. **可选清单**（`options`）：此刻真能用于「读懂数据」的连接——Key 连接（与 `first_key_connection` 同一套跳过规则）与订阅登录里 `supports_structured()` 且解析得出型号的；每项 `{id, label, logo, vendor, kind: "key" | "subscription", model}`，label / logo / vendor 沿用连接视图的字段，**不含密钥 / 地址 / 令牌**。只看本地配置与能力缓存：不联网、不刷新令牌、不探测。
6. **接口**：`GET /api/ai/data-understanding`（轻量，随连接状态变化可反复取）与 `POST /api/ai/data-understanding`（body 只认 `{mode, connection?}`，多余键 / 类型不对 400 `propose_input_invalid`；`connection` 的 id 不在此刻的清单里 400 `understanding_connection_unavailable`，什么都不写），都在 `ai.taf.enabled` 后面、走会话鉴权 guard。`/api/ai/data-files` 多带 `understanding: {state: "on" | "off" | "none" | "unavailable", mode, connection, options}`；`understanding_available` 保留，语义 = `state == "on"`。`propose` 在 `off` / `unavailable` / `none` 时响应里没有 `understanding` 键（原有的 None 路径）。
7. **换连接后的缓存与在途任务**：缓存键、单飞登记键、60 s 负缓存键都含连接身份（Key = provider id + 实际型号与强度；CLI = agent + 型号），换连接不会命中上一个连接的缓存 / 任务 / 负缓存。写入接口另会清掉「身份与新选择对不上」的任务登记（不分项目——选择是应用级的；同一连接的任务不动），用旧连接判断出的建议不再被轮询到（回 `off`）。**已经发出去的那一次请求收不回**（后台线程在途；它的结果只会落进缓存，不会再被用）。
8. 不加任何遥测；用户的脚本与数据不被改动。

**没做**：界面（小图标与列表，前端另做）；订阅登录的能力缓存为空时指定的订阅连接是 `unavailable`（前端在连接状态到达后重取）。

**界面**（`components/datastudio/DataAiChoice.tsx`、`DataDrawer.tsx`、`useConnectAi.ts`、`store/dataFigureStore.ts`；用户 2026-11-25 看过本地真页面后回「可以，就这样继续」）

- **位置与形状**：数据抽屉左下角一枚扁平的图标按钮（ghost，悬停 / 展开才有浅灰底；浮着、不占布局高度，清单底部留出内边距）。`none`（没有任何可用的 AI）不出图标，仍是原来居中的「连接 AI 以供智能分析」胶囊；其余三种状态靠**形状**区分：在用 = 当前连接的厂商图标（没有则星光）、关了 = 星光加一道贯穿的斜线（图标集新增 `SparklesOff`）、指定的用不了 = 拔掉的插头。悬停提示与无障碍名是同一句：「分析用的 AI：{{name}}」/「不用 AI 分析」/「该模型无法连接，请换一个尝试」。
- **列表**（`ui/Menu` 的 `MenuRadioGroup`）：「自动」（在用时下面一行小字「当前：{{name}}」）、每个可用的 AI 一行（名字 + 型号小字）、「不用 AI 分析」、分隔线、「连接其他 AI…」（现有的连接弹窗）。指定的连接此刻不在清单里时补一行指认它（`understanding.chosen`），下面小字「该模型无法连接，请换一个尝试」。点选即关、图标立刻换，失败回退并用现有的胶囊说后端那一句。只选连接，不选型号；**不加任何解释数据发去哪里的句子**。
- 为了让带小字与不带小字的行对齐、图标与勾相对整行垂直居中，`MenuRadioItem` 多一个可选的 `roomy`（不传 = 逐字不变；只带 `description` 的现有菜单仍是顶对齐，有用例锁住）。
- **切换之后**：关 / 用不了 → 开，或换了一个 AI：对当前草稿走原有的「连上后重跑一次读懂数据」（用户动过下拉 / 正在出图时照旧不重跑）。变成关：停掉在途的轮询，**已经按 AI 建议填好的列角色保持原样、不回退**。留存的草稿（上一节）把 `mode + chosen` 放进命中判断，AI 选择不同 = 不命中、重新出。
- **重新询问**（#2208 的那条路）：有这个状态对象时，只在 `none` / `unavailable` 且前端看到有可用连接时才重取，改用轻量的 `GET /api/ai/data-understanding`；`off` 时绝不为此重取。连接集合变了时任何状态都用这个轻量接口刷新一次清单选项（只读，不是模型调用）。
- **界面显示的选择与实际行为保持一致**（Codex 评审 #2230）：①显式换 AI 重跑前，先把上一个 AI 自动填的那几个答案（`autoPicks` 记着、值仍是自动填的）从 `answers` 里拿掉——否则后端当成「用户定了」而不去问新 AI；用户自己动过的保留。②出候选请求带「实际在用的 AI」的代际，回来时代际变了，其中的读懂数据结果一律不应用（关了绝不会再套 AI 的结果）；换 AI 时候选正在加载，就等它落定后补跑一次。③读状态与写选择各自计序，读刷新排在未完成的写之后，一次 GET 吞不掉写的结果和它该触发的重跑。AI 建议改过的表头行不在 `autoPicks` 里：#2232 起另记一份 `autoHeaderRows`（路径 → AI 给的行号；用户自己点选表头行就从中删掉，那一条算用户的），显式换 AI 的重跑在清自动答案的同一处把「仍等于 AI 写的值」的表头行一并回退（所有文件的，不限当前这张表；用户 2026-11-25 定：「换 AI 时把 AI 改的表头行也回退」）。当前草稿的表头行因此变了就重新读表（`run({keepChoices: true})`，用户点的答案保留）。变成「关」不回退。正在出图 / 用户动过下拉时那一次重跑不发、也不回退，留一个标记到下一次重新读表之前再退。
- 用户定的两条行为：指定的 AI 用不了时**不自动换别家**（按不分析处理）；关闭后已由 AI 选好的横轴 / 纵轴 / 分组**保持原样**。

## 修订（2026-11-25）：按看图目的重排，并说明为什么不是另几张（`ai_data_purpose`、`ai_data_propose`；路线第③步）

**起因**：维护者定的第③步是「按科学任务推荐并说明为什么不推荐另外几种」，口吻像老师 / 科研助理给建议，而不是直接画完。决策框架 §2 规定任务**不由推荐器自己定**：只有用户点选的算「用户确认过的目的」，从数据结构推出的永远只是假设。本期不让模型猜目的、不加自由文本入口。

**命名**：框架 §2 / 推荐考卷里的「任务」（`task`）在这里叫 **`purpose`（看图目的）**，只为避开 `ai_data_task`（提交 / 生成脚本的流程）。取值是同一套词，界面本期只开四个：`compare_groups` / `trend` / `relationship` / `distribution`；考卷里别的任务（`dose_response` / `paired_change` / `agreement` / `part_whole` / `ranking` / `pattern_in_matrix`）没有模板或不在本期范围，不进闭集。

**事实**（规则判，不调模型、不联网、不读数据；仍在 `ai.taf.enabled` 后面）：

1. **请求**：`answers.purpose`。进 `_ANSWER_KEYS`（闭集外的值丢弃、当没给）；**不进** `_user_took_over`（点选目的不算接管列角色，与 `error` 同理）；进幂等键（`_answers_digest` 带整个 `ans`）。
2. **纯排列**：`ai_data_purpose.resolve` 在 `default_candidates`、合画成对、换误差类型**之后**只调换已有 `Candidate` 的顺序（稳定分区：该目的对应的画法排前，其余与每类内部保持规则原顺序）；不新增、不复活被规则拿掉的备选（`x_independent` 拿掉的折线、`summarised_not_raw`、`x_is_measure`、被 `MAX_CANDIDATES` 截掉的都不会因目的回来）。`Candidate` 对象原样搬动，成对的两张仍相邻；合画成对的标记长在候选自己身上（`Candidate.variant`，2026-11-27 #2179，不再按 `id(recipe)` 旁挂），`replace` 换配方时跟着走，不用手动挪。排前的码：看比较 = `points_mean` / `box_points` / `violin_points` / `points_err`（框架 §4）；看趋势 = `line_*`（G-018）；看关系 = `scatter_fit` / `scatter`（G-018）；看分布 = 规则**留着**的 `box_points` / `violin_points`（没留着——每组点太少被规则拿掉——就不重排）。没有依据的并列项（如 `line_band` 与 `line_points`、`hist` 与 `ecdf`）保持规则原顺序；`bar_error` 从不被提前。
3. **没有 `answers.purpose` 时**：假设的目的 = 规则现在第一名所属的族，重排是恒等；候选的集合与顺序、`recommended`、响应里除新增键以外的一切逐项不变（对拍：41 条推荐考卷 × 带 / 不带标准 `picks`、14 条跑分用例，剥掉 `purpose` / `why_not` / 随机 id 后与改动前逐项相同）。
4. **响应**：顶层新增 `purpose: {code, source: "user" | "data", options: [...]}`——`code` 是实际生效的目的（用户点的、且这批备选里有它的族；否则回到假设的，`source` 为 `data`），`options` 只列「这批备选里至少有一张能回答」的目的（固定顺序），不足两个时为空（界面不出这一行）。非推荐的备选可带 `why_not: {code, guidance: [G-…], tier}`，只在有适用理由时出现，推荐的那张没有。
5. **原因码闭集 `WHY_NOT`（4 个）**：`summary_hides_spread`（G-012，`bar_error` 相对看比较 / 看分布）、`line_needs_order`（G-018，`line_*` 相对看关系）、`order_prefers_line`（G-018，`scatter*` 相对看趋势）、`answers_other_purpose`（无 G，`tier: "purpose"`，这张回答的是另一个问题）。前三个有 G 依据，**不论目的是用户点的还是假设的都给**；`answers_other_purpose` **只在 `source: "user"` 时给**（没确认过的目的不能当理由，框架 §6）。理由三档 `data_fact` / `perceptual` / `purpose` 分开，不出「最优」「置信度」。测试核每个码引用的 G 在 `sciviz_guidance` 里存在且 basis 不是 `taste`；品味条目（G-042–G-051，如 G-050）不能当把某种画法排后的理由。
6. **推荐考卷回放臂**：`tests/support/data_figure_recommend.py` 的 `purpose_arm`——把用例标准答案里的 `task`（只取四个之内的，41 条里 30 条）当 `answers.purpose`，**等于「用户点了对的目的」**。规则第一名落在 `acceptable_top` 的条数 28 → 31（RC-P02B、RC-P04B、RC-N03 修好，无回退）。`acceptable_top` / `forbidden` 一个字不改，`expressible` 本期不重量。

**已知限制**：

- **先截到 4 张再重排**：规则先取至多 `MAX_CANDIDATES` 张，目的只在这几张里排；被截掉的族不出现在 `options` 里，点它（手填请求）也不会让它回来（`test_a_family_squeezed_out_by_the_four_card_cap_is_not_offered`）。
- **分布的「箱线在场」是点数够不够的代理**：每组点数的门槛（`MIN_N_BOX`）是工程选择（G-013 没给 n）；没有箱线 / 小提琴时「看分布」不改顺序，也没有「点数太少」的理由码（被规则拿掉的备选用户预览不到，理由没处显示）。
- **两条考卷用例没有目的时第一名就是 `forbidden`**：RC-P03B（`agreement`）、RC-H01（`paired_change`）——它们的任务不在这四个里、目标画法（`scatter_identity` / `paired_lines`）没有模板，本期救不了；RC-P04B 没有目的时同样违规，点对「看关系」后修好。测试把这三条列为有名字的例外，只要求带目的后不比从前差，其余用例不许新增 `forbidden_recommended`。
- 换目的是一次新的 `propose`（新会话，懒模式下缩略图重画），不是本地切换。
- 目的行的界面文案与交互（前端）不在这一期。

**不变的**：不加遥测、不联网；用户脚本与数据不动；`default_candidates` 的签名与行为不变（目的在它之后才用）。

**界面**（`DataPanel.tsx`、`CandidateStage.tsx`、`store/dataFigureStore.ts`；用户 2026-11-25 在示意页上逐项点选、2026-11-26 改了两句原因的说法）

- 右栏多一行「目的」（用户定的行名），放在列角色之后、「误差线」/「需要你确认」之前；选项来自响应的 `purpose.options`（「比较各组 / 看趋势 / 看关系 / 看分布」），当前值是 `purpose.code`。**没选时就是普通一行，不标它是推测的**（用户选的方案 C）：`source` 是 `data` 还是 `user` 在这一行上不做区分，也不进「需要你确认」。`options` 为空或 `code` 为 null 时这一行不出现。
- 选了目的 = 带 `answers.purpose` 重新出候选；**改「目的」的这一次把预览放回重排后的第一名**（用户换目的就是想看按这个目的该先看哪张）。改误差线类型等其它项不重置预览；留存草稿还原、换 AI 重跑、套用 AI 建议也不重置。`purpose` 不进 `autoPicks`，换 AI 重跑时保留。
- 主图下面「依据」之后多一行同样样式的小灰字，只在当前预览的那张带 `why_not` 时出现，**独立一行、不带开头**（用户定）。四句：`summary_hides_spread`「柱和误差线通常只代表均值与离散程度，不显示每个观测值。」；`line_needs_order`「连线通常代表连续变化的量。」（用户给的原话——原草稿「两个量没有先后顺序……」在横轴明明有序、而用户选了「看关系」时不是事实，所以改成陈述画法通常代表什么、不对数据下断言）；`order_prefers_line`「横轴有顺序，连线更能看出走势。」；`answers_other_purpose`「你选的是「{{purpose}}」，这张回答的是另一个问题。」。有这一行时主图的最大高度多让出约 20px。
- 已知：`bar_error` 现有说明句末尾的「个别点会藏在柱里」与 `summary_hides_spread` 那句意思重叠，现有说明是已上线文案、没有动，是否合并待用户定。

## 修订（2026-11-25）：现成误差列的后续——柱、多个 y、合画（`ai_data_recipe._summarised_shape`、`FigureRecipe.err`）

接上一条「已汇总的表——把现成的误差列画成误差线」里「没做」的几项（依据同样是 sciviz G-053 / G-065 / G-002 / G-050）：

1. **`bar_err`：已汇总分组表上的「柱 + 现成误差线」**。只在「类别 × 分组各一行 + 配得上误差列」的形状里给（趋势没有柱），排在 `points_err` 之后（G-050：均值柱常有更好的画法；`enforce_order` 本来就把「只有柱、没有原始点」的备选排在含原始点的之后），类别顺序仍是 `as_given`（后端判不了类别有没有顺序，G-040 不在这里做）。**资格四条缺一就不给柱**（宁可不给，不截须）：线性轴（对数轴上的柱表示比值，要先问用户，G-003）；每个画的 y ≥ 0（G-002，柱必须从 0 起）；每个有误差的位置 y − 误差 ≥ 0（柱图把 y 轴下限固定在 0，低于 0 的须会被截掉）；误差列是纯数值列、画的行里没有负误差。配方是 `[{bar, summary: none, main}]` + `err` / `err_kind`（`ERR_MARKS["comparison"]` 多了 `bar`），指纹：柱可核（`bars`），误差线仍核不了。新增候选码 `bar_err`；没有新拒绝码和响应字段。汇总表没有原始点，所以 G-012「露出原始数据」不适用——界面文案按事实写（柱高是表里的均值、误差线取自表里的误差列），不替读者推断每个观测。
2. **多个 y，各配各的误差列**（`time, a_mean, a_sd, b_mean, b_sd`，宽表规则本来就会选出 2–3 个 y）：每个 y 各自 `paired_error` 唯一、各误差列互不相同、纯数值且无负值、每个 y 自己「分组 × 分面 × 横轴取值」一行，**缺一个整体不给**（没有「一半有误差线」）。只有趋势类。配方：`err` / `err_kind` 单个 y 仍是字符串（逐字不变），多个 y 是与 `y` 等长的数组（`FigureRecipe.err_cols` / `err_kinds` 统一读）；一个元素的数组不是合法写法。响应：单个 y 仍出 `error` / `error_column` / `error_stated`；多个 y 不出后两个，改出 `error_columns: [{y, column, kind, stated}]`；**顶层 `error` 只在各列种类一致时才出**（不一致时由前端逐列说明，不拿一个值去概括）；`error_source` 仍是 `column`。`answers.error` 覆盖时所有 y 一起换；各 y 的种类可以不同（`a_sd` / `b_sem`）。
3. **合画**：去掉原来对合画条目的排除，按**合并后的那张表**照同一口径判（各份列名相同，文件标签是分组列）；某一份有重复测量 = 整体不是已汇总（不给 `*_err`、也不剔别的）。已汇总时不再生成「只按文件比较」的成对变体（它会对各组的汇总值再算一次均值 ± 误差 / 分布，G-053）。类别形状下，横轴是合成的文件列、颜色分组是原来的分组列（每个（文件, 组）一行）。
4. **明确不支持（留了用例锁住「不触发、别的备选不被剔」）**：大表（几乎不可能是每个位置一行的汇总表；缩减后只剩各箱的包络行，没有行级判据）；有个体编号（`unit`，每行是某个体的一次观测，与「一行就是一个汇总」互斥）。
5. 考卷（`tests/support/data_figure_recommend.py`）：`METHODS` 多一项 `bar_err`（`coded` / `comparison`），`classify` 把带 `err` 的比较类按有没有柱分成 `bar_err` / `points_err`；**标准答案（`acceptable_top` 等）一个字没动**，只重量了 `expressible`：规则第一名落在标准答案里仍是 28/41，只有 RC-P06A / RC-H05 的候选清单多了 `bar_err`。

**还没做**：创建图之后换误差线类型（调研结论：取自现成误差列的误差线换种类不改变图上任何像素，因为图里不写脚注；换个画法本来就是重出一份新脚本）；多个 y 的分组比较；类别有顺序时按顺序 / 按值排序。


## 修订（2026-11-26，用户决定）：点选算出来的误差线，在属性页换 SD / SEM / 95% CI（`web/src/store/figureOriginStore.ts`、`lib/dataFigureOrigin.ts`、`components/inspector/ErrorKindRow.tsx`）

上一条里「创建图之后换误差线类型」做了其中**算出来的**那一半（`error_source === 'computed'`：均值 ± SD / SEM / 95% CI；取自现成误差列的类型由数据决定，不换）。后端不改。

1. **入口**：误差棒属性页（`ErrorBarPage`）最上面多一行「误差线 [SD ▾]」，三项沿用数据页右栏的 `dataFigureV2.confirm.option.*`，不新增文案。出现条件缺一不显示：实验开关开着；这块面板是本次会话里用数据页画的（`figureOriginStore` 记着它的来历）；误差线是算出来的、单文件（合画不记）；**图没被改过**。
2. **来历**：数据页提交成功、进编辑器之前（`dataFigureStore.noteOrigin`，要赶在 `goEditor` 清候选之前）按**脚本路径**登记：表、表头行、当时的 `answers`、已解析规范与字号范围、候选身份（`candidateIdentity`，不含误差类型）、当前类型。不持久化、换项目即清（`clear()` 带着清）、最多 8 份。面板第一次出现在文档里时（宿主订阅文档）再记下它的来源指纹。
3. **「没被改过」不是「overrides 为空」**（`figureUntouched`）：写回脚本（ADR 0094）会把写进脚本的 override 从文档里清零；绑定样式的画布上新图会被自动对齐、overrides 一开始就不空。判据按图上看得见的东西列：每条 override 都得是样式写的、且仍是样式写下的值（`ownedLive`）；素材没有写回基线；面板记的来源指纹、素材清单里的指纹都与登记时一致（指纹含脚本内容的 sha256），素材状态是 `fresh`。位置 / 缩放 / 裁剪 / 旋转不算改过（与 `replacePanelAsset` 同一口径）。拿不准一律不给换。换图前（等脚本跑完之后）再核一遍。
4. **换图**：不借 `dataFigureStore.propose()`（它会改写开着的草稿）。直接 `propose`（`answers.error` 换成点选的）→ 用候选身份认回同一种画法（找不到 / 新一批里不是算出来的这种误差就不换）→ `commit` 成**新脚本**（旧脚本留在项目里不删）→ 放弃这个会话；整条在数据页提交共用的串行锁里。会话名额：后端同时至多 6 个，换类型多开一个之前先把「按表记住」的留存丢到只剩 `KEEP_MAX - 1` 个（`reserveBackendSession`，最旧的先丢、同时放弃它的会话），开着的草稿不动。
5. **原位换**：宿主（`lib/dataFigureHost.ts`）刷新项目 → `scriptRunStore.run(新脚本)` → 在**同一块面板**上 `actions.replaceRuntimeFigure`：保留面板 id（选区 / 组 / 锚在它上的标注）、外框、层级、裁剪、旋转，只换图源，overrides 清空，**一条可撤销的历史**（标签复用「替换素材为 …」，撤销回到旧图）。换完选中它并回到图内编辑。新脚本也登记自己的来历（可以再换回去）；旧脚本的来历留着（撤销之后那一行仍在）。
6. **失败一律静默**：认不回画法、提交没成、运行没出图、换了项目、等的时候图被改了 / 面板没了，都回到原图、不弹错误块（下拉在换的过程中立即显示新值、转灰禁用、状态槽转圈，回退即回原值）。提交之后才放弃时新脚本已落盘、留在项目里，旧图不变。
7. **阴影带也能换**（同日补）：趋势图的「均值线 + 阴影带」（候选 `line_band`，`error_source: computed`）里，带是 manifest 的 `fill` role（gid `axes_0.fill_N`，多组时每组一条、换的是整张图）。点中带时属性页（通用 `FieldList`）最上面出同一行「误差线」（`ErrorKindRow` 的 `band` 形态，同一组 i18n 键、不新增文案）。登记时记 `band`（候选 `title_code` 含 `band`）：点带的页只在带的图上出；误差棒页两种图都出；线 / 散点点中不出。换完选回同一个 gid，没有就第一个误差棒，带的图再退到第一条带（`figureSwitchSelect`，订阅渲染态、不计时）。画布名还是旧图当初自动起的名字（`doc.name === 旧 source.stem`）就在同一次 commit 里跟着改成新图名，用户改过名不动。
8. **换类型的重出不起「读懂数据」**：后端 `propose` 新增可选请求参数 `understand`（布尔；`false` = 不起 `ai_data_roles` 后台任务，响应没有 `understanding` 键、候选逐字不变；不给 / `true` = 与从前逐字相同；非布尔 400 `propose_input_invalid`；进幂等键）。换类型那次重出不是用户在看数据，不该悄悄多发一次模型出站，前端一律带 `understand: false`。看护 `tests/test_ai_data_no_understanding.py`。
9. **只换发起的那一块面板，换图前先结束手势**（Codex #2275 两条 P1）：同一张图可以在画布上复制成几块，所以目标不能按脚本去找（来历现在随面板走，复制出来的每一块各带自己的一份，见第 10 条）（排版模式没有激活面板，会退回第一块匹配的，悄悄换错）。属性页那一行把**自己那块面板的 id** 一路传进 `switchKind(panelId, kind)` → 宿主 `replaceFigure({ panelId, … })` → `replaceRuntimeFigure`；宿主只认这个 id（还在、仍画旧脚本、没被改过，任一不成立静默放弃；换完另一块的来历不动）。`switching` 也带 `panelId`，转圈与 `aria-busy` 只出现在发起的那一块；换完另一块仍指向旧脚本、来历仍在，可以各自独立再换。换图是离散动作：等脚本跑的时候用户可能已按下拖动 / 缩放，`commit` 会并进那次手势事务（一次撤销连移动一起退）。所以提交前经 `runDiscreteAction('app', null, …)` 先结束进行中的手势，**在收手势之后**再复核（面板、脚本、画布、没被改过）再提交，换图永远是独立的一条历史。看护 `figureOriginSwitch.test.tsx`。
10. **来历落在面板上，关掉项目再打开仍能换**：`PanelObject.dataOrigin`（可选字段，schema 不升，旧构建忽略、图照常；复制面板、`.tavotto` 项目包带着它）。内容是表（**只存项目相对的 POSIX 路径**；数据在项目文件夹外的不登记，写盘前和读出来各判一次，`lib/dataFigureOrigin.isProjectRelativePosix`）、表头行、answers、已解析规范与字号范围、候选身份、当前类型、`band`、来源指纹、画图时数据文件的 `dataSha256`。写入有两处：面板第一次出现在文档里时宿主把内存里待认领的来历写上去（`applyDerivedUpdate`，不进撤销历史，与 `panelSourceSync` 同档）；换图那一步在 `actions.replaceRuntimeFigure` 的同一次 commit 里写新来历（撤销一起回到旧图的那份）。第一处写不进撤销历史，所以新建图那一步被撤销再重做时，面板是照历史里「还没有来历」的样子回来的：宿主把写过的那份留在内存里（`figureOriginStore.claimed`，按脚本路径、连同当时的来源指纹，最多 8 份、换项目即清），同一个脚本、同一个来源指纹的面板再出现就补回去，否则这一行会永久消失、还会把没有来历的面板存进项目（`figureOriginRedo.test.ts`）。读一律经 `readPanelOrigin`，**严格**：版本不是 1、形状不对、路径不是项目相对、摘要不是 64 位十六进制 = 当没有。**不外流**：只在用户自己的项目文档里；导出请求（`toExportObject` 的面板分支是显式字段表）、分享审阅的元素表（`reviewshare.layout_elements` 按字段拉取）、前端诊断快照与后端诊断包（白名单拉取）都不带，各有用例钉住（`web/src/store/figureOriginPersist.test.tsx`、`tests/test_data_origin_no_leak.py`）；唯一的例外是用户在发送诊断包时自己勾选的「损坏会话」，那是原始排版文档，与 overrides、脚本路径同一暴露面。
11. **数据变没变，只看内容摘要**（不是大小、也不是素材的 `fresh`：后者与来源指纹都不覆盖输入数据，`runtimeasset.stale_status` 明写「数据依赖不追踪」）。后端两处小改：`propose` 的 lazy 响应多回 `data_sha256`（这次读的那份数据文件原始字节的 sha256，单份才有；合画没有）；新端点 `GET /api/ai/data-figure/data-digest?table=`（实验开关后面、会话认证、只读；路径经 `normalize_rel` + `contained_path`；文件不在 / 不是普通文件 / 出界 / 超 512 MiB / 读不了回 `{sha256: null}`，不是错误）。属性页那一行出现前去核一次（`figureOriginStore.checkData`）：摘要与来历里的一致才出；不一致、`null`、请求失败、**还没核对完**都不出，全程静默。换类型时 `propose` 回的 `data_sha256` 与来历里的不同、或旧后端没回，不提交、不换（同样大小改了内容也认得出）。

## 修订（2026-11-27）：多个 y 的「依据」按每条线数（`ai_data_propose.series_facts`、`web/src/lib/dataBasis.ts`）

**问题**：`candidate_facts` 只用 `recipe.y[0]` 数，前端却把它说成所有线共同的依据（「按 4 行绘制，每条线每行一个点」）。多个 y 各有各的缺失 / 误差缺失时，这句话对第二条线是错的（实例：`a` 4 个有效值、`b` 6 个，界面写 4）。出图本身没错（`ai_data_template.trend_data` 本来就逐个 y 各画各的），错的是对图的说明。

1. **统计粒度与来源**：新增 `ai_data_propose.series_facts(values, recipe)`，只对**多个 y 的趋势类、小表、非 stack** 给（`_multi_y_trend` 是它与旧 `candidate_facts` 的唯一分界；别的族的多列纵轴仍走旧 `facts`）。数据来自出图用的同一个数据准备函数——逐行各调一次 `tpl.trend_data`（行的身份就是它在表里的位置，不再按「行号」去重，合画时不同文件的同一行号不会撞），不另写解析规则；横轴按解析后的数值 / 日期去重（`1`、`1.0`、`1e0` 是同一个坐标）。
2. **响应合同**：这类候选**不再输出旧 `facts`**（旧前端最多不显示依据，不会显示第一条线的数）；改出 `series_facts: {rows, eligible_rows, rows_any, rows_all, skipped_missing, skipped_other, series: [{y, n, x_values, err_drawn?, err_missing?}]}`。`rows` = 整表行数；`eligible_rows` = 横轴 / 分组 / 分面 / 个体都有效的行；`rows_any` / `rows_all` = 至少一个 / 每个 y 都有效的行；`series` 与 `recipe.y` 同序，`n` = 该 y 的有效值个数，`x_values` = 有效值所在的不同横轴取值数；配了现成误差列才有 `err_drawn`（有中心值且误差可画，**0 也算**）与 `err_missing`（有中心值但误差空 / NaN / 文字 / 负数），两者相加 = `n`；有误差无中心值的不计（没有中心就没有误差线）。`skipped_*` 同 `facts`。单个 y、大表、stack 的响应、`facts`、脚本逐字不变；大表 / stack 不支持就省略，不伪造。
3. **前端句式**（封闭、走 i18n；整句仍「依据：…。」）：各系列数量相同且有效的行是同一批（`rows_all == rows_any`）=「2 个系列，各有 6 个数据点」；数量不同（至多 3 个系列）=「2 个系列，分别有 4、5 个数据点」；数量相同但不是同一批 =「2 个系列，各有 2 个数据点；有效数据覆盖位置不同」；超过 3 个系列且数量不同 = 范围「5 个系列，各有 116–120 个数据点」；其后是「误差线取自「a_sd」和「b_sd」列」（两列用「和」、三列及以上用顿号相连），有中心值缺误差时紧跟「，其中 N 处缺失」（各系列相加；没有误差列那半句时单独写「N 处缺少误差值」），再是「另有 N 行未计入」。措辞 2026-11-27 用户定：不说「有效值」（理工科里也指均方根值），说「数据点」；先说误差线取自哪几列、再说缺几处。判断是不是多个 y 看 `series_facts`、趋势画法（`line_points` / `line_band` / `line_units` / `line_err`）的 `values.y` 与 `error_columns` 三处，不只靠误差列；别的族的多列纵轴、大表与「把列当分组」既没有 `facts` 也没有 `series_facts` 时整行不显示。
4. **旧响应降级**：多个 y 却没有 `series_facts`（留存草稿）不写数字那半句，绝不拿 `facts` 顶替；「误差线取自哪几列」与没画的行那一句照旧；没有任何可写的就不显示整行。不加任何新控件（没有展开、明细表、tooltip）。
5. **顺带核实审计的几条**：①合画时 `candidate_facts` 的 `gone` / `row_at` 只按本地行号索引，A 文件第 3 行无效会把 B 文件第 3 行也排掉——**复现并修**（行身份改为「来自哪一份 + 行号」，抽成 `_skipped_split`）；②同一行多个 y 都是文字时 `unplotted_rows` 每个 y 记一条、被当成行数——**复现并修**（每行只记一条，与该函数注释「每行只记一个原因」一致；调用方 `app.py` 的 `count`、`ai_data_task` 的 `goal.rows_plotted`（只看 `unplotted_unmet` 是否非 0）与 `observed.total` 都把条数当行数，没有谁依赖逐格条目；`unplotted_unmet` 在那一行上仍是 1，用例锁住）；③某个 y 的文字把同行另一个 y 排除——**复现**（旧 `candidate_facts` 对 `a` 有 6 个有效值的表写 `drawn: 5`，因为同行 `b` 是文字、`gone` 排掉了整行），新统计按每个 y 各自判，用例锁住。
6. **没做**：不给分组 / 分面覆盖统计（`group_cells`、每组范围）；不给可展开的明细表；不改 `unplotted_rows` 里「某个 y 是文字就把这格记为没画」的口径（这是诊断合同，`goal.rows_plotted` 依赖它）；不动 `error_missing_for`（算出来的误差线）与指纹核验。

## 修订（2026-11-27）：表头第一格是空的也能读（`ai_data_figure.fill_header`）

**起因**：pandas `df.to_csv()` 默认把行索引写成第一列、表头留空（`,id,diet,pulse,…`），R `write.csv`、Excel 的 A1 也常是空的；此前读表只要表头里有一格空就整张报 `data_header_invalid`，用户直接卡住。

**做法**（最窄）：

1. **只放宽第一列**：表头第一格的名字是空（去空白后）且有两列以上时，叫 **`col1`**——与 `xvg_text` 缺名列同一个叫法（`col<N>`，N 从 1 数），ASCII、不带任何一种语言的词。别处的空名字、重复名字（含补名后与已有的 `col1` 撞名）**仍是 `data_header_invalid`**：这样「标题行 `实验一,,,`」之类不可能是表头的行不会被当成表头（猜表头行与 `header_rows` 重试靠这个码判断「这一行不是表头」，不放宽）；整行都没有名字的行读表时就是全空记录、被丢掉，剩不出表头。
2. **一份实现**：占位由运行时函数 `fill_header` 算，进 `RUNTIME_FUNCTIONS`（公共模块内嵌同一份源码）；`iter_table`（文本小表 / 大表流式 / 显式表头行，也是脚本里 `table_from_text` / `read_delimited` 的读法）、`read_xlsx`（脚本）与父进程的 `_summarize` / `_scan_summary`（XLSX 与老入口）都调它，所以父进程与脚本读出的是同一张表头；`fill_header` 幂等。
3. **公共模块换名**：公共模块按内容寻址（`tavotto_plot_<sha256 前 8 位>.py`），加了函数名字就变；老脚本继续 import 它们当时那一份，不受影响（它们读过的表本来就不含空表头）。没有写死的哈希 / 期望指纹要更新。
4. **没做**：不放宽重名；不放宽非第一列的空名字；不改猜表头行与 `unsure` 的判据（候选表头里第一格空的行仍是「猜的」→ `unsure`，只是现在能读出表）；不改界面文案；`chartdata`（预检读表）本来就把任何空名叫 `col<N>`，不动。

## 修订（2026-11-27）：行号列默认不当轴；大表默认备选全被拒时的退路（`ai_data_figure.row_index_columns`、`ai_data_recipe._resolve_roles_raw` / `_large_fallback_x`）

**问题**（24 份公开数据集、不连 AI 的 lazy 路径实测）：①Rdatasets 的表第一列是 `rownames`（1..n 的行号），10 份表的默认横轴都是它，画出来是「数值随行号变化」，没有科学意义；连 AI 时「读懂数据」会把它判成 `row_id` 并让规则避开，没连 AI 的用户没有这一层。②`nyc_flights_large.csv`（33.6 万行）默认答案是 `nothing / all_candidates_rejected`：规则按文字列分组出比较 / 分布，大表只放行 `LARGE_MARKS`（趋势的线 / 点、关系的点），五个候选全被 `mark_not_allowed`（`large:jitter` / `box` / `violin` / `ecdf` / `bar`）拒掉，能画的散点 / 折线因为没选横轴没生成。

1. **行号列判据（唯一实现 `ai_data_figure.row_index_columns`，大表扫描用同一个 `_SeqTrack`）**：**两条同时满足**。①列名规范化（小写、去掉空白 / `_` / `.` / `-` / `:`）后在小闭集 `_ROW_INDEX_NAMES`（`rownames` / `rowname` / `rowid` / `rownumber` / `rownum` / `rowno` / `row` / `index` / `idx` / `no` / `sn` / `#` / `序号` / `行号`），或是 pandas 的 `Unnamed: 0`（可带 `.1` 后缀）；②这一列每一行都是整数（没有缺格、文字、`nan`、小数），第一行是 0 或 1，之后逐行恰好 +1，至少 3 行。结果放 `DataSummary.row_index`，**不进 `to_dict`**（对外响应、历史库逐字不变）。**为什么两条都要**：单凭取值，`day` / `trial` 这样的 1..n 是真的横轴（「第几天」「第几次测量」），规则不能替用户断言它没有意义；单凭名字，`index` / `no` 也可能是别的量。判据是保守的：取值有一处不符（断号、重复、步长不是 1、缺格）就不认。
2. **与 `id_like` / `row_id` 的关系**：`id_like`（`_id_name`）是**个体编号线索**——编号可以重复（重复 = 同一对象的重复测量），会触发「同一对象重复测量吗」的提问，永不当 y / x / 分组。行号列每行一个、不可能是个体，所以**不进 `id_like`**，也不影响 `repeats`；`row_index` 排除已在 `id_like` 里的列（`id` / `subject_id` / `编号` 等照旧由 `id_like` 管，行为不变）。它对应「读懂数据」闭集里的 `row_id` 角色：模型判 `row_id` 时走 `ai_data_roles._NOT_AN_AXIS`（x 被否决）；没有模型时由这条规则给出同样的默认。
3. **怎么用**：`_resolve_roles_raw` 里行号列不进 y / x 的**默认**候选池（`nums` 去掉它，横轴候选 `xs` 随之没有它）。例外与误差列 / 样本量列同一口径：用户在 `picks` 里点名它（y / x）或话里点名它、或去掉后一个数值列都不剩（别让表画不了）时不去。`options` 里仍列着它（`options.y` 本来就含；`options.x` 与误差列同一处补回），用户在右栏手动选它照样画——与模型判 `row_id` 时的处理一致（只否决默认，不从下拉里拿走）。命中后没有别的横轴候选，就走没有横轴的那条路（比较 / 分布），不报错。
4. **大表退路（`_large_fallback_x`）**：`default_candidates` 末尾，一个备选都没有时，**同时满足**才走：大表（`values.large` 非空）；`picks` 里没有 `x`（用户 / 模型对横轴表过态，包括 `none`，一律尊重）；规则默认没有有序横轴、不是「把列当分组」；这次**每一条**被拒原因都是 `mark_not_allowed` 且 `detail` 以 `large` 开头（只要有一条是别的原因，就不是「大表限制」造成的空手，不动）。满足时挑一列当横轴：不是纵轴 / 分组 / 编号列 / 行号列 / 误差列 / 样本量列；纯日期列优先，否则纯数值列里整表最小值 < 最大值（排掉 `year` 全是 2013 这样的常数列）的最左一列；挑不出就不走。然后带着 `picks.x = 这一列` **重算一次 `default_candidates`**——重算走的是同一条流水（`validate_recipe` + `enforce_order`，`picks.x` 与用户点名同一条路，不绕过任何校验），所以出来的只会是大表放行的趋势 / 关系类（折线的 x 重复 / 没排序仍由缩减那一遍的 `line_duplicate_x_unconfirmed` 拒掉），`roles.x` 如实是实际用的列；重算仍没有备选（或先要问文字格）就返回原来的结果。小表一行不变。**没做**：大表的比较 / 分布流式画法（另一件大事）；退路挑的横轴只是「能画出一张图」的确定性选择，不代表科学上对——用户可以在右栏换。
5. **看护**：`tests/test_ai_data_row_index.py`（名字闭集、取值逐项反例、小表 = 大表、默认角色与 `options`、点名仍可选）、`tests/test_ai_data_large_fallback.py`（触发条件逐条、配方仍过校验、用户表态 / 非大表原因 / 行号不当退路轴时不走）；`tests/fixtures/data_figure_cases/*.json` 与 `tests/support/data_figure_recommend.py` 不动（`--suite recommend` 前后逐字相同；`--suite roles --arm rules` 里 RC-12 `idx` 流水号一条由错变对，没有一条由对变错）。

## 修订（2026-11-27）：默认横轴 / 纵轴看取值结构——「像条件 / 顺序」还是「像测量」（`ai_data_figure.design_kind`、`ai_data_recipe._resolve_roles_raw`）

**问题**：没有模型时规则按**位置**猜：「唯一的横轴候选就是横轴」「纵轴取最后一个数值列」，不看一列像「实验条件 / 时间」还是「测量结果」。24 份表里的实例：ToothGrowth 出 `x=len, y=dose`，ChickWeight 出 `x=weight, y=Diet`，airquality 的 `y=Day`；上一条修订去掉行号后，ToothGrowth / anorexia 还因为「只剩一个横轴候选」被改成折线。

1. **线索（唯一实现 `adf.design_kind`，结果放 `DataSummary.design`：列名 → `block` / `monotone_levels` / `levels`，没有的列就是「像测量」；不进 `to_dict`）**。**只看一列数值按行序的取值结构，不看列名、不看数据集**。只对「纯数值、不是 `id_like` / 行号列」的列算（缺格 / `nan` 不参与，缺超过 `DESIGN_MAX_MISSING` = 20% 的列不判）；只算小表 v2 的行数据，大表 / 合画 / 转置（`stack_view`）/ v1 是空 = 保持现有行为。
   * `block`（个体 × 时间：块内递增、块间重置）：按「下降处」把整列切成块（所以块内按行不减，允许同一个取值重复几行——每个时间点有几个重复测量）；至少 2 块；≥ `DESIGN_BLOCK_MIN_LEN` = 3 行的块里的行占整列 ≥ `DESIGN_BLOCK_LONG_SHARE` = 0.9；每个够长的块 ≥ `DESIGN_BLOCK_MIN_DISTINCT` = 3 个不同取值；够长的块里**同一个起点值**的块占 ≥ `DESIGN_BLOCK_SAME_START` = 0.8（每个个体都从时间 0 起；一个测量值随时间增长，各个体起点各不相同——这条把 ChickWeight 的 `weight` 与 `Time` 分开）；整列不同取值个数 < 行数。
   * `levels`：不同取值 2–`DESIGN_MAX_LEVELS` = 50 个，平均每个取值至少对应 `DESIGN_LEVEL_AVG_REPEATS` = 4 行，每个取值至少出现 `DESIGN_LEVEL_MIN_REPEATS` = 2 次（几个剂量 / 时间点各做若干次）。
   * `monotone_levels`：满足 `levels`，且整列按行不减、至少 3 个取值（按组排好序的编号 / 剂量，如 ChickWeight 的 `Diet`、`temp_C` 4 / 25 / 37 各 5 行）。
   * **没有「整列单调就是条件」这一条**。第一版有，复核后撤掉：递增的测量值（累积量、按产率排过序的读数）与真正的递增横轴、递减的真横轴（红外波数从大到小）与随剂量下降的响应，单列上结构完全相同，分不开——按「线索打架或不明确时保持原行为」不判。
   日期列（`kind == date`）本来就是条件 / 顺序，不进 `design`。整数编码的类别列复用已有的 `DataSummary.numcat`，没有另算一套。
2. **打架时不用**（`_effective_design`）：`block` 有两列及以上（几列都是组内递增、组间重置的斜坡，如一组重复列 `rep1 / rep2 / rep3`）时谁是时间分不开，`block` 一律不用，回到原来的位置规则。
3. **默认横轴**（`picks.x` 没有、话里也没点名时）：只从日期和 design-like 列里挑，偏好次序 日期 > `block` > `monotone_levels` > `levels`，同级按最左（宽表的第一列不再有「先」的待遇，只是候选之一；候选里没有 design-like 的宽表，如 `VADeaths` / `iris` 这类，行为与以前逐字相同）。**例外**：二分类编码列（`numcat` 里恰两个取值、无序的 `levels`：敲除 0 / 1、group 1 / 2）不当默认横轴；三个以上水平（温度 4 / 25 / 37）当横轴当分组都说得通，保持原行为。**唯一的横轴候选**：像条件 / 顺序 → 横轴；二分类编码 → 没有横轴；**去掉行号列之后才只剩它**、像测量、而表里有分组列 → 没有横轴（去掉行号之前本来有歧义、没有横轴，走比较 / 分布；anorexia 的 `Prewt` 属于这种）；一开始就只有它的表（温度对产率、按处理分组）与其它情况照旧。候选里没有一个像条件 / 顺序时，行为照旧。
4. **默认纵轴**：从不在 `design` 里的列挑，仍是既有的次序规则（`unsure` = 最后一个，唯一一个就是它不再问）；一个都没有就回到原来的候选池。宽表规则（第一列当横轴、其余都是系列）只在别的列是 `block`（个体 × 时间）时让位。**不再另外把 `numcat` 列排出默认纵轴**（第一版这样做，只有 4 档的整数测量值因此被排掉、原来对现在错）：整数少档列只有同时是 `levels` 结构（每个水平连着出现好几行）才被排出。
5. **没动的**：`picks`（用户 / 模型 / 话里点名）一律优先，上面的默认只在没人点名时生效；`options`（下拉、问题的选项）不变，被默认排除的列仍然可选；规则仍**从不**自己把数值列当分组（那是模型 + `numcat_pick_ok` 的事）。
6. **已知局限**：整列单调的测量值被当成「像测量」（所以它不会被误当横轴）的同时，**真正的单调横轴（一个时间序列、按顺序记录的 `time`）也得不到加分**——有两个以上数值列、没有 `block` / `levels` 线索时回到位置规则；起点各不相同的个体时间不是 `block`；横轴选对以后纵轴仍是「像测量里最后一个」，多个测量值时哪个是因变量只能问；ryegrass 一类 `conc` 递增排序的表仍可能出 `x=rootl, y=conc`（与第一版修订之前的行为相同）。
7. **看护**：`tests/test_ai_data_design_measure.py`（`design_kind` 逐种正反例、随机测量值与「起点各不相同的增长」不被认成条件、摘要口径、大表为空、默认角色、点名不被推翻、`options` 不变、`block` 打架不用、行号去掉后的唯一候选）。度量见该修订的汇报：`--suite roles --arm rules --cases all` 默认 x/y/分组全对 46% → 58%（第一步后 48%）、规则本来对的 24 → 30 条，由错变对 RC-08 / RC-09 / RC-12 / HO-13 / HO-14 / HO-15，没有一条由对变错；「规则 + 录好的模型答案」三种回放（good / trap / mixed）每条用例的模型臂结果与 origin/main 相同或更好。

**补充（同日，记分表发现）：重复号列默认不当轴**。重复号（每个处理里各 1..k）的取值结构与「每个个体各自的时间点」一模一样（`block`），只看取值分不开——实测一张「品种 / 重复 / 株高 / 产量」的表被画成「产量随重复号变化」，而 main 上它是画对的。所以这里**用列名**，但只做减法：列名规范化后在闭集 `_REPLICATE_NAMES`（`rep` / `reps` / `repl` / `replicate(s)` / `replication` / `重复` / `重复号` / `平行` / `平行样`，逐字相等，不做子串匹配）里的数值列，① 不参与「像条件 / 顺序」的线索（也不让真正的时间列因为「两列 `block`」而失去资格），② 与行号列同样默认不当 x / y（点名了、或去掉后一个数值列都不剩时不去；`options` 里照旧可选）。不凭这个名字**认**任何角色（不当分组、不当个体编号）。`Run` / `trial` / `batch` 不在闭集里：它们常常就是要比较的条件。
同一条也管「每组各自从头数的行号」（未见集实测：`crabs` 的 `index` 在每个物种 × 性别里各 1..50，被当成了横轴）：列名像行号（`adf._row_index_name`）**并且**取值结构是 `block` 的列同样处理（`ai_data_recipe._counter_col`）；整表从头数到尾的仍由行号列判据管，只凭名字不认。

## 修订（2026-11-27）：读脏表之一——表头末尾没有名字、整列都是空的列丢掉（`ai_data_figure.nameless_tail` / `cut_tail`）

**起因**：Excel / WPS 另存 CSV 常在每行末尾多出几个空格子（表头 `depth_m,temp_C,salinity,,`，数据行 `0,18.16,34.000,,`），此前整张表报 `data_header_invalid`，用户直接卡住。

**做法**（最窄）：

1. **判据**：表头末尾**连续**名字为空（去空白后）的列，且这些列在**每一行**里都是空格子 → 丢掉这几列。只丢尾部（至少留一列）；中间的空名字（`a,,c`）、名字空但有数据的尾部列仍是 `data_header_invalid`——前者像标题行、后者是列名缺失，都不替用户猜、不替用户丢数据。数据行的宽度不是表头原来的宽度时同样按 `data_header_invalid`（有空名字的表头，表头判定先于行宽判定，与此前的先后一致，不变成 `data_ragged`）。
2. **一份实现、各条读表路径同一判据**：运行时函数 `nameless_tail`（数几列）与 `cut_tail`（逐行裁剪并核对）、`_trim_tail`（表头 + 行迭代器）、异常类 `NamelessTailData`（运行时类）。`iter_table`（小表 / 大表流式 / 显式表头行，也是脚本里 `table_from_text` / `read_delimited` / `reduce_file` 的读法）、`_scan_summary`（老读法的大表）、`_summarize`（老读法的小表与 XLSX，**只在 v2**，v1 逐字不变）与脚本的 `read_xlsx` 都调它。流式读看不到整列，所以在逐行核对里发现尾部列有数据时抛 `NamelessTailData`，父进程换成 `data_header_invalid`（小表在 `_read_small_text_table`、大表在 `_scan_summary`，同一个码、同样走「猜的表头 → `unsure`」那条分支）；脚本里它不会出现（读的是父进程已经读过、核过 sha256 的同一个文件）。
3. **公共模块换名**：加了运行时函数，`tavotto_plot_<sha8>.py` 的名字换成新的；`PINNED_RUNTIME_NAME` 追加一行；老脚本继续 import 它们当时那一份。
4. **没做**：XLSX 不用另处理（`xlsxread.read` 只补齐到最右一个非空格，不会读出整列为空的尾部列）；不丢中间的空名字列；不改猜表头行与 `unsure` 的判据；不改界面文案。

## 修订（2026-11-27）：读脏表之二——XLSX 开头有标题行也能读（`ai_data_figure.xlsx_header_guess`）

**起因**：酶标仪等导出的 XLSX，第 1 行只有 A1 有一句标题（`酶标仪导出 2026-11-20 OD600`）、第 2 行空、第 3 行才是列名，此前整张表报 `data_header_invalid`。文本表早有「猜表头行」（`_detect` → `header_rows` / `unsure`），XLSX 没走到：`inspect_data_source` 对 XLSX 直接 `_summarize_book`，把第一条非空行当表头，根本不进 `_detect`，也不认 `header_row`。

**做法**：

1. **同一套判据，不另写一套**：把 `_detect` 里「表头候选」那一段原样抽成 `_header_end`（第一条有数值的记录之前是候选）与 `_pick_header`（候选里挑第一条像名字的、单位行例外），文本表与 XLSX 共用。XLSX 的每一行已经是切好的格子，所以只喂「工作表行号 + 格子」给 `_block` / `_numeric_columns` / `_header_end` / `_pick_header`，没有分隔符那一层。文本表的结果逐字不变（既有用例全过）。
2. **只在原读法读不成时才猜**：XLSX 没有「猜的 → 让用户点一行」的界面（`header_rows` 的点行只给文本表），所以不拿猜测改写现在读得对的表：v2 读 XLSX 时，第一条非空行读不成表头（`data_header_invalid`，与文本表「猜的表头读不出合格的表」同一个信号）才调 `xlsx_header_guess`；猜不出（没有成块的数据 / 没有候选 / 猜的还是第一行 / 候选里有单位行）或猜的行也读不成，仍报原来的错。读得对的表（常态）一个字节都不变。
3. **行号的往返**：`inspect_data_source(…, header_row=N)` 现在对 XLSX 生效（工作表里的行号，与文本表同一口径）：跳过 N 之前的行；N 落在空行上读它之后的第一条非空行；超出末尾 `data_header_row_invalid`。**真的跳过了行**才把真正的表头行号记进摘要（`header_row` = `header_line`），常态两者仍是 None；出图 / 合画 / 提交那几处把 `header_line` 当 `header_row` 传回来重读，读出同一张表。
4. **脚本同源**：运行时函数 `read_xlsx(path, sheet, header_row=None)` 多一个可选参数，按 `xlsxread` 的行号跳过标题行；模板只在摘要记了行号时才把它写进 `read_xlsx(DATA, SHEET, 3)`（`_xlsx_reader_expr`，含合画每一份），其余脚本逐字不变。公共模块换名，`PINNED_RUNTIME_NAME` 追加一行。
5. **没做**：XLSX 不切单位行（候选里有单位行就不猜）；不给 XLSX 做「没有表头」（第 3 项的纯数字表只管文本表）；不改界面、不加点行的入口；v1 的 XLSX 读法逐字不变（标题行在前仍是 `data_header_invalid`）。

## 修订（2026-11-27）：读脏表之三——第一行整行都是数值 = 没有表头（`iter_table(no_header=)`）

**起因**：仪器导出的两列纯数字、空白分隔的文件（XRD 扫描 `10.00  116`）没有表头行，此前把第一行数据当成列名（`10.00`、`116`），第一个数据点丢了、图的轴标签是两个数。

**做法**：

1. **判据**（`_headerless`，只在 `_detect` 里「没有表头候选、也没有块外的表头行」的最后一步用）：块的第一条记录**每一格都是数值**（`parse_cell` 判 `numeric`；空格、NaN、文字都不算），且紧随其后的记录（最多 50 条非 `#` 行、至少 1 条）里每个非空格子也是数值（数或 NaN）。只看整行：表头本来就是数字、第一列却是文字的宽表（`sample,2019,2020`）第一格是文字，不算——有反例用例。
2. **读法**：嗅探结果 `TextLayout.no_header`（`header_line` 是第一条数据行的物理行号，仍标「猜的」→ `unsure`，与此前没有表头的兜底同一个把握）；读表时列名叫 `col1`、`col2`…（与 `xvg_text` / `fill_header` 同一个叫法），第一条记录算数据，物理行号照旧。`DataSummary.no_header` 记下它。
3. **一份实现、各路同一判据**：运行时函数 `iter_table` / `table_from_text` / `read_delimited` / `reduce_file` / `read_reduced` 多一个可选参数 `no_header`（默认 False）；小表（`_read_small_text_table`）、大表（`_read_large_text_table` → `_scan_summary`）、脚本（`_reader_expr` / `_large_reader_expr`，只在摘要里 `no_header` 为真时才多写 `True` / `no_header=True`，其余脚本逐字不变）、大表缩减对账（`large_reduce`）都经它。公共模块换名，`PINNED_RUNTIME_NAME` 追加一行。
4. **往返**：摘要的 `header_line` 被出图 / 合画 / 提交当 `header_row` 传回来重读时，只要它就是嗅探认出的那一行，仍按「没有表头」读（与单位行同一个先例）；读出同一张表。**用户另指一行**（比如点第 2 行）就按他指的读，那一行当列名。代价：用户明确点「第 1 行是表头」与往返传回的第 1 行分不开，后者赢——对这类整行都是数值的表，第 1 行当列名本来就不是想要的读法。
5. **分不开的情形（已知）**：数字表头压在整张纯数字的表上（`2019 2020 2021` 下面全是数）按没有表头读，第一行成了数据；嗅探仍标「猜的」，用户在预览里看得到。
6. **没做**：不给 XLSX 做没有表头（XLSX 的第 1 行读不成表头时只找标题行，见上一节）；不改界面文案（`header_row` 在响应里仍是第一条数据行的行号）；`xvg_text` 没有名字、不写表头行时「交给既有的没有表头流程」现在读成 `col1…`，不再把第一行数据当列名（`test_ai_data_xvg` 里对应一条断言随之改了）。

## 修订（2026-11-27）：读脏表之四——更多日期写法（`ai_data_figure.parse_when`）

**起因**：`2026年3月1日`、`2026/3/1`、`2026-03-01 08:00` 这几种常见写法此前都不是日期（只认 `YYYY-MM-DD`），整列成了文字，趋势图画成了直方图。

**做法**：

1. **认哪些**：年份在最前、读法不会分不清日月的三种——ISO `YYYY-MM-DD`，可带时刻（`T` 或一个空格分隔，`HH:MM` 或 `HH:MM:SS`，秒可省）；`YYYY/M/D`（月、日可以只写一位）；`YYYY年M月D日`。**不认**：`03/04/2026`、`3/4/26`、`04-03-2026` 这类分不清日月的（不猜）；时区后缀（`Z`、`+08:00`）；不存在的日子或时刻（`2026-02-30`、`25:00`）；横杠写法只写一位的 `2026-3-1`（与此前一致）。一列里混了认得的和不认得的，整列仍是文字（`dates < text_count`，走「混了文字」）。
2. **现有表示怎么装时刻**：日期在横轴上本来就是浮点数（自 1970-01-01 起的天数，`date_number`）。带时刻的折成小数天（`+ 秒数 / 86400`），所以**同一天不同时刻是不同的横坐标，不会被当成同一个 x 的重复测量去取均值**；只有日期的列与此前逐字相同。matplotlib 的日期轴按数值直接画，不需要改脚本模板的轴设置。`ai_data_propose._x_text`（没画误差线的位置的标签）遇到带时刻的值写 `2026-03-01 08:00`（有秒才写秒），只有日期的仍写 ISO 日期。
3. **v1 不变**：`parse_date`（只认 ISO 日期）与 v1 的摘要、脚本逐字不变；新增运行时函数 `parse_when`（日期 → `date`，带时刻 → `datetime`），v2 的所有读取点（`_summarize` / `_scan_summary` 的日期计数、模板里 `trend_data` / `relationship_data` 的 x、`ai_data_propose` 的横轴核对、`ai_data_roles`、`ai_data_recipe`、合画的标签判断）都改用它；`date_number` 认 `datetime`。`chartdata`（预检读表）只拿它判「日期样的格子要转换」这条诊断，不动。公共模块换名，`PINNED_RUNTIME_NAME` 追加一行。
4. **没做**：不认时区、不认只有时刻没有日期的写法；不改横轴的刻度格式（交给 matplotlib 自动选）；不把日期列的时刻拆成两列。

## 修订（2026-11-27）：读脏表之五——整列带 % 的数值、小数逗号（`ai_data_figure.tag_columns` / `NumberCell`）

**起因**：`52.3%` 这样的百分数列、欧式 CSV（分号分隔、`1,5` 当 1.5）的数值列此前都是文字，整张表「没有数值列」、什么图也出不来。`parse_cell` 已认千分位（`1,000`），但单格看不出 `1,5` 是小数还是别的，`52.3%` 也不该在混了普通数的列里被悄悄当成数。

**做法**：判据是**列级**的，`parse_cell` 是格级的，所以拆成两步（大表流式读看不到整列，另有一步预扫，见第 3 点）：

1. **整列判定**（运行时类 `ColumnTagger`：逐行 `feed`、最后 `tags()` 给出 `{列下标: 真 = 百分号 / 假 = 小数逗号}`；`tag_columns(header, rows, delimiter)` 是它的整份版，`table_from_text` 的最后一步，老读法的小表走同一个函数）：
   * **百分号**：整列每个非空格都是 `数字%`（允许正负号、小数、`%` 前的空白）→ 数值是 `%` 前面的数，**不除以 100**。有一格不带 `%`、或写成别的 → 整列照旧。
   * **小数逗号**：**只在分隔符是分号 / 制表符**时；整列每个非空格都是 `数字` 或 `数字,数字`，至少一格带逗号，且**没有任何一格长得像千分位**（`1,234`：首组 1–3 位不以 0 开头、其后每组恰好 3 位）→ 逗号是小数点。有一格像千分位就分不清是 1.234 还是 1234，整列照旧（分号文件里仍是文字，不猜；制表符文件里仍按千分位读）。逗号分隔、空白分隔的文件里绝不这样读。
   符合的整列每格换成 `NumberCell`（`str` 子类：原文 + 数值；`strip` 返回自己，标记不丢）。原文照旧，摘要里看到的仍是 `52.3%` / `1,5`。
2. **格级**：`parse_cell` 见到 `NumberCell` 直接给 `numeric`；裸的 `52.3%` / `1,5` 仍是文字（所以别处——`chartdata`、混列的诊断——对单格的判断不变）。`NumberCell` 与 `tag_columns` 进 `RUNTIME_FUNCTIONS`：父进程读表与生成脚本里的 `table_from_text` 是同一份源码，读出同一张表；公共模块换名，`PINNED_RUNTIME_NAME` 追加一行。
3. **大表（流式）读出同一张表**：已有的看护（`test_ai_data_large::test_the_streaming_reader_agrees_with_the_whole_file_reader` 的分号小数逗号用例）要求小表整份读与大表流式读结果一致，所以大表不能「不做」：`_read_large_text_table` 在扫描之前先预扫（`_large_tags`）——读开头 `TAG_HEAD_ROWS`（2000）行喂同一个 `ColumnTagger`，**没有任何一列还可能符合就不再读第二遍**（绝大多数大表没有带 `%` / 小数逗号的列，不多花一遍）；还有候选列才整份再读一遍（流式、可取消，进度记在 `scan` 阶段）。判定结果 `tags` 记进摘要（`DataSummary.col_tags`），扫描（`_scan_summary`）、缩减（`reduce_file` / `read_reduced`，父进程 `large_reduce` 与脚本是同一份源码）、预览行都经 `iter_table(…, tags=)` 逐行 `apply_tags`；脚本只在 `col_tags` 非空时才多写 `tags={…}` 字面量（`_large_reader_expr`），其余脚本逐字不变。两条路用的是同一个 `ColumnTagger`，所以判定相同；开头筛选只会更早排除、不会放进整份读会排除的列（有反例用例：开头全带 `%`、最后一行不带）。
4. **没做**：
   * XLSX 不做（Excel 里的百分数本来是数值格）；`chartdata` 的预检读表不动。
   * 不做「列里夹着别的写法的容错」（`52.3%` 与 `0.18` 混列、小数逗号与千分位混列）——这类列本来就是混列，仍走「数值列混了文字」问用户。
   * 不改 `_numlike`（嗅探分隔符与表头用的「像数」）：百分数不参与；`_numlike` 早就把欧式小数算作像数，所以分号文件的分隔符判断不变。
   * 老读法（`_detect` 嗅探不出读法的 `.csv` / `.tsv`）的大表读法没有整列判定（小表有，脚本与父进程同一个）：这类文件本来就是嗅探不出表的畸形文件，大表与小表在这里仍可能不同。
   * 不是读表的问题：全是百分数的单列配一列名字（一行一个成分）这种表，读对之后推荐的是分布图（`ecdf`）而不是按成分的柱 / 点——那是推荐规则（`_one_row_per_cell` 一族）的事，本次不动。
