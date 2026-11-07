# SVG 计算图表编辑：当前效果与下一阶段安排

> 复核时间：2026-11-07 11:33（Asia/Shanghai）。唯一实施树 `E:/0 - Project/Tavotto-svg-r00`、分支 `zixu.wang/svg-computational-r00`，代码 HEAD `135aba94`。原 `E:/0 - Project/Tavotto` 仍 clean `main@69bcafae`，成果未合入；本轮未 fetch。
> 本文维护当前安排；[PROGRESS.md](PROGRESS.md)按日期保留证据，[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)维护完整范围，[NEXT_PHASE_PROMPT.md](NEXT_PHASE_PROMPT.md)可直接复制接续。恢复先核实际 HEAD/diff，不回退到文档 SHA。

## 1. 当前判断与可见效果

**计算编辑核心已能使用，完整 SVG 导入、跨项目迁移和全部原功能对等尚未验收。** 上一轮真实 UI 已完成安装、明确采用、线宽编辑、撤销/重做、保存和跨项目继续编辑；本轮正式 35 成员包确实安装成功，随后真实采用验收严格失败，并暴露历史图预览选源问题。应先闭合迁移与历史编辑，再扩大科学图族。

方向仍为轴模型、显式数据绑定及原计算编辑器。普通 SVG 图元可拖动不代表自动具备原始数值或 Python 来源。可信来源复用原执行管线；缺少语义的输入通过明确绑定建立模型，不能从外观猜出来源。

| 范围 | 当前事实 | 尚未达到 |
| --- | --- | --- |
| 核心计算编辑 | 数据绑定、范围/样式、撤销、保存、部分精确 INPUT 冷恢复已有真实 UI 证据 | 图例、复杂几何、完整图族和全部入口未闭合 |
| 项目归属 P0 | `8f4985ee` 已提交，正文 pj 贯穿图源捕获、HTTP、缓存、版本和恢复候选 | 新历史预览接线后的真实跨项目请求及缩略图仍待重验 |
| 安装界面 N3e | 两入口、Dialog、API、单 writer 协调已实现；成功保存后取消、编辑、重开导致旧安装正文覆盖当前稿的问题已修，新增 7 个回归 | 整个 N3e 功能尚未提交；取消/重开等当前源码的真实 UI 验收待收口 |
| 正式混合安装 N3f | 35 成员包真实安装返回 201，资源核验 exit0；旧 AS/命名稿保留，科学/module 计数仍 4/4 | 初次采用出现 PUT35/50 两次，严格 oracle 红；未删自建旧源/cache，未做完整新进程冷迁移 |
| 初次保存根因 | 夹具把页面放置 55 mm 当图源 native；实际源 100 pt（约 35.28 mm），原尺寸同步正确派生第二次保存 | 修正后的完整夹具构造器已准备，尚未生成/安装新尝试；不能改产品同步规则或放宽单 PUT 判据 |
| 归档预览后端 | `135aba94` 已提交：完整引用选择 PDF/SVG 配对，SVG 和 PNG 显示不执行脚本；历史 A/B 不借同名当前源 | 归档显示不授予当前编辑/导出资格，不伪造 manifest 或恢复可运行来源 |
| 归档预览前端 | 主画布、自动渲染、缩略图、版本、导出预览和离开快照接线已收口；66 文件 1273 pass，正式及派生构建/check 通过 | 尚未功能提交或新真实 UI 验收；归档可看不能推定历史可计算编辑 |
| runtime 家族 | 附加正式包确实保持 `fileKind=runtime`，正式构造及独立产物核验通过 | 入口/fallback/安装/冷恢复未验；35 成员包三个 runtime 来源快照实际 pdf，不能替代 |
| 刻度裁切 N5 | 外部结构原型已冻结，有源图幅 S/可见外框 F 配对实验和反证 | 未接生产消费者；旧截图左/下刻度仍裁切，不能宣称已解决 |
| 总体交付 | 同一实施分支按功能提交，原 main 未动 | N4、M1/M2/M3 均未退出，未 push/PR/合并/发布 |

真实效果截图：`E:/0 - Project/svg-review-20261106/n3e-ui-second/output/playwright/n3e-second-resumed-linewidth.png`。红线和 Inspector 的 12 pt 明确可见，刻度裁切也明确可见；它属于旧源码窗口，不能用作当前归档预览或最终视觉通过证明。

## 2. 已核验证与证据边界

证据根 `E:/0 - Project/svg-review-20261106`。不同窗口、重叠组不能相加为“最终总测试数”。

