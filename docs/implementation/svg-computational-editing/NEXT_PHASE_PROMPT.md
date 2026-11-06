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

2026-11-06 19:10状态：功能HEAD=95684f89，精确INPUT消费者2452d8fc、v4后端5abb9a7d、共享打开意图95684f89已提交。N2前端43文件已冻结：真实UI命名保存→原SVG缺席→零localStorage新浏览器/新后端自动恢复同v4 ID→继续编辑/undo/redo→PDF/PNG/SVG实际导出，独立检查通过；尚未提交。阶段810项与末改动206项重叠不累加，正式build/i18n/oxlint/23反证有日志。最新playground构建/check通过，指纹0d91b54086f7e829。冷开projectFile=null和来源422误报磁盘空间尚未修。EPS HTTP10项已通过，界面仍“无脚本”禁用；ZIP writer121项通过，未接安装。先核PROGRESS和当前树，不从旧hash恢复代码，不重做已验基础，M1未通过。

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
- 95684f89：跨格式共享打开意图、显式GET snapshot/latest和repair，默认legacy不变；新55项/HTTP15项及4+2反证通过。

此前层序/CSV/转换/连续文字/来源配对/附件核心全部保留，见PROGRESS。尤其029dfac2的profile/v2闭式版本、3236307c的native物理坐标、bbbffed1的精确INPUT附件和ea0a7027的字体/TeX边界不重做。

保留此前双曲线、范围/样式、v2七条编辑恢复和多目标预览资格。最新N2真实正式保存/零缓存冷恢复已另行完成，不再用旧消费者夹具拼称：工作ID d_kyyf7jn1，保存N2_正式保存_20261106，冷开native线宽3.02→4.03→undo3.02→redo4.03，始终exact、sourceRef不变。SVG v2模型/绑定/原序edits及374B INPUT相等，PDF100×100pt且Source keep文本保留，PNG833²及红线位置/线宽正确。证据n2-ui-{edit,save,cold,cold-edit}-result.json和n2-ui-cold-export-qualification.json。冷开命名文件绑定丢失仍未修，单曲线场景不代替完整双系列/图例/混合画布M1。

2452d8fc消费者提交时18文件700项、正式build/i18n/9反证通过，playground重建/check指纹77d32934d571f6a6；后端附件13项通过，真UI及PDF/PNG/SVG产物独立检查通过。N2后续已改相关前端，不将700或更早983资格转给当前dirty树。v4最后20项42.167秒、旧312通过/20skip74.960秒，三组进程内反证与legacy规范化独立定向复核通过。不同范围/时刻不累加。desktop既有PID回执失败仍在，最终全产品/平台/性能资格未完成。

三、依赖队列，完成一项即自动选择下一就绪项

1. N0/N1/共享意图后端已提交，不重复实现。当前三个代理已冻结/交接，不按旧表假定它们仍在写。根代理继续唯一app/ADR/进度/提交；先核43个N2文件SHA与n2-frontend-validation.json、正式build及最新playground，按N2实际合同同步受影响规则/ADR和严格同源镜像后独立commit；不混EPS/ZIP。后续重新委派时给独立范围/退出条件，一个文件一个写入者。实际HEAD可能继续前进，以当前树为准。

