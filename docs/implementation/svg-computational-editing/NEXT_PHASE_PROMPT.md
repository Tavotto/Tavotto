# SVG 计算图表编辑：下一阶段无人值守提示词

2026-11-07 14:05复核：产品HEAD35adee6d，N3e真实取消/迟到确认、保存与两种继续已完成并提交。P3画布/原图SVG转换已入工作区，新28专项、overflow回归2项及前端70文件1514项通过。留白/标注多格式来源一致性仍待正式修复，OUT最终9项通过、原相关合同55通过/1跳过，补丁和报告已交接、root apply-check0。下一步为P3修复/正式验证/提交，再做历史来源和冷恢复、N5/N4、M1–3。详见[NEXT_PHASE.md](NEXT_PHASE.md)、[PROGRESS.md](PROGRESS.md)、[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。

以下可整体复制：

~~~text
继续完成Tavotto的SVG计算图表编辑，以最终可用和现有功能不回归为目标。复用原轴/数据模型、编辑器、writer、Preparation/runner、RenderCore/pdfbackend、legendmodel。在当前同一分支自主实现、修复、验证，按功能分别commit；常规可逆步骤不逐批等待确认。

一、恢复实际状态
唯一实施树E:/0 - Project/Tavotto-svg-r00，分支zixu.wang/svg-computational-r00。13:53产品HEAD35adee6d；文档commit可能随后前进，以实际HEAD/diff为准。原E:/0 - Project/Tavotto仍clean main@69bcafae、未合入；证据根E:/0 - Project/svg-review-20261106。本次没有fetch，不称远端最新。
先读PROGRESS最新checkpoint、NEXT_PHASE和改动目录AGENTS/规则/ADR，核Git身份、代理所有权和实际live句柄。保全部未提交代码、已安装目标及首红；不新建/切分支或worktree，不reset/stash/git add .，不push/PR/合并/发布。
归属8f4985ee、归档后端135aba94、归档前端e2c026fa、Moment归属c9e2980d、N3e35adee6d及此前功能保留，不重做。当前产品WIP是P3完整16文件候选及overflow修复/新测试，N3e已没有待提交实现。来源一致性app修复仍在OUT，先核最终交接再集成。
产品方向始终为轴模型、显式原始数据绑定、可信Python/绘图来源恢复。普通SVG缺失的科学信息由用户确认绑定补充，不能从图元拖动推断原数据或伪造来源。

二、当前资格与边界
n3f-svg-production/frontend-first-exit.json和XML：当前前端70文件1514pass/0fail/error/skip，28.43秒，1502前端源及测试SHA与当前一致；status-review-20261107-1400/audit.json已复核。原owner-final的1320联合及六组正式/派生构建属于N3e源码窗口，不能当P3新build，不能与1514累计。
正式production-targeted-first.xml新28pass，production-overflow.xml新2pass；广泛后端、P3正式/派生构建、新HTTP待验。源未变不重跑已绿组。Python先Ruff再pytest，正式前端类型检查走项目build，不用tsc --noEmit冒通过。
正式包attempt-05保65661B/35成员/双画布11对象/5完整ref/2个INPUT图8附件，SHA381625a558caa6ed62a0841bf8fc1505070567d1bb25bf082e249949ff865f2c。native100pt与页面55mm分开。保A/B/C/D安装、原data/config、AS/命名稿，不重新生成/安装/采用包正文掩盖问题。
原真实A图内12→undo7.8→redo12pt、三次保存仅目标linewidth/时间变；五归档SVG HTTP→可见DOM字节全等，版本/导出14个DOM PNG与原PDFium逐RGB相等。七条原CDP二进制body空Buffer仍是观察器边界，不能伪称原wire已补齐。
B旧窗口PDF/PNG/报告已独立核180×130mm、value6三处、红/红/绿，PNG4252×3071/600dpi。A含ordinary.svg的旧HTTP导出被拒；新P3已接线但还未正式HTTP复验，不能用单SVG专项代替完整画布。
最新截图仍有左/下刻度与部分标题裁切，N5待办；它与已修的CSS越界是不同问题。History render500缺来源仍归P2，不能写零console或完整冷迁移已过。

三、已完成安装与项目切换，不重复实施
原跨项目422已由Moment消费本次writer owner修复，真实A→B→A六条close/AS均正确A/200，全文/revision/旧稿与计数保持；原失败保留。
N3e已提交35adee6d（17路径），含两入口/Dialog/协调器/API及ADR/规则/翻译/待发说明。D实际install90/201、唯一初次PUT137/200；其后真实确认GET200暂停→UI取消→原APIResponse原样释放，编辑undo/redo、命名保存、同项目继续保undo、跨项目取最新PUT256而非初次137均通过。见n3f-mixed/n3e-cancel-confirm-proof/FINAL_REPORT.md/qualification-final.json。
C首次工具误中保存前GET404，未构造取消时序，保失败；C/D两个目标均已存在，不能重装冒fresh。D native5→8→5→8→5→8，13pt因原max8×页面比例正常钳为12.47pt，不是数值bug。AS/named字节与修订、binding clean、A/B旧稿和科学/module/argv计数各4保持。
最新n3e-ui-cancel-confirm浏览器124-normal-close0；exec50760/child76164终局0，launcher23968/child76164消失、24099无监听，停前1790源/11HTTPbundle核验通过。owner-resume及calibrated旧窗口也已终局；不能续等旧句柄或直接重启一次性serve。

四、当前第一批P3：统一来源、正式验证、按功能提交
完整static-svg-canvas-and-original.candidate.patch（16文件，SHA b9346e6767100843009ba3bd5fada024b3a2902283b79262e9d7b75c5f2240f6）已应用，旧15文件不要再套。沿原SourceResolver/FrozenSource/IR/ExportRequest/job接原图与画布，保单一svgasset安全校验、源SHA、字体/资源、文字/透明/几何，不另建writer/runner。
读n3f-svg-compose-independent-review/REVIEW.md，两条实际P1：CSS overflow:visible越过物理frame已用原外层clip修复，正式两回归绿；有pad/annotations的原图多格式仍分次冻结，可成功混交PDF红/PNG蓝，正式app待修。
读已交接的n3f-original-source-consistency/qualification.md/.json和original-composition-one-scene.patch，SHA256 34272c622af365f91a142114952594aa4c6b868532bfff6f8d624a752515b6de。root已核app基线642f968712b49b3fd4434b70f3a983ac9cd94618bde16a471cd5ac3128b26961及apply-check0；仅改app._export_produce_original_annotated和新tests/test_original_composition_sources.py，正式未应用。下一步审查两路径后应用。最终新9项pass/0skip；原静态7回归在正式源7红，chart混合EPS共享源反证原实现1红/EPS-only对照1绿。原annotations/pad/chart EPS三文件55pass/1skip，补丁/报告已封口。唯一旧skip硬编码/usr/local/bin/gs，不能写成全部通过，也不改测试掩盖；本机可用GS的实际新产物用例另核。
核最终基线SHA/diff/apply-check后正式应用，不等待用户逐批批准。所有非EPS原图格式共用同次冻结和scene job；chart EPS需要内部SVG时同次产生并过滤内部输出。保物理尺寸、xy DPI/native pixel grid、pad/标注z序、原EPS bound/live/existing_only和逐格式失败。三种静态来源家族在首份canonical后改写自有源，PDF/PNG/TIFF/SVG仍同版本或明确失败，不能成功混交。
先Ruff、新9行为及原留白/标注/尺寸/EPS合同；稳定后冻结后端跑RenderCore全组、旧facade/export/compose、SVG安全/配对/退休扫描。OUT/n3f-svg-production/qualify.py可记录命令/exit/XML/源SHA；backend选集原前缀不含test_original_composition_sources.py，先将该新文件显式加入并冻结选集，后端跑时不改源。前端1514对应源未变则不重复。
后端通过，再做正式scripts/build_frontend.py和playground/widget构建/check，取得当前P3新回执。读n3f-svg-export-ui/PREPARATION.md，以现有A/原data/config、新24109/session n3f-svg-export-20261107正常CLI认证启动；不install/adopt/reseed。先核当前回执再freeze，不能借旧1790源资格。
真实UI走validate→start→job，导出A完整混合画布及ordinary.svg原图的PDF/PNG/TIFF/适用SVG；覆盖无pad/有pad/标注、多实例CSS/ID、crop/rotation/opacity。独立PDFium/PIL读物理尺寸/文本/点位/颜色/透明，核原对象/fullref/计数和持久稿。新SVG分支用可用Chromium/Inkscape独立验证；未执行精确记未验，不冒Office互操作。
补原规范/ADR/待发说明；正常关浏览器、认证shutdown/wait、末核源/HTTP bundle及精确父子PID/listener。按完整依赖分别提交画布合成、原图转换功能，相关安全修复和必要测试同交，状态文档另交。gradient/mask/pattern/marker等继续M2，不标不适用、不空白成功、不静默整页栅格化，不把专项当全部SVG已完成。

五、第二批P2：历史计算编辑与原INPUT冷恢复
读n3f-edit-source-prototype/REVIEW.md及n3f-snapshot-authority/third-path-review.md。同receipt活Figure编辑/undo零重跑已有实验；peek后直接override可lazy build，Workerd unknown_session可透明重连，必须结构守卫。
safe候选n3f-edit-guard-candidate已有Python24/Rust3项及真实safe实验。native候选n3f-edit-native-guard-candidate已有native30+wire7项、四反证、真实一次入口及Python3.10标准库闭包实验。读最终REVIEW/source-release/handoff；native-delta-after-safe.patch与combined-safe-native-with-tests.patch二选一，不得两份都套。候选未应用，组数不累计为产品资格。
正式接专用render_existing，贯穿parent真实dispatch/worker实例、原native锁/屏障、Workerd重连和child实际execution identity；旧端忽略未知field可隐式build，单加require_built不足。保safe/native/legacy原路径。
接app正常认证、完整fullref/receipt及持久编辑来源，贯穿首改/撤销/保存/包/重开；输出figureSnapshot清除不丢来源，不退同名当前图。合同扩展同步前后schema/包闭包/ref遍历/迁移和request/renderKey/want/last/latest/recent/mounted/exact，引用不授予执行许可。
冷态复用load_input_archive、PreparationPlan/PreparationService、safe spec/runconfig/工作目录和原runner，核精确source/data/argv/rc及当前授权；INPUT比对视图不是可执行目录。pair独立几何/像素合格再进入原编辑，取消回收，不建第二runner/store/writer，不静默执行陌生代码。
验证safe/Workerd/native热编辑不增执行，冷/错来源零隐式执行、取消/迟到、普通Python原样重建。来源合同/执行守卫/真实消费者/冷准备按完整功能commit，不能只接协议就称历史编辑完成。

六、第三批P2c：完整冷迁移与真runtime
P3及历史来源/准备链就绪后再统一冷迁移。仅清已核绝对路径/归属/清单的自建旧源和派生cache；保新根、包、INPUT、AS/命名稿、runconfig/config、绑定/打开意图和layout-sessions(.bak)，不全清含AI history的data/cache。
结束旧父/浏览器后按清单处理，再新父/新浏览器验证双画布/两个历史、数据范围和样式、undo/redo、保存/再开/版本恢复及适用导出。保首红、修具体缺口，不循环重跑同一失败或提前标N3f全过。
另验n3f-runtime-kind/attempt-02/真正runtime家族.tavotto的实际fileKind/runtime/fallback/编辑冷开；12成员包不替代35包，混合包runtime来源对象实际pdf不能冒覆盖。

七、后续N5→N4→M1/M2/M3
N5读n5-tick-clipping-review/prototype/IMPLEMENTATION.md及validation。OUT13后端/92前端/14文件overlay不是产品。接S/F同源、同次render_frame/native2与旧1兼容，闭合schema/请求/cache/exact/frame采用事务/F→S几何/C确认/原图画布多页导出/ink-page策略。守CSS/percent/clip/use/PAR/旋转，不缩字体/轴/数据或偷改页面；x10负框不证明glyph越页，x5才有实际字形越界反证。
N4复用legendmodel，显式entry→series/custom；保同名同色、复合句柄、手工文字和未绑定注释，编辑/undo/保存/冷开/导出一致。
M1双系列+图例+注释完整数据/轴范围/样式→undo/redo→保存→空派生cache冷开→适用scope×format真产物，画布EPS仍eps_not_for_canvas。
M2按SG00–17/A/B/C逐格补图族/多轴/投影/文字与组/use、写回/预检/出版及CLI/MCP/playground/桌面/发行。替代engineTransport归档读取仍缺，构建绿不等入口已实现；缺实现不标不适用。
M3最终冻结后完整原Python/旧项目回归、等价性/CompatBench/E2E/性能及支持矩阵平台安装物；缺平台条件明确未验，不把Windows/浏览器结果当跨平台资格。

八、无人值守执行协议
每批核状态/规则→实现真实消费者→必要反证→受影响验证→实际exit/源SHA/diff→定向功能commit→日期checkpoint→下一项。保既有多代理授权，只在任务独立且收益明确时并行；每文件一个写入者，root统一合同/浏览器/集成/提交。先核既有代理，不重复派工；来源一致性候选也已交接，所有本轮子任务自有测试/服务句柄已终局。
长检查前后/提交/约30分钟更新PROGRESS：HEAD/diff、所有权/句柄、命令cwd/env、actual fail/skip、实物SHA、首红、未验项与下一条动作。UI期间冻结源/build，正常认证、遥测关闭、观察器只读，禁止注入store或伪造成功JSON；新snapshot再用ref，只结束已核身份的自有进程。
局部失败保原日志→复现→修复→定向复验并继续，不删断言、放容差或改golden消红。同类问题修两轮仍扩散时按第三路径规则审结构。缺外部条件只挂起依赖项，继续独立就绪项；不反复计划或跑旧绿组代替实现。
写回仍prepare→完整重放/几何/像素verify→commit，失败409原件零改。保认证/沙盒/隐私/零PyMuPDF；运行数据走config.data_dir。无人值守为活跃会话自主接续，未创建常驻调度器；M1/M2/M3全退出前不宣布最终可用或目标完成。
~~~
