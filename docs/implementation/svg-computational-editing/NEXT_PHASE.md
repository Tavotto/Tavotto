# SVG 计算图表编辑：当前效果与下一阶段安排

> 复核时间：2026-11-07 10:45（Asia/Shanghai）。实施树 `E:/0 - Project/Tavotto-svg-r00`，唯一实施分支 `zixu.wang/svg-computational-r00`，代码 HEAD `8f4985ee`。原 `E:/0 - Project/Tavotto` 仍保留 clean `main@69bcafae`，未合入成果；本轮未 fetch。
> 本文维护当前安排；[PROGRESS.md](PROGRESS.md)按日期保留证据，[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)维护完整范围，[NEXT_PHASE_PROMPT.md](NEXT_PHASE_PROMPT.md)可直接复制接续。实际 HEAD/diff 和最新 checkpoint 优先，不能回退到文档 SHA。

## 1. 执行情况与效果

**已跑通计算图表的核心编辑闭环，尚未达到全部功能最终可用。** 实际界面已经验证项目包安装、明确采用、保存、线宽修改、撤销/重做及跨项目恢复最新稿。新修复已收敛预览归属，下一步应把正式混合包走完真实界面及冷迁移，而非重做已有实现。

方向保持轴模型、显式数据绑定与原计算编辑器。普通 SVG 可拖动的文字和图元，不等于具备原始数值或 Python 来源；可信来源沿原 Python 管线，缺语义的 SVG 走显式绑定，不从外观猜来源。

| 范围 | 已实现/已验证 | 未完成或限制 |
| --- | --- | --- |
| 计算编辑 | 显式绑定、范围/样式、撤销、保存及部分精确 INPUT 冷恢复有真实 UI 证据 | 图例、复杂几何、完整科学族及入口矩阵仍有缺口 |
| 安装后端 | `0d86b357` 已提交；受检复制、renderer 验证、排他发布、准确分阶段回执 | 后端资源发布不等于界面已采用或正文已保存 |
| 目录与保存 | `0f85d4b2` 最新完成导航；`4cfccfcf` 同一 writer 合并捕获/慢 hydration；`48bdbda9` 独立修测试等待 | 按影响补验，不能反复重做已提交修复 |
| 安装界面 N3e | API、协调器、Dialog、两入口、翻译已实现；上一源码窗口真实首次采用恰 1 PUT，旧稿/命名稿保留，零 Run | 功能仍未提交；新归属修复后的正式混合包真实 UI 复验待做 |
| 预览归属 P0 | `8f4985ee` 已独立提交；正文所属项目贯穿快照、请求、缓存、历史及找回候选；1048 条联合测试和三类构建通过 | 新源码窗口的跨项目真实请求/缩略图验收待做；不能已称浏览器 422 消失 |
| 继续编辑 | 上轮真实 native 线宽 `[5,7.7,5,7.7]`，物理 Inspector 约 7.8→12→7.8→12 pt；跨项目回到最新稿仍 12 pt | 同一服务恢复不替代新父进程/空派生缓存的冷恢复 |
| 混合迁移 N3f | 正式 35 成员包、严格安装核验器、独立 DOM/panels 观测核验器及 fresh UI 生命周期已准备 | 尚未真实安装该包，尚未删自建旧源/cache 或做新进程整链 |
| runtime 家族 | 附加正式包保持真正 `fileKind=runtime`，原 descriptor→workspace/actions→保存/打包/检视及独立产物核验通过 | 浏览器入口、fallback、安装及冷恢复仍未验证；35 成员包的三个 runtime 来源对象实际为 pdf，不能顶替 |
| 可见效果 N5 | 现有截图能看到计算红线、标题、PNG 和 12 pt；裁切根因及结构候选已有独立正反证 | 左/下刻度仍裁切；外部 overlay 原型在途，尚未接入产品消费者 |
| 总体交付 | 沿同一实施分支分功能 commit，未改原 main | M1/M2/M3 未通过，未 push/PR/合并/发布，不宣称全部旧功能无回归 |

效果截图：`E:/0 - Project/svg-review-20261106/n3e-ui-second/output/playwright/n3e-second-resumed-linewidth.png`。它是上轮真实 UI 证据，同时直接显示裁切；不是当前修复后的新截图或最终视觉合格证明。

## 2. 最新验证及证据边界

证据根 `E:/0 - Project/svg-review-20261106`。各组有各自源码窗口，重叠测试和历史通过数不相加。

