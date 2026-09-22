# 统一导出管线（2026-08-31，Prompt 12；ADR 0031）

> 原文出自 `web/AGENTS.md`「统一导出管线（2026-08-31，Prompt 12；ADR 0031）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`web/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

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
- **作业活在 `store/exportStore.ts`，不活在对话框里**：关掉弹窗不取消作业。
  进度经 SSE `export.progress`，**外加一条轮询**——SSE 是加速器不是唯一通道
  （浏览器演练场、断线、代理下必须照样拿得到终局）。两条路进同一个
  `applyExportJob()`，晚到的旧快照按 job_id + 终局状态挡掉。
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
- **严格核验是请求里的可选段**：高级选项「严格核验产物」→ `buildExportRequest({ strictInspection, profileId })`
  才带 `inspection: { mode: "strict", profile_id }`；不勾时载荷**逐字节不变**（老服务端不认这个键也无妨），
  快照指纹不含它（它改的是发不发布，不是出来的文件）。阈值只在服务端从出版规范取，前端不算第二份。
- **`/api/render` 的一次失败可能只是背压**（候选后端下 child 队列满 → 503 + `Retry-After`）：`<img>`
  看不见状态码，`lib/imgRetry.useRetryingSrc` 对 `/api/render` 地址按 1 / 2 / 4 s 有界重试（cache-bust
  `r=n`，`src` 变了归零）；blob / data / `/api/file` 失败**不**重试。画布面板（`PanelView`）、缩略图
  （`CanvasThumb`）、版本 / 图库 / 导出对话框的缩略图（`ui/RetryImg`）共用这一份。
- **manifest 的 `identity` / `provenance` 与作业的 `trace` 只在类型上接住**（2026-09-21，统一实施包 U09，
  ADR 0070 / 0071）：`ArtifactManifestSummary.identity`（semantic / render / artifact / run 四身份并列）、
  `provenance`（源产物公开身份、回执公开事实 `ArtifactReceiptFacts`、节点表）、`ExportJob.trace`（有界阶段轨迹，
  `failed_phase` 是坏在哪一步）。界面**不画、不解读**它们（解读产物核验的仍只有 `inspectionState()`）；要显示时
  先定一份解读的唯一出处，别在组件里各自读字段。它们都是可选键：老服务端没有 = `undefined`，不是错误。
