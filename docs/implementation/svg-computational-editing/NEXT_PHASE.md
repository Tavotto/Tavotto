# SVG 计算图表编辑：当前效果与下一阶段安排

> 当前状态：2026-11-09本轮修复完成，按用户最新要求暂停。最新产品提交71ee8c78，沿原唯一树/分支；全部计划只在用户明确恢复后执行。完整前端未启用、最终功能未验收，不宣称可发布。
> [PROGRESS.md](PROGRESS.md)保留日期证据，[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)维护SG00–17完整范围，[NEXT_PHASE_PROMPT.md](NEXT_PHASE_PROMPT.md)仅供明确恢复后复制使用。

## 1. 当前判断

方向仍是计算图表编辑：沿用轴模型、显式数据绑定与可信 Python/Figure 来源；普通 SVG 本身没有的科学语义不从图形位置猜出。已有声明式计算编辑、保存/版本、静态 SVG 矢量合成、普通位图 SVG 及精确来源检查继续保留，其既有资格窗口见 PROGRESS。

此前已提交两项独立基础能力：应用层按完整历史引用、基准 P 和真实 live receipt 接纳 render/PNG/export 等请求；PDF 原内容流到现有 IR 的内部适配。前者只允许已存在的执行实例，旧无 origin 请求保持原路径；后者尚未接入 facade/HTTP，不能据此宣称普通 PDF 已可导出 SVG。本轮另提交8129b8d6的隐藏图层保护和71ee8c78的外部原件并发保护。

**前端自动来源接线仍延期启用，但前轮native原件回归的理由已被真实证据纠正。** 当前native描述符没有磁盘原件绑定，磁盘写回实际固定safe；合法runtime native原本就无原件可写回，不为此扩新协议。真实候选缺口是snapshot-only普通safe面板冷开被无条件当历史来源、显式运行后已固化旧origin未正式采用新来源，以及script=null和当前元信息误判。先修这些可达问题，再启用候选。

写回验证和备份期间外部更改被覆盖已修：冻结候选目标身份/内容，在最终目标收窄后、备份前和首替换前复核，失败409保外部新字节并清staging。关联67pass/5skip，另15项来源/归属兼容通过；最终检查与替换之间仍非针对外部程序的原子CAS，后续并发协议需单独处理。

| 范围 | 当前进展 | 仍需完成 |
| --- | --- | --- |
| 历史执行基础 | 既有 render/export/PNG execution-id 守卫；app 复用原 resolver、ledger、实际 receipt 和同一 worker，原窗口真实 safe/Workerd/native HTTP 21 项通过 | 自动捕获与所有前端消费者须基于同次执行正证及明确来源采用接通；正常认证产品 UI 尚未复验，不扩native磁盘写回 |
| 其他历史消费者 | specfix、history、sync 与 safe 磁盘写回接入同一接纳结构；Q 保存/写回及公开 RenderPlan 身份已有验证；本轮补外部原件并发保护 | 旧无 owner 引用、完整错误/迟到及平台矩阵仍需最终资格；最终check→replace外部writer原子性待结构性方案 |
| 前端候选 | 来源键、回包身份、临时 PNG、导出、版本/undo、记录来源与确认归属均有候选和专项证据 | 候选不是生产启用状态；恢复时审阅最终归档，不重复套早期 WIP |
| PDF 内部适配 | 原内容流/CTM/clip/CID 字形/透明组/图像及预算；受控 approved-font ordinary 实物两条往返零像素差；本轮XObject /OC不再静默漏语义 | 原 RenderHost 有界 child/facade/导出入口未接；OCG未实现而是明确unsupported，通用字体、连续文本隐式起点精度及复杂资源未全面保真 |
| 冷恢复与迁移 | 被动 origin 持久化、INPUT 包闭包与迁移基础已提交 | P2b 明确授权后原 Preparation 冷重建、P2c 真删源/新父进程迁移未完成 |
| 视觉与最终验收 | 既有图片重复 clip 修复、位图 SVG、普通 SVG 矢量合成保留 | N5 刻度/标题裁切、N4 图例、复杂 SVG/图族、全部入口、M1/M2/M3 与平台安装物未验收 |

