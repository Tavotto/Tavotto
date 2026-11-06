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

2026-11-06 18:17状态：附件消费者2452d8fc、后端v4保存/恢复5abb9a7d已分功能提交；真UI删源编辑/导出已验，v4后端20项和旧312项通过（另20既有平台skip）。N2前端格式/回执/恢复/版本仍是中间稿，阶段build不代表最终资格。共享打开顺序合同已冻结并进入生产实现；package/EPS叶子107/36项已验但尚未接app。先核实际HEAD/PROGRESS，不从旧app索引blob或31文件hash恢复当前代码，不重做N0/N1后端基础。

二、保留已完成成果，不从旧缺陷重做

最新独立提交：
- 242cabc0：共享native API/前端render与PNG、manifest/revision，复用现有图内编辑器。
- c2fd47d5：生产原图/标注/留白/画布/多页冻结native profile与完整原序overrides，实际SVG输出v2；legacy缺省和无chart合同保留。
- 53393810：inspect恢复完整v2编辑，向导一次事务确认；当前面板编辑优先、迟到状态防串；后端重绑科学归属guard。
- 8e6c399a：最终manifest唯一svg_targets和同源DOM镜像，跨父/split/errorbar/colorbar多目标预览与取消；不伪造复杂clip/member slots资格。
- 2a40fec9：新增实际PNG/TIFF生产尺寸/PPI/alpha/文字/像素测试，未改变产品逻辑。
- 5b2b1403：chartprocess复用pool/环境lease/DesktopProcess ownership；chart类admission、同deadline输入输出、预算、退出码及组回收接入两个app入口。
- ae064662：buildProject只替换实际安装的首个活动画布位置，保护重复/缺失/空ID文档的非活动内容。
- 83f4ffbf：documentformat目录/格式叶子与layoutsession v4 namespace。只是叶子，不是正式v4保存已实现。
- 2452d8fc：精确INPUT附件进入全部渲染/PNG/缩略图/timeline/重建/导出消费者，实际UI删源后编辑和生产产物已验。
- 5abb9a7d：v4正式命名/autosave/session/versions/thumbs隔离及精确INPUT冻结；20项含真实生产v2和新父进程恢复通过，旧格式规范化回归已修。

此前层序/CSV/转换/连续文字/来源配对/附件核心全部保留，见PROGRESS。尤其029dfac2的profile/v2闭式版本、3236307c的native物理坐标、bbbffed1的精确INPUT附件和ea0a7027的字体/TeX边界不重做。

当前真实效果：点选曲线、Inspector线宽2→6和X上限10→20、undo/redo达到exact；实际ExportDialog导出v2后，新浏览器会话新文档恢复7条原序编辑，确认/撤销/重做history 1→2→1→2，进入既有图内编辑后线宽7、X刻度20.0。该UI使用原后端，不能拼称正式保存/冷父进程UI全链。截图仍有未联动旧图例和“仅版面”统计，M1未通过。

2452d8fc消费者提交时18文件700项、正式build/i18n/9反证通过，playground重建/check指纹77d32934d571f6a6；后端附件13项通过，真UI及PDF/PNG/SVG产物独立检查通过。N2后续已改相关前端，不将700或更早983资格转给当前dirty树。v4最后20项42.167秒、旧312通过/20skip74.960秒，三组进程内反证与legacy规范化独立定向复核通过。不同范围/时刻不累加。desktop既有PID回执失败仍在，最终全产品/平台/性能资格未完成。

三、依赖队列，完成一项即自动选择下一就绪项

1. N0已完成；后端N1基础也已验，不重复实现。根代理继续唯一app/ADR/进度写入，N2代理独占前端格式/保存/恢复/版本消费者，其他代理叶子按所有权并行；按功能定向暂存，不混中间稿。

