# SVG 计算图表编辑：下一阶段无人值守提示词

以下内容可直接复制继续实施。它沿用现有本地授权；详细任务与退出条件见 [NEXT_PHASE.md](NEXT_PHASE.md)，实际状态见 [PROGRESS.md](PROGRESS.md)，最终范围仍为 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) 的 SG00–SG17。

```text
继续 Tavotto 的 SVG 计算图表编辑，按整夜无人值守方式持续实现、验证、修复和按功能提交，目标是最终用户可用且既有功能不回归。用户已授权多子代理并行，按独立文件/模块登记写入所有权，根代理统一共享契约、集成验证和提交。常规可逆步骤自行完成，不逐批等我确认。

一、只续接当前实施树

工作树：E:/0 - Project/Tavotto-svg-r00
分支：zixu.wang/svg-computational-r00
原目录 E:/0 - Project/Tavotto 是另一棵 main，不要往那里覆盖实现。
计划目录：docs/implementation/svg-computational-editing
当前证据根：E:/0 - Project/svg-review-20261106

我最新要求：不新建/切换分支，不新建工作树，保留全部已有提交和未提交成果；该要求覆盖旧规则的新建要求。先读当前AGENTS.md、最近子目录规则、对应ADR、同源契约表和判据/第三路径/本地验证要求，再核对真实HEAD、diff、Git身份、解释器源码位置、已有进程。2026-11-06 14:05最新功能提交3236307c；app.py、test_chart_native_api.py与32个前端消费者文件未提交，不是冻结候选。文档提交可能使HEAD前进，不reset到此快照，不丢草稿或自动向旧main合并。

二、按最新状态接续，不重做已修项

先读PROGRESS最新checkpoint与NEXT_PHASE第4节最新队列；它们优先于历史checkpoint。保留以下已完成提交：
- e836a8de：组合白底遮挡修复，双系列可见，12 组层序回归。
- 979a4962：真实 original/canvas/多页请求携带冻结 chart，编辑改变身份。
- 48d3d27a：declarative 来源版本与严格 PDF/SVG 配对。
- cae96f89：安全自包含包，删除原源后真正新进程重算；实际重放核对可见内容。
- f6c4a13d：CSV/TSV真实解析、精确保留quoted字段与类别x条件合同。
- 3ed06553：SVG→IR保真、ClipUnion及native声明式SVG写出合同。
- 5bc82a81：共享冻结快照进入原图/标注/画布/多页导出。
- 82b142d2：C恢复API/UI，真浏览器确认/撤销/重做、全引号CSV和特殊字符类别通过。
- f04d8f63：连续tspan整段chunk定位/锚定，157项受影响回归，含22项Chrome参考和9项PDFium基线对拍。
- 54161afd：原生manifest脚手架adapter，当前树25项通过；明确composed=False，尚非最终命中权威。
- bbbffed1：不可变INPUT SVG附件与schema4显式校验核心，当前树144通过/4既有Windows skip；默认schema仍3，正式保存尚未接线。
- ea0a7027：字体族按字面值经FontProperties选择，不能把fontconfig file模式当字体文件权限；正常SVG输出有逐字节对照。
- 029dfac2：legacy-scaffold/1和v1输出保留；native-manifest/1/v2闭式包携完整原序overrides，身份另含精确JSON摘要；profile/包88项及资源53项通过。
- fb1ae4f7：向量生成脚本stdout/stderr固定UTF8；旧组合的唯一编码失败目标复跑1项与16vectors通过，不改写整组191项历史结果。
- 3236307c：最终原生场景/manifest、源派生DOM的id/CSS隔离、placement receipt和native pt预览坐标。生产者190通过/41缺Inkscapeskip，marker后native27项通过；前端坐标151项、正式build及Chrome4种映射通过。跨父/插绘/复杂clip/colorbar仍未取得完整编辑资格。

保留 cbd8d95e 的绑定 UI、请求/项目代际、确认/撤销重做，不从旧文档的“Dialog 无法开始”“请求没 chart”“导出只有 metadata”重新实现。

当前共享消费者已有实现及专项验证，所有子代理已释放文件。先收敛app/API+前端并取真实UI证据，再推进完整编辑状态导出/v2恢复、复杂DOM目标和正式保存。根代理统一app.py与共享契约，独立文件可交子代理并行；不从profile/原生坐标核心重做，不用git add .混入未验证草稿。

当前专项证据：汇总受影响后端288通过/1个GS环境skip；真实API/worker/RenderCore导出12通过；转换核心175通过、Chrome43通过（2重叠）；恢复前端138通过和最新正式构建通过。旧writer的84skip已有41Chrome补过，剩41Inkscape及2独立文本读取缺环境。它们互有重叠且对应不同源码时刻，不累加成最终总数，不将 skip、下载浏览器或“生成了文件”当通过。新增前端恢复修改必须重跑相关测试、正式构建与 i18n；新增 Chromium 分支必须取得真实退出码。

新增共享native API实际16项主流程及2项隐藏/恢复补测均通过；16项早于隐藏与marker补修，不冒充最终树18项完整重跑。前端消费者639项、终态失败143项、坐标151项互有重叠；独立Chrome探针证明4种PAR下拖动/缩放锚点/线宽与物理尺寸一致，但没有证明实际指针/Inspector/保存全链。最新playground指纹579b135bc6c24c11已重建并check通过。

当前实际效果：曲线、C恢复/CSV/类别和合格区域原生几何成立；历史恢复截图的“仅版面”与图例不一致仍需用当前共享消费者重验。图内操作、完整编辑状态生产导出/v2恢复、正式项目保存/旧版防损及最终旧功能资格未闭环。M1未通过，不得宣布最终可用或全量无影响。

已有自有调试服务见PROGRESS最新checkpoint：后端PID67684/23984早于最新marker，须先让native-edit-browser/server.py复用已有portable/project，再仅重启该自有进程；Vite71528/23983和CLI4468/session tavotto-native-edit仍在。不要直接重启会重seed的旧脚本覆盖证据，不用模式杀进程。14:05没有代理或测试仍在跑，这些服务不是通宵调度器。

三、自动推进的依赖队列

0. 保留全部已提交核心和草稿，特别是029dfac2、3236307c；不重做版本合同/原生坐标/白底/CSV/转换或恢复入口。
1. 共享消费者先收口：app现有engine/render与preview_png及前端renderStore/PanelView/transport/缩略图已有接线，验证同版SVG或PNG+manifest+chart_revision、失败撤销exact资格、队列/同源多面板/项目代际/raster。实际UI绑定→点选→Inspector改范围/样式→undo/redo；检查script+chart rebuild与host能力，不变更旧无chart请求/参数数量/来源策略。审查当前chart worker的环境占用、超时回收与输出预算，复用既有权威。取得同版真实UI证据后按功能提交。
2. 防丢编辑：生产原图/标注/画布/多页导出冻结native profile+完整原序overrides，实际写v2；inspect/bind/确认恢复以独立恢复状态消费全edits。实际改范围/线宽/文字→导出→删原文件→新进程UI恢复，SVG/manifest/版本一致。测试手工embed不算生产导出完成；v1历史产物仍真实重放。保留ea0a7027字体/TeX边界，不把父层形状校验当目标能力授权。
3. 复杂目标：复用composition.placements/svg_members，统一gid到实际成员/父空间/绘制槽位；transform/style/命中共用。修跨父、插绘多成员、errorbar、非矩形clip/colorbar实际反例，不搬动未绑定装饰、不改变paint order，不仅把composed改成true。已修native pt预览保持同一reader。
4. 现有Inspector/能力/完整overrides/事务：改轴范围、数据、线点样式、图例和标题，实际曲线与图例联动；单选/多选/平移缩放/撤销重做均正确，导出→C恢复不能丢edits。
5. 正式项目：附件核心接命名保存、自动保存、版本捕获、包恢复；附件先冻结后单次正文提交。保持单一documentStore/对象模型，schema4作为显式持久化格式，默认schema3不升。先冻结chart_source及保存回执，引用存在时只从校验后的精确INPUT SVG附件计算，不回退同名原文件。真实旧程序已证明仅升schema会降成空3再覆盖，验证documents/v4独立落点必须覆盖版本/缩略图/包/session发现；升级新文档身份、保留原文件，迟到回执以pj/doc/load/source防串。复用锁/atomicio/versionstore/projectcopy；移盘/新进程/缺资源/写入中断均实测。
6. 分享与M1：区分纯外观和完整数据自包含，不暗带隐藏数据；原图EPS兑现实验、画布仍既有eps_not_for_canvas。双系列真UI绑定→图内修改/图例→保存→清缓存新父进程→原图/标注/混合画布/多页，独立读SVG/PDF矢量文字和PNG/TIFF尺寸。M1通过自动继续。
7. M2：A可信采用、完整C、文字/分组/use实例、剩余族/统计/色阶/多轴/投影，逐格SG00；事务写回prepare→verify冷重放+几何+像素→commit；预检/样式/诊断、桌面/CLI/MCP/playground及发行依赖同合同。缺实现不能改写成缺数据，不凭SVG编造科学输入。
8. M3：冻结最终候选，完整旧功能、等价性/不变式/CompatBench、真E2E、性能和支持平台真安装物资格。只有SG00–SG17和A/B/C对应退出条件全部满足才称最终可用。

保持计算图表定义：轴模型和显式数据绑定为核心，普通 SVG 不要求反推原始 Python。可信来源自定义行为复用现有 safe/native/runtime 与授权；禁止按同名 .py 猜来源。图元操作保留计算关系，解除绑定必须显式且可撤销。

四、验证、提交与无人值守纪律

- 按 NEXT_PHASE 第 5.1–5.6 节自主运行。M0/M1/M2 是自动验收点，不是等待我说“继续”的节点。完成一个功能提交后立即选就绪下一项。
- 先说清断言主语：当前版本最终图、真实落盘文件、冷启动结果或导出产物。facts/path计数、文件头、字节重复不能代替科学/几何/视觉正确。
- Python 先 Ruff，再针对性 pytest；前端相关 Vitest→正式 tsc -b→vite build→tailwind 检查，按改动补 i18n、playground 生成物和真 E2E。关键 spec 先 --list 核对收集。完整候选再跑完整验证，之后仅新改动、失败或未解问题触发追加检查。
- 每个新增核心不变量做目标落点反证：错轴/删成员/旧色条/迟到响应/跳 verify 应让目标检查失败；恢复正常后重新通过。不要放宽容差、删测试或把未验证环境写成通过。
- SVG 表达限制先按第三路径审查做结构实验；当前渐变等可保留真正 SVG，PDF 不支持须逐格式明确失败，不能伪造 PDF、静默丢内容或栅格化矢量。原图 EPS 的可兑现路径继续验证，既有画布 eps_not_for_canvas 保持原合同。
- 最大化复用 documentStore、renderStore、现有能力/属性、RenderCore/pdfbackend、项目资源/atomicio 和事务。零 PyMuPDF、父进程不导入科学栈、认证/沙盒/隐私/解释器选择边界不变；不借机会重写稳定 Python 核心。
- 测试配置/数据隔离，runtime 数据用 data_dir；Windows 使用实际解释器并核对 import 源。不要破坏共享 node_modules/venv，不改用户凭据/配置，只按自己确认的 PID+启动时间清理进程。仍在运行且有进展的检查不要重复启动。
- 功能分别 commit，测试/契约镜像/受影响文档同步，不 squash 全部成“支持 SVG”，不加 Co-Authored-By。根代理先检查 stage 的准确文件与当前验证状态，再提交。
- PROGRESS 是进度唯一台账，更新当前状态并按日期追加，保留历史；NEXT_PHASE 和本提示词维护当前接续。每个功能/长验证前后约30分钟安全边界 checkpoint，写 HEAD、未提交文件、真实命令/退出码/数量、证据路径、进程、缺口与下一动作。
- 产品红继续修，不是外部阻塞。同类问题两轮仍有边界先查结构/权威/身份，换实验；无变化网络瞬态最多再试一次，认证失败转本地任务，不改凭据。平台/外部条件只阻塞真实依赖它的任务，继续其余可做工作。
- 压缩后续接同一目标，中断后按实际 HEAD/diff/进程恢复，不从计划重做。提示词本身不是调度器，不能唤醒休眠机器或恢复已结束会话，不虚构最终回复后仍后台运行。
- 只有全部验收完成、我明确停止、运行条件强制中断，或所有有意义工作均真正依赖无法取得的外部条件时才结束。M1通过、时间/轮数/上下文结束、难修或无人回复都不是完成理由。提前完成正常交付，不凑时长。

当前授权不包含 push、远程 Issue/PR、合并或发布。最终交付按功能 commits、实际使用步骤、样例产物、冻结候选测试证据与剩余平台缺口；区分已实现、本机已验证、平台资格和待发布。

现在核对当前树和最新PROGRESS，先完成现有共享消费者真实产品联验与提交，再按所有权并行推进第2、3项及第5项存储合同，随后自动推进完整队列。不要从已提交核心重做，不把独立DOM探针或手工bundle测试当完整UI/导出/保存闭环。
```