| 检查 | 实际结果 | 证据与边界 |
| --- | --- | --- |
| 最新受影响前端联合 | 53 文件、1048 pass、0 fail/error/skip、exit0；1494 前端源码零漂移 | `n3e-owner-final/second.xml`、`second-exit.json`；不是全部前端或全部旧功能 |
| 正式前端构建 | exit0；i18n、tsc-b、Vite、Tailwind、包内 11 文件同步完成 | `n3e-final/packaged-owner-second*`；首轮 nullable TS 错误及原日志保留，修参数类型后最终联合/构建重验通过 |
| 派生构建 | playground/widget 及两个 check 均 exit0、1494 源零漂移 | `n3e-final/{playground,widget}-owner-first*`；正式构建保留体积/动态 import 警告，不称零警告或 release artifact |
| P0 专项与反证 | 子任务最终 15 文件 489 pass/0 skip，已含在1048；5 组 AST 内存变异实际红 | `n3e-preview-owner/REPORT.md`；根最终资格含另一个 nullable 参数类型修正，见 `root-final-qualification.json`，不可混为子任务原始冻结 |
| 规则 | 41 pass/0 skip/exit0；budget check exit0，已有 OVER 提示保留 | `n3e-preview-owner/root-final-rules.xml`；本轮计划文档修改后另做最终文档检查 |
| 历史后端资格 | 356 pass/3 Windows 权限 skip；安装后删自建源/cache新父冷读 2 pass/0 skip；Python3.10 stdlib 窄边界通过 | `n3d-stage-final.json` 等；不等于最低版本完整科学栈、浏览器或全平台资格 |
| 历史真实 UI | 初次1 PUT；PUT/浏览器回读/磁盘全文与revision一致；修改/撤销/重做和跨项目最新稿通过 | `n3e-ui-second/browser-effects-initial-measured.json`、`edit-undo-redo-proof.json`、`status-review-qualified-ui.json`；限原冻结窗口 |
| N3f 准备 | 35成员包/原167文件保留；观测 oracle 1正17反符合实际退出码；附加 runtime 包独立科学核验通过 | `n3f-mixed/observed-verifier.md`、`n3f-runtime-kind/preparation.md`；离线准备不能当真实迁移通过 |

原真实 422 已确认：离开快照携新项目 chartSource，却在发送时重读旧全局 pj；相同请求用正确 pj 有200对照，错误请求在附件读取处拒绝，未进 worker。P0 修复把 `captureDocumentMoment.identity.pj` 传进实际图源捕获，并使活动预览使用所属正文/候选的项目与代次；保明确离开快照可跨换代完成、同名资源不串项目、历史正文同归属读取，以及 needs_user 下合法找回候选预览。后端校验和原 writer/queue/gate 算法未放松。浏览器调用栈未捕获，不把最初 CanvasThumb 源码线索冒称该请求唯一运行时归因。

原上轮服务/浏览器已正常退出。10:42 本轮核24049/24059/24069均无监听，新 `n3f-ui/source-ready.json` 不存在、正式混合目标不存在；当前无主代理自有 live UI/service。外部 N5 原型工作不改变产品源码。所有既有首红保留。

## 3. 下一阶段：顺序、工作与验收

最近交付目标：**正式混合包安装到中文新根，旧稿无损；移除自建旧来源与派生缓存后，新进程仍能恢复、继续计算编辑、保存、恢复版本和导出。**