2. N1/R05保留已验保存合同，补跨namespace共享打开意图序。
   复用已提交documentformat和using_namespace；旧2/3缺省不变。显式/api/documents/v4/*、tavottofile/documents/v4、_autosave/v4、v4的versions/thumbs；复用FORMAT1信封、原锁/atomicio/既有view与认证，不移动FigureSnapshots根，不新建历史数据库。
   named/autosave/summary/清理/owner/last/candidates/versions/thumb/rebuild及别名已接通，20项已验；v4不借旧目录。chart只在v4跳过Figure捕获，旧2/3未知chart字段仍核验并清除无归档FigureSnapshot。未实现包安装前不挂空壳package别名。
   各namespace的at没有共享总序：回拨和迟到pending晋升都可让max-at选错旧稿。复用同layout-sessions文件/锁/atomicio，以版本化共享打开意图+实际指针witness解决；seq只分配明确打开，迟到保存仅更新witness，旧程序不更新ledger时须检测stale，不用mtime/at猜。先冻结具体API/wire再接前端发现；不得以永远needs_user替代正常自动恢复。同步全失败导出仍沿既有HTTP500。
   已冻结wire：仅显式GET include_open_intent=1增加open_intent，旧默认dict不变；ok含version1/status/sequence/snapshot/latest，latest为{document_format,doc_id,pointer}或null；stale/untracked含实际双ns指针snapshot而不猜latest；unavailable不猜snapshot。两GET快照相同才采用共享latest，变化有界重读，正常可信状态自动恢复。set_last明确打开同锁同写分seq，重复pending重开也分；commit_slot不分seq且不洗白stale。清指针同步见证、清latest置null。
   自动补推notifyDocumentPersisted/confirmRestored使用PUT body repair:true；可信latest同地址只原seq晋升或幂等确认durable；异址/null回superseded，stale/untracked/v4无账本回unconfirmed零写，前端保待确认不假ack；future仍拒写。纯legacy-only原行为保持。明确用户alternative按(format,id)分新intent；自动发现/恢复安装不分序，迟到正文不能抢回另一格式的新文档。

3. N2–N3/R05接前端保存、冷恢复和包安装搬迁。
   chart_source存在时render/PNG/export只从校验附件读，缺失/篡改不回退漂移原文件。保存锁内验证→严格冻结整份INPUT（含v2外层）→实际重放→读回→单次原子正文提交；不清引用后假成功。保持普通FigureSnapshots既有策略。
   一个documentStore/对象模型；明确legacy/v4格式，活动画布沿用原模型，仅持久化边界schema3/4。首次绑定及粘贴/恢复计算图共用升级入口，产生新working documentId，保留旧文件/历史和project/canvas/panel身份；不得清空当前undo后宣称无损。undo编辑不能降格式。
   format贯穿API地址、本机键、保存队列/revision、project文件绑定、恢复门、版本/thumbs；旧缺省URL/键/实参数量不变。新启动没有本机缓存也必须发现后端v4 namespace的last/candidates，不能只读legacy。失败保持needs_user，不悄悄选择更旧文档。
   保存回执是五字段数组{canvas_index,object_index,canvas_id,panel_id,chart_source}，响应document_format:'v4'，GET raw schema4及X-Tavotto-Document-Format:v4。context保存实际发送的深拷贝正文、pj/documentId/loadSeq/format；按索引及原id核对，sourceId/sceneDigest一致，再比较当前完整输入{fileId,fileKind,chart,原序overrides,旧chartSource}。重复index pair整份拒合入，重排/插入/换源/改模型/重复(gid,prop)编辑顺序反转只略过受影响项；不能按panelId建Map或新增ID唯一性限制。不加undo/循环autosave，迟到回执不能覆盖新输入。参考r05-frontend-persistence-audit.md，不把审计当已实现。
   包沿现有容器显式安装经验证附件后单次正文提交，不任意extract，不把package/open只读检查当安装。实际UI保存→删除/漂移原源→清缓存新父进程→重开/继续编辑/导出；移盘/中文路径/并发冲突/断写/缺附件/版本恢复都要实测。

4. R02/R06补产品能力，独立文件可并行。
   图例/标题/数据联动和readiness共用明确科学归属；不能按颜色猜或硬编码可编辑。逐对象重绑迁移保留同归属编辑，重排/删对象不套错；现guard拒绝时保留草稿，不默清。
   多DOM基础已完成，下一步成员binding/paint placement解决source compound/decor/native的真实偏序冲突；不搬动装饰造容器。参考native-clip-geometry-design.md的成员配对区域及运动归属，SVGIR Path/ClipUnion表达祖先AND/子形状union/fill-rule/物理坐标；距离/框选/宽描边/高亮共同完成，不能只gate查询点inside或强改composed=true。该报告14案例/37像素/31覆盖点是外部原型，未生产落地。

5. R07/R08完成分享、格式和M1。
   用户可选纯外观或完整模型/数据/编辑包，纯外观不暗带隐藏数据。原图EPS参考native-eps-implementation.md：生产originaleps/epscontainer叶子36项含Ghostscript独立像素已验，继续接facade/pdfbackend、固定owned worker及app/入口；不往用户worker新增HB原生依赖。画布eps_not_for_canvas保持；不伪PDF、静默丢装饰或把矢量栅格化凑通过。
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

18:17自有服务复核：旧43356已停止，后端33104（17:17:32启动）/session79201/23984仍加载N0源码、尚无N1新路由；Vite11676不存在、23983无监听，避免N2中间稿HMR。浏览器4468/78620在。先核实时PID/创建时间/命令，不照旧数字杀；source-ref-browser/server.py复用自身隔离fixture，历史native项目/portable不重seed。截图在证据根output/playwright。未启动新整夜调度器。

现在先核实际树/PROGRESS/代理，保留2452d8fc及已验N1后端，收敛共享打开意图序和N2前端真实保存冷开，继续N3包/N4图例迁移/N5复杂几何/N6分享EPS与M1/N7 M2/N8 M3。不要从旧索引/hash重做N0，不停在重新生成计划，不把本批绿灯当整个目标完成。
```
