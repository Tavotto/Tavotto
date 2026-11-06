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

我最新要求：不新建/切换分支，不新建工作树，保留全部已有提交和未提交成果；该要求覆盖旧规则的新建要求。先读当前AGENTS.md、最近子目录规则、对应ADR、同源契约表和判据/第三路径/本地验证要求，再核对真实HEAD、diff、Git身份、解释器源码位置、已有进程。2026-11-06 13:12复核完成核心批次HEAD=bbbffed1，前端原生render接线仍未提交；它不是冻结候选，实际HEAD可能继续前进。不要reset、丢弃草稿或覆盖代理文件，不自动向旧main合并。

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

保留 cbd8d95e 的绑定 UI、请求/项目代际、确认/撤销重做，不从旧文档的“Dialog 无法开始”“请求没 chart”“导出只有 metadata”重新实现。

当前并行线：最终原生场景/坐标及组合、前端renderStore/scheduler/PNG/编辑入口、profile与自包含v2兼容。先核对实际HEAD/diff和负责人；已完成adapter/附件核心不重做，未提交前端必须保留。新差异汇总验证后按功能分别提交，不用git add .混入未验证草稿。

当前专项证据：汇总受影响后端288通过/1个GS环境skip；真实API/worker/RenderCore导出12通过；转换核心175通过、Chrome43通过（2重叠）；恢复前端138通过和最新正式构建通过。旧writer的84skip已有41Chrome补过，剩41Inkscape及2独立文本读取缺环境。它们互有重叠且对应不同源码时刻，不累加成最终总数，不将 skip、下载浏览器或“生成了文件”当通过。新增前端恢复修改必须重跑相关测试、正式构建与 i18n；新增 Chromium 分支必须取得真实退出码。

当前实际效果：曲线可见，自包含恢复/真实CSV和类别预览打通；实际恢复截图仍显示“仅版面”，蓝色系列与旧图例标记未联动。图内精确编辑、完整项目保存迁移/旧版防损、分享数据范围、原图EPS、A采用/剩余族/写回/入口/平台资格待完成。M1未通过，不得宣布最终可用或原功能全量无影响。

三、自动推进的依赖队列

0. 保留全部已提交核心和真恢复UI证据，按实际负责人接续，不重做白底、CSV、转换或恢复入口。
1. 版本化重放：legacy-scaffold/1输出和v1包不变，native-manifest/1独立；v2包携完整原序overrides，先实际重放包内profile+edits再处理新请求。新身份包含完整顺序，canonical patch_hash相同不代表同档应用顺序相同。未知profile/非法字段/超预算拒绝；父层形状校验不等于native owned gid/能力授权。
2. 最终原生生产者：svgchart→chartmanifest→svgcompose输出同版最终SVG+manifest。先实验物理viewport的Figure与viewBox矩阵逆映射，让现有pos_frac/pt属性输入也正确；不是只改输出bbox。保留原生gid，隔离原scene派生副本id/CSS，源字节和绑定digest不变。祖先clip/opacity与最终paint_order必须匹配，不能把非矩形clip假装成矩形命中。native创建到draw固定usetex=False，族名必须按字面值进入字体选择，不把fontconfig file模式带入未信任包权限。
3. 共享消费者：app现有engine/render与preview_png接同一生产者，返回原子SVG或PNG+manifest+chart_revision；前端renderStore/exactPanelRender/PanelView/transport及CanvasThumb/Export/Version缩略图带同版chart。旧无chart请求/参数数量/来源策略不变；队列冻结、防乱序、同源多模型不串，raster可编辑且不绕过预算。取得真实后端浏览器点选证据后才称接通。
4. 现有Inspector/能力/完整overrides/事务：改轴范围、数据、线点样式、图例和标题，实际曲线与图例联动；单选/多选/平移缩放/撤销重做均正确，导出→C恢复不能丢edits。
5. 正式项目：附件核心接命名保存、自动保存、版本捕获、包恢复；附件先冻结后单次正文提交。真实旧程序已证明仅升schema会降成空3再覆盖，继续验证documents/v4独立落点、显式发现读取、新文档身份和保留原文件。复用既有锁/atomicio/versionstore/projectcopy，不另建历史数据库；移盘/新父进程/缺资源/写入中断均实测。
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

现在核对当前树、代理所有权和最新PROGRESS，先并行完成第1–3项并做真实产品联验，随后自动推进第4–8项。不要从已提交核心重做，不把当前前端mock回归当后端已经接通。
```
