# 统一导出管线（2026-08-31，Prompt 12；ADR 0031）

> 原文出自 `web/AGENTS.md`「统一导出管线（2026-08-31，Prompt 12；ADR 0031）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`web/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`lib/guardedExport.test.tsx`、`lib/artifactApi.test.ts`、`lib/exportRequest.test.ts`、`store/exportStore.test.ts`、`lib/exportFigures.test.ts`、`lib/artifactInspection.test.ts`、`lib/imgRetry.test.tsx`、`components/ExportDialog.test.tsx`、`tests/golden/filename_vectors.json`、`tests/test_export_request.py`、`tests/test_export_phase_labels.py`

```text
prepareExport(input)   请求成形 + 就地校验（**不发网络**，输入框每敲一个字都能调）
validateExport(input)  真的开始之前能看出来的：重名 / 目录写不写得了
runExport(input)       起作业 → SSE + 轮询跟进度 → 落终局
cancelCurrentExport()  取消（清临时文件；最终目录一个字节没动过）
```

- **载荷的构造只有 `lib/exportRequest.buildExportRequest()` 一处**。组件不许
  自己拼那个对象，也不许在第二个 API 上把同一批参数再抄一遍——那正是
  「预检按一套规矩、导出按另一套」的来源。
- **`scope=original` 的载荷里没有 x/y/w/h，也没有页面尺寸**。不是"记得别填"，
  是那几个键不在类型上。尺寸来自 `lib/originalSpec.getOriginalOutputSpec()`，
  被忽略的变换逐项进 `ignored` 并**说给用户听**。
- **PPI 只在有位图格式时是数字**，否则 `null`。压成一个默认值的话，界面就会
  去显示一个不影响任何东西的设置（T-49 同一个形状）。
- **「这次导出用哪份来源」前端只读后端的 route，不再自己推算**（#1209 / A2b）：`/api/export/validate` 的 `route`
  经 `store/exportRouteStore.ts`（按 `routeKeyOf` 的键存取，键对不上 = 没有）进 `lib/exportRoute.ts` 的读取器：
  `embedsDiskBitmap`（预检 `panelSpec` 的 kind）、`copiesSourceVerbatim`（像素预览 / 透明背景）、`epsAvailability`
  （`offers.eps`）、`unavailablePanels`（画布范围画不出的图，点导出前就说）。**缺 route**（validate 还没回来、旧后端、
  算不出）时退回 `exportRoute.ts` 里的 `legacyEmbedsDiskBitmap`——后端判据的镜像，金向量
  `tests/golden/export_route_vectors.json` 看护同源——这是前端里**唯一**还在推算来源的地方，且不比旧行为乐观。
  画布范围的问题面板（`validationStore`，不在对话框里）不带 route，走退路。看护 `lib/exportRoute.test.ts`、
  `store/exportRouteStore.test.ts`、`components/ExportDialog.test.tsx`（「来源读后端 route」）。
- **作业活在 `store/exportStore.ts`，不活在对话框里**：关掉弹窗不取消作业。
  进度经 SSE `export.progress`，**外加一条轮询**——SSE 是加速器不是唯一通道
  （浏览器演练场、断线、代理下必须照样拿得到终局）。两条路进同一个
  `applyExportJob()`，晚到的旧快照按 job_id + 终局状态挡掉。
  请求失败保留本地化描述符、作业归属和用户输入；状态读取失败时暂停轮询，明确提示
  用户重试同一作业的状态。非终局 SSE 不代表读取恢复，不撤掉这个重试入口。
  取消的请求中、服务器已接受、作业已取消是三种状态；只有终局回执才宣告取消完成。
- **「导出期间又被编辑过」用此刻的文档重算指纹**，不是拿 `lastInput.doc`
  跟自己比（那份是开始时冻住的引用，比出来永远相等，而空的 diff 与"没变化"
  长得一模一样）。指纹量的是**载荷**：改画布名、折叠侧栏、撤销又重做，
  导出结果一样就不该冒这句话。
- **文件名规则是严格同源对**（`engine/exportreq.py`），八条闭集原因 +
  `tests/golden/filename_vectors.json`。首尾空白的字符集**写死一份**，
  不许退回 `String.trim()`（它与 Python 的 `str.strip()` 认的集合不同）。
- **原图不可用时说出原因，不隐藏选项、不静默改成画布**：一个消失的按钮
  无法解释自己，一次悄悄换掉的范围会让用户拿到一张他没要的图。**但原因只在
  原图范围下说**（2026-09-13，审计 B20）：以前「当前画布」选着、下面同时红着一句
  「还没定要导哪一张」并摆着图清单——两个状态互相矛盾。现在「原图尺寸」按钮只在
  项目里**一张图都没有**时禁用（画布范围下用一句普通说明解释它为什么灰）；选了原图
  但还没定是哪一张 → 不摆「没有当前图」的对象头，清单展开、指引是普通文字、主按钮灰；
  源文件不见了 / 找不到这张图才是红字。画布范围的对象头缩略图复用 `CanvasThumb`
  （画布列表 / 版本列表同一张，画真实内容），不再有第二套灰方块示意。
- **「这次按原图导的是哪一张」只在 `lib/exportFigures.ts` 判**（2026-09-06，
  用户反馈 06）：候选 = 文档里的面板（所有画布）+ 素材 / runtime 清单里还没上
  画布的；默认对象 = 快速编辑正在编的 → **画布上选中的面板**（主选优先）→
  项目里只有一张时就是它；对话框在这之上只叠一层「列表里点过哪一张」
  （对话框本地状态，打开时清空，不改画布选区）。之前只读 `activePanelId`，
  而它按 ADR 0028 只在快速编辑里非空——画布模式选中了面板仍说「先选中一张图」。
  列表的缩略图**不发渲染请求**：有图内修改的面板挂 `renderStore` 里已画好的
  SVG，其余走素材库同一条 `panelSrc`。「没选」（`no_figure`，让用户点一张）与
  「没得选」（`no_figures`，项目里一张图都没有）是两句话。
- **产物核验的解读只有 `lib/artifactInspection.inspectionState()` 一处**（2026-09-21，统一实施包 U08，
  ADR 0068）：回执 `outputs[].manifest` 是服务端重新打开封口文件量出的逐项四值
  （`verified / failed / unknown / not_applicable`）。每件产出一行：**全部可判项 `verified` 才画绿勾
  「已核验」**；有 `unknown` 按名字列「未核验：…」——中性色、没有勾；有 `failed` 红着列「核验未通过：…」
  （standard 政策下文件已经交付了，用户投出去之前得知道）；`not_applicable` 不画；**没有 `manifest`
  （老服务端 / 检查器没跑）= 未核验**，不是通过。Codex 内嵌画布的「已导出」提示按同一份解读
  （`inspectionRollup`）按文件名点名失败 / 未核验的文件。
- **严格核验是请求里的可选段**：「检查」组里的「严格核验产物」→ `buildExportRequest({ strictInspection, profileId })`
  才带 `inspection: { mode: "strict", profile_id }`；不勾时载荷**逐字节不变**（老服务端不认这个键也无妨），
  快照指纹不含它（它改的是发不发布，不是出来的文件）。阈值只在服务端从出版规范取，前端不算第二份。
- **`/api/render` 的一次失败可能只是背压**（候选后端下 child 队列满 → 503 + `Retry-After`）：`<img>`
  看不见状态码，`lib/imgRetry.useRetryingSrc` 对 `/api/render` 地址按 1 / 2 / 4 s 有界重试（cache-bust
  `r=n`，`src` 变了归零）；blob / data / `/api/file` 失败**不**重试。画布面板（`PanelView`）、缩略图
  （`CanvasThumb`）、版本 / 图库 / 导出对话框的缩略图（`ui/RetryImg`）、问题面板卡片的缩略图
  （`ProblemThumb`，`onGiveUp` 之后才换图标）共用这一份。
- **manifest 的 `identity` / `provenance` 与作业的 `trace` 只在类型上接住**（2026-09-21，统一实施包 U09，
  ADR 0070 / 0071）：`ArtifactManifestSummary.identity`（semantic / render / artifact / run 四身份并列）、
  `provenance`（源产物公开身份、回执公开事实 `ArtifactReceiptFacts`、节点表）、`ExportJob.trace`（有界阶段轨迹，
  `failed_phase` 是坏在哪一步）。界面**不画、不解读**它们（解读产物核验的仍只有 `inspectionState()`）；要显示时
  先定一份解读的唯一出处，别在组件里各自读字段。它们都是可选键：老服务端没有 = `undefined`，不是错误。

- **结果就是一句摘要**（2026-10-23）：成功 =「已导出 N 个文件」+ 一个「在文件夹中显示」（动作在摘要行上，脚部不再重复）；有失败 =
  「N 个成功，M 个失败」。逐个文件、每件的核验（已核验 / 未核验）、警告、失败原因、诊断都在默认折叠的「详情」里。**例外：核验未通过
  （`failed`）与警告不许只藏在详情里**——摘要行用一句说出数量（`resultInspectionFailed` / `resultWarnings`）并把语气升到 warn；
  「未核验」是常态，不上摘要。看护 `ExportDialog.test.tsx`「导出页改版」组。
- **点了之后的回应不许在视口外，完成后不许还挂着「开始导出」**（2026-10-07，设计审计 §10.2 P0）：
  进度 / 撞名 / 拒绝 / 结果住在 `Dialog status` 那块**不随正文滚**的状态区（脚部正上方，
  `data-dialog-status`），不在可滚正文的末尾。这一回在对话框里导完（`done` / `partial` 且至少一件写出）
  → 脚部换成「再次导出」（ghost，左，`data-export-again`，同一个 `canStart` 闸）·「在文件夹中显示」
  （secondary，只在 `canRevealInFileManager()` 时摆，定位第一件写出的文件，失败说出完整路径）·
  「完成」（唯一主动作，关对话框）；新的一次导出必须是显式的「再次导出」。重新打开对话框时上一回的
  完成态不留在脚部（回到「开始导出」）。只改呈现，载荷与作业逻辑不动。
- **两档对话框：左栏预览 + 右栏表单**（2026-10-21，渐进式画布改版）：档位由工作区 `mode` 定（`fast_edit` = 单图，
  `layout` = 多图；以后改成按图数推出时只换 `ExportDialog` 里 `single` 那一行）。左栏 `ExportPreviewPane` 复用对象头 /
  缩略图同一份取图（`useFigureThumbSource`），不新起渲染，且显示「预览仅为示意」。
  * **整列都是预览区**（2026-10-23，beta 0.19.0b3 反馈「导出页很丑」）：灰底面板从上到下铺满左栏，图在里面按比例 contain、
    **不留边距**（上下或左右顶满，`ExportZoomStage`）；滚轮 / 触控板捏合以指针为中心缩放、放大后可拖动（夹在内容范围内）、双击或右下角小按钮回到适合大小。
    缩放是 CSS 视图状态（不进文档 / 撤销 / 后端）；位图跟着放大换更高档的 `/api/render`（`lib/previewBuckets`，镜像后端 `RENDER_BUCKETS`，
    `px` 只增不减），整版缩略图（`CanvasThumb` 的 `flush` + `pxWidth`）同理按每张面板自己的显示宽度挑档。顶部说明只有一行「名字 · 尺寸」
    （不复述范围词 / 对象数 / 文件名清单——右栏的文件名输入框与格式勾选已经说了）；「示意 · 非最终文件」与预览尺寸是预览左下角的一枚小标。
    SVG 的「默认保留文字…」说明已删（导出对话框与设置页都没有，`svgNote` 不再存在）。
  * **单图**：左栏是导出后的大预览（真实图幅比例 + mm 尺寸）；右栏**没有「输出范围」**，固定 `scope=original`
    （标注 / 序号照常带入），没有要导哪一张的清单。
  * **单图档原图口径不可用时静默改按画布导**（这张图没有磁盘原件，如脚本现跑出来后重开、素材清单里还没有它）：内部 canvas scope，界面上不出现范围选择、不报错，导出区域按**可见对象的并集包围盒**裁掉页面白边（`lib/exportCrop.cropToVisibleContent`，经 `croppedDocument` 得到一份裁后文档，检查 / proof report / 请求 / 预览都吃这同一份（页面 = 包围盒、对象平移；包围盒含描边半宽与箭头头部外扩，与 RenderCore `geometry.py` 同源）；只改导出输入，不改文档、不进撤销，协议不变），左栏预览与 mm 尺寸显示裁后的区域（`fallbackPage`），文件名仍是这张图的名字。多图整版 / 每张图 / 原图口径不裁。
  * **多图**：左栏顶部缩略图条「整版 / 每张图」，有检查提示（除建议级以外的问题所指的对象）的缩略图带橙点；右栏「导出内容」
    = 整版（`scope=canvas`，默认）/ 每张图单独导出（`scope=original`）。**点缩略图只换预览，不改导出内容**；仅在
    「每张图单独导出」下点图只换预览与右栏检查摘要对准的那张。
  * **冻结 = 先取齐 route、再在一个同步块里整批建输入**（`ExportDialog.freezeBatch` + `lib/exportBatchInput.buildBatchInput`）：
    ① 逐张 await 它的 export route（只取后端的回答，1.5 s 超时按退路；不读文档、不生成请求输入）；② 之后在**同一个同步块**里
    （没有 await / then / 定时器）对整批图连着调 `buildBatchInput`——同一个同步块里活 store 不可能被用户或后台（`useEngineSync` 一类）改动，
    整批读的是同一个时刻的状态，等价于快照，不混两个文档版本、也不作废。「点击那一刻」的语义是「route 齐了那一刻」：等待期间用户改了东西，
    整批一致地用改后的。`buildBatchInput` 与单张导出的 memo 链是**同一批库函数**的两种调用方式（单张等渲染、批同步算），
    由「批里某张的请求体 == 同一张单张导出的请求体」「冻结是一个同步块」两条用例钉住。发送段只读冻结好的输入；冻结段里表单整块禁用。
  * **「每张图」只指当前激活画布上的图面板**（2026-10-21 用户裁决）：隐藏的图也算在画布上（与「隐藏的图也计数」一致）；素材库里还没上画布的图、别的画布上的图
    既不在左栏缩略图条里，也不进批（`canvasFigures`）。看护：`ExportDialog.test.tsx`「素材库里另有一张还没上画布的图」、`guardedExport.test.tsx`「changed bytes」整批零 start。
  * **「每张图单独导出」= 一次点击把每张图各导一份**：后端协议不变（一次作业仍只导一张原图，`buildExportRequest` / 同源对不动），
    由对话框**依次发多个普通作业**——`exportStore.batch`（`beginExportBatch` / `recordBatchJob` / `requestBatchCancel`）只记进度与各张结果，
    驱动在 `ExportDialog` 的两个 effect：先把表单摆到「这一张」（范围 = 原图、要导的图、文件名 = 图名），摆好了再走**单张导出同一条 `start()`**，
    所以每张各自按它自己的原图口径做 preflight / proof report，没有第二套。进度句「第 k/N 张 · 图名 · 阶段」；撞名停在那一张的冲突条上，
    选覆盖 / 另存重发这一张后继续；取消 = 取消当前这张并停掉剩余，已写出的如实报（汇总为 `partial`）；某张导不出（文件名不合法 / 源文件没了）
    记一笔失败继续；收尾后 `aggregateBatch` 合成一份汇总作业给 `ResultBlock`（部分失败走既有 `partialSummary`）。统一文件名输入在这一档隐藏
    （每张用各自的名字）。route 按每张各取一次，迟迟不来按退路发，不卡住整批。
- **导出永远可点，没有知情确认勾选框**（2026-10-21）：阻断项 / 无法核验项 / 没查成不再拦「开始导出」。检查区只有摘要芯片（计数）与「在问题面板中查看」；**没有逐条阻断清单、没有任何警告句、不变红**（用户 10-21 裁决）。**原来记入 proof report 的确认，改成自动记「导出时未处理的项」**：有未处理项就强制生成样式检查报告
  （`reportRequired`，开关置灰），`forced` / `acknowledged`（阻断与无法核验的规则码）/ `check_failed` / `acknowledged_check_failed`
  照旧写，语义变为「导出时仍未处理」而不是「用户点过头」；请求的 `validation` 有阻断项时恒为 `{policy:'acknowledged',
  acknowledged:[阻断规则码]}`（后端契约不变，`buildExportRequest` / `tests/test_export_request.py` 不动）。因此没有「确认只对这一批有效」
  的状态要维护：每次导出读的就是此刻这一批。看护：`ExportDialog.test.tsx`「阻断不拦导出」「未处理的项随每次导出自动记录」组。
- **正文三组、脚部四态**（2026-10-07，设计审计 §10.2 P1）：对象头（`TargetHeader`）是对话框的说明槽；正文是
  三个 `FormSection + FieldGroup`——**输出**（导出内容（仅多图）· 文件名 · 只读的「位置」行 +
  「更改…」深链设置 › 导出）、**格式**（四种格式均分一行 · 分辨率 · 透明背景）、**检查**（规范 · 摘要 ·
  严格核验 · 样式检查报告）；没有「高级选项」折叠。脚部是四态：空闲「关闭 / 开始导出」、进行中
  「关闭 / 取消导出」（关闭不取消作业）、撞名「取消 …… 另存一份 / 覆盖」（覆盖是危险浅底胶囊，Esc = 另存一份）、
  完成（见上）。状态区里的回应一律是 `Notice`，进度是阶段句 + 不定态 `ProgressBar`。位置读 `project.export_dir`
  （后端说了算），界面不拼路径。
  对象头（P02，不折叠）依次说：缩略图 · 对象名 · 范围与最终尺寸（同一份 spec 只在对象头与左栏预览各报一次，别处只说话不报数）· **将写出的文件**
  （`data-export-target-files`，即 `prepareExport().names`——与 `buildExportRequest` 发给后端的 `filename` + 格式
  同一份，含扩展名，不在组件里另拼；文件名不合法或原图范围下这张图导不出时不报）。检查行的范围句
  （`data-export-check-scope`）点名检查的是哪份产物：原图范围 =「仅查原图「名」」（原图口径那份），画布范围 =
  「查整个画布「名」」，只带名字不带尺寸。「无法核验」已有芯片（`chipNotVerifiable`）、0 问题时的
  覆盖说明（`data-export-check-coverage`）三处，不重复。
  导出**进行中**头部的文件名取正在跑的那次请求（`lastInput` 经同一个 `prepareExport`），不跟此刻还能改的表单走；
  原图范围下选中的图不在任何画布上（`panel` 为 null）时，范围句说「不在画布上，这次导出不做规范检查」，摘要记作
  「没查成」（`failed`，报告记 checkFailed），**不借当前画布的问题当结论**，画布阻断项也不挡这次导出（#1228 P2）。
- **关窗之后的终局通知**（PR2 任务状态统一）：`ExportDialog` 里的终局 effect 以「作业 + 终态」为键只报一次。对话框开着
  （且没被设置 / 样式压住）时结果就在结果块里，**不弹第二份**；关着时走通知轨——`done` / `cancelled` 一句话自己消失，
  `failed` / `partial` / `unknown`（错误语气）与 `conflict`（作业停下等你决定，info 语气）带「查看」动作、**不自己消失**
  （`setStatus(…, { owner: 'export.result', action })`），点「查看」或重新打开对话框即撤；切项目时同主人的也撤。
  新一轮导出开始（`running`）同样撤掉上一条。看护：`components/ExportDialog.test.tsx`「导出终局通知」组、`notificationRail.test.tsx`。

## 速查表原要点（2026-09-25 迁入，#608）

`web/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 载荷只由 `buildExportRequest()` 构造
- `scope=original` 没有 x/y/w/h（类型上就没有）
- PPI 无位图时 `null`
- 作业活在 store、SSE 外加轮询
- 「导出期间被编辑过」用此刻文档重算指纹
- 文件名规则严格同源对、不用 `String.trim()`
- 「哪一张」只在 `exportFigures.ts` 判
- 产物核验的解读只有 `inspectionState()` 一处（全部可判项 verified 才画绿、没有 manifest = 未核验）
- 严格核验只在勾了时带 `inspection` 段
- `/api/render` 失败按 `imgRetry` 有界重试
- `manifest.identity / provenance` 与作业 `trace`（U09）只是类型上接住，界面不画、不解读

带完整 PNG guard 的导出先经既有 `/api/export/validate` 确认 `artifact_sources`，再发同步/
后台导出；不认识策略的旧服务端不能靠忽略字段发布 legacy 输出。两次请求绑定发起项目。
确认只表示策略/磁盘字节，不冒充 Figure 准入；有编辑的输出仍由 worker 做初始化验证。
FRAME 是唯一有效 override 时原图导出保持静态复制语义。畸形 guard 必须变成 prepared
refusal，不能在常驻导出对话框的 React render 中抛出并弄坏文档。每个变体用自己的 guard。
同 stem PDF 可让仍存在的 PNG 不出现在素材清单；合法且匹配的 PNG guard 可进入既有导出
预检，由后端校验文件/策略/字节，不能把清单去重当成原件丢失。无 guard 的旧路径不放宽。
清单缺席时保留文档像素网格、derived 密度与未知透明度，不冒充新读到的磁盘元数据。
