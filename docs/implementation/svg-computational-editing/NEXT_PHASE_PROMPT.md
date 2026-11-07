# SVG 计算图表编辑：下一阶段无人值守提示词

2026-11-07 11:33 复核：当前代码 HEAD `135aba94`。正式 35 成员包已真实安装、资源核验通过；初次采用严格失败，根因为夹具 native 尺寸与实际图源不一致。历史预览另有 404/500，归档后端已独立提交，前端联合/构建已通过、真实 UI 待验；完整冷迁移/历史计算编辑未通过。详细安排见 [NEXT_PHASE.md](NEXT_PHASE.md)，证据/完整范围见 [PROGRESS.md](PROGRESS.md)、[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。

以下可整体复制：

```text
继续完成 Tavotto 的 SVG 计算图表编辑，以最终可用为目标，不影响既有 Python 绘图和逻辑。复用原模型、编辑器、writer、准备/执行管线、RenderCore/pdfbackend、legendmodel；自主实现、修复、验证，当前同一分支按功能分别 commit，常规可逆步骤不逐批等确认。

一、先恢复真实状态
唯一实施树 E:/0 - Project/Tavotto-svg-r00，分支 zixu.wang/svg-computational-r00；本次代码 HEAD135aba94，以实际 HEAD/diff 为准。原 E:/0 - Project/Tavotto 仍 clean main@69bcafae、未合入；证据根 E:/0 - Project/svg-review-20261106。
读 PROGRESS 最新 checkpoint、NEXT_PHASE、对应 AGENTS/规则/ADR；核身份、现有代理所有权和实际 live 句柄。保已有未提交改动、已安装目标和所有首红；不新建/切换 branch/worktree，不 reset/stash/git add .，不 push/PR/合并/发布。
已提交 P0 归属8f4985ee、归档后端135aba94以及此前保存/目录/安装后端，不能重做；N3e完整前端、取消后继续编辑修复和归档前端仍在工作区。
n3f-ui 原浏览器/服务终局 exit0，原进程消失/24069无监听，目标已存在，不能重等旧句柄或重启旧 serve。原窗口初始 HTTP11 bundle 已核，服务结束源未漂移，但未取得停前bundle末核，不冒称全窗口bundle零漂移。

二、按证据窗口理解当前资格
取消保存后重开：n3e-install-review/source-release.json，4文件95pass/0skip，新增7回归，3个AST变异实红，未单独提交。
其后、归档前端改动前：n3e-owner-final/third-after-adopt-fix*，53文件1055pass/0skip/exit0；n3e-final/*after-adopt-fix* 正式/playground/widget及check均0、1494源零漂移。不能当当前新预览源的资格。
归档后端：n3f-snapshot-preview/freeze.json，37新预览+368受影响旧契约分别全绿/0skip、Ruff/format0，3反证实红，已135aba94。归档显示允许旧A/B原配对，原当前编辑/导出撤旧门不放松。
当前归档前端最终资格 n3f-preview-final/status-review-source-qualification.json：66文件1273pass/0fail/error/skip/exit0；1500源零漂移；正式packaged-second、playground-first/widget-first及两个check全0。首轮测试装饰图片alt硬编码被i18n拒绝，改为空alt后重验，未放宽策略。子代理12源与root5源已释放，全部现有代理无live；真实UI与冷恢复未验，前端尚未功能commit。未改源不重跑已绿组。
替代engineTransport的归档读取能力尚未接，必须在M2入口矩阵补齐；派生产物构建绿不等于该入口功能已完成。
N3f真实安装201、资源核验0；初次PUT35/50严格红，旧AS/命名稿保持，科学/module计数4/4。原35包和167件证据不动。历史runtime请求404，未带引用History render500，worker缺plot.py未增加科学执行；不能称编辑恢复成功。

三、第一批：归档前端接线与 N3e 正式界面收口
核现有 svg_architecture_review 已释放的 source-release.json/REPORT.md，归档前端已完成，不重复派工。root负责 PanelView/useEngineSync/归档hook及测试；代理负责API/helper/useVariantPng/CanvasThumb/版本/导出预览/renderStore捕获等。每文件一个写入者。只修已提交P0遗漏的测试显式pj期望，不回全局归属。
新归档显示按完整ref+pj/owner/orderedpatch分键，覆盖同名A/B、迟到、ABA/离开快照、全部预览消费者和零隐式Run。SVG只在img/Blob图片上下文，hash不是DOM净化，不挂inline编辑层/伪造manifest。保无ref旧路径。
复用当前66文件1273与正式/派生构建资格，逐源核对后直接准备真实UI新窗口。只因新改动/失败/未解疑点补验。真实UI通过后前端归档按独立功能commit，不混N3e API wrapper/规则段。
用n3f-mixed/prepare_mixed_calibrated.py生成全新正式35成员尝试（尚未运行），原API保存/打包，native取实际100pt图源并核manifest、页面摆放仍55mm，不手改ZIP/缩水。保attempt-04和首次2PUT失败，不改产品native同步、不放宽单PUT门。
核验器显式参数化新ready/attempt，旧默认/严格断言保留。全新UI目录/端口/未存在中文目标，正常认证，核本树__file__/HTTPbundle/observerSHA，冻结期间不改源/重建。
真实旧稿编辑保存→35包安装→独立资源核验→明确采用→初次严格1PUT，PUT/浏览器GET/磁盘完整正文与revision一致、旧稿保留。覆盖取消/保存确认/重开、历史A/B、编辑撤销重做、跨项目继续最新稿；核pj/ref/Inspector/图像/零隐式执行。失败先正常停自有进程、保目标/日志，再修复补验，不重装覆盖。
通过后定向commit：feat(packages): adopt installed charts through the document writer，带对应ADR/规则；状态文档另交。

四、第二批：真正历史计算编辑与完整冷迁移
读n3f-snapshot-authority/third-path-review.md。归档图片能显示不等于可继续计算编辑。活Figure必须同receipt、pj/epoch/worker实例，经原existing-only/peek路径取得同次SVG/manifest；普通render可能创建worker，不当只读探测。
冷态复用原load_input_archive/校验和PreparationPlan/PreparationService、safe spec/runconfig/工作目录、原runner。INPUT比对视图不是可执行工作目录；配置可携建议不继承本机授权。重建经独立几何/像素对照后才接精确编辑，不建第二套runner/store/writer。
原renderRequestIdentity/renderKey/want/last/latest/recent/mounted/exact完整来源一致，A/B不能别名。第一次override会清旧输出快照，编辑来源必须由原合同保留，不得因此回同名当前脚本。普通无模型SVG走显式绑定，不能永久只读或假称恢复Python。
冷迁移仅删除经绝对路径/归属核实的自建旧源和派生缓存，保新根/正式包/INPUT/AS命名稿/runconfigs/config/项目绑定/打开意图/layout-sessions(.bak)；data/cache含AI history，禁止全清。结束旧父/浏览器再删清单目标，启动新父/新浏览器。
验双画布/历史、数据范围样式、undo/redo、保存/再冷开/版本恢复及适用导出，执行计数按显式行为独立核。附加真runtime包n3f-runtime-kind/attempt-02/真正runtime家族.tavotto（12成员）另验入口/fileKind/fallback/编辑冷开，不替代35成员包。

五、随后 N5→N4→M1/M2/M3
N5外部prototype已冻结：13后端/92前端及反证通过、14文件overlay，不是产品。读其IMPLEMENTATION/validation，集成源图幅S/可见外框F、真实render_frame及native2/旧1兼容；补闭合schema、全请求cache/exact、frame采用事务、F→S几何、C确认、原图/画布/多页导出、历史冷开、真实ink/page策略。守CSS/percent/clip/use/PAR/旋转，不缩数据/轴/字体或偷改页面。x10负框不证明glyph越页，x5才有真实反证。
N4复用legendmodel，显式entry→series/custom，保同名同色/复合句柄/手工文字/未绑定注释，编辑撤销保存冷开导出一致。
M1完整双系列+图例+注释及适用scope×format真产物，画布EPS保持eps_not_for_canvas；M2按SG00–17/A/B/C补全图族与入口；M3最终冻结后完整原Python/旧文档回归、等价性/CompatBench/E2E/性能/支持平台实际安装物。缺条件明确未验，不把缺实现改不适用。
写回始终prepare→完整重放/几何/像素verify→commit，失败409原件零改；不放松认证、沙盒、PyMuPDF退役或隐私。

六、无人值守协议
每批核状态/规则→真实实现/消费者→必要反证→受影响验证→实际exit/源SHA/diff→定向功能commit→日期checkpoint→下一项。已绿只因新改动/失败/未解疑点补验，最终候选再跑完整链；不反复计划/跑绿测代替实现。
已有多代理授权，独立任务可并行，root统一契约/浏览器/集成/提交。Python先Ruff；前端正式和派生构建；隔离配置/data/证据、关闭遥测，运行时走config.data_dir。只结束已核PID/创建身份的自有进程。
长检查前后/提交/约30分钟记PROGRESS：HEAD/diff、所有权/句柄、命令/cwd/env、actual exit/fail/skip、SHA/产物、首红与下一步。中断读最新checkpoint续，不用旧终局句柄/已存在目标/旧bundle。
无人值守为活跃会话自主接续，未创建常驻调度器；不以一夜时长或专项绿当完成。M1/M2/M3全退出前保持完整目标未完成。
```