| 检查 | 实际结果 | 证据与边界 |
| --- | --- | --- |
| N3e 取消后恢复 | 4 文件 95 pass/0 skip，3 个 AST 变异在目标断言实红；新增 7 个回归 | `n3e-install-review/source-release.json`、`FIX_REPORT.md`；未单独提交，依赖尚未提交的 N3e 功能 |
| 归档前端改动前的联合 | 53 文件 1055 pass/0 fail/error/skip，1494 源零漂移 | `n3e-owner-final/third-after-adopt-fix*`；不能外推为当前归档前端源资格 |
| 同窗口正式/派生构建 | packaged、playground/widget 及两个 check 均 exit0、源零漂移 | `n3e-final/*after-adopt-fix*`；归档前端发生新修改后必须补验 |
| 已提交归档后端 | 新预览 37 pass/0 skip，受影响旧契约 368 pass/0 skip，Ruff/format 通过；3 个 AST 变异实红 | `n3f-snapshot-preview/freeze.json`；原无引用 PNG、当前编辑/导出撤旧门保留 |
| 当前归档前端最终联合 | 66 文件 1273 pass/0 fail/error/skip/exit0，1500 前端源零漂移；子代理 12 源与 root 5 源 lint exit0 | `n3f-preview-final/second*`、`status-review-source-qualification.json`；有 React act/已有 lint 警告，不是完整回归或 UI 资格 |
| 当前正式/派生构建 | 正式 i18n/tsc-b/Vite/Tailwind/包内同步、playground/widget 及两个 check 均 exit0，同 1500 源零漂移 | `n3f-preview-final/packaged-second*`、`playground-first*`、`widget-first*`；首轮仅测试 alt 硬编码被 i18n 拒绝，改装饰图片 alt 为空后重验；体积/动态 import 警告保留 |
| 正式混合包真实窗口 | 安装 201、资源核验 exit0；初次采用严格 exit1，PUT35/50；旧稿保持，执行计数 4/4 | `n3f-mixed/install-receipt-live.json`、`installed-live-first.json`、`initial-observed-live-first.*`、`n3f-ui/closed-window-proof.json` |
| 历史来源故障 | runtime 来源静态请求 404，未带引用的 History render 500；worker 找不到原 plot.py，未增加科学执行 | `n3f-ui/output/playwright/32-initial-errors.log`；不称已完成编辑恢复，也不称额外脚本实际运行成功 |
| N5 外部原型 | 后端 13 pass、前端 92 pass，类型/格式/lint 检查通过，配对及缩放反证实红 | `n5-tick-clipping-review/prototype/validation.json`；14 文件 sparse overlay，不是产品代码；后补旋转 1 项属于原 13 范围 |

`n3f-ui` 旧服务和浏览器均正常退出 0，原 runtime/launcher 已不存在，24069 无监听；服务结束时源未漂移。启动时实际 HTTP 11 bundle 已核对；停机后先重建才做回看，未补到停前 HTTP bundle 末核，因此不能宣称该窗口末核 bundle 零漂移。原目标已存在，旧 `serve`/ready 不可直接重启或当作 fresh 安装。

playground/widget 构建通过只证明产物能构建并与当前源同步。替代 engineTransport 尚未接归档读取能力，属于 M2 入口矩阵的待实现项；不得把 HTTP 预览接线外推为全部入口完成。

## 3. 下一阶段工作包

最近可交付目标：**中文新根完整安装，旧稿无损，历史显示来源准确；移除清单内自建旧来源/派生缓存后，新进程能恢复并继续计算编辑、撤销、保存、恢复版本和导出。** 只显示归档图片不能关闭“计算编辑”目标。

