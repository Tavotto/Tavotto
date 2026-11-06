# SVG 计算图表编辑：下一阶段无人值守提示词

可直接复制以下提示词。状态截至2026-11-06 21:03（Asia/Shanghai），实际树优先。详细任务：[NEXT_PHASE.md](NEXT_PHASE.md)；进度：[PROGRESS.md](PROGRESS.md)；最终范围：[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。

```text
继续完成Tavotto的SVG计算图表编辑，保留现有功能和逻辑，最大化复用现模型/事务/writer/RenderCore与既有编辑器。持续自主实现、验证、修复，按功能分别commit；一个文件同时一个写入者，根统一共享契约、集成和提交。常规可逆步骤自行推进，不逐批等我确认；已授权多子代理，但只给真正独立且有收益的任务。

一、恢复当前树，不另起分支

实施树 E:/0 - Project/Tavotto-svg-r00
唯一分支 zixu.wang/svg-computational-r00
原目录 E:/0 - Project/Tavotto 仍为clean main@69bcafae，未包含功能；不要自动覆盖/合并原目录。
稳定文档 docs/implementation/svg-computational-editing/{PROGRESS,NEXT_PHASE,NEXT_PHASE_PROMPT}.md
证据根 E:/0 - Project/svg-review-20261106
当前功能HEAD d7b6eef9；可能已有后续提交，先核实际HEAD/index/diff、身份、Python import路径、代理和自有进程，禁止reset到文档hash。

先读AGENTS及最近层规则/ADR。沿用当前树/分支，不新建/切换分支或worktree，不reset/stash覆盖草稿，不git add .混批，不push/PR/合并/发布。当前索引为空；旧EPS索引保护指令已过时，a06cc3d3已提交。复用现有venv/node_modules，测试数据/config隔离、遥测关闭，运行可写数据走data_dir。

二、保留已完成，不按过时报告重做

- 2452d8fc精确INPUT附件消费者，5abb9a7d v4落盘基础，95684f89共享打开意图，264ce59c前端格式/恢复，ec2cb144准确保存错误均已提交。
- c9fa29a4/8fa84f5d EPS矢量准备与nested viewport；a06cc3d3完整公共契约/owned action/app/route/offer/UI已提交。真UI五格式PDF/PNG/SVG/EPS/TIFF均done，模型/全部编辑/精确INPUT、文字/尺寸/PPI/像素及独立GS成图已验，见eps-ui-five-formats-qualification.json。只授予单曲线原图，不代替完整M1。
- a7008840命名文件关联后端；d7b6eef9明确冲突恢复后端。GET/reserve/confirm/cancel及AS prepare→body→commit、project_file_recovery均已接，最新74HTTP通过/0skip，不重接或回滚成旧37项叶子。
- ffe2fb32预算约束ZIP/原子发布叶子121项通过；c40805bf计算来源包核验/准备/发布54专项通过，含删原目录新worker精确重放。app原子输出/混合资源安装/API/UI仍未接。
- 真实普通保存关联：d_d88wfjsq打开A=N2_正式保存_20261106→真实F2改标题B且另有B→AS仍关联A且dirty→关闭旧浏览器/新后端/空localStorage新浏览器仍恢复A和dirty→真实CtrlS只写A，另一B摘要不变、原chart.svg缺席。n21-ui-binding-qualification.json；用的是397项阶段普通保存冻结bundle，不称最新恢复按钮已验。

三、最近冻结候选及当前第一动作

1. N2.1完整前端20文件冻结，未提交。
   n21-complete-frontend-validation.json列精确20SHA/命令/日志，根已逐个核对0漂移。21文件428项通过/0skip，正式pnpm build/i18n:check/lint exit0，28实际故障反证均落点+AssertionError/exit1。正式入口index-BBA0tQCV.js。单documentStore/原writer；普通save/open与明确覆盖/重载一起构成完整功能，不能分出unknown后永久409的半成品提交。
   先核所有权及SHA。named外改→真实F2编辑/CtrlS409、外部A与另一B零改/旧revision保留已验，见n21-ui-external-named-qualification.json（普通冻结bundle）；优先用新冻结bundle做真API浏览器联验：外改AS制造unknown→真实覆盖确认/重新加载各走新branch和冻结完整proof，成功后下一普通编辑能保存。读取等待期间的最新本机编辑必须先入既有恢复槽，最新副本/恢复槽Storage失败停止安装保留内存。多窗口独立editor_id，跨actor不取max；项目/format/workingId/loadSeq/绑定代次/操作意图挡迟到读盘/确认/回执。
   不自动刷新recovery proof；只有用户明确覆盖/重载能建立新branch。后端project_file_recovery的revision为用户观察的实际AS，expected为完整原GET view，编辑base_revision与proof固定一致；失败不能静默洗基线。正文成功/meta失败准确分列，不重复覆盖正文。
   仅明确404回旧wire，503/未知不降级；derived不推进用户轴。同步前端落盘规则/ADR后定向stage20文件+必要规则并commit。根统一正式build/playground及--check现已全部exit0，unified-current-build-validation.json与指纹551ca2013f6b078c；22文件前后SHA无漂移，冻结dist在n21-recovery-ui-dist-2056。源码未受影响不重复刚通过的构建。

2. N4预检窄修复2文件冻结，独立提交。
   n4-preflight-freeze.json：preflight.ts debfe111059ef74157597beb4aa53b7ba15971ada7febf72cdb002af7b3d59af；preflight.chartSource.test.ts 4f66ad89a574c3487ab9f26bb53f098ee1d69c94d16c57d6c1568a8fe43af072。
   前端78、Python33、49向量只读检查、真实HTTP response两个范围通过；旧64场景×2完整spec JSON逐字一致，两AST反证实际失败。根统一正式build/playground现已通过，见unified-current-build-validation.json；审stage后独立提交。缺素材豁免只认同完整输入exactPanelRender，unapplied用既有renderRequestIdentity，不能因任意chart字段而放行。该修复不表示图例/readiness广泛能力已完成。
   两代理均已冻结释放，不把完成状态当仍在后台实施。根拥有统一构建/UI与提交，不混22文件为一个功能。检查新的源变化后再决定必要复验，不能沿用旧397/428报告给新增改动无条件授资格。

四、后续队列：第一项退出后自动选下一就绪项

3. N3完整包搬迁，先旧ZIP原子输出，再v4导出/显式安装。
   app.api_package仍ZipFile最终路径，api_package_open仍只读。用staged_package接原输出，临时helper退出前流入资源，add失败锁定整包失败，CRC/结构核验后原子发布。保留旧manifest与合法容量，caller六预算有实际依据；内部合法symlink检查后捕获canonical源，manifest保原相对ID，越界与竞态拒绝。注入ENOSPC/中途异常/并发同名，旧包/用户文件不损坏。
   复用chartpackage write/inspect/install_chart_sources，包含精确完整INPUT；remaining_resources必须落实普通素材/脚本/runtime/快照，不能叶子成功即称混合安装完成。显式安装路径/摘要/版本/预算→安全资源安装→实际重放→单次正文提交，不任意extract、不执行未知脚本、不覆盖用户资产。中文新目录/移盘/删原源的新父进程继续编辑/undo/导出/版本恢复；旧包/旧只读检查兼容。

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

五、验证和无人值守纪律

判据先说谁/哪个进程/时刻/维度；热态、落盘、冷开、导出分别量。Python先Ruff再针对性pytest；前端针对性Vitest→正式tsc-b/Vite/Tailwind，按影响补i18n、playground及真实UI。核命令真实退出码，skip/没跑不写通过，重叠组不累计。新增核心不变量做真实产品落点反证，不删测试/放宽容差；同类两轮仍出边界先查结构与单一权威。

共享契约/Schema/app/规则/三文档一个写入者。分功能定向stage并核diff再commit，不加Co-Authored-By；保留用户已有改动。日志/临时fixture留仓库外证据根。Playwright CLI通过run-playwright-captured.py以subprocess原始UTF8字节捕获，避免PowerShell native管道损坏中文；UI操作走真实DOM，观察可只读，不能直接改store假造用户效果。

PROGRESS唯一进度台账，NEXT_PHASE/PROMPT维护当前队列；功能提交/长检查前后及约30分钟安全边界checkpoint，记HEAD/diff/index、源码身份/命令/cwd/env/退出码、所有权、真实自有PID+创建时间+命令、缺口和下一动作。已有合格且未受影响检查不重复；冻结后才跑playground，避免追逐变化指纹。

20:55服务快照仅作恢复线索：后端71856/23984（20:43:13，source-ref-browser/server.py）；Vite10168/23983（18:51:24，native-edit-browser/vite.mjs）；普通冻结预览8452/23986（20:33:46，n21-preview.mjs）；旧EPS预览76372/23985（19:48:20，eps-preview.mjs）；浏览器tavotto-binding-cold daemon50704（20:43:24）。旧66888已结束。先核实时PID/创建时间/命令，不能按旧数字杀进程或重seed覆盖证据。

这些服务不是整夜调度器，未创建定时任务；提示词不能唤醒休眠机器或自动恢复已结束的执行会话。在活跃goal内持续选下一就绪工作；中断从真实checkpoint接续，不重新生成整套计划。仅全部验收完成、用户明确停止、运行条件强制中断或所有有意义工作确需外部变化时结束；状态询问简短答复后继续原目标。M1/M2/M3当前均未通过，goal保持active。

现在先核当前22文件与新提交，采用新冻结bundle完成N2.1真实冲突恢复联验，分别收口前端/预检，再接N3。不要停在反复写计划。
```
