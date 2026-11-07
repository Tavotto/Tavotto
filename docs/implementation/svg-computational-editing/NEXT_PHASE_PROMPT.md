# SVG 计算图表编辑：下一阶段无人值守提示词

2026-11-07 10:45复核：P0预览归属已独立提交`8f4985ee`；最终53文件1048通过/0skip，正式前端、playground/widget及check均通过、1494源零漂移。N3e界面功能仍未提交，新源码的真实UI复验待做。下一项直接复用正式35成员混合包，完成真实安装/采用与跨项目归属验收，再做删自建旧源/cache的新进程冷迁移。详细批次与验收见[NEXT_PHASE.md](NEXT_PHASE.md)，历史与完整范围见[PROGRESS.md](PROGRESS.md)、[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。

以下可整体复制：

```text
继续完成Tavotto的SVG计算图表编辑，以最终可用为目标，不影响既有Python绘图和逻辑。最大化复用原模型、编辑器、writer、事务、RenderCore/pdfbackend、legendmodel。持续自主实现、修复和验证；保持当前同一分支，按功能分别commit，常规可逆步骤不逐批等待确认。

一、恢复真实状态
实施树E:/0 - Project/Tavotto-svg-r00；唯一分支zixu.wang/svg-computational-r00；本次复核代码HEAD8f4985ee，以实际HEAD/diff为准，不回退。
原E:/0 - Project/Tavotto仍clean main@69bcafae，未合入。证据根E:/0 - Project/svg-review-20261106。
先读PROGRESS最新checkpoint、NEXT_PHASE、对应AGENTS/规则/ADR，核提交身份、代理文件所有权及实际live句柄。不新建/切换branch/worktree，不reset/stash，不git add .，不push/PR/合并/发布。保已有改动/已发布目标/首红；隔离配置/data、关闭遥测，手工服务钉当前src并核__file__。
已提交安装后端0d86b357、timeline端口c761dc7e、目录0f85d4b2、保存4cfccfcf、测试时序48bdbda9、预览归属8f4985ee，不重做这些修复。

二、最新资格与边界
n3e-owner-final/second.xml：53文件1048pass/0fail/error/skip/exit0；1494前端源零漂移。
n3e-final/packaged-owner-second、playground-owner-first、playground-owner-first-check、widget-owner-first、widget-owner-first-check对应exit.json均0且零漂移。正式首轮nullable TS错误保留，根补参数可接受null后已最终联合/构建重验。构建/旧lint警告保留；不是全仓/全部旧功能或release资格。
P0根最终索引n3e-preview-owner/root-final-qualification.json；原子任务REPORT.md及21文件冻结是此前窗口，根另有nullable类型修正。5组内存AST反证实际红。原writer/queue/gate算法及后端chart_source校验未放松。
上轮n3e-ui-second真实安装初次1PUT、全文/revision/旧稿/资源通过；edit/undo/redo的native线宽[5,7.7,5,7.7]，Inspector约7.8→12→7.8→12pt；跨项目GET全文等于离开前最新稿，恢复仍12pt。这是旧源码窗口、同一服务，不冒当前修复后UI或新进程资格。
旧preview_png422已原样重放确认是快照图源与全局pj错配；新修复将identity.pj贯穿SourceCapture/HTTP/static fallback、真实cachedSVG响应projectId、活动正文/候选owner及代次。明确离开快照仍能跨换代完成，unknown候选不借当前素材。浏览器复验还未完成，不称422已实消失。
N3d356pass/3Windows权限skip、safe/native新API安装后删源新父冷读2pass、Python3.10stdlib窄边界是历史资格，不能冒界面/最低完整科学栈/全平台。测试组不叠加计数。
10:42核24049/24059/24069无监听；旧服务/browser正常终局，当前root无live UI/service。n3f-ui/ready.json已准备，source-ready尚未生成，正式混合目标不存在；不要重跑prepare。恢复仍先查最新状态，不重等旧终局句柄。

三、先完成N3e真实收口
保未提交API、projectPackageInstallStore、Dialog、两入口/App、翻译/专项、ADR和规则N3e段，不重写。P0已独立commit；当前下一项是正式混合包真UI，同时验证P0与N3e。
复用n3f-ui/ready.json：旧pj9ed49f29cb8b、working d_n3f_original_old_draft、port24069、fresh data/config已备。目标n3f-mixed/attempt-04/fixture/中文 新根 混合安装启动前核不存在。
用n3f-ui/freeze_for_ui.py和四份不同真实绿回执生成source-ready：--tests-receipt n3e-owner-final/second-exit.json；--packaged-receipt n3e-final/packaged-owner-second-exit.json；--playground-check-receipt n3e-final/playground-owner-first-check-exit.json；--widget-check-receipt n3e-final/widget-owner-first-check-exit.json。参数路径用证据根绝对路径。
重新生成n3f-mixed/observed-authority-live.json，另记观察器最终SHA，初始observer-provenance早于body_read_at修改不可当最终。源码冻结期间不改产品/测试或重建。原正常认证fixture.py serve，核真实import/HTTP bundle；root独占CLI browser n3f-svg-ui-20261107，不打印bootstrap秘密。
旧稿实际编辑/保存并冻结独立期望；真实上传35成员包、检视、安装到新中文根。资源发布后采用前先跑verify_installation.py --mode installed。明确打开后、未编辑/未切活动画布时保存真实初次请求/DOM metrics/同新pj panels观察。
按n3f-mixed/observed-verifier.md填真实receipt/请求/pj/workingId，执行verify_installation_observed.py --mode initial。要求初次恰1PUT，安装receipt/PUT/浏览器GET/磁盘全文/revision一致；只从冻结包/原Registry/真实DOM/panels推导有限合法派生，其余完整正文严格。不能从AS生成期望、删第二次PUT或放松尺寸。
图内编辑→undo/redo→保存→冻结离开前最新稿→切旧项目→已保存回执继续。核实际preview URL/header与图源同pj、迟到响应/cache、缩略图、Inspector最新值；保旧稿/命名稿和零隐式Run。
若失败先正常close/shutdown/wait解除冻结，最小反例→必要fix/反证→受影响验证→独立commit，不边验边改、不删已安装目标。资源/配置/项目打开/正文分阶段；未知响应保attempt与目标，不假报成功/自动重装/Run。跨项目continue走原最新工作稿restore，不重套installation.doc。
通过后定向提交N3e功能及规则/ADR：feat(packages): adopt installed charts through the document writer。状态文档另交，不混N5原型。

四、N3f完整中文根冷迁移
复用n3f-mixed/attempt-04/混合完整项目.tavotto：65662字节/35成员，SHA256 2de26ee47cb7f80605445f3d1d8f2c7536dfedb7aeb820e24bca993f39636bd7。双画布11对象/5refs/2graphs/8INPUT blob，原167件保留；科学/module计数4/4，安装恢复不自动增。
安装/采用整包独立核验通过后，仅按已核绝对归属清单移除自建旧源和派生cache，保新根/正式包/证据/用户目录及data/state/layout-sessions.json等持久恢复意图，不能全清data抹掉恢复状态。
旧service/browser正常终局并核源/bundle，删上述自建源/cache后起新父进程和全新浏览器。恢复活动/非活动画布与两历史，继续改数据/范围/样式、撤销/重做、保存、再冷开、恢复版本及适用scope×format独立有效产物。核模型/绑定/完整引用/原序重复patch/INPUT bytes/几何/文字/普通媒体PPI/配置重审；历史没有当前脚本不伪称可运行。schema2/3、普通Python、默认无声明路径保兼容。
附加n3f-runtime-kind/attempt-02/真正runtime家族.tavotto：15622字节/12成员，SHA256 e2fcd88d97ddaa034124d8f669cda3c4e37cde994892c804ffe54545b3b4012e。它经原descriptor→workspace/actions→正式save/package/inspect保持fileKind=runtime，独立PDFium/SVG与反证已过，但未UI/安装/冷恢复。用实际项目选择器安装入口和runtime卡入口验证来源、缺cache exact/fallback、编辑/保存/冷开；不能用35包三个实际pdf的runtime来源快照替代，亦不能用附加包缩减35成员包。

五、N5裁切→N4图例
先核n5-tick-clipping-review/structural-implementation-review.md、structural-validation.json和当前代理最终冻结。外部overlay原型不等于生产实现，未接renderStore/持久化/页面UI。仅overflow和固定物理宽扩viewBox已被反证（80pt线缩到约67.0772pt），不要重选。
保source frame/anchor/完整edits，独立render ink frame；同draw配对，新profile及旧格式兼容，真实render_frame provenance，不冒savefig。消费原SVGIR winning declarations，守直子CSS、percent、root clip、opacity和use继承；不能仅后包裹DOM或冻结全部computed defaults。
接通app/chartbundle、预览/PNG/canonical、前端FrameGeometry/命中/移动缩放/cache、保存冷开/历史/transport。面板外框负坐标不证明真实ink越页面，x10不得据此自动移版；x5才有真实越界反证。必要布局操作沿原事务，不缩字体/轴/数据、不偷改用户页面；独立SVG/PDF/PNG和真UI一致，旧native1/Python保留。
N4复用legendmodel，显式entry→series/custom。两系列同名/同色不串，复合句柄、手工文字和未绑定注释保留；编辑→撤销→保存→冷开→导出一致，合同和真实消费者同批。

六、最终范围与无人值守循环
M1双系列+图例+未绑定注释完整数据/范围/样式、撤销保存、空派生cache冷开，以及原图/标注/混合/多页适用scope×format；画布EPS保持eps_not_for_canvas。外观分享不暗带数据，完整分享携模型。
M2按SG00–17/A/B/C逐格补科学族、多轴/投影、文字/分组/use、事务写回、出版/预检及CLI/MCP/playground/桌面/发行入口，缺实现不能改为不适用。写回prepare→完整重放/几何/像素verify→commit，失败409原件零改。
M3冻结最终候选跑完整旧功能/等价性/CompatBench/E2E/性能/支持平台实际安装物资格；缺条件列未验，不把本机专项绿外推。
顺序N3e收口→N3f→N5→N4→M1→M2→M3；每批核状态/规则→真实实现/消费者→必要反证→受影响验证→实际退出码/源SHA/diff→定向功能commit→日期checkpoint→下一项。已有多代理授权，独立核验/外部有界原型可并行，每文件一个写入者，root集成/浏览器/提交单写；先核现有代理/句柄，避免重复。
Python先Ruff，前端正式build及派生检查。已绿只因新修改/失败/未解疑点追加验证，不反复计划和重跑绿测代替推进，不放宽门禁/容差。长检查前后、功能commit及约30分钟记HEAD/diff、所有权/进程创建身份、命令/cwd/env、actual rc/fail/skip、SHA/产物和下一步，只结束已核身份的自有进程。
无人值守依赖活跃会话，未创建常驻调度器；中断从最新checkpoint续接，不以一夜时长当完成。M1/M2/M3实过前保持完整目标未完成。

现在先核最新状态，直接完成正式混合包真UI的N3e/P0资格和N3e功能commit，随后推进N3f，不重做已提交P0。
```
