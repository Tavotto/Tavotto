# ADR 0143：生成可编辑版本——位图库出的图，由改图助手另写一份 Matplotlib 新脚本

日期：2026-10-21 · 状态：**Accepted**（1.0 登记例外，见 `docs/1.0-release-readiness.md` §1.8）
相关：[0138 TAF](0138-tavotto-ai-foundation.md)、[0139 来源驱动采用（对照确认）](0139-source-backed-figure-adoption.md)（含 2026-10-21
修订：`no_figure_hint.library` / `path`）、[0132 助手提交幂等](0132-ai-submit-idempotency-and-draft-cache.md)。
起因：脚本用 Pillow / OpenCV / plotly / ROOT 画布直接画图时，Tavotto 捕获不到 Matplotlib 图，图只能当位图摆放（原因卡 D，#1271）。

## 决定

### 一、任务：`convert_to_editable`，登记在 TAF 任务适配器注册表

不另起 AI 通道：沿用 `ai_bridge.run` 的会话 / 历史 / 取消 / 幂等 / SSE，`run(..., convert={"library", "original_rel"})` 走
`ai_convert.ADAPTER`（`ai_tasks.ADAPTERS["convert_to_editable"]`）。契约：`allowed_writes` 恰好一个**候选目录里的**新文件名
`<原名>_matplotlib.py`，`new_figures=true`，预算 Agent ≤ 1 + 修正 ≤ 1、校验运行 ≤ 3。提示词在 `ai_prompt_pack` 单独一节
（不进 figure_edit 的 `V1` 模板闭集，Harness Lab 的候选 Pack 契约不变）：保留原脚本的数据读取与计算（ROOT / RooFit 拟合仍用 ROOT，
只把绘图改成 Matplotlib；Pillow 热图用 imshow / pcolormesh 复现色标、格子标注、标题与脚注），图幅 / 字号对齐原图，不运行脚本。

### 二、权限边界

1. **绝不修改用户的任何脚本**。原脚本字节在整条流程里不变（`tests/test_ai_convert.py::test_original_script_bytes_never_change_through_the_whole_flow`）；
   转换会话的回滚入口被拒（`ai_revert_not_applicable`，回滚会重写用户脚本）。
2. **Agent 只写候选工作区里的一个新文件**。候选 `data_dir()/cache/ai_convert/<sid>/` **复制**原脚本、≤ 8 MB 的原输出图与下述有界只读依赖快照，
   不建链接；提交前检查：除这一个新文件外候选树逐字节不变（含原脚本副本）、原项目指纹不变、项目里原脚本仍是起跑那一版。
   CLI 把写入挡在 cwd 之外是 CLI 的行为，指纹只能发现、不能阻止（同 `figure_edit`，R-03）。
   **完整性闸门先于依赖等待（Codex #1330 P1）**：校验运行前的依赖调查期间项目文件被改（`project.untouched` / `project.target` 失败）→ 不进入依赖等待、
   不留存候选，结局同不缺依赖时的完整性失败。续跑的项目指纹基线是**起跑时**存进 `meta.json` 的那份（`project_before`），不用此刻的项目重建；
   停住期间项目或原脚本被改、或 meta 里没有基线 → 续跑失败（`convert_project_changed` / `convert_source_changed`）并删掉候选脚本与预览，不可采用。
3. **提交不碰用户文件**：任务的 `commit` 只把通过检查的候选留存待采用（候选目录 + `meta.json`，24 h 过期清理）。用户点「采用」才由后端
   `ai_convert.adopt` 把候选**新建**进项目——新原语 `atomicio.create_new_bytes`（同目录临时文件写满 fsync → `os.link` 到目标，已存在由内核
   原子拒绝；不支持硬链接的文件系统退到 `O_CREAT|O_EXCL`）经 `scriptlock.create_script`（必须持目标的 `script_guard`）。存在即失败、
   不覆盖（含悬空符号链接），重名自动加序号 `_matplotlib_2.py` … 至 99。采用前再核：原脚本 sha256 仍是起跑基线（`convert_source_changed`）、
   候选字节仍是留存时的摘要（`convert_candidate_changed`）、目标路径经 `contained_path` 钉在项目内。
4. **校验**：用户同一解释器 / 同一路由下真的跑一遍候选（`run_validation` → app 注入的 `runner` 走 `engine_probe.probe`，带原脚本的默认运行配置，
   返回前 `invalidate(force=True)` 退役 worker）。必须捕获到 Matplotlib 图，否则判失败并给一句原因（`no_matplotlib_figure` / `run_failed`，
   一轮有界修正）。候选以紧挨原脚本的**临时文件** `tavotto_convert_probe_<sid>.py`（后端 O_EXCL 建、跑完即删；worker 按模块名 import，
   不能用点开头的隐藏名）存在几秒，使相对路径数据读取与 cwd 判据与原脚本一致——Agent 没有写它的路径。存在期里文件监视器可能看见它，
   删除后登记不变。**校验运行的写入是隔离的（Codex #1277 P1）**：运行带 ADR 0141 的写入根绊线（`probe(write_roots=)` →
   `pool.build` / `invalidate` / `force_cancel` 同一份根，池键多一段），根只有候选目录——候选把相对路径输出（`out.png` / `data.csv`）写进
   项目目录时在写发生的那一刻以 `write_outside_roots` 失败，用户文件不被打开写；跑完的指纹复核只是兜底。绊线的盲区同 ADR 0141
   （C 层写、子进程、位于系统临时目录 / 用户缓存目录之下的项目）。
   内置执行器可用 ADR 0147 的 `check(final=true, summary=...)` 在同次真实校验通过后结束模型会话，省去纯收尾的下一次请求；
   这不是采用或父进程验证的替代品。Task Core 仍独立校验当前候选，失败仍不可采用，用户仍须对照确认。
   转换校验只执行共享发现逻辑选出的首个入口，与采用后重开的入口一致。主入口报错或未出图时，不能换到
   辅助绘图函数取得部分图后宣告整份候选成功；真实失败交回既有有界修正流程。普通素材探测的多入口能力不变。