## 2. 证据与暂停现场

最新证据根 **E:/0 - Project/svg-review-20261109-resumed**：

- `writeback-toctou/DELIVERY.md`、`freeze.json`、`regression-final.xml`：71ee8c78对应67pass/5skip、旧实现8项首红与AST撤门8项反证；`root-source-compat.xml`为另15项来源/归属兼容。app及测试SHA与提交前资格一致，未跑全仓。
- `pdf-review/REPORT.md`：8129b8d6对应77项定向通过，两个真实OFF图层反例与AST反证。新内部叶子尚无产品入口。
- `native-archive-review/`、`native-facts/`、`native-verifier/`：真实producer纠正、未联验草稿归档及六文件恢复证明；草稿不得直接套回。
- `frontend-review/REVIEW.md`：候选可达缺口；`p2-live-render-proof-design/REVIEW.md`是未实施、未行为验证的同次执行正证提案，不能当已有API使用。

此前证据根 **E:/0 - Project/svg-review-20261109** 保留以下独立源码窗口：

- `p2-app-backend-tests/DELIVERY.md`：真实三控制面 21 项、完整引用反证及首红；`native-writeback-third-path.md` 是独立验证器的 OUT 实验，未实现产品接线。
- `root-qualification/`：应用消费者补充、既有导出/写回/specfix 回归、最终构建与文档门禁；具体结果、退出码和冻结窗口见各 XML/JSON，不能把重叠测试相加为全仓资格。
- `p2-app-frontend/REVIEW.md` 与 `pause-closeout/`：完整候选、源码 SHA、1052 项受影响验证、正式构建首红及修复记录、来源反证、恢复基线证明。候选通过不等于产品已启用。
- `p3-pdf-production/`：新适配专项、全部 RenderCore 及旧契约回归、5 条 AST 反证、普通 PDF 零差实物和未批准字体的真实残差。

旧证据根 `E:/0 - Project/svg-review-20261108` 及更早记录继续保留。先前实际 UI 的普通 PDF→SVG 失败尚未被本轮产品 UI 关闭；本轮没有重新开 UI、发布、推送或合入 main。原 `E:/0 - Project/Tavotto` 仍保留 main；不要在恢复时覆盖用户稿件或重新安装既有目标。

## 3. 用户恢复后分批安排与退出条件

恢复顺序：**P2a来源接纳与全部热编辑消费者→P2b原INPUT冷恢复→P2c完整迁移→N5裁切→N4图例→M1/M2/M3**；下列批次1的P3剩余能力作为独立支线推进。编号保留以便既有引用，本轮从P2a与独立P3支线续接。

### 批次 1：P3剩余一致性与能力合同

1. **已完成，不重做**：96cbbb33修复问题面板/报告来源误报，4985938c支持普通位图原图SVG；正负/刷新/ABA/迟到反例及实际报告已验证。余项是复杂内容、所有入口及完整scope×format矩阵，不把只读正证当执行资格。
2. 独立核旧文档native40/48、源PDF35.278/源SVG42.333mm与放置/框的合同。当前只修了Produced测量值，不强改文档native或来源字节；接N5时贯穿显示/请求/实物一致性。
3. **普通PDF→SVG及含该PDF的整版SVG产品入口仍未实现**。本轮已提交 `rendercore/pdfcontent.py` 内部原内容流适配，支持范围与失败合同见 `p3-pdf-production/REPORT.md`；52项专项包含于1320pass/68skip回归。原字体程序/实际CID→GID/宽度证明可让受控批准字体进入原文本链，ordinary实物两路线零像素差；不能因此授予未批准字体和任意连续文本精度资格。
   下一步将 native 解码放进原 RenderHost 有界 child，再接原 pdfbackend/facade/IR/SVGwriter 与逐格式失败合同，保源SHA、页框、透明与资源。预算计数不能替代 native 内存/时间边界，不在 Flask parent 直接解析。补非批准字体 hinting/轮廓残差、多个 Tj 隐式起点精度与复杂算子/资源和 HTTP/UI；不伪配同名SVG、不整页栅格化。旧 `pdfcontent-deferred.py` 首稿已被本轮内部模块取代，不重复套用；50e3817f图片clip修复保持，不重做。
