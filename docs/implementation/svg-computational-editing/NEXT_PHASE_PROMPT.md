# SVG 计算图表编辑：下一阶段无人值守提示词

2026-11-07 12:17 复核：校准完整 35 成员包已真实安装、严格初次 1 PUT、编辑/撤销/重做/命名保存及历史显示通过；B 的 PDF/PNG 实物通过。切项目仍有 Moment 错 pj 的实际 422，外部候选已由红转绿，未应用。A 含普通 SVG 的画布导出仍被拒绝；历史计算编辑/完整冷迁移/N5/N4/M1–3 未完成。详细安排见 [NEXT_PHASE.md](NEXT_PHASE.md)，证据见 [PROGRESS.md](PROGRESS.md)，完整范围见 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。

以下可整体复制：

~~~text
继续完成 Tavotto 的 SVG 计算图表编辑，以最终可用及现有功能不回归为目标。复用原轴/数据模型、编辑器、writer、Preparation/runner、RenderCore/pdfbackend、legendmodel。自主实现、修复、验证，在当前同一分支按功能分别 commit，常规可逆步骤不逐批等确认。

一、恢复实际状态，不重做已完成工作
唯一实施树 E:/0 - Project/Tavotto-svg-r00，分支 zixu.wang/svg-computational-r00。2026-11-07 12:17复核起点HEAD f69fa488，最近产品提交135aba94；状态文档可能已有后续commit，以实际HEAD/diff为准。原E:/0 - Project/Tavotto仍clean main@69bcafae、未合入；证据根 E:/0 - Project/svg-review-20261106。
先读docs/implementation/svg-computational-editing/PROGRESS最新checkpoint、NEXT_PHASE及改动目录AGENTS/规则/ADR，核Git身份、代理所有权和实际live句柄。保全部未提交代码、已安装目标和首红；不新建/切branch/worktree，不reset/stash/git add .，不push/PR/合并/发布。
归属8f4985ee、归档后端135aba94及此前功能保留。归档前端和N3e安装/采用/取消后继续编辑仍未功能提交；api.ts/规则有两功能混合hunk，定向提交逐项区分。

二、当前资格与边界
n3f-preview-final/status-review-source-qualification.json：前端受影响联合66文件1273pass/0fail/error/skip；正式i18n/tsc-b/Vite/Tailwind/包内同步、playground/widget与两个check均exit0，1500前端源零漂移。不是完整回归，重叠历史组不相加。未改源不重复跑绿组，P0改源后建新窗口。
n3f-snapshot-preview/freeze.json：后端新37/旧受影响368分别全绿、Ruff0、3反证目标红，已135aba94。归档显示不授予历史计算编辑/重建许可，原当前编辑/导出撤旧门保持。
正式包n3f-mixed/attempt-05：65661B/35成员/双画布11对象/5完整ref/2个INPUT图8输入附件，SHA381625a558caa6ed62a0841bf8fc1505070567d1bb25bf082e249949ff865f2c。native100pt与页面放置55mm分开。installed-calibrated-live、initial-calibrated-live严格exit0，唯一初次PUT39。不重生成/重装掩问题；旧attempt04/167件/原2PUT首红保留。
真实图内12pt→undo7.8→redo12，PUT95/109/113全body仅目标linewidth/updatedAt变，命名保存成功。五归档SVG wire→可见DOM Blob字节一致；Timeline/ExportDialog实际DOM PNG五ref/14观察与原PDFium逐RGB通道全等、容差0。ui-readonly-proof/png-dom-proof保七个/三个反证。
原CDP七条response.body空Buffer没有恢复；实际DOM已用Blob是独立新证据，不把独立POST当原响应字节，png-observer-review保工具边界。
B画布真实PDF/PNG/报告已出，独立PDFium/PIL：180×130mm、value6三处、红/红/绿，PNG4252×3071/600dpi。A含ordinary.svg的整版PDF/PNG实际拒绝，须补原SVG合成，不能标不适用或要求用户预转PDF作为终点。
恢复named全文保留、AS只变writer时间戳；最终PUT364与磁盘全文/revision闭合；旧稿保留、科学/module计数4/4。但A→B两次各3条预览错用B而422，跨项目尚未验收。

三、旧服务已终局，复验新生命周期
n3f-ui-calibrated浏览器session n3f-calibrated-20261107正常close0；exec28051/child4464终局0，launcher55348/child4464消失、24079无监听。停前实际HTTP11bundle、1789源末核零漂移，见stop-bundle-verification、service-exit、review-closed-window.json。勿续等终局session。
attempt05目标和n3f-ui-calibrated/data已存在；旧fixture.py serve要求目标不存在，禁止直接重跑。建新外部生命周期/身份/端口，明确从已有安装和持久状态继续，核当前源/实际HTTP产物。不重装覆盖、不重新采用原包正文、不清状态绕问题；独立fresh安装才用新目标。
UI期间冻结源/build，主代理独占真实浏览器；新snapshot后再用ref，观察器只读，不注入store。正常停已核身份的自有进程，停前末核HTTP后解除冻结。

四、第一批P0：writer与Moment同源地址
读n3f-owner-second-review/REVIEW.md、validation.json、moment-owner-candidate.patch。A=1f4c864a2576、旧B=4a9b09ec21fb。adoptSteps已pinDocumentOwner，writer正确按A保存；flushAutosaveNow捕获Moment读currentProjectId(B)，组成A正文+B地址。
原adopt→writer→Moment→renderer→API受控回归baseline exit1；外部AST四行候选exit0，三条预览仍发送且均A、原PUT全文不变。apply-check0但未应用；用例只量离开autosave三条，其他三处须正式专项，不能冒产品通过。
审查后最小落地四处capture第二参：本次owner、address.projectId、scope.pj、scope.pj。复用本次保存地址，不改切项目顺序/queue/recovery gate/timeline gen，不关闭snapshot、不放松422、不回全局兜底。
把原消费者回归入库，补原保存/命名/冲突/空稿票据、离开迟到/ABA、chart/fullref/普通媒体；判同次保存正文与捕获地址。按新SHA跑受影响联合、正式/派生构建；新生命周期真实A→B→A无错pj、离开图正常、最新稿/旧稿/计数保原。独立commit fix(previews): bind save moments to the document owner。

五、第二批P1：现有功能收口提交
归档前端保完整ref+pj/owner+原序patch键、同名A/B/迟到隔离、SVG仅img/Blob上下文、不伪造manifest；主画布/缩略图/版本/导出预览/离开捕获均保。新P0源资格/UI后独立commit fix(previews): display explicit archived Figure references，不混N3e wrapper。
N3e两入口/Dialog/API/单writer复用已有实现和取消恢复修复；n3e-install-review已有4文件95pass、7新回归、3反证。当前源真实验取消/确认迟到/重开/跨项目最新稿，不重复建立adopted工作稿。独立commit feat(packages): adopt installed charts through the document writer，带ADR/规则/翻译；状态另交。

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