5. 对照数据（`comparison`）：原图像素宽高 / 字节 / 摘要（PNG / JPEG / GIF 头部）、新版图幅 mm 与图个数、宽高比相对差、新版 SVG 预览
   （沙盒 CSP + nosniff 的 `GET /api/ai/convert/<sid>/preview.svg`，只给 `<img>`）。**不做像素差**（`pixel_diff: null`）：
   原件是位图而新版是矢量重画，逐像素差没有可辩护的含义，交给用户并排看。

### 项目资源上下文修订（2026-10-25）

外部语料复现了“原项目有字体 / helper，但模型只见入口脚本，因而替换字体或放弃转写”。
`engine/convertcontext.py` 在不运行源码的前提下提名依赖：Python 本地 import 复用 `importscan`，
文字中的文件引用及 MATLAB 同目录 helper 调用只作提名，不宣称完成运行时依赖解析。按该模块的文件数、
单文件/总字节与递归预算提供普通文件，保留项目相对路径；敏感路径拒绝，文本视图用 `fixtools.readscope` 脱敏，
不进入 Python 环境，不跟随链接 / 重解析点，不触发云端占位文件下载。打开时及读后核文件身份，
防检查后换成链接或特殊文件。未提供与超限项写进 `context.json`，覆盖度明确为 `static_partial`。

`project_context/` 与清单只读，内置工具沿既有路径判据浏览子目录；create/edit 仍只许写顶层一个新候选。
来源与视图分别记完整 SHA256，脱敏项标 `redacted`；执行仍读取原项目资源，脱敏文本不能当成原始数据或直接采用。
check、留存、续跑、采用核同一份起跑基线，资源变化即拒绝，不能用 size/mtime
或续跑时的新基线替代。已有无 context 的持久候选仍按旧契约处理。生成代码沿用原项目位置，不能依赖本次缓存；
采用后清掉候选目录、从其他 cwd 新起进程仍须能读取原 helper / 数据。看护：`tests/test_convert_context.py`。

数据修复后的转换还须获知运行时读取语义：`input_read_policy` 只说明项目是否配置了用户确认的只读映射，
不暴露目标路径或复制外部文件。映射仅接管缺失的只读打开，`exists/is_file/glob` 仍返回真实文件系统事实；
模型必须保留原始输入路径串与读取方式，不能因快照缺项新增跳过绘图的分支或把相对输入改成绝对路径。
必需输入读取失败交回校验循环，不能宣告部分输出为完整转换。清单记录同一 `inputremap.fingerprint`，
检查、续跑、留存和采用前复核，映射改变即 `convert_context_changed`；旧清单无此字段时兼容既有契约。

用户可在缺少输入的原因卡中填写项目内参考文件（`reference_files`），点击「带参考文件重试」后才创建新会话。
Python、R、MATLAB 共用这条路径；无效路径返回 `convert_reference_invalid`，保留输入框供更正，不发模型请求。
最多 16 个文件；显式参考优先占用既有快照预算，并沿用同一脱敏、文件身份、完整摘要和采用前复查。
明确选择的文件若不可读或超预算，整次请求失败，不能默默丢掉后继续生成。项目切换不带走未发送输入。

