# SVG 计算图表编辑：下一阶段无人值守提示词

2026-11-07 12:45 复核：归档前端 e2c026fa、Moment 归属 c9e2980d 已分别提交。1320 联合及正式/派生构建通过；原安装/持久状态的新父新浏览器完成 A→B→A，六条 close/AS 预览均正确 A/200，12 pt 与旧稿保持，离开图正常。N3e 尚未提交，下一项取消/迟到确认真实界面收口；A 普通 SVG 整版导出、历史计算编辑/完整冷迁移/N5/N4/M1–3 未完成。详见 [NEXT_PHASE.md](NEXT_PHASE.md)、[PROGRESS.md](PROGRESS.md)、[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。

以下可整体复制：

~~~text
继续完成 Tavotto 的 SVG 计算图表编辑，以最终可用及现有功能不回归为目标。复用原轴/数据模型、编辑器、writer、Preparation/runner、RenderCore/pdfbackend、legendmodel。自主实现、修复、验证，在当前同一分支按功能分别 commit，常规可逆步骤不逐批等确认。

一、恢复实际状态，不重做已完成工作
唯一实施树 E:/0 - Project/Tavotto-svg-r00，分支 zixu.wang/svg-computational-r00。2026-11-07 12:45起点HEAD c9e2980d，归档前端e2c026fa/Moment归属c9e2980d已分功能提交；状态文档可能后续commit，以实际HEAD/diff为准。原E:/0 - Project/Tavotto仍clean main@69bcafae、未合入；证据根 E:/0 - Project/svg-review-20261106。
先读docs/implementation/svg-computational-editing/PROGRESS最新checkpoint、NEXT_PHASE及改动目录AGENTS/规则/ADR，核Git身份、代理所有权和实际live句柄。保全部未提交代码、已安装目标和首红；不新建/切branch/worktree，不reset/stash/git add .，不push/PR/合并/发布。
归属8f4985ee、归档后端135aba94及此前功能保留；当前归档前端/P0已提交，不重做。只剩N3e16路径WIP，api.ts归档hunk和规则同次writer两行已分开提交，N3e wrapper/10行规则/ADR仍待收口。

二、当前资格与边界
n3f-owner-final/source-and-ui-qualification.json：当前受影响联合68文件1320pass/0fail/error/skip；正式packaged-after-contract、playground/widget与两个check均exit0，1501前端源零漂移。首build因新增测试误用prepareDocument无参合同失败，原接口不改，测试改普通无参switchDocument并实际断言ready/prepared，新窗口全通过。旧1273窗口保留；非完整回归，不相加。未改源不重复跑绿组。
n3f-snapshot-preview/freeze.json：后端新37/旧受影响368分别全绿、Ruff0、3反证目标红，已135aba94。归档显示不授予历史计算编辑/重建许可，原当前编辑/导出撤旧门保持。
正式包n3f-mixed/attempt-05：65661B/35成员/双画布11对象/5完整ref/2个INPUT图8输入附件，SHA381625a558caa6ed62a0841bf8fc1505070567d1bb25bf082e249949ff865f2c。native100pt与页面放置55mm分开。installed-calibrated-live、initial-calibrated-live严格exit0，唯一初次PUT39。不重生成/重装掩问题；旧attempt04/167件/原2PUT首红保留。
真实图内12pt→undo7.8→redo12，PUT95/109/113全body仅目标linewidth/updatedAt变，命名保存成功。五归档SVG wire→可见DOM Blob字节一致；Timeline/ExportDialog实际DOM PNG五ref/14观察与原PDFium逐RGB通道全等、容差0。ui-readonly-proof/png-dom-proof保七个/三个反证。
原CDP七条response.body空Buffer没有恢复；实际DOM已用Blob是独立新证据，不把独立POST当原响应字节，png-observer-review保工具边界。
B画布真实PDF/PNG/报告已出，独立PDFium/PIL：180×130mm、value6三处、红/红/绿，PNG4252×3071/600dpi。A含ordinary.svg的整版PDF/PNG实际拒绝，须补原SVG合成，不能标不适用或要求用户预转PDF作为终点。
原两次422已修并保首红。最新owner-resume真实A→B→A的close8/9/10及AS13/14/15六条全A query/header/200，writer17/最终回盘严格闭合；12pt及旧稿/named/全模型/五ref/计数4/4保留。真实12:38离开节点对比图正常；限定4个历史DOM PNG与原PDF逐RGB全等。不把保cache的新父恢复冒完整冷迁移。

三、旧服务已终局，复验新生命周期
n3f-ui-owner-resume浏览器session n3f-owner-resume-20261107已42-normal-close0，exec11251/child46200终局0，launcher61264/child46200消失、24089无监听；停前HTTP11bundle/1790源末核通过，见stop-http-verification、service-exit、closed-window及root source-and-ui-qualification。calibrated原窗口也终局。勿续等旧session或直接重跑一次性serve。
attempt05目标和n3f-ui-calibrated/data已存在；旧fixture.py serve要求目标不存在，禁止直接重跑。建新外部生命周期/身份/端口，明确从已有安装和持久状态继续，核当前源/实际HTTP产物。不重装覆盖、不重新采用原包正文、不清状态绕问题；独立fresh安装才用新目标。
UI期间冻结源/build，主代理独占真实浏览器；新snapshot后再用ref，观察器只读，不注入store。正常停已核身份的自有进程，停前末核HTTP后解除冻结。

四、归档/P0已完成，保原能力不重做
四处Moment已使用owner/address.projectId/scope.pj/scope.pj，documentMomentOwner新6回归覆盖原adopt→writer→Moment→renderer/API、迟到ABA、persistEmpty/named空稿和overwrite/reload冲突。生产未修首跑2红、AST撤修再2红；新138专项/1320联合/正式与派生/真实UI均通过，见n3f-owner-production/AFTER_CONTRACT.md与source-release-after-contract。
提交顺序archive e2c026fa→P0 c9e2980d，因完整ref回归依赖归档消费者，资格属于共同候选源码窗口。保原切项目顺序/queue/恢复门/timeline gen、离开图Promise及后端422；不回全局兜底，不重新套旧patch。

五、第一批P1b：N3e取消/迟到确认/继续编辑收口
归档前端已提交，保完整ref+pj/owner+原序patch、同名A/B/迟到隔离、SVG仅img/Blob上下文、不伪造manifest。替代engineTransport仍留M2，不把HTTP资格冒全部入口。
N3e两入口/Dialog/API/单writer复用已有实现和取消恢复修复；n3e-install-review已有4文件95pass、7新回归、3反证。当前源真实验取消/确认迟到/重开/跨项目最新稿，不重复建立adopted工作稿。独立commit feat(packages): adopt installed charts through the document writer，带ADR/规则/翻译；状态另交。
本项使用独立未存在的安装目标和新生命周期，保持现有A/B与原data，不覆盖旧目标。完整35包真实安装后，让真实AS PUT成功而后续确认响应暂迟到，取消→编辑/保存→重开记录→继续编辑；核当前稿/undo不被旧package正文替换。若控网络时序，只延迟真实响应，不模拟成功JSON。继续严核资源/单PUT/旧稿/计数/全文revision。读到已存在目标或旧终局身份不能重装/重启绕过问题。

六、P2：真实历史计算编辑和完整冷迁移
读n3f-edit-source-prototype/REVIEW.md及n3f-snapshot-authority/third-path-review.md。同receipt活Figure→同次manifest/SVG→编辑/undo零重跑实验已过，但peek后override可lazy build、workerd unknown_session可透明重开。
生产守卫覆盖parent实际dispatch/worker实例、workerd重连、child真实execution identity；不能只传当前不生效require_built。保safe/native/legacy原路由/锁/屏障。
编辑来源与当前输出figureSnapshot分角色，首override清输出仍保来源，不退同名当前图。优先复用合同，扩展同步前后schema/包闭包/ref遍历/迁移和request/renderKey/want/last/latest/recent/mounted/exact；引用不是运行授权。
冷态沿原load_input_archive/校验、PreparationPlan/PreparationService、safe spec/runconfig/工作目录/runner。INPUT比对视图不是可执行目录，精确source/data/argv/rc和当前授权，pair独立几何/像素合格再进原编辑，取消回收。不建第二runner/store/writer，不静默运行陌生代码。
仅清绝对路径/归属核明的自建旧源和派生cache；保新根/包/INPUT/AS命名稿/runconfig/config/绑定/打开意图/layout-sessions(.bak)，不全清含AI history的data/cache。结束旧父/浏览器再清清单、启新父/新浏览器，验双画布/两历史、数据范围样式、undo/redo、保存/再开/版本恢复/适用导出。
另验n3f-runtime-kind/attempt-02/真正runtime家族.tavotto的实际fileKind/runtime/fallback/编辑冷开；12成员包不替代35包，混合包runtime来源对象实际pdf不能冒覆盖。来源合同/执行守卫/消费者/冷重建按功能commit，资格另交。
P2c如只剩A普通SVG导出未实现，保首红继续P3；不循环重跑冷开、不提前标N3f全过。P3后回验冷态A整版产物，再联合关闭完整迁移出口。

七、P3→P4→P5→M1/M2/M3
P3补普通SVG画布/跨格式合成。从真实A失败和复杂SVG语料有界实验，接原SourceResolver/FrozenSource/IR/ExportRequest/job，核文字/clip/透明/字体/资源；保单一SVG校验权威，不静默栅格化/要求预转PDF。实际A整版PDF/PNG和适用格式/多页/热冷通过后独立功能commit。
P4=N5，读n5-tick-clipping-review/prototype/IMPLEMENTATION.md/validation。外部13后端/92前端/14文件overlay不是产品。集成S/F、同次render_frame/native2/旧1兼容，闭合schema/请求cache/exact/frame事务/F→S几何/C确认/原图画布多页导出/ink-page策略。守CSS/percent/clip/use/PAR/旋转，不缩字体/轴/数据或偷改页面。x10负框不证明glyph越页，x5才有实证。
P5=N4，复用legendmodel，显式entry→series/custom；保同名同色/复合句柄/手工文字/未绑定注释，编辑undo保存冷开导出一致。
M1双系列+图例+注释完整数据/轴范围/样式→undo/redo→保存→空派生cache冷开→适用scope×format真产物，画布EPS仍eps_not_for_canvas。M2按SG00–17/A/B/C逐格补图族/多轴/投影/写回/预检/出版及CLI/MCP/playground/桌面/发行；替代engineTransport归档读取仍缺，构建绿不等入口实现。M3最终冻结后完整原Python/旧项目回归、等价性/CompatBench/E2E/性能及支持平台安装物。缺实现不标不适用，缺平台条件明确未验。

八、无人值守协议
每批核状态/规则→实现真实消费者→必要反证→受影响验证→实际exit/源SHA/diff→定向功能commit→日期checkpoint→下一项。已有多代理授权，仅独立有收益任务并行，每文件一个写入者；root统一合同/浏览器/集成/提交。不要反复计划或跑绿测代替实现。
Python先Ruff，前端正式/派生检查，最终候选再完整验证。长检查前后/提交/约30分钟更新PROGRESS的HEAD/diff/所有权/句柄/命令cwd env/actual fail skip/实物SHA/首红/下一项。未知保未知，mock/候选/准备不写产品通过。
写回仍prepare→完整重放/几何/像素verify→commit，失败409原件零改。保认证/沙盒/隐私/零PyMuPDF；运行数据走config.data_dir，隔离证据、关闭遥测，只结束已核身份自有进程。
无人值守为活跃会话自主接续，未建常驻调度器。局部失败修复继续、缺外部条件推进独立项，不以一晚时长/专项绿/测试数冒完成；M1/M2/M3全退出前总目标保持未完成。
~~~