| 顺序 | 具体交付 | 验收出口 | 提交边界 |
| --- | --- | --- | --- |
| P0a / 归档预览 | 收口完整引用、pj/epoch、A/B 缓存、画布 image-context 及全部预览消费者 | 源码接线、联合及正式/派生构建已通过；下一出口为同名 A/B、跨项目/迟到、全部消费者零隐式 Run 的真实 UI | 后端已 `135aba94`；前端独立 `fix(previews): display explicit archived Figure references`，不得混 N3e wrapper |
| P0b / N3e 收口 | 正确 native 完整混合夹具，新生命周期安装/明确采用/取消重开/跨项目最新稿 | 初次采用严格 1 PUT；完整正文/revision 与磁盘一致；旧 AS/命名稿/资源保持；新预览 pj/ref 正确，计数不增 | `feat(packages): adopt installed charts through the document writer`，含对应 ADR/规则；状态另交 |
| P1 / N3f 历史编辑 | 完整引用对应同 receipt 活 Figure，或由精确 INPUT 沿原准备/执行管线重建；图源身份贯穿真正编辑态 | 旧 A 不借 B；第一次改动及撤销/再开不丢来源；同次 manifest/SVG 及独立几何/像素对照后才进入精确编辑；无静默 Run | 按来源合同/原管线消费者闭合分功能 `fix/feat`，不建第二 runner/store/writer |
| P2 / N3f 完整冷迁移 | 删除仅自建旧源与派生缓存，新父进程/新浏览器恢复，追加真 runtime 家族 | 双画布、两历史、INPUT/媒体/rc/argv 保持；计算编辑、撤销保存、版本恢复、适用导出都实过；N3a–f 全出口才称完整迁移 | 实际缺陷独立 fix，迁移资格记录单独提交 |
| P3 / N5 | 集成 S/F 合同和所有生产消费者，修刻度裁切 | 文字完整、数据锚点/单位/缩放无漂移；命中、SVG/PNG/PDF、页面及冷开一致，旧 native1/Python 保持 | 合同/兼容与真实消费者分可用功能提交；外部 overlay 先审后集成 |
| P4 / N4 | 图例条目显式绑定 series/custom，复用 legendmodel | 同名同色不串，复合句柄、手工文字/未绑定注释保留；编辑→撤销→保存→冷开→导出一致 | `feat(svg): bind legend entries to computational series` 等独立功能提交 |
| P5 / M1 | 双系列+图例+未绑定注释的完整用户闭环 | 数据/范围/样式、undo/redo、保存、空派生缓存冷开及适用 scope×format 真产物或原稳定拒绝码 | 只有完整矩阵实过才标 M1；画布 EPS 保持 `eps_not_for_canvas` |
| P6 / M2 | SG00–17、A/B/C 能力和全部入口逐格补齐 | 科学图族、多轴/投影、文字/分组/use、事务写回、出版/预检、CLI/MCP/playground/桌面/发行有证据 | 按功能提交；缺实现不改成“不适用” |
| P7 / M3 | 冻结最终候选，完整兼容与交付验证 | 原 Python/旧文档、完整回归、等价性、CompatBench、E2E、性能及支持平台实际安装物资格 | 最终资格文档；没有实际平台条件的格子明确未验 |

### 3.1 立即执行：复用已完成源码资格，开启全新 UI 窗口

1. 复核实际 HEAD/diff、代理写入所有权和句柄。root 持有 PanelView/useEngineSync/新 hook 及测试；当前 `svg_architecture_review` 持有 API/helper/缩略图/版本/导出预览/离开捕获。现有代理已全部释放、无 live 句柄；归档 12 源与 root 5 源责任明确，N3e 取消恢复两文件也已释放。保所有改动，不重复派工。
2. 先核 `n3f-preview-final/status-review-source-qualification.json` 与实际源码：66 文件 1273 及正式/派生/check 已按同 1500 源通过，未改源不重跑。首轮测试 alt 的 i18n 失败与修正、VersionDialog 旧期望补显式 null 的首红保留；没有放宽 i18n 或退回全局 pj。若源有新变化，仅按影响补验并建立新冻结窗口。
3. 使用 `n3f-mixed/prepare_mixed_calibrated.py` 创建**全新尝试目录**，保原 attempt-04 和全部首次失败。native 从实际 SVG pt 图幅导出，交叉核真实 manifest，页面放置保持原 55 mm。必须仍由原保存/打包 API 正式产出完整 35 成员，不手改 ZIP 或缩小范围。
4. 严格安装/initial/观测核验器新增显式 ready/attempt 参数，旧默认与所有严格断言保留；新期望只来自新包、原注册表和独立 DOM/panels 度量。原校准构造器已 lint/help 通过，尚未真正运行；不要把“已备”写“已生成”。
5. 创建新的外部 UI 生命周期目录、未存在的中文目标和新端口。正常认证，核本树 `__file__`、HTTP bundle、observer SHA；冻结时不改产品源/构建。主代理独占真实 CLI 浏览器。旧稿先编辑保存，再正式安装→独立资源核验→明确采用→严格初次保存核验。
6. 覆盖普通/归档两个画布、同名两历史、取消/保存确认/重开、实际编辑/undo/redo、切旧项目再继续最新稿；核 pj、完整 ref、正文/revision、Inspector、真实图像和执行计数。错误响应不偷借当前源；离开快照能完成，活动迟到结果不能落新代。
7. 失败先正常停已核身份的自有服务/浏览器，保目标/日志，解除冻结后再最小修复与补验；不直接重装覆盖已存在目标。通过后按上述功能边界定向 stage/commit，API 与规则混合 hunk 逐项核对。

### 3.2 历史继续编辑与冷迁移