共享转换提示允许读取原字体度量，并先注册项目字体再按真实族名绘制，保留逐字形回退；强调 Pillow anchor、
独立输出/页面、统计定义与分类顺序。依据：[Pillow anchors](https://pillow.readthedocs.io/en/stable/handbook/text-anchors.html)、
[Matplotlib addfont](https://matplotlib.org/stable/api/font_manager_api.html)。提示词和成功出图都不是科学保真证明；
原生参照与数值/几何复测另行记录，不改变 `side_by_side` / `unknown` 的含义。默认执行器路由不变。

### 三、默认开与逃生口

用户 2026-10-21 裁决：全部默认开，不是 beta-only，不挂默认关的实验开关（与 TAF 的 `ai.taf.enabled` 不同，不看它）。保留一个总开关作为逃生口：
`ai.convert.enabled=false`（只认显式 `false`）或环境变量 `TAVOTTO_NO_CONVERT_TO_EDITABLE=1`，关掉后 `POST /api/ai/convert` 回 404
`convert_disabled`，界面显示一句话（不再起任何会话）；原因卡与次按钮不受影响。

### 四、端点

`POST /api/ai/convert`（`{script, library, path?, reference_files?, agent?, client_request_id?}`；`library` 取 `rasterhint.LIBRARIES`；`script` / `path` 经
`contained_path`）、`GET /api/ai/convert/<sid>`、`GET /api/ai/convert/<sid>/preview.svg`、`POST /api/ai/convert/<sid>/adopt`、
`POST /api/ai/convert/<sid>/discard`。全部过会话认证，候选按**项目**隔离（别的项目的 sid 一律 `convert_not_found`）。

### 五、遥测

本 ADR **不加新遥测事件**（新事件须攒齐一次升 `CONSENT_VERSION`）。后续候选：`convert_started` / `convert_outcome`（闭集：library、
outcome、corrections 个数、是否采用）——不带脚本、路径或图内文字。

## 修订：R / MATLAB 来源（2026-10-23）

起因：论文图常由 R（ggplot2）或 MATLAB 画，用户手里只有脚本与（或没有）导出的图。维护者四条裁决：

1. **范围**：第一版只收 `.R` / `.r` / `.m` 纯文本脚本，不含 `.Rmd` / `.qmd` / `.mlx` / `.fig` / `.rds`。`.m` 是 MATLAB 与 Objective-C 共用的后缀，
   所以来源语言由 `engine/sourcelang.detect(path, text)` 看内容定（Obj-C 特征 `#import` / `@interface` / `@implementation` / `@end` 等一票否决；MATLAB 特征
   `%` 注释、`function`、独占一行的 `end`、绘图 / 读数据调用等至少两类），≤ 1 MB、UTF-8；**判不出就拒绝，不猜**。
2. **不运行**用户的 R / MATLAB：对照只用脚本旁已有的原图（沿用本 ADR 的 `path` / `original_rel`；请求没给时取脚本旁同主干名的位图）。没有原图时
   `comparison` 带 `reference: null`、`verification: "unknown"`（有原图为 `side_by_side`，只是并排给人看，不是判定），界面一句话说明。
3. **没有逐一对应的统计方法时停下**：Agent 首行写 `TAVOTTO_NEEDS_INPUT: <原因>` 并且不写新文件 → 任务结局 `needs_input`（`ai_tasks.NeedsInput`，在 Agent 运行结束后判，
   与 `Plan.needs_input` 的“执行前”不同），不生成新脚本。标记只在“没有新文件”时算数、只认 Agent 的完整回答、行首；原因清洗 + 限长 8,192 字（2026-10-26：容纳完整导出指引，超限仍不截断命令），只随结局给界面，不进日志 / 遥测。
   没有标记且没写文件仍是原来的 `target_missing` 失败。
4. **新脚本缺的 Python 包自动装进用户环境**（用户改了默认值）：不新写 pip 路径，缺什么由 `deprepair.joint_plan_for`（`importscan` 静态扫 + `depplan`）算，装由
   `create_joint_plan` + `prepare`。装好的包进结果 `installed`（界面一句“已为这个项目装好 pandas、statsmodels”）；装失败是不可修正的失败（`dependency_install_failed`）。

### 修订二（维护者 2026-10-23 第二次裁决）：大名单 + 每个环境问一次 + 真卡片 + 取消

替换上面 4 里的“14 包闭集 + 只两类目标”。

- **名单**：只装“在名单里”的发行包。名单 = 上一轮 14 个科学计算包 ∪ 30 个常见大包（torch / tensorflow / jax / transformers / xgboost / sympy …，维护者 2026-10-23 裁决，不看当月排名）∪ PyPI 近 30 天下载量前 1000 的项目名 − 排除名单。数据取自 ClickHouse 公开的 PyPI 数据集
  （源头是 PSF 在 BigQuery 公开的 `bigquery-public-data.pypi` 下载日志）；下载次数是事实数据，我们只取项目名、自己排序生成，不分发任何第三方文件。
  存成纯数据文件 `src/tavotto/engine/autoinstall_dists.txt`（PEP 503 规范名，一行一个，文件头写来源 / 窗口天数 / 快照日期 / top N / 条数），
  随 `packaging/tavotto.spec` 的 datas 进冻结产物；`scripts/dev/refresh_autoinstall_list.py` 重新生成（开发侧工具，产品运行时从不联网取名单）。
  **按主键切片查询**：公开端点每次最多读 10 亿行、默认超了静默截断，全表 30 天聚合约 11 亿行——脚本按 project 切四片、每片 `read_overflow_mode='throw'`，并拿 numpy 的切片合计与单独按主键查的值交叉核对，不一致就不写文件（2026-10-23 首版名单就是被截断的数据生成的，漏了 torch / sympy）。
  **排除名单**：pip / setuptools / wheel / virtualenv / poetry / uv / conda 等管理环境与打包工具本身不许被自动装进用户环境（脚本常量 `EXCLUDE`，文件头同列）。
  名单读不到时退回 14 个种子（只会更小）。名单外的包：不自动装，卡片点名“这个包不常见，确认是你要的再装”。
- **每个环境问一次**（环境按 `pool.resolve_worker_python` 此刻会跑脚本的解释器分类，`convertdeps.describe_env`）：
  `project_venv` / 已存在的受管环境一代：名单内直接装（保持上一轮口径；受管目标要新建环境或下载私有 Python 时仍问）；
  `system`（OS 自带 / `EXTERNALLY-MANAGED`）与 `builtin`（内置 runtime / 自建 worker-env / 本进程解释器）：**永不装**，卡片“这个 Python 是系统自带的……请给项目换一个环境”，
  主按钮打开现有的「选择运行环境」入口（`openEngineEnvPicker`）；其余（Conda、设置里指定的、环境变量固定的、项目记录里用户采用的）：**第一次**需要装时卡片
  “要给这个环境（conda: myenv）装缺的包吗？”+ 主按钮“装，以后这个环境都自动装”+ 次按钮“不装”；点主按钮才对**这个环境**记下常驻同意
  （`ai.convert.auto_install_envs`，键 = 解释器路径 sha256 前 16 位，只存指纹 + 种类 + 目录名，不存明文路径），之后同一环境再缺名单内的包直接装；
  设置 → Python 库有“已允许自动装包的环境”列表，每行一个“撤销”。
- **现有修复链对非项目 venv 的环境不支持原地 pip**（`deprepair.joint_target_for`：非项目 venv 一律以受管环境为目标），所以对 Conda / 指定解释器这类环境，主按钮执行的是
  现有链条的标准动作：装进 Tavotto 为本项目建的受管环境并让项目以后用它跑（不改动用户的那个环境；卡片文案如实写明）。不新增 pip 调用路径。
- **真卡片（Codex #1330 P1-2）**：候选校验遇到缺依赖且不自动装时，任务以 `needs_input` 终局（`unmet[].reason_code = "dependency_decision"`，`unmet[].data` 是结构化卡片事实：
  种类 / 包名 / 名单外的包 / 环境种类 + 可读名 / 动作 / 摘要 / 是否新建环境或下载 Python），**候选不删**（`meta.json` 状态 `awaiting_dependencies`），不进修正轮。
  用户点主按钮 → `POST /api/ai/convert/<sid>/dependencies`，后端起作业装依赖并**接着校验同一个候选**（`resuming` → `ready` / 回 `awaiting_dependencies` / `failed`），不重跑 Agent；
  “不装” = `discard`。计划不可执行 / 被修复链拒绝 / 开关关着这几种没有可执行动作的情况仍照常跑校验、以 `missing_dependency` 如实失败，失败一句话不再指向并不存在的提示。
- **换环境后重新判定（Codex #1330 P1）**：系统 Python / 内置 runtime 卡的主按钮打开现有的环境选择器（不另造第二个）；选完且解释器确实变了（前端比环境身份：全局解释器 + 本项目来源 / 解释器），
  前端自动 `POST …/dependencies {action:"replan"}`——不带摘要、不重跑 Agent，后端用此刻的项目解释器走自动路径的同一棵决定树（`convertdeps.ensure`）：能直接装的装并接着校验同一个候选，
  要问一次 / 名单外 / 仍是系统 Python 的回新卡，已不缺包的直接续跑。会话认证、项目隔离、续跑忙碌判定（`convert_resume_busy`）、取消传递与 `install` 动作一致；解释器没变保持原卡。
- **取消传到安装（Codex #1330 P1-1）**：任务的取消标记（`TaskRun.cancel_event`）与续跑作业的取消标记原样传给 `convertdeps`，由监视线程在取消到来时调 deprepair 现有的
  `cancel_status`（`prepare` 复用同一个取消句柄，开跑前就取消也会在拿锁前停手、一个字节不写）；过了提交点的取消被 `cancel_status` 拒绝，结果如实回 `installed`。
  卡片上点停止 / 关闭 = `discard`，它把取消传给正在进行的续跑作业，作业停手后删候选目录，结局 `cancelled`。
- **晚到的停止以安装结果为准（Codex #1330 P2）**：续跑里依赖步骤返回 `installed`（过了 deprepair 提交点，`cancel_status` 拒绝了取消，环境已换代）而取消标记仍置位时，
  **不删候选、不自动往下跑校验**（避免违背停止意图），候选停在 `meta.json` 状态 `deps_installed`（“已装好、等你继续”，`installed` 如实记录），界面一句「依赖已经装好了（停止来得太晚，没能撤回）。」
  + 主按钮「继续」（走 `{action:"replan"}` 入口：已不缺包，直接校验同一个候选）。依赖步骤真的被取消（未过提交点，结局 `cancelled`）或根本没装过东西：照旧 `cancelled`、删候选。
  取舍：停止是对“整件事”的意图，但环境写入已撤不回，保留候选让用户用已付出的安装继续，比删掉再让用户重来更不浪费；若用户在这之前已关掉卡片，候选留到 24 小时过期。
  **首跑同一语义（Codex #1330 P2 再续）**：AI 任务阶段（首跑）里依赖步骤过了提交点返回 `installed` 而取消已置位，也不落入取消检查被 Task Core 丢弃：适配器的 `hold` 把候选留存成 `deps_installed`，
  任务结局记 `needs_input`、`unmet[].reason_code = "dependency_installed_late"`（`data.installed` 如实带出）；本地取消仍先让会话显示 `cancelled`，前端在取消回复 `ok:true, mode:"local"` 后不再立即清条目，
  而是等会话 `final` 后再 `GET /api/ai/convert/<sid>` 对账：`deps_installed` → 出同一张「依赖已经装好了……」+「继续 / 放弃」卡，404 / 其余 → 清。未过提交点的取消不变（`cancelled`、删候选）。
  **普通依赖等待遇停止，取消赢（Codex #1330 P2）**：首跑校验期间依赖步骤停在 `system` / `uncommon` / `ask_once` 卡（环境没被动）而取消已置位时，不再 `hold` 成 `awaiting_dependencies`、也不以 `needs_input` 终局把已停止的转换藏起来：
  Task Core 在调 `hold` 之前查取消，经适配器的 `settle_hold_on_cancel(候选)` 问「这次停住是否必须保留」——只有 `late_installed`（环境已换代，上一条）为是；否则适配器放掉停住态，任务按 `cancelled` 终局并删候选（Task Core 不认识 convert 的 reason_code）。
  **停止后对账遇传输错误不清卡**：前端 `settleAfterStop` 对网络错误 / 超时 / 5xx（非 404、非 409）与 409 一样重试（至多 5 次、间隔 `POLL_MS`）；仍连不上则不清条目，落「暂时连不上」（`failed` + `resume_unreachable`，`lost.stopped`），
  重试与 × 都先对账、不重发任何 POST；只有明确的 404 / `discarded` / 其余真实状态才清，`deps_installed` 照旧出「继续」卡。
- **续跑期间点停止先对账再清卡（Codex #1330 P2）**：`resuming` 里点「停止」/ 关闭不再先清条目——条目留在 `resuming` 并标 `stopping`（卡片只一句「正在停止…」、无按钮），
  发 `discard` 后由续跑轮询按后端真实状态收尾：候选已删（404 / `discarded`）→ 才清卡；`deps_installed`（停止来得太晚）→ 落 `installed`，出上面那张「依赖已经装好了……」+「继续」；
  `ready` / `awaiting_dependencies` / `failed` 如实展示。`discard` 请求本身失败 → 立刻 `GET` 对账再按结果展示（仍在续跑 = 取消没送到，退回可再点停止）。后端不改：
  `discard` 对续跑作业只置取消标记并立即回 `{ok:true}`（作业异步收尾，回复里本来就不可能知道结局），结局以状态读口为准。非续跑阶段（`deps` / `installed` / `ready` / `running`）的停止 / 关闭行为不变。
- **AI 任务阶段点停止先看取消回复（Codex #1330 P1）**：`running` 里点「停止」/ 关闭不再先清条目。条目先标 `stopping`（卡片只一句「正在停止…」、无按钮），发 `POST /api/ai/sessions/<sid>/cancel` 后**看回复**：
  `ok:true, mode:"local"`（本进程已终止）→ 清；`ok:false`（`refused_committing` 等，任务已进提交闸门）或 `mode:"owner"`（只转交给别的进程、结局未定）→ 条目与会话号保留，
  由 `start()` 的轮询按会话 `final` + `GET /api/ai/convert/<sid>` 对账——与续跑阶段的 `stopping` 是同一套 `applyConvertStatus`，不写第二份：最终 `cancelled` / 会话或候选 404 / `discarded` → 清；
  候选已 ready → 落 `ready` 并标 `stoppedLate`，卡片一句「转换已经完成，停止来得太晚。对照一下，要用就采用」+ 主按钮「采用」/ 次按钮「放弃」，不重跑 Agent；`needs_input` 等其余结局如实展示。
  取消请求本身失败（传输错误）：不清、`stopping` 复位，用户可再点。
- **续跑的指纹基线测不出按「已变化」拒（Codex #1330 P2）**：`_rebuild` 在 install / replan / continue 的**任何动作之前**比对起跑基线；`fingerprint_changes()` 回 `None`（任一侧超限 / 读不了，
  例如停住后项目长过 `FINGERPRINT_MAX_FILES`）一律 fail-closed，按 `convert_project_changed` 拒：不装包、不跑候选、删候选。首跑与续跑落 ready 前的 `project.untouched` 对 `None` 本就记 `unknown`（非 `verified`，不放行）；
  TAF figure_edit 的 `None` = unknown = 不提交语义不变。
- **续跑落 ready 前原子地再查一次取消（Codex #1330 P2）**：「查取消标记 → `stage` 落 ready → 摘续跑登记」在 `_RESUMES_LOCK`（与 `_cancel_resume` 同一把锁）里一气做完。之前到达的 discard 在此被看见：删候选、结局 `cancelled`，绝不 ready；
  之后到达的 discard 因登记已摘，按「已 ready 的候选」照常删。
- **R / MATLAB 同名旁图只收浏览器能直接显示的格式（Codex #1330 P2）**：对照视图经 `/api/file` 原样取字节显示，所以 `sibling_original` 的候选后缀只有 png / jpg / jpeg / gif / webp / svg
  （`ai_convert._SIBLING_EXTS`，仓库里没有现成的“可直接显示”名单：`project_refresh.IMG_EXT` 是素材盘点集、含 TIFF）。只有 `fig.tif` / `fig.tiff`（或 bmp）时不当参照：`reference: null`、`verification: unknown`，
  照常说「未对照验证」；同时有 `fig.png` 用 png。TIFF 不走预览路径（那需要渲染，对一张用户的旁图不值得）。显式给 `path` 指认原图的路线不变。
- **续跑里的取消在启动校验之前生效，并传到在跑的探测（Codex #1330 P2）**：`run_validation` 在依赖旁路返回“继续”（调研 `none` / `skipped` / 不缺包）之后、**启动校验 runner 之前**再查一次取消标记，已置位 → `cancelled`、
  不启动 runner、删候选。校验运行中取消到来：`_convert_runner` 带 `force_cancel`（= `pool.force_cancel` 以同一份 run / write_roots 键硬杀在跑的探测 worker），`run_validation` 的监视线程在取消到来时调它，
  阻塞中的 probe 读到 EOF 返回，结局 `cancelled`、删候选。runner 没有该入口（纯引擎单测）则只做启动前检查。
- **「暂时连不上」点 × 也要停与对账（Codex #1330 P2）**：`failed` + `lost`（依赖 POST 已受理而对账也连不上）后端可能仍在装 / 校验，所以视同续跑中：× / 停止先标 `stopping`（一句「正在停止…」）、发 `discard`，
  再走同一套对账收尾（候选已删 → 清卡与提议；`deps_installed` → 「继续」；`ready` → 可采用）；对账再连不上回到「暂时连不上」。不再只清提议。
- **续跑遇到传输错误先对账（Codex #1330 P1）**：前端续跑（install / replan / 继续）的 POST 或轮询遇到网络 / 超时 / 5xx / 响应丢失，或 409 `convert_not_awaiting_dependencies`，不直接判失败：
  先 `GET /api/ai/convert/<sid>`（转换自己的状态，**不是**已终结的 AI 会话，后者永远是 `needs_input`，会把已 ready 的候选盖回旧依赖卡），按后端真实状态展示
  （`resuming` 继续等 / `ready` 可采用 / `awaiting_dependencies` 换新卡 / `deps_installed` / `failed` / 404 已不存在）。对账也连不上 → 「暂时连不上，请稍后重试」（`failed` + `resume_unreachable`，保留动作）；
  重试先对账：后端已在续跑 / 已 ready 绝不重发 POST，仅当后端仍停在同一张卡（摘要一致，或 replan）说明那次点击没到，才重发一次。**后端不改**：不为 POST 加幂等，
  因为 `convert_resume_busy` / `convert_not_awaiting_dependencies` 的 409 正好是对账的触发点，状态读口已足够区分各情形。
- **孤儿续跑持久化回滚（Codex #1330 P2）**：`meta.json` 说 `resuming` 而本进程 `_RESUMES` 里没有该 sid（后端重启 / 续跑线程意外死掉）是同一个判据、同一处处理（`_reconcile_orphan_resume`，状态读口与 `prepare_resume` 共用）：
  持 `_RESUMES_LOCK` 重读 meta 后原子写回——有 installed 记录 → `deps_installed`（等「继续」），否则 → `awaiting_dependencies`（卡片字段保留；环境若已被改动由下一次 install 的摘要校验兜住），没卡可回 → `failed`（`resume_interrupted`）。
  于是卡片再点 install / replan 能续跑同一个候选；正在真实续跑的（已登记）不回滚。候选没有启动期扫描（`prune_expired` 只在新建候选时调用），故只在读路径与 `prepare_resume` 做。
- **指纹不含 Python 环境目录（Codex #1330 P1）**：项目自己的 `.venv` 是自动装依赖的合法写入目标（`deprepair.prepare` 在校验前后写它），若指纹把它算进去，装包成功的候选会被
  `project.untouched` 误拒（首跑与续跑同理）。`ai_figure_edit.project_fingerprint` 因此整棵跳过 Python 环境目录：判据是目录里有 `pyvenv.cfg`（venv / virtualenv / uv）或 `conda-meta/`
  （conda，同 `launcher.conda_prefix_of`），**不按目录名猜**——叫 `.venv` 却没有这两个标记的目录照常计入；链接目录仍按原规则记链接目标。这同时影响 TAF figure_edit 的指纹（环境目录不是用户内容）。
  **盲区（如实）**：Agent 或任何进程往项目环境目录里写东西，指纹发现不了；兜底是 CLI 沙盒 / 内置执行器的路径判据仍把 Agent 限制在候选目录（候选目录里本来没有项目的 `.venv`）。
- **授权口径（两种，需要维护者知悉）**：`deprepair._check_confirmed` 要求调用方回显给用户看过的影响摘要，服务端自己持有的摘要不得代替回显。
  ① 问过一次 / 点过卡片：确认来自用户这次真实点击——前端把卡片带着的 `impact_digest` 原样回传，服务端重算计划，对不上就不装、回新的卡片，**不自回显**；
  ② 已记过常驻同意的环境（含 `project_venv` / 受管环境这两类上一轮就直接装的目标）：由服务端回显**同一次调用里刚算出的**摘要，依据是维护者 2026-10-23 的常驻同意裁决，
  可用 `ai.convert.auto_install=false` / `TAVOTTO_NO_CONVERT_AUTO_INSTALL=1` 收回，也可在设置里撤销单个环境；`deprepair` 自己的 `dependency_impact_changed` /
  `repair_plan_stale` 照样拦。

其余不变：端点同一个 `POST /api/ai/convert`（多可选 `source_language`，缺省 `python`；r / matlab 时 `library` 必须缺省，声明与文件内容对不上 400
`convert_source_language_mismatch`）；候选 / 校验 / 采用全套（第二节逐条）；目标名 `fig.R` → `fig_matplotlib.py`；`library` 在 r / matlab 时为 null；
会话元数据（sidecar、`meta.json`）记 `source_language`；预算与契约不变；**不加遥测事件**（第五节）。前端：主页拖入 / 选择 `.R` / `.r` / `.m` 时打开所在文件夹并出一张
一句话卡片（主按钮「生成可编辑版本」），不当成“不是脚本”，也不走“打开并运行”；桌面壳的原生拖放分类（`drop_paths.rs`，Codex #1330 P2）认这三个后缀（区分大小写，`.M` / `.Rmd` / `.mlx` 不算，与 `foreignScript.ts` / `sourcelang.extension_hint` 同一份）：
`DropTarget::ForeignScript { folder, script, name, ignored }`（载荷 `kind: "foreign_script"`），分派顺序 `.py` > 外语脚本 > 文件夹 > 不支持，多个取第一个；页面收到后与 DOM 拖放、选择器走同一个入口
（`openForeign`：开所在文件夹 → 出卡，不运行；语言按文件名后缀在页面侧判）。

### 项目内入口（2026-10-26 用户裁决：素材卡 + 脚本库两处都加）

主页拖入只覆盖“用户手里正好拿着脚本”；已打开的项目里，R 画的 `fig3_yield.png` 旁边就躺着 `fig3_yield.R`，素材库却只说“没有找到生成它的脚本”。两处补入口，**判据在后端、前端只翻译**：

- **素材卡**：`engine/foreignsource.sibling_of`——素材**同目录**下与它同主干名（逐字，`scandir` 的真实文件名，不靠 `stat`）的 `.R` / `.r` / `.m`，且 `sourcelang.detect_file` 认得出语言
  （≤ 1 MB、UTF-8、`.m` 的 Obj-C 一票否决，判不出就没有入口、不猜）。多个文件都认得出（`fig.R` + `fig.m`，或 Linux 上 `fig.R` + `fig.r`）有歧义，不给。
  结果挂在 readiness 报告的该素材上，新字段 `foreign_source: {script, language} | null`（`CAPABILITY_FIELDS` 同源，`/api/panels` 的 `capability` 带同一个值；老前端忽略）。
  **只在 `layout_only` + `no_source_candidate` 那一档给**——已连接、有 Python 候选、扫描没跑成的图说的是别的事；`status` / `reason_code` 一字不动。
  报告缓存键（`body_sig`）含候选文件的 `(路径, 大小, mtime_ns)`，同名脚本出现 / 消失 / 改动后下一次读取就重算。
  界面：选中这样的图，列表下方一句「这张图是 R 画的」/「这张图是 MATLAB 画的」+ 主按钮「生成可编辑版本」（`reasonText` 也换成这一句，所以角标 title、可达名、菜单第二行一致）；
  按钮走与主页那张卡**同一个**转换入口（`foreignScriptStore.setOffer` + `convertStore.start(script, null, null, language)` → `POST /api/ai/convert`，`source_language` 取后端给的语言），不新起第二套流程或端点；这个脚本已有转换条目时只出卡、不重发。
- **脚本库**：`GET /api/registry` 新字段 `foreign_scripts: [{script, language}]`（同一套 walk：`discover.iter_foreign_scripts` 与 `_iter_py` 是同一个实现，只换后缀；同一个 `detect_file` 判语言，判不出的不列）。
  **独立于 `all_scripts`**：R / MATLAB 脚本不能“打开并运行”，不进运行环境探测、依赖准备、准备会话、runstate，也不进 readiness 的“可运行脚本”判据（`iter_scripts` / `iter_all_scripts` 仍只认 `.py`）。
  界面单独一组「R / MATLAB 脚本」，每行文件名 + 语言（R / MATLAB），没有 ▶；点一行 = 出主页那张卡（主按钮「生成可编辑版本」）。
- **不变**：不运行 R / MATLAB；不收 `.Rmd` / `.qmd` / `.mlx` / `.fig` / `.rds`；后缀区分大小写（`.M` 不算）；原脚本永不改写；不加遥测事件；错误码与既有文案 key 不动。
  后缀名单现在在 `sourcelang.extension_hint`、`discover.FOREIGN_SUFFIXES`、`foreignScript.ts`、`drop_paths.rs` 各有一份，`tests/test_foreign_source.py` 比后端两份。
- **已知边界**：项目 watcher（`project_watch`）仍只盯 `.py` 与素材，所以“只新增了一个 `.R` 文件”不会自己触发刷新；下一次素材 / 脚本变化、手动刷新或重开项目时读到（报告缓存键已含候选文件，读到就是新的）。把 `.R` 纳入 watcher 会牵动脚本 delta 的全套处理，不在这一版。

## 修订：Pillow 画图脚本的识别放宽 + 转写对照表（2026-10-23，维护者裁决「先照常跑，跑完没有 matplotlib 图就出」）

`rasterhint` 原先要求「找得到本次运行写出的文件」，Pillow 画图脚本一旦输出路径动态拼、只 `im.show()` 不存盘、或文件还没出现，
就进不了「生成可编辑版本」。本修订只动 Pillow 分支（opencv / imageio / skimage / plotly / ROOT 判据不变，仍要文件证据）：

1. **时机不变**：仍只在结局 `execution_finished_no_figure`（跑完没有 Matplotlib 图）之后问一次；纯 AST、只读、不执行，读不了 / 解析不了 / 扫描被截断一律 None。
2. **Pillow 画图证据**（AST，接收者必须真是 `ImageDraw.Draw(...)` 的结果——赋值名 / with-as / 链式调用，所以 `ax.text`、`plt.show()` 不算）：
   强证据＝对 Draw 对象（同作用域、`ImageDraw.Draw(...)` 绑定在前，含 with-as / 海象 / 带标注）调了 `line / rectangle / ellipse / polygon / text / arc / pieslice / point`，或同时有 `ImageDraw.Draw` 与 `ImageFont`（画字）；
   弱证据＝`Image.new(...)` 的结果本身（赋给的名字含带类型标注 / with-as / 直接链式；**同作用域、绑定在前**：接收者名字须在同一作用域里、位置在调用之前最近一次绑定就是 `Image.new`，形参 / 重赋成别的值 / global·nonlocal 声明的名字都不算）被 `.save` / `.show`，别的接收者的 `.save` 不算（`Image.open(...).resize().save()` 这类图片处理没有 `Image.new` 也没有绘制，**不算**）。
3. **文件证据变成可选**：有（字面量路径文件 / 素材盘点新文件）就带 `path`，没有就 `path: null`、`in_project: false`；旧的文件证据路径原样保留并优先。
4. **同时 import matplotlib**：结局已是“没捕获到 Figure”这个运行事实，不再一刀切沉默；但只认**强证据**（弱证据在有 matplotlib 时仍沉默），文件证据只认 Pillow 自己的保存。
5. **看不全时**：强证据在动态 import 下仍可说（运行事实已替代“有没有 matplotlib”的静态猜测）；弱证据遇动态 import、或脚本里还有别的库在存图（分不清是谁）仍沉默。
6. **卡片**：`path` 为空时一句话＝「这张图是用 Pillow 画的，Tavotto 不能直接编辑它。」，主按钮「生成可编辑版本」，次按钮「知道了」；有 `path` 时保留原句与「打开素材库」。
7. **提示词**：`convert_prompt(library="pillow")` 在硬性要求之后追加一段 Pillow → Matplotlib 对照表（别的库与 R / MATLAB 来源不出现）：
   ImageDraw 的 line / rectangle / ellipse / polygon → `ax.plot` / `patches.Rectangle` / `Ellipse` / `Polygon` 且处理原点翻转（`set_ylim(H, 0)`）；
   text + ImageFont → `ax.text`（`pt = px × 72 / dpi`）；`Image.new` → `figsize=(w/dpi, h/dpi)` + 背景色 + `axis("off")` + `subplots_adjust(0,0,1,1)`；
   `Image.open` / `paste` → `imshow(np.asarray(img), extent=...)`；没有可靠对应 → `TAVOTTO_NEEDS_INPUT:` 停下。
8. **盲点（写在明处）**：绘制方法的接收者只跟踪同文件内赋值得到的 Draw 对象；Draw 对象当参数传进 helper 后在 helper 里画（接收者是形参）看不见，除非同时有 `ImageFont`。
   不改变“永远不改用户脚本”：结果仍另存 `<原名>_matplotlib.py`，不加遥测事件。

## 不做

- 不自动采用；不替用户改任何已有脚本；不在原脚本上叠改。
- 不运行用户的 R / MATLAB；不收 `.Rmd` / `.qmd` / `.mlx` / `.fig` / `.rds`；不为统计方法近似替换。
- 不把新版标成“与原图一致”：它是重画，用户看过对照才采用。
- 不做像素差度量；不为转换放宽 safe 档或新增 native 路径。

## 回滚

关总开关即回到 D 的纯提示卡；采用产生的只是项目里一个新文件，删掉即恢复。

### 外部语料复测发现的校验闭环（2026-10-25）

候选校验复用 probe 的逐图隔离能力，但其接受条件更严格：只要本次运行带
`partial`、非空 `failed_figures` 或 `dropped_figures`，即使已有其他图成功捕获，也以
`capture_incomplete` 拒绝 check / precommit / resume；应用适配层须原样传递这些事实。
不能把“至少一张可用”当作“这份转换通过”。语义保真仍由原生参照另验。

临时校验脚本仍放在原脚本同目录，文件名使用有效 Python 私有模块前缀 `_tavotto_convert_probe_`，
因此相对资源解析不变，而项目发现沿用其既有的基础设施脚本规则，不会把短暂的
校验副本登记成用户脚本或输出归属。每次运行的 finally 仍负责移除它；不删除
原项目的任何归属记录来解决冲突。回归：`tests/test_convert_capture_completeness.py`。

新生成的 Matplotlib 候选在真实校验之前，把 savefig 路径规范化为本次转换的
独立输出名（原目录、扩展名保留，主干追加稳定转换标识）。这是候选文件本身的
可审阅代码，采用时不再改写；不修改原脚本、原图或抢占原注册表归属。
`convertoutputs` 用 AST 封装调用，运行时只对实际 Matplotlib savefig 改名；
同名的其他对象方法、文件流、参数求值顺序保持原样。动态别名未能覆盖时，
真实捕获仍由现有 `probe._live_stem_conflicts` 判归属冲突，不会默默采用。
多页 PDF 在 `PdfPages` 构造时就打开路径，必须在构造前赋予独立输出名，不能等
savefig 收到已打开的流时再处理。已识别的构造调用同样在运行时核对实际类，
其他同名构造器及 BytesIO 不变；真实两页/五页执行验证原 PDF 字节保留、页数完整。
回归含多种调用形态、幂等、篡改拒绝和删缓存后的真实应用重开
（`tests/test_convert_outputs.py`）。


### 修订：保真缺口优先于可运行草稿（2026-10-25）

外部 MATLAB 案例中，模型缺少实际 parula 色表，却写出了可运行的 viridis 替代版；这不满足视觉保真要求。当时共享提示将缺失数据、实际 axes 色表和可靠语义对应都纳入 `TAVOTTO_NEEDS_INPUT`。**视觉默认值的阻断范围由下节维护者裁决替代**。标记在写文件或 check 后仍有效，取代上文“只在没有新文件时算数”的限制；提交前不执行/采用明确报告语义缺口的草稿。原项目零改动，界面沿用既有 needs_input 详情。它不是自动科学保真认证，也不把任意代码注释当作判据。

第三路径：不把近似色表当成功，也不按源码词语猜测或禁止整个 MATLAB 类别。保留已有准确转换路径；缺事实时说明原始 axes 色表、版本和导出方法，补齐证据后的转换独立复测。原生事实补充组与未提供数字事实的组分别记账，不计入默认路由放行证据。

### 修订：流程验收与视觉验收分开（维护者 2026-10-25 裁决）

先解决真实用户脚本的完整流程，逐像素一致是后续验收。主体是**本次候选在用户选择的 Python 环境里**完成执行、捕获、经确认采用并可重开；不是 CLI 自己能运行，也不是模型说“修好”。

所有来源与执行器共用 `ai_prompt_pack` 的验收顺序，按能力建立对应关系，不按外部脚本名加例外：

| 能力 | 共同要求 | 来源差异 |
| --- | --- | --- |
| 数据和项目资源 | 保留真实数据、路径基准、helper 与字体；不依赖候选缓存；缺输入给出补充步骤 | Pillow 的照片/蒙版；R 的表格和 factor 元数据；MATLAB 的矩阵和 helper |
| 计算语义 | 不编数据、不改单位/统计定义/缺失值/类别顺序/数学含义 | R 统计函数须核默认参数；MATLAB 核下标、列优先和逐元素运算；Python 沿用原计算 |
| 可编辑对象与输出 | 保留全部独立图/页面及顺序，文字与形状可编辑，原文件不改 | 原照片的处理可保留 Pillow；不能把绘制完成的文字/形状位图充作可编辑对象 |
| 目标运行环境 | 三类来源统一进入 `convertdeps.ensure`，沿用既有环境选择、确认、取消、同一候选续跑 | 修的是候选 Python 依赖；不安装/执行原生 R 包或 MATLAB 工具箱 |
| 视觉验收 | 保留已经提供的样式事实；未知布局/相机/字体像素/抗锯齿独立报告，运行通过不等于保真通过 | MATLAB 原生默认色表未知时可明确使用目标色表，必须保留 CData/CLim/colorbar 数值，不冒充 parula |

先用常规 Matplotlib 对象产生完整候选并运行检查，不因缺原生渲染器事实反复研究像素而耗尽预算。
外观未知项在候选注释和最终回复中说明；`comparison.verification` 仍只有 `unknown` / `side_by_side`，不增加“保真通过”状态。
真实数据缺失、统计方法不能可靠对应、必要可编辑对象无法表达，仍用 `TAVOTTO_NEEDS_INPUT` 阻断；该标记不能因已有可运行文件而忽略。
补充输入说明须给出可执行的命令、运行位置与实际产物；只打印控制台的命令不能声称已写出参考文件。
颜色/几何编码的数据含义不属于可放宽的像素差异。

第三路径的冲突根因是把执行闭环与渲染器一致性混成一个成功条件。分层验收保留两类证据；不放宽源文件完整性、真实运行、完整捕获、采用确认或环境权限。
回归：`test_ai_convert_app` 对三种来源跑相同依赖准备及停住/续跑/采用检查，实际 safe worker 执行 Python 候选；安装和模型是桩，真实模型与装包另行记账。

### 2026-10-25 保真收尾补充

候选中能静态指认的 Pillow 绘制文字/形状调用以 `raster_drawing` 拒绝，字体测量与照片读取仍允许。
这是一条针对已复现假成功的负证据，不替代原生数值、几何、颜色及逐页视觉验收。
长脚本的单响应预算增为 32768 token（包括隐藏思考），6000 字符的文件分块与总预算不变；
响应截断、网络、额度和会话期限分别以稳定原因码展示，不再统一成为未知失败。
