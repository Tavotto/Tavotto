# SVG 计算图表编辑：下一阶段无人值守提示词

可直接复制以下提示词。状态截至2026-11-06 21:41（Asia/Shanghai），实际树优先。详细任务：[NEXT_PHASE.md](NEXT_PHASE.md)；进度：[PROGRESS.md](PROGRESS.md)；最终范围：[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。

```text
继续完成Tavotto的SVG计算图表编辑，保留现有功能和逻辑，最大化复用现模型/事务/writer/RenderCore与既有编辑器。持续自主实现、验证、修复，按功能分别commit；一个文件同时一个写入者，根统一共享契约、集成和提交。常规可逆步骤自行推进，不逐批等我确认；已授权多子代理，但只给真正独立且有收益的任务。

一、恢复当前树，不另起分支

实施树 E:/0 - Project/Tavotto-svg-r00
唯一分支 zixu.wang/svg-computational-r00
原目录 E:/0 - Project/Tavotto 仍为clean main@69bcafae，未包含功能；不要自动覆盖/合并原目录。
稳定文档 docs/implementation/svg-computational-editing/{PROGRESS,NEXT_PHASE,NEXT_PHASE_PROMPT}.md
证据根 E:/0 - Project/svg-review-20261106
当前功能HEAD 206a1c34；可能已有后续提交，先核实际HEAD/index/diff、身份、Python import路径、代理和自有进程，禁止reset到文档hash。

先读AGENTS及最近层规则/ADR。沿用当前树/分支，不新建/切换分支或worktree，不reset/stash覆盖草稿，不git add .混批，不push/PR/合并/发布。当前索引为空；旧EPS索引保护指令已过时，a06cc3d3已提交。复用现有venv/node_modules，测试数据/config隔离、遥测关闭，运行可写数据走data_dir。

二、保留已完成，不按过时报告重做

- 2452d8fc精确INPUT附件消费者，5abb9a7d v4落盘基础，95684f89共享打开意图，264ce59c前端格式/恢复，ec2cb144准确保存错误均已提交。
- c9fa29a4/8fa84f5d EPS矢量准备与nested viewport；a06cc3d3完整公共契约/owned action/app/route/offer/UI已提交。真UI五格式PDF/PNG/SVG/EPS/TIFF均done，模型/全部编辑/精确INPUT、文字/尺寸/PPI/像素及独立GS成图已验，见eps-ui-five-formats-qualification.json。只授予单曲线原图，不代替完整M1。
- a7008840命名文件关联后端；d7b6eef9明确冲突恢复后端。GET/reserve/confirm/cancel及AS prepare→body→commit、project_file_recovery均已接，最新74HTTP通过/0skip，不重接或回滚成旧37项叶子。
- ffe2fb32预算约束ZIP/原子发布叶子121项通过；c40805bf计算来源包核验/准备/发布54专项通过，含删原目录新worker精确重放。旧app原子输出随后已提交336783bf；v4完整混合资源导出/安装API/UI仍未接。
- 真实普通保存关联：d_d88wfjsq打开A=N2_正式保存_20261106→真实F2改标题B且另有B→AS仍关联A且dirty→关闭旧浏览器/新后端/空localStorage新浏览器仍恢复A和dirty→真实CtrlS只写A，另一B摘要不变、原chart.svg缺席。n21-ui-binding-qualification.json；用的是397项阶段普通保存冻结bundle，不称最新恢复按钮已验。

三、本轮已提交，保留证据不重做

1. 0b611411预检，d7a73ce1完整保存前端（含404兼容备份失败P1），e468d516包预算/旧名/ZIP64，336783bf旧schema2/3包原子HTTP，71e637c8原writer真实回执，206a1c34计算包budget_factory。均已精确stage分功能提交，旧22未提交候选/保护EPS索引的指令作废。
2. 真浏览器明确覆盖和重载已验：外改AS→409→真实确认/按钮→固定实际revision+完整view证明→新actor写入200；reload真实GET暂停期间F2新增编辑进入原恢复槽；下一普通编辑无proof仍200。双独立浏览器零缓存peer保继承base_axis，新actor在另一窗口先写后409且双方稿保留。n21-recovery-{overwrite,reload}-qualification.json、n21-two-window-qualification.json。普通改标题/冷开/外改named409证据保留。
3. 404旧后端reload P1原探针确实丢等待期编辑，已修并独立复跑；最终431项、build/i18n/lint0，两反证。其后共用回执叶子最新448项、build/i18n/lint0、10反证，playground7f69cc026d69e1f4。n21-p1-frontend-validation.json、n3-working-document-receipt-validation.json。现代真UI基于先前冻结恢复bundle，之后仅404保护/回执reader的资格由最终测试证明，不冒称全部浏览器场景新跑。旧landing曾被复跑配置覆盖，索引已说明，保留原失败log和源SHA不伪造复原。
4. 旧包基础204、HTTP18新/8旧、计算包末次82各范围通过且0skip，不累计。实际ENOSPC/add/close/publish、同名并发、最终名不提前可见、Windows真实junction与改指、NFC双文件已验；小ZIP完整字节与budget static/factory对拍、真实反证均有日志。ZIP64是缩小阈值真实分支，不冒充>2GiB物理压测。n3-packagebudget-freeze.md、n3-atomic-http-validation.md、n3-chart-budget-factory-freeze.md。

四、当前第一动作：N3 v4完整包产品链

1. 根接v4完整资源清单/导出API与TopBar同入口。
   现TopBar.createPackage仍送旧/api/package，v4会400；旧2/3输出已原子化，不重新实现。新增真实显式v4路由进入正确format/认证namespace；复用现完整来源冻结与chartpackage.write_chart_package(budget_factory=packagebudget.output_budget)。唯一layout/manifest/派生chart行在chartpackage，不在app复制私有规则。所有画布的完整INPUT、普通素材、脚本、runtime、FigureSnapshots均要盘点；旧默认API参数/响应保持。以严格reader/删源新worker实际重放及独立模型/原序edits/INPUT核对证明完整导出。

2. 冻结并兑现显式检视/安装合同。
   旧package/open保持只读2/3，不把v4塞旧relink（它会误报有效chartSource缺原文件并清edits）。新v4检视核CRC/路径/摘要/六预算/全部资源及语义闭包，不执行未知脚本、不任意extract。file与script不同ZIP namespace可安装同根同ID且内容不同，plan须识别；opaque figure_snapshot字节hash不足以证明svgstore引用/归档语义，须复用原权威验证。
   优先评估作为新项目完整安装，保全部相对sourceId/script/runtime/快照identity，复用现projects/open和projectStore.adoptOpenedProject(status,{prepareDocument})/switchTransaction在ready前装正文。现projects/open create=true会复用既有目录，不能当exclusive reserve；copy_project允许skipped>0成功，不能当完整资源安装器。若支持现目录冲突必须真迁移全引用，不能仅改panel.fileId或覆盖用户资产。
   n3-package-install-identity-probe.json已真测：整树复制保ref/INPUT，但新根runconfig_missing、workdir设置丢失；snapshot仍绑定remap_fingerprint；仅脚本+runtime manifest而无registry会resolve=None。把可携运行声明/配置/重定向与现权威关系落实，才能宣称完整搬迁。环境不随机器复制，未知脚本不自动执行；保持既有准确缺依赖/重建状态。

3. 安装正文只经原writer真实回执。
   已有waitForWorkingDocumentSave(scope,{persistEmpty?})，scope用pj/format/documentId/loadSeq，receipt含实际address/document/revision/loadSeq/capture/moment。非空只等待原switchDocument已排一次写，不另saveNow补PUT；空稿明确persistEmpty一次，普通empty wrote:true仍非文件存在证明。与named保存共用私有reader，无named预留/第二writer。
   资源确认/发布在作用域守卫下，真实重放后openDocumentAs或项目prepareDocument采用最终pd，再等真实AS回读。beforeInstall前必须已冻结最终正文（不能回调返回另份pd却被旧闭包采用）；前后核project/format/id/loadSeq/操作意图。取消/切项目/迟到不安装进别的项目；资源成功、AS失败、正文成功/meta未确认、HTTP超时准确分列。新包不继承发送者本机绑定/绝对路径。
   中文新目录/移盘/原目录删除，新父进程冷开→编辑/undo/redo→实际导出→版本恢复；普通素材/脚本/runtime/快照/计算附件全量验。坏包/缺件/断写/同名/资源变化保旧资产/正文，检视取消零项目写。N3未通过前不称搬迁完成。

五、后续就绪队列

4. N4图例、readiness和逐对象重绑迁移。
   n4-preflight-and-legend-review.md已有真实曲线红/旧图例蓝反例及native Legend正常控制实验。根因是Binding只有axes/series、producer没Legend/ownership。先冻结可持久化显式SVG图例区域/条目→系列/custom关系及版本/镜像/向量，再复用现legendmodel跟随/脱开/handle_*，按原paint slot组合。不能按颜色/同名文字/列表顺序猜，不另写SVG重染。未知图例保装饰，确认一次可撤销事务。
   验同色同名、多系列、bar/errorbar复合句柄、范围/数据更改、冷恢复与实际导出。readiness消费同一后端能力事实；迁移按科学身份保编辑，重排/删除不套错，未匹配项显式可撤销。

5. N5成员placement与复杂clip。
   保留多DOM成果，解决source/compound/native成员paint slots偏序，保留装饰/group opacity/clip。复用SVGIR Path/ClipUnion统一祖先AND、子形状union、evenodd、objectBBox、成员运动。命中距离/框选/高亮/宽描边墨迹共同验证，圆外/孔洞实际拒绝命中，不只查查询点inside或改composed。截图刻度边缘裁切先对照原SVG/最终产物量主语，不凭猜测改结构。

6. N6分享选择和完整M1。
   纯外观不暗含模型/数据，完整模式含模型/数据/全部编辑。真实双系列+图例+未绑定注释→绑定/数据/范围/样式→精确选取/undo/redo→正式保存→空缓存新浏览器/新父进程→原图/标注/混合画布/多页所有适用scope×format，独立文字/矢量/尺寸/PPI/像素核对。EPS画布原拒绝合同保持。全部退出才M1，通过立即继续M2。

7. N7/M2能力对等，再N8/M3最终资格。
   SG00–SG17完整范围保留：A可信来源/C自包含、文字/分组/use/图元、适用系列/统计/色阶/多轴/投影、事务写回、出版/预检/样式、CLI/MCP/playground/桌面/发行入口逐格验。普通SVG不反推原始Python，不猜同名相邻脚本。写回prepare→verify全量冷重放+几何+像素→commit，失败409原件零改。
   冻结最终候选，完整旧功能/等价性/不变式/CompatBench/E2E/性能与支持平台真实安装物。既有Windows desktop PID回执失败有原版本对照，须处置其资格影响；缺平台列未验证。每功能commit，不因一夜结束、一个里程碑、上下文耗尽缩小目标。

六、验证和无人值守纪律

判据先说谁/哪个进程/时刻/维度；热态、落盘、冷开、导出分别量。Python先Ruff再针对性pytest；前端针对性Vitest→正式tsc-b/Vite/Tailwind，按影响补i18n、playground及真实UI。核命令真实退出码，skip/没跑不写通过，重叠组不累计。新增核心不变量做真实产品落点反证，不删测试/放宽容差；同类两轮仍出边界先查结构与单一权威。

共享契约/Schema/app/规则/三文档一个写入者。分功能定向stage并核diff再commit，不加Co-Authored-By；保留用户已有改动。日志/临时fixture留仓库外证据根。Playwright CLI通过run-playwright-captured.py以subprocess原始UTF8字节捕获，避免PowerShell native管道损坏中文；UI操作走真实DOM，观察可只读，不能直接改store假造用户效果。

PROGRESS唯一进度台账，NEXT_PHASE/PROMPT维护当前队列；功能提交/长检查前后及约30分钟安全边界checkpoint，记HEAD/diff/index、源码身份/命令/cwd/env/退出码、所有权、真实自有PID+创建时间+命令、缺口和下一动作。已有合格且未受影响检查不重复；冻结后才跑playground，避免追逐变化指纹。

21:41服务线索（先核实时归属）：新冻结预览24652/23987（n21-recovery-preview.mjs/session46153），新浏览器tavotto-recovery-ui daemon9260、tavotto-recovery-peer daemon77356。以下旧服务仍留作证据恢复：后端71856/23984（20:43:13，source-ref-browser/server.py）；Vite10168/23983（18:51:24，native-edit-browser/vite.mjs）；普通冻结预览8452/23986（20:33:46，n21-preview.mjs）；旧EPS预览76372/23985（19:48:20，eps-preview.mjs）；浏览器tavotto-binding-cold daemon50704（20:43:24）。后端71856仍载入d7b6eef9时期app，尚未加载本轮旧ZIP新改动；新HTTP资格由新pytest进程取得。旧66888已结束。先核实时PID/创建时间/命令，不能按旧数字杀进程或重seed覆盖证据。

这些服务不是整夜调度器，未创建定时任务；提示词不能唤醒休眠机器或自动恢复已结束的执行会话。在活跃goal内持续选下一就绪工作；中断从真实checkpoint接续，不重新生成整套计划。仅全部验收完成、用户明确停止、运行条件强制中断或所有有意义工作确需外部变化时结束；状态询问简短答复后继续原目标。M1/M2/M3当前均未通过，goal保持active。

现在核实际HEAD/index/diff、报告和已运行句柄；当前保存/预检/旧包原子输出已提交，直接完成N3 v4完整包导出/安装主线，再继续N4–N8。不要从旧冻结SHA回滚当前树，不停在反复写计划。
```