归档显示仅消费完整引用的原配对，SVG 保持 `<img>`/Blob 图片上下文，不挂可执行 inline DOM、不制造 editable manifest。主应用没有能兜底的 CSP，hash 验证不等于 SVG 净化。

真实编辑先确认来源。若原 worker 中存在**同一 receipt** 的活 Figure，经原 existing-only/peek 路径取得同次绘制 SVG/manifest，核 pj/epoch/实例，不能用可能启动 worker 的普通 render 当只读探测。冷态使用原 `load_input_archive`/校验与 PreparationPlan/PreparationService、safe spec、runconfig/工作目录和原 runner；INPUT 比对视图不是可执行工作目录，未知覆盖不能当安全已知。详见 `n3f-snapshot-authority/third-path-review.md`。

编辑 request/want/last/latest/recent/mounted/exact 必须消费同一完整来源身份，不能继续由 `fileId+patches` 把 A/B 别名。保存第一次 override 后会清除旧输出快照，因此要复用或扩展原来源合同保留**编辑来源**，不能清掉后退回同名当前脚本。静态截图、活 Figure、可重建输入三者分别验证；缺模型的普通 SVG 沿显式绑定，不从显示成功推定可计算。

真正冷迁移前只列举并核实本任务自建旧源/派生缓存绝对路径；保新根、全部包、INPUT 附件、AS/命名稿、runconfigs/config、项目绑定/打开意图和 `data/state/layout-sessions.json(.bak)`。`data/cache` 还含 AI history，不能整目录清空；`data/figure_snapshots` 可派生部分按归属清单处理。结束旧父/浏览器后删除清单内目标，再启新父/全新浏览器，不能用热恢复冒冷开。

附加真 runtime 包 `n3f-runtime-kind/attempt-02/真正runtime家族.tavotto`，15622 字节/12 成员，SHA256 `e2fcd88d97ddaa034124d8f669cda3c4e37cde994892c804ffe54545b3b4012e`；须另验真实入口、fileKind/source、缺热 cache 的 exact/fallback、编辑/保存/冷开，不能取代 35 成员包。rc/argv 为可携建议，不继承本机授权或默认执行选择。

### 3.3 N5 原型落地要求

外部原型冻结于 `n5-tick-clipping-review/prototype/IMPLEMENTATION.md`/`validation.json`。保源坐标 S，独立派生可见 F；同次绘制有真正 render_frame provenance、新 native-manifest/2 和旧 /1 兼容。消费原 SVGIR winning declarations，守直子 CSS、percent、root clip、opacity/use、非零原点、PAR、旋转及无 viewBox px→pt 基准；不能简单扩 viewBox 让原 80 pt 线缩短。

未接产品的消费者包括闭合 origin/profile schema、请求/缓存/exact 身份、documentStore frame 采用事务、F→S 几何写入、C 确认、原图/画布/多页 ExportRequest/jobs 和真实 ink/page/framePad 策略。必须与后端、共享 FrameGeometry、命中/移动缩放、持久化/冷开/历史一起接通。x10 外框负坐标不能证明文字越页；x5 clone 才有真实 glyph 越界，布局只能沿原明确用户事务处理，不能偷改页面/缩字体/轴/数据。

## 4. 无人值守运行与中断恢复

- 当前树/分支继续，不新建或切换 branch/worktree，不 reset/stash/`git add .`。已授权本地分功能 commit；不自动远程发布。
- 主线 P0a→P0b→历史编辑→完整冷迁移→N5→N4→M1→M2→M3。已有多代理可并行独立任务，每文件一个写入者；主代理统一契约、浏览器、集成和提交。先核已有代理，不为流程造新代理。
- 每批“核状态/规则→实现真实消费者→必要反证→受影响验证→实际退出码/源 SHA/diff→定向功能 commit→日期 checkpoint→下一项”。已绿只因新修改、失败或未解疑点补验；最终候选才跑完整链。不放宽门禁，不反复写计划替代实现。
- Python 先 Ruff；前端正式 build/派生 check；只结束明确属于本任务且 PID/创建身份核对过的进程。证据隔离，遥测关闭，运行时沿 config.data_dir。
- 长检查前后、提交和约 30 分钟写 PROGRESS：HEAD/diff、所有权/进程、命令/cwd/env、实际 exit/fail/skip、SHA/产物、首红、下一项。中断只从最新 checkpoint 续接，不复用终局句柄/旧 target/旧 bundle。
- 未创建常驻调度器；无人值守依赖活跃会话。不以工作时长、测试数量或专项绿代替最终完成。M1/M2/M3 未实过，完整目标持续标未完成；缺外部条件精确列未验，并推进独立就绪项。