4. 补普通SVG标注/留白、透明/复杂组及scope×format的实际UI/独立实物矩阵；复用已绿专项，不无限重复原普通六图绿流程。

**退出**：正负来源/报告一致，所有要求格式格有实现或明确未完成记录，保原编辑/文件/事务。已提交a4ffd5f3/1def88dc/1b7c5006/2c4d5aaa/4985938c/96cbbb33不要重套候选或重做；新增修复各自commit，不以局部绿宣布P3/总目标完成。

### 批次 2：P2a 来源接线与既有普通编辑不回归

不实施前轮误推断的native磁盘原件写回：真实producer没有这样的描述符绑定；普通磁盘身份固定safe，runtime native原已禁止写回。原有native热编辑/预览/导出守卫保留。

- **已完成，不重做**：71ee8c78修原写回事务验证/备份期间原件竞争，两个入口/两载体/两窗口8项反例及关联67pass/5skip、另15项兼容通过。不放宽重放两门；剩余最终检查到替换的外部writer窗口单独审结构。
- 前端区分显式figureOrigin历史意图与snapshot-only普通输出，不能凭归档可读就推导live执行权限。首次P捕获与实际渲染/正式采用绑定，保普通Python冷开原流程；显式历史来源不得悄悄改用当前同名脚本。
- 同次执行正证先冻结合同和反例：仅可选投影实际本次render的eid、已核receipt/owner/P；缺证据省略正证，保原normal render冷构建、Workerd透明重开、native普通渲染的成败与默认响应。当前receipt不能补签旧响应，撤销事实不能作正向执行权限。现有只读设计尚未实现，不据此直接接前端。
- 复用原明确运行、来源重连/采用和文档事务更新新receipt/P；不能仅清输出ref却留下无法再次使用的旧origin。补真实safe冷开/明确运行对照，旧源/迟到结果不入exact。
- 审阅最终41路径候选后一次应用并修script=null入口、来源自己的baked/profile资格；候选测试/构建通过只覆盖对应源码窗口。正常认证UI和全部受影响消费者仍须最终验证。

**退出**：普通Python现有冷开、运行、写回/历史恢复保持；历史来源选择明确且同次渲染一致，错误/迟到不借来源；正式/派生构建和真实UI通过。不能以不可达的native原件身份制造新能力或新验收项。

### 批次 3：P2b 复用原 Preparation 的 INPUT 冷恢复

复用 load_input_archive、原 PreparationPlan/Service/prepsession、execspec/runconfig、原 safe/native runner、资源租约和授权链。比较视图不是执行目录，包/旧rc不是运行许可。

- 冻结 origin+INPUT revision、脚本/数据角色、cwd、精确argv/rc、租约至worker终止/reap；核同名脚本/数据、prefix/空成员、相对IO与输入 remap。
- safe 复用原 shadowscript；native 需接原受控启动中的私有来源角色，不硬塞 safe 参数、不写当前用户脚本、不建第二 runner/租约表。
- 明确运行后先重放基准P，取得同次真实 PDF/SVG 与 manifest，独立核几何/像素/文字，再接当前Q。新执行有新receipt；选正式采用新ref或受限私有接纳映射，不伪造旧receipt。
- 取消、过期与迟到均保持正文/ref/exact，正常回收自建 runtime/输入租约。

**退出**：真正缺旧脚本/数据/派生cache的新父进程，仅靠包INPUT及当前明确授权完成 safe/native 重建；两份同名历史不串，精确argv含中文/空串/负数保留；失败/取消零正文改动，原 Python 准备链回归通过。该批独立 commit。

### 批次 4：P2c 完整迁移与真 runtime

P3、P2a/b 完成后再做完整冷迁移。对自建旧源及派生cache先列绝对路径、归属、保留/隔离清单；保新根、原包、INPUT、AS/命名稿、config/runconfigs、绑定/意图及 layout-sessions(.bak)，不全清 data/cache（含AI history）。