2. N1/R05保留已验保存合同和跨namespace共享打开意图序，不重新实现。
   复用已提交documentformat和using_namespace；旧2/3缺省不变。显式/api/documents/v4/*、tavottofile/documents/v4、_autosave/v4、v4的versions/thumbs；复用FORMAT1信封、原锁/atomicio/既有view与认证，不移动FigureSnapshots根，不新建历史数据库。
   named/autosave/summary/清理/owner/last/candidates/versions/thumb/rebuild及别名已接通，20项已验；v4不借旧目录。chart只在v4跳过Figure捕获，旧2/3未知chart字段仍核验并清除无归档FigureSnapshot。未实现包安装前不挂空壳package别名。
   各namespace的at没有共享总序：已在同layout-sessions文件/锁/atomicio用版本化共享意图+实际指针witness解决，前端已接两GET同snapshot和单一显式发送队列；seq只分配明确打开，迟到保存仅更新witness，旧程序混写检测stale，不用mtime/at猜。不要退回max-at或永远needs_user。同步全失败导出仍沿既有HTTP500。
   已冻结wire：仅显式GET include_open_intent=1增加open_intent，旧默认dict不变；ok含version1/status/sequence/snapshot/latest，latest为{document_format,doc_id,pointer}或null；stale/untracked含实际双ns指针snapshot而不猜latest；unavailable不猜snapshot。两GET快照相同才采用共享latest，变化有界重读，正常可信状态自动恢复。set_last明确打开同锁同写分seq，重复pending重开也分；commit_slot不分seq且不洗白stale。清指针同步见证、清latest置null。
   自动补推notifyDocumentPersisted/confirmRestored使用PUT body repair:true；可信latest同地址只原seq晋升或幂等确认durable；异址/null回superseded，stale/untracked/v4无账本回unconfirmed零写，前端保待确认不假ack；future仍拒写。纯legacy-only原行为保持。明确用户alternative按(format,id)分新intent；自动发现/恢复安装不分序，迟到正文不能抢回另一格式的新文档。

3. N2前端收尾提交后，优先N2.1/N2.2，再N3包安装搬迁。
   chart_source存在时render/PNG/export只从校验附件读，缺失/篡改不回退漂移原文件。保存锁内验证→严格冻结整份INPUT（含v2外层）→实际重放→读回→单次原子正文提交；不清引用后假成功。保持普通FigureSnapshots既有策略。
   一个documentStore/对象模型；明确legacy/v4格式，活动画布沿用原模型，仅持久化边界schema3/4。首次绑定及粘贴/恢复计算图共用升级入口，产生新working documentId，保留旧文件/历史和project/canvas/panel身份；不得清空当前undo后宣称无损。undo编辑不能降格式。
   format贯穿API地址、本机键、保存队列/revision、project文件绑定、恢复门、版本/thumbs；旧缺省URL/键/实参数量不变。新启动没有本机缓存也必须发现后端v4 namespace的last/candidates，不能只读legacy。失败保持needs_user，不悄悄选择更旧文档。
   保存回执是五字段数组{canvas_index,object_index,canvas_id,panel_id,chart_source}，响应document_format:'v4'，GET raw schema4及X-Tavotto-Document-Format:v4。context保存实际发送的深拷贝正文、pj/documentId/loadSeq/format；按索引及原id核对，sourceId/sceneDigest一致，再比较当前完整输入{fileId,fileKind,chart,原序overrides,旧chartSource}。重复index pair整份拒合入，重排/插入/换源/改模型/重复(gid,prop)编辑顺序反转只略过受影响项；不能按panelId建Map或新增ID唯一性限制。不加undo/循环autosave，迟到回执不能覆盖新输入。参考r05-frontend-persistence-audit.md，不把审计当已实现。
   N2.1修命名文件关联冷持久化。旧legacy在清localStorage后也projectFile=null；ADR0108已接受迁后端，ADR0096要求不进可携正文。在现layout-sessions/data_dir按(project,format,working docId)存closed binding/revision/用户编辑同步轴，仍用原锁/atomicio；显式保存/打开须传真实working ID，目标取后端规范name/file，不能按doc.name/portable project.id/mtime猜。自动保存不改目标/分新open intent，升级新ID不继承legacy绑定；dirty继续用现用户编辑轴，派生回执不置dirty。正文成功与元信息失败分开报告，迟到A回执不能盖另存B。验收A保存→改标题B且存在同名B→清缓存/重启仍写A；仅autosave后冷开保A且dirty；外部改A后CtrlS409；不同format/项目同ID不串。单独commit并同步ADR/规则。
   N2.2修保存错误信息。diskWriter把非冲突ApiError丢掉，documentStore转io，App统一“检查磁盘空间”；直接命名保存已有backendErrorText。经现SaveIssue/事件保留结构化code/params，来源422显示真正原因，网络/真实I/O用原通用提示；403/409及本机稿/原正文安全保持。独立commit，不做SVG专用错误store。
   N3先把旧api_package直接最终ZIP写入替换为已验staged_package，保持临时asset立即流入和helper吞错误也失败的latch；调用方明确预算，不能无依据给旧合法大项目加低限。随后沿现zip容器显式校验资源路径/摘要/预算、安装附件、实际重放后单次正文提交，不任意extract、不覆盖用户既有资产/脚本，不把package/open只读检查当安装。实际新目录/中文路径/移盘/并发冲突/断写/缺附件/版本恢复都要测；叶子121项不是HTTP安装资格。

4. R02/R06补产品能力，独立文件可并行。
   图例/标题/数据联动和readiness共用明确科学归属；不能按颜色猜或硬编码可编辑。逐对象重绑迁移保留同归属编辑，重排/删对象不套错；现guard拒绝时保留草稿，不默清。
   多DOM基础已完成，下一步成员binding/paint placement解决source compound/decor/native的真实偏序冲突；不搬动装饰造容器。参考native-clip-geometry-design.md的成员配对区域及运动归属，SVGIR Path/ClipUnion表达祖先AND/子形状union/fill-rule/物理坐标；距离/框选/宽描边/高亮共同完成，不能只gate查询点inside或强改composed=true。该报告14案例/37像素/31覆盖点是外部原型，未生产落地。

5. R07/R08完成分享、格式和M1。
   原图EPS可与N2.1/N3不同文件并行收尾：叶子36、公共契约/owned action102及真实HTTP10项已验。标注/留白nested SVG导致首次HTTP4失败，已通过共享SVGIR嵌套viewport/百分比/overflow/clip结构修复；新45+旧相关281通过/0skip，writer/assets/retirement105通过/41缺Inkscape跳过，两反证杀死错误实现、五份旧IR字节一致，见nested-viewport-freeze.json。先独立提交共享viewport，再EPS叶子/适配/app、统一exportroute事实与前端类型/界面；界面当前仍无脚本禁用EPS，须实际操作验证及旧Python/EPS回归。检查_chart_eps_failure的格式归属，不能所有捕获异常都标EPS。partial alpha/gradient/mask等边界仍诚实返回错误，不往用户worker新增HB。画布eps_not_for_canvas保持，不伪PDF或静默栅格化。
   之后用户可选纯外观或完整模型/数据/编辑包，纯外观不暗带隐藏数据；分别验证字段/元数据与实际产物。
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

19:06自有服务复核：后端10460（18:56:58启动、source-ref-browser/server.py、23984/session18140），Vite10168（18:51:24启动、native-edit-browser/vite.mjs、23983/session80916），浏览器tavotto-v4-cold daemon71840（18:57:08）；旧25224和save浏览器已关闭。后端载入时尚无后来nested SVGIR修复，EPS界面联验前需重启。先核实时PID/创建时间/命令，不照旧数字杀；隔离fixture只复用不重seed，保留n2_cold_chart实际产物。截图在证据根output/playwright。未启动新整夜调度器。

现在核实际树/PROGRESS/代理，N2同步受影响规则后定向提交→N2.1保存关联→N2.2准确错误→N3包；独立收尾EPS入口，再N4图例/readiness/迁移、N5复杂几何、N6分享与M1、N7 M2、N8 M3。不要从旧hash重做已验功能，不停在重新生成计划，不把本批绿灯当整个目标完成。
```