| 顺序 | 工作包 | 完成条件 | commit 边界 |
| --- | --- | --- | --- |
| P0 / N3e 收口 | 复用已提交归属修复，真实 UI 复验安装/继续编辑及跨项目预览 | 初次采用1 PUT、旧稿完整、离开/回到最新稿、请求图源/pj配对正确、缩略图真实，零隐式Run | P0已`8f4985ee`；N3e功能通过后单独`feat(packages): adopt installed charts through the document writer`；状态另交 |
| P1 / N3f | 35成员混合包完整中文根安装及冷恢复；追加真runtime家族 | 引用/输入/普通媒体/历史/参数独立核验；删自建旧源/cache，新父/新浏览器仍能编辑、撤销、保存/版本恢复/导出；安装恢复零隐式Run | 实际缺陷按功能fix；资格记录另交；N3a–f全退出才称完整迁移 |
| P2 / N5 | 区分源坐标图幅S与可见外框F，接入全部生产消费者 | 刻度完整且数据/单位/锚点不缩放漂移；预览、SVG/PNG/PDF、命中、页面布局和冷开一致；旧格式保持 | 合同/兼容与消费者按可用功能分批；外部原型不是产品完成 |
| P3 / N4 | 图例条目→系列/custom明确绑定，复用legendmodel | 同名/同色不串；复合句柄、手工文字和未绑定注释保留；编辑/撤销/冷开/导出一致 | 合同和真实消费者同批，不只加helper或字段 |
| P4 / N6 / M1 | 双系列+图例+未绑定注释完整用户闭环 | 数据/范围/样式→undo/redo→保存→空派生缓存冷开；适用scope×format真实独立产物或原稳定拒绝码 | 全矩阵实过才标M1；画布EPS仍`eps_not_for_canvas` |
| P5 / N7 / M2 | 按SG00–17、A/B/C能力映射补齐科学族和全部入口 | 多轴/投影、文字/分组/use、事务写回、出版/预检、CLI/MCP/playground/桌面/发行逐格有证据 | 独立功能commit；缺实现不改成“不适用” |
| P6 / N8 / M3 | 冻结最终候选，跑完整兼容及交付验证 | 原Python/旧文档、完整回归、等价性、CompatBench、E2E、性能及支持平台实际安装物资格 | 资格文档；缺平台条件列未验，不以本机专项绿外推 |

### 3.1 下一批立即执行：正式混合包同时复验N3e和P0

1. 先核实际HEAD/diff及代理所有权。P0已提交，不重做；N3e API/协调器/Dialog/两入口/App/翻译/专项、ADR0166及规则N3e段仍未提交，全部保留。没有第二个writer，不把资源发布当正文保存，未知响应仍保attempt与目标而不自动重装/Run。
2. 复用 `n3f-ui/ready.json`：旧项目pj `9ed49f29cb8b`、working `d_n3f_original_old_draft`、port24069、隔离data/config已准备。不要重跑prepare。正式目标 `n3f-mixed/attempt-04/fixture/中文 新根 混合安装` 当前不存在；启动前再核。
3. 用已有最终绿回执执行 `n3f-ui/freeze_for_ui.py`：tests=`n3e-owner-final/second-exit.json`，packaged=`n3e-final/packaged-owner-second-exit.json`，playground-check/widget-check各用`*-owner-first-check-exit.json`。生成新的source-ready及live authority；观察器最终SHA另记，不沿用修改前的observer-provenance。冻结期间禁止改产品源码或重建。
4. 正常认证服务，核当前树实际`__file__`和HTTP bundle。主代理独占CLI浏览器`n3f-svg-ui-20261107`；真实旧稿编辑/保存，冻结旧稿期望；上传正式35成员包→检视→新中文根安装。发布后、明确采用前，用原`verify_installation.py --mode installed`独立核资源。
5. 明确打开采用，保存完成但未用户编辑/未切活动画布时冻结初次请求、DOM文本度量、真实同pj panels响应。按`n3f-mixed/observed-verifier.md`运行实际initial核验：恰1 PUT，安装回执/PUT/浏览器GET/磁盘全文与revision严格一致。只允许冻结资源+原Registry+真实DOM/panels可独立推导的有限派生，不从AS生成期望。
6. 图内修改、撤销/重做、保存；离开前冻结完整最新稿，切旧项目再从已保存回执继续。核实际请求URL/header的pj与图源归属、422、cache缩略图及Inspector最新值。离开快照可以完成，活动迟到结果不能写新代。若失败先正常停自有browser/service解除冻结，再最小修复、受影响验证、独立fix，保首红及已安装目标。
7. 通过后定向提交N3e功能及对应规则/ADR，不能把状态文档、未收口N5实验或其他功能混入。普通跨项目复验与新进程冷恢复分别登记。

### 3.2 随后N3f：混合包完整冷迁移

正式包 `n3f-mixed/attempt-04/混合完整项目.tavotto`，65662字节、35成员，SHA256 `2de26ee47cb7f80605445f3d1d8f2c7536dfedb7aeb820e24bca993f39636bd7`。双画布11对象、5refs、2graphs、8去重INPUT blob；含计算SVG、PNG/PDF/SVG、runtime来源快照、同名两历史、外部file/prefix/空文件/空目录和原rc/精确argv。科学/module计数原4/4，不得因安装或恢复自动增加。