新父/新浏览器验证双画布、五历史引用、数据/轴/样式编辑、undo/redo、保存/再开/版本恢复和适用 scope×format。另验 n3f-runtime-kind/attempt-02/真正runtime家族.tavotto；混合包的 runtime 来源对象实际 kind=pdf，不能替代真 runtime 家族。

**退出**：首次源缺失环境实际走通，旧稿和执行计数可独立核对，所有自有进程正常终局。失败修具体缺口，不重复安装同一目标或循环重开代替修复。

### 批次 5：N5 可见内容裁切，再接 N4 图例联动

N5 已有 OUT 原型，不在产品。保源坐标S、派生显示框F，接同次 render_frame/native-manifest/2 与旧/1兼容、schema、全部请求/cache/exact、frame采用事务、F→S几何写入及原图/画布/多页导出。用实际 glyph ink 跨页反例验证 CSS/percent/clip/use/PAR/旋转；不能只扩 viewBox、缩小数据/字体或偷改用户页面掩盖裁切。

N4 复用 legendmodel 的 entry→series/custom 绑定，覆盖同名同色、复合句柄、手工文字和未绑定注释；编辑/undo/保存/冷开/导出一致。

**退出**：当前 fitted 截图中的真实刻度/标题裁切在正确坐标和图幅下消失；图例与系列联动完整生命周期通过。裁切、图例分别 commit，不混成大重写。

### 批次 6：M1/M2/M3 最终验收

- **M1 完整流程**：普通SVG+明确数据绑定，双系列+图例+注释，改数据/轴范围/样式→undo/redo→保存/版本→空派生cache冷开→适用各格式实际产物；可信 Python 路径与无原Python的完整声明式路径分别跑。
- **M2 能力对等**：沿 SG00–17/A/B/C 逐格补复杂SVG、图族、多轴/投影/组/use/文字、写回/预检/出版和 CLI/MCP/playground/桌面/发行。替代 engineTransport 的归档读取尚缺；构建绿不等入口实现。缺实现不能标“不适用”。
- **M3 不回归与交付**：最终源码冻结后跑项目完整原 Python/旧项目回归、等价性/CompatBench/E2E/性能及支持矩阵平台安装物。缺 macOS/桌面/外部reader条件精确记未验，不把Windows/浏览器结果当跨平台资格。

**退出**：需求矩阵有对应最终源码、退出码与实物证据；已实现、已验证、已发布分别记录。三门全退出前不称最终可用，不标总目标 complete。

## 4. 无人值守接续规则

当前已按用户要求暂停；以下仅适用于用户明确恢复后的活跃会话，优先正确性与回归，不创建常驻调度器。读取本文不触发继续执行。

1. 沿当前唯一树/分支，不新建或切分支/worktree；保改动和首红，不 reset/stash/git add .，不 push/PR/合并/发布。按功能定向 commit，状态文档另交。
2. 每批：规则/实际状态→最小实现→必要反证→受影响验证→退出码/源SHA/实物→定向提交→日期checkpoint→下一批。先前绿组源未变不重跑，最终冻结后才做完整链。
3. Python先Ruff再pytest；前端类型走正式build及所需派生产物；不以 tsc --noEmit 代替。新失败先分产品、夹具和观察器，不删断言/放容差消红；同类两轮仍扩散先按第三路径审结构。
4. 已有多代理授权；仅独立任务有收益时并行，每文件一个写入者。root统一合同/浏览器/集成/提交，先核既有代理，避免重复实验。
5. UI期间冻结产品源/build，隔离自建证据与正常认证；只读观察，不注入store/伪造成功。不重用终局session；只关闭核明身份的自有进程，末核HTTP bundle/PID/listener及持久稿。
6. 长检查前后、提交及约30分钟追加 PROGRESS：HEAD/diff、所有权/句柄、命令/cwd/env、真实 fail/skip、实物SHA、首红、未验项和下一条动作。对用户的工作更新不超过60秒失联。
7. 保认证、沙盒、隐私、零PyMuPDF和 config.data_dir；写回仍 prepare→完整重放/几何/像素verify→commit，失败409原件零改。
8. 无人值守指活跃会话自主接续，本任务未创建常驻调度器。预算/时长不替代验收；遇外部条件缺失记录依赖并推进独立就绪项，不反复只写计划。
