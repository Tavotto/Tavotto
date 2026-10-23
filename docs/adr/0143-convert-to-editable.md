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
2. **Agent 只写候选工作区里的一个新文件**。候选 `data_dir()/cache/ai_convert/<sid>/` 只**复制**原脚本（与 ≤ 8 MB 的原输出图当参考），
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
5. 对照数据（`comparison`）：原图像素宽高 / 字节 / 摘要（PNG / JPEG / GIF 头部）、新版图幅 mm 与图个数、宽高比相对差、新版 SVG 预览
   （沙盒 CSP + nosniff 的 `GET /api/ai/convert/<sid>/preview.svg`，只给 `<img>`）。**不做像素差**（`pixel_diff: null`）：
   原件是位图而新版是矢量重画，逐像素差没有可辩护的含义，交给用户并排看。

### 三、默认开与逃生口

用户 2026-10-21 裁决：全部默认开，不是 beta-only，不挂默认关的实验开关（与 TAF 的 `ai.taf.enabled` 不同，不看它）。保留一个总开关作为逃生口：
`ai.convert.enabled=false`（只认显式 `false`）或环境变量 `TAVOTTO_NO_CONVERT_TO_EDITABLE=1`，关掉后 `POST /api/ai/convert` 回 404
`convert_disabled`，界面显示一句话（不再起任何会话）；原因卡与次按钮不受影响。

### 四、端点

`POST /api/ai/convert`（`{script, library, path?, agent?, client_request_id?}`；`library` 取 `rasterhint.LIBRARIES`；`script` / `path` 经
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
   与 `Plan.needs_input` 的“执行前”不同），不生成新脚本。标记只在“没有新文件”时算数、只认 Agent 的完整回答、行首；原因清洗 + 限长 200 字，只随结局给界面，不进日志 / 遥测。
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
