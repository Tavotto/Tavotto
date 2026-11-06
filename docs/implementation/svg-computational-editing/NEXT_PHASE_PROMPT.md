# SVG 计算图表编辑：下一阶段无人值守提示词

以下内容可直接复制继续实施。详细工作、依赖与退出条件见 [NEXT_PHASE.md](NEXT_PHASE.md)，实际进度唯一台账为 [PROGRESS.md](PROGRESS.md)。最终范围保持 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) 的SG00–SG17，不因M1或一夜结束缩减。

```text
继续完成 Tavotto 的 SVG 计算图表编辑。按整夜无人值守方式持续实现、验证、修复、按功能提交，直到最终用户可用且既有功能无新增回归。已授权多子代理处理独立模块；根代理统一共享契约、集成和提交，一个文件同时一个写入者。常规可逆步骤自行推进，不逐批等待我确认。

一、只续接现有实施树

工作树：E:/0 - Project/Tavotto-svg-r00
分支：zixu.wang/svg-computational-r00
原目录 E:/0 - Project/Tavotto 是干净main，尚未包含实施功能，不往那里覆盖或自动合并。
稳定文档：docs/implementation/svg-computational-editing/{PROGRESS,NEXT_PHASE,NEXT_PHASE_PROMPT}.md
证据根：E:/0 - Project/svg-review-20261106

不新建/切换分支或工作树，保留全部提交及未提交成果，不reset/stash覆盖草稿。该要求覆盖旧文档的新建指令。先读当前AGENTS、最近子目录规则、对应ADR、同源对/判据/第三路径/验证要求，再核对实际HEAD、diff、Git身份、解释器import源、已有代理和进程。

2026-11-06 15:32最近功能HEAD=2a40fec9；后续文档和实现可继续前进，不回退到这个SHA。desktopproc.py及新chartprocess.py当时是现有代理的生命周期草稿，尚未接app/冻结。先收取该代理最新结果，不再启动另一位写同一runner。

二、保留已完成成果，不从旧缺陷重做

最新独立提交：
- 242cabc0：共享native API/前端render与PNG、manifest/revision，复用现有图内编辑器。
- c2fd47d5：生产原图/标注/留白/画布/多页冻结native profile与完整原序overrides，实际SVG输出v2；legacy缺省和无chart合同保留。
- 53393810：inspect恢复完整v2编辑，向导一次事务确认；当前面板编辑优先、迟到状态防串；后端重绑科学归属guard。
- 8e6c399a：最终manifest唯一svg_targets和同源DOM镜像，跨父/split/errorbar/colorbar多目标预览与取消；不伪造复杂clip/member slots资格。
- 2a40fec9：新增实际PNG/TIFF生产尺寸/PPI/alpha/文字/像素测试，未改变产品逻辑。

此前层序/CSV/转换/连续文字/来源配对/附件核心全部保留，见PROGRESS。尤其029dfac2的profile/v2闭式版本、3236307c的native物理坐标、bbbffed1的精确INPUT附件和ea0a7027的字体/TeX边界不重做。

当前真实效果：点选曲线、Inspector线宽2→6和X上限10→20、undo/redo达到exact；实际ExportDialog导出v2后，新浏览器会话新文档恢复7条原序编辑，确认/撤销/重做history 1→2→1→2，进入既有图内编辑后线宽7、X刻度20.0。该UI使用原后端，不能拼称正式保存/冷父进程UI全链。截图仍有未联动旧图例和“仅版面”统计，M1未通过。

当前相关前端34文件983项通过，正式tsc-b/Vite/Tailwind、i18n及playground --check实际exit0，指纹931a34814cf2316a。native API21项、focused恢复4项、profile/归属46项、多目标47个唯一核心、生产SVG/PDF9项及PNG/TIFF2项为不同范围/时刻，互有重叠，不累计为最终总数。旧缺环境skip不算通过，完整旧功能/平台/性能资格未完成。

三、依赖队列，完成一项即自动选择下一就绪项

1. R03先收敛现有生命周期草稿并接入app两个入口。
   固定python -B -m tavotto.engine.svgchart；解释器和环境仍经pool权威。启动前envlease，确认整个进程组退出才释放；unknown保留lease/admission等待owner清理。请求写入、输出读取、退出/取消共用deadline；预算从SVGBudget/既有JSON预算派生，stderr有界tail；exit0先于解析成功JSON。
   chart active/unknown容量动态消费pool.max_alive，仅称chart任务admission，不宣称全产品总进程cap；本批不重写resident/native/one_shot。测试真实worker、mutating互斥、满容量恢复、不读stdin、大stdout/stderr、exit37成功JSON、Windows继承管道后代、超时取消与未知状态。根代理唯一写app.py，改引擎后重启自己的服务再验。

2. R05实现v4显式持久化与旧版隔离。
   先冻结唯一格式/目录模块，旧2/3缺省行为不变。候选/api/documents/v4/*、tavottofile/documents/v4、_autosave/v4、v4独立versions/thumbs；复用FORMAT1信封、原锁和atomicio，不移动FigureSnapshots根，不新建历史数据库。
   覆盖named/list/read/autosave/summary/versions/thumbs/rebuild/session/owner/last/package等所有消费者；_doc_objects/草图不能把4当2漏掉对象。路径每层拒绝junction/symlink。
   会话继续同一layout-sessions文件/锁，document_namespaces.v4保留旧写入兼容；不能只给project_key加前缀，旧prune会删全局owner。旧程序真实读写/清理反证是退出条件。

3. R05接精确INPUT附件、前端保存和冷恢复。
   chart_source存在时render/PNG/export只从校验附件读，缺失/篡改不回退漂移原文件。保存锁内验证→严格冻结整份INPUT（含v2外层）→实际重放→读回→单次原子正文提交；不清引用后假成功。保持普通FigureSnapshots既有策略。
   一个documentStore/对象模型；升级新working documentId，保留原命名文件/历史和project/canvas/panel身份。保存回执按完整画布/面板/source和加载代次合并，不加undo或循环autosave；undo不能降格式。
   包沿现有容器显式安装经验证附件后单次正文提交，不任意extract，不把package/open只读检查当安装。实际UI保存→删除/漂移原源→清缓存新父进程→重开/继续编辑/导出；移盘/中文路径/并发冲突/断写/缺附件/版本恢复都要实测。

4. R02/R06补产品能力，独立文件可并行。
   图例/标题/数据联动和readiness共用明确科学归属；不能按颜色猜或硬编码可编辑。逐对象重绑迁移保留同归属编辑，重排/删对象不套错；现guard拒绝时保留草稿，不默清。
   多DOM基础已完成，下一步成员binding/paint placement解决source compound/decor/native的真实偏序冲突；不搬动装饰造容器。复杂clip复用SVGIR Path/ClipUnion表达祖先AND、子形状union、fill-rule和物理坐标；前端命中/高亮共用。圆外/孔洞/union反例必须真实拒绝命中，不能强改composed=true。

5. R07/R08完成分享、格式和M1。
   用户可选纯外观或完整模型/数据/编辑包，纯外观不暗带隐藏数据。原图EPS做真实矢量适配实验，画布eps_not_for_canvas保持原合同；不伪PDF、静默丢元素或把矢量栅格化凑通过。
   真实UI双系列+未绑定注释：导入绑定→数据/范围/样式/图例→精确选择和undo/redo→正式保存→清缓存新父进程重开→原图/标注/混合画布/多页各格式；独立读取SVG/PDF文字矢量、PNG/TIFF尺寸/PPI。全部退出条件满足才M1，通过后立即继续。

6. R08–R12/M2与R13/M3保留完整范围。
   A可信来源采用、文字/分组/use实例、剩余族/统计/色阶/多轴/投影、事务写回、预检/出版/样式、CLI/MCP/playground/桌面/发行逐格SG00核验。普通SVG不反推原始Python，不按相邻同名.py猜来源。写回prepare→verify全量冷重放+几何+像素→commit，失败409且原件零改动。
   冻结最终候选，跑完整旧功能、等价性/不变式/CompatBench/E2E/性能与支持平台真安装物资格；缺平台单列未验证，不用mock冒充。M1/M2、时长/轮次/上下文耗尽都不代表最终完成。

四、验证、提交、失败与恢复

- 先说清断言主语：当前最终图、落盘文件、冷启动结果或真实产物。元数据、path数量、生成了文件不能替代科学/视觉/几何正确。
- Python先Ruff后针对性pytest；前端相关Vitest→正式tsc-b/Vite/Tailwind，按改动补i18n/playground和真UI。用实际退出码，skip/未执行不写通过。已通过且无受影响改动的昂贵检查不重复；最终候选完成项目规定完整验证。
- 新核心不变量做目标落点反证，错轴/删成员/迟到响应/跳verify应真失败，生产字节恢复后再验证。不删测试、不放宽容差迎合错图。
- 最大化复用现有文档/渲染/能力/属性/几何/事务/RenderCore/pdfbackend/锁/资源。零PyMuPDF、父进程stdlib、认证/沙盒/隐私和解释器选择边界不变。
- 各功能分别commit，含测试/契约镜像/受影响文档，不git add .混草稿，不squash全部，不加Co-Authored-By。根代理审stage范围，按实际验证状态提交；不push/PR/合并/发布。
- 测试配置/数据和日志隔离；只按确认属于自己的PID+创建时间/命令清理，不能模式杀或重seed覆盖产物。不破坏共享venv/node_modules。
- PROGRESS唯一进度台账；NEXT_PHASE/PROMPT维护当前安排，保留历史。每功能提交、长验证前后和约30分钟安全边界checkpoint，记录HEAD/diff、命令/cwd/环境/真实退出码/证据、代理所有权、自有PID/端口、缺口和下一具体动作。
- 产品红继续缩场景修复；同类两轮后查结构/单一权威并换反例。无变化网络瞬断最多再试一次；认证/平台仅阻塞真实依赖项，继续其他就绪任务，不改凭据或虚构通过。
- 压缩后继续同一目标；中断后先核真实HEAD/差异/进程与未结束检查，不从计划重做。提示词不是常驻调度器，不会唤醒休眠机器或恢复已结束会话；调试服务不是整夜任务。本轮未另建自动化。
- 仅全部验收完成、用户明确停止、运行条件强制中断，或所有有意义工作真正依赖无法取得的外部条件时结束。最终交付写能力/入口、功能commits、实际测试与产物、平台/待发布及未完成项，不以专项全绿称最终可用。

当前自有服务记录：后端45648/23984、Vite71528/23983，浏览器session tavotto-native-edit和tavotto-v2-restore；它们可能变化，先核实际PID/命令，不直接杀旧数字。外部native-edit-browser/server.py已能复用已有project/portable，不重seed。

现在先核实际树/PROGRESS/代理，收取chartprocess冻结结果并接入两个app入口；同时按文件所有权推进v4格式/session合同，之后完整保存冷恢复和后续队列。不要停在重新生成计划。
```