1. 完整安装/初次采用通过后，列出仅本任务自建的旧源和派生缓存绝对路径并核归属；保新根、正式包、证据、用户目录及`data/state/layout-sessions.json`等持久恢复意图。不能用全清data目录把恢复状态一起抹掉。
2. 正常停止旧服务/浏览器并核进程退出、源/bundle零漂移；移除上述自建旧源/派生缓存后，启动新父进程和全新浏览器。没有发生实际删除/新进程前，不标冷迁移通过。
3. 恢复活动/非活动画布、当前稿与两历史。继续改数据/范围/样式、undo/redo、保存、再冷开、恢复版本；独立核模型/绑定/原序重复patch、INPUT bytes、历史身份、几何/文字、普通媒体/PPI与配置重审。没有当前脚本的历史资源不伪称已恢复可运行脚本。
4. 追加`n3f-runtime-kind/attempt-02/真正runtime家族.tavotto`（15622字节/12成员，SHA256 `e2fcd88d97ddaa034124d8f669cda3c4e37cde994892c804ffe54545b3b4012e`）。经实际项目选择器入口安装/采用，验证真实runtime卡入口与fileKind/source保持、缺热cache的exact/fallback、编辑/保存/冷开；不得用pdf来源快照顶替。它是附加家族，不缩减35成员包。
5. 核适用scope×format真实产物及原Python/schema2/3/无声明受影响回归。配置pending/needs_review/ready与资源、正文分别表达；原rc/argv只是可携建议，包不继承机器授权或默认执行选择。

### 3.3 N5：裁切结构与接入要求

`n5-tick-clipping-review/structural-implementation-review.md`及`structural-validation.json`已有正/反证：源100pt图框包含x=-19.265625pt/y=107pt刻度文字，但物理viewport裁切；仅overflow无效，固定100pt宽只扩viewBox使原80pt线缩为约67.0772pt，已否定。

采用方向：保原source frame/anchor与全部原序edits，独立派生render ink frame；同次绘制生成manifest和SVG，明确新profile/旧格式兼容与真实render_frame provenance，不能冒充savefig。非零origin、非均匀PAR、旋转祖先/member clip已有受限实测；后包裹SVG会破坏直子CSS，完整computed-style冻结又会破坏use继承，必须消费原SVGIR的winning declarations并守住root clip/opacity/percent/use。

当前外部overlay后端已有有限专项通过，前端配对adapter仍在途，未接生产renderStore/持久化/页面UI，先等其冻结清单再评审/集成。产品落地必须覆盖app/chartbundle、preview/PNG/canonical、前端共享FrameGeometry/命中/移动缩放/缓存、保存冷开/历史与transport，不能只扩大manifest尺寸。

页面布局按真实ink判定：面板外框负坐标不自动证明文字越页面；x10样例不能据此自动移版，x5独立clone才有真实越界反证。仅在真实越界且有明确用户操作时沿原事务一次移动/扩页，不偷偷缩图或改用户页面。最后独立SVG/PDF/PNG、数据锚点/命中和真UI同验，旧native1/Python几何保留。

## 4. 无人值守执行纪律

- 在当前树/分支继续，不新建/切换branch或worktree、不reset/stash、不`git add .`。本地分功能commit已授权；远程操作/发布未授权。
- 主线依次N3e收口→N3f→N5→N4→M1→M2→M3。现有多代理可并行做独立核验与外部有界原型，每文件一个写入者；根代理统一契约、产品集成、浏览器和提交。复核旧代理/句柄后再派任务。
- 每批按“核状态/规则→实现真实消费者→必要反证→受影响验证→检查实际退出码/源SHA/diff→定向commit→日期checkpoint→下一项”推进。已绿只因新改动/失败/未解疑点追加验证，最终候选再跑完整链，不反复写计划替代进度。
- 配置/数据隔离、遥测关闭、运行时写入沿config.data_dir；Python先Ruff，前端正式build及派生检查。手工服务/子进程明确当前src及__file__，不能测到原main或旧bundle。
- 长检查前后、功能commit和约30分钟记录PROGRESS：HEAD/diff、所有权、进程创建身份、命令/cwd/env、实际退出码、fail/skip、源/产物SHA及下一步。只正常结束已核身份的自有进程，不按端口/模式kill。
- 中断按最新checkpoint接续。未创建常驻调度器；无人值守依赖活跃会话，不以一夜时长或专项绿作为完成。M1/M2/M3实过前完整目标保持未完成；缺外部条件精确列未验并推进独立就绪项。
