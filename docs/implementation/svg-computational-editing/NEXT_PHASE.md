# SVG 计算图表编辑：当前效果与下一阶段安排

> 当前复核：2026-11-08（Asia/Shanghai，P2收束）。**按用户要求暂停，本文不授权自动继续。** 实施树E:/0 - Project/Tavotto-svg-r00，沿用zixu.wang/svg-computational-r00；产品HEAD c09fc110，42382027为守卫提交，后续文档提交以实际HEAD为准。原Tavotto/main@69bcafae干净、未合入；本轮未新分支/fetch/远程写入。
> [PROGRESS.md](PROGRESS.md)保留日期证据，[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)维护SG00–17完整范围，[NEXT_PHASE_PROMPT.md](NEXT_PHASE_PROMPT.md)仅供明确恢复后复制使用。

## 1. 当前判断

本轮新增的是**底层执行守卫和已有历史来源的被动持久化**，完整历史编辑尚未接通。42382027的低层守卫通过实际safe/native及Workerd回归；c09fc110的来源保存/打包/迁移、同archive去重与迟到保存保护已验证。自动来源捕获和渲染/PNG/导出接线外置为候选，普通Python流程维持原行为；当前仍不能称完整功能已达到PR验收标准。

计算编辑和项目持久化的核心链路已具备，静态SVG合成、批量归属、精确来源与本轮位图SVG/报告修复均已提交；**完整产品尚未验收**。方向仍是轴模型、显式数据绑定和可信绘图来源。普通SVG未包含的科学语义须显式绑定，拖动文字不等于恢复数据或Python来源。

| 范围 | 当前已实现/已验证 | 仍待解决 |
| --- | --- | --- |
| 计算编辑 | Inspector真实属性编辑、undo/redo、保存、命名稿及声明式冷恢复已有证据 | 历史Figure全链热/冷编辑、图例联动、完整图族 |
| 包与历史显示 | 35成员混合包、双画布11对象、5完整ref、2INPUT图/8附件；取消/迟到确认/最新稿恢复已提交 | A/C/D History render500来源缺口，完整删源冷迁移、真runtime包 |
| 静态SVG合成 | 1def88dc：矢量合成、overflow物理裁切、原图单scene；专项及Chrome29点 | gradient/mask/pattern/marker等完整矩阵、Office/Inkscape |
| 批量任务 | 1b7c5006：旧终态不推进新批，旧prepare不污染新批；runtime有合法默认名 | 默认输出名不是恢复原科学来源名，完整命名/host矩阵仍待 |
| 精确来源/检查 | 96cbbb33：问题面板与报告复用隐藏PNG/完整归档正证，单张/批量等事实后冻结；保真缺失及旧代次反例 | 旧文档native与源页面/显示框口径仍需统一；替代host归档读取未接通 |
| 位图与实际逐图批次 | 4985938c：PNG/JPEG/TIFF原图SVG复用Image；真实六图、6冲突另存续行，28成功含5报告，仅1失败；PNG四格式/64×48像素/32×24mm已独立核验 | 普通PDF无配对的SVG仍失败；位图SVG是内嵌像素，不是矢量科学数据 |
| 实际整版 | 新job7b4f551976024465 PDF/PNG/TIFF+报告成功；6份实际报告与主问题面板均无missing-asset；旧独立几何证据保留其窗口 | ordinary.pdf无可信SVG配对使整版SVG partial；历史runtime-edited的unapplied-override仍正确报告 |
| P2基础修复 | 守卫及导入70pass；旧worker/bridge456pass/6skip；Rust65pass；来源后端325pass/25skip、前端160pass；正式build/i18n/playground均0 | app接纳、自动捕获、全消费者及完整HTTP/UI未验证；不能累计窗口或把skip当通过 |
| 上一轮P3检查 | 前端84文件1969pass/0skip；后端冻结112pass及预检42pass；旧导出171pass/1skip；正式/派生构建与i18n均0 | 不累计窗口；旧组起始未捕获完整SHA；未重跑完整109/全仓/平台资格 |
| 视觉与入口 | 普通SVG四格式独立尺寸/文字/颜色正确；旧稿/引用保持 | 刻度/标题裁切N5、图例N4、替代host/桌面/发行与平台资格 |

后端原109文件首红仍为3539pass/16fail/128skip、exit1；4条旧SVG判据及12条夹具已分别修复/正式复验，未把原首红覆盖为绿。原128skip的84平台条件/41缺Inkscape/旧GS/pdfminer/Poppler各1仍是该历史窗口事实；前轮夹具25skip也不算通过。

## 2. 证据与暂停现场

本轮证据根 **E:/0 - Project/svg-review-20261108**，完整索引见PROGRESS最新P2 checkpoint。当前新增证据为p2-guard-production、p2-origin-backend、p2-origin-frontend、p3-pdf-content-facts及root-qualification/p2-passive-*；包含首红、实际exit、最终源SHA和延期候选。原产品UI未在本轮重开，下面P3证据是此前独立窗口：

- root-qualification：round-frontend-first（84文件1969项）、raster-source-final（112项）、preflight-contract-first（42项），实际exit0且源码零漂移；正式/playground/widget各build/check及i18n为0。
- p3-source-checks与p3-raster-svg：代码、专项、4+3个有效AST反证、首红、已知skip和源码释放清单。
- round-ui/verified-outputs.json：真实六图28成功/1失败、5逐图+1整版报告无误报，PNG原字节与SVG/TIFF像素相同，SVG浏览器正常显示。actual-jobs.json列出实际job，不混入取消冲突。
- round-ui/root-close-proof.json：browser17 close0、exec17200/child0；PID10400/39352消失、24139无监听；1794源/11HTTPbundle末核。A/B/C/D图表内容/命名稿与执行计数保留；A自动保存updatedAt及派生revision变化，配置变化为最近打开时间和版本查询缓存。已捕获旧配置正文并记录字段差异，未恢复/覆盖；不能声称全部持久文件字节不变。
- p3-pdf-svg/REPORT.md：OUT-only结构实验，无可直接应用的生产补丁。W/W*丢失和CID/ToUnicode反例已实证，详见下一批第3项。
- 四份状态文档引用/规则索引41项通过，预算检查exit0（既有OVER提示保留）；root-qualification/docs-final与agents-budget-final保留实际退出码。

下表保留**2026-11-07历史窗口**，证据根E:/0 - Project/svg-review-20261106；不能与本轮相加。

| 证据路径 | 结论 |
| --- | --- |
| n3f-svg-production/batch-source-facts-frontend-first-exit.json | 正式76文件1726pass/0skip，1503源零漂移 |
| n3f-svg-production/source-facts-production-final-exit.json | 当前后端3文件56pass/0skip，289源零漂移 |
| n3f-svg-production/source-facts-production-first-exit.json | 较早受影响194pass/1skip；最后badSVG增量另由final验证 |
| n3f-svg-production/platform-fixtures-production-first-exit.json | 正式两完整文件253pass/25skip；生产守卫不变 |
| n3f-svg-production/batch-source-facts-*-exit.json | 新正式构建、playground/widget build/check均actual_exit0 |
| p3-batch-export-ui/output/playwright/26.json、review-actual-jobs.json | 正常认证UI真实validate/start/state；六图与冲突续行，非伪造POST |
| p3-batch-export-ui/review-artifacts-final.json | 新整版独立PDFium/Pillow尺寸、文字、12色点、PNG/TIFF像素及保存稿身份对齐 |
| p3-batch-export-ui/review-original-artifacts-final.json | 六图文件SHA/像素；PNG原字节与无密度、PDF实际mm、SVG四格式、完整refs/patch保持 |
| p3-batch-export-ui/root-close-proof.json、service-exit.json | 1792源/11HTTPbundle停前核；close30=0，exec40459/child0，79648/61324消失，24129无监听 |

原A/B/C/D及data/config/AS/命名稿保留，执行计数counter/module/argv各4；无本轮live浏览器/服务/测试，既有代理已结束。本UI仅验证普通原图路径；标注/留白有专项证据，尚未在本轮真实UI铺满矩阵。观察器PDF容器字节误判首红保留，纠正为既有copy_foreign合同下的独立像素与来源SHA检查。

该历史窗口的收尾审计见status-review-round-pause-20261107/audit.json：当时源/bundle与8份正式回执一致；正文/绑定/计数及12份非config文件SHA不变。当时隔离验证环境config.json哈希有变化，未捕获旧正文，具体字段差异未知，未恢复/覆盖配置；保audit-first-red，不称整个配置完全不变。本轮已补配置旧正文基线与字段差异，见本节上方2026-11-08记录。历史文档门禁41pass，预算检查exit0（既有OVER提示保留）。

## 3. 用户恢复后分批安排与退出条件

恢复顺序：**P2a来源接纳与全部热编辑消费者→P2b原INPUT冷恢复→P2c完整迁移→N5裁切→N4图例→M1/M2/M3**；下列批次1的P3剩余能力作为独立支线推进。编号保留以便既有引用，当前不执行下面任务。

### 批次 1：P3剩余一致性与能力合同

1. **已完成，不重做**：96cbbb33修复问题面板/报告来源误报，4985938c支持普通位图原图SVG；正负/刷新/ABA/迟到反例及实际报告已验证。余项是复杂内容、所有入口及完整scope×format矩阵，不把只读正证当执行资格。
2. 独立核旧文档native40/48、源PDF35.278/源SVG42.333mm与放置/框的合同。当前只修了Produced测量值，不强改文档native或来源字节；接N5时贯穿显示/请求/实物一致性。
3. **普通PDF→SVG及含该PDF的整版SVG仍未实现**。本轮实验已证明直接使用PDFium getter会丢W/W*（受控例10.2273%像素错）与实际CID/GID身份（01可错成00），且轮廓不等于live text。下一步先取得可信原内容display list/字体与裁剪事实，再接原IR/SVGwriter，保原PDF SHA、页框/透明/资源；验证普通PDF像素差和复杂矩阵。不伪配同名SVG、不整页栅格化或仅改提示关闭缺口。该结构支线不能阻止独立的P2就绪任务，缺格保留M2未完成。
   本轮原内容驱动实验已补W*、ca/CA与isolated组并在两例零像素差，CID→GID/TJ反例取对字形；仍为OUT-only，普通PDF差>8占1.1867%、SMask0.1664%，没有live text/HTTP/UI资格。细节见p3-pdf-content-facts/REPORT.md，不把这些局部实验当生产转换已完成。
4. 补普通SVG标注/留白、透明/复杂组及scope×format的实际UI/独立实物矩阵；复用已绿专项，不无限重复原普通六图绿流程。

**退出**：正负来源/报告一致，所有要求格式格有实现或明确未完成记录，保原编辑/文件/事务。已提交a4ffd5f3/1def88dc/1b7c5006/2c4d5aaa/4985938c/96cbbb33不要重套候选或重做；新增修复各自commit，不以局部绿宣布P3/总目标完成。

### 批次 2：P2a 历史来源和同次热编辑

闭式合同已在ADR0166 §3a落实为figureOrigin={version:1,snapshot:<完整ref>,patches:<基准有序patches>}，当前overrides独立代表完整Q。42382027已提交低层guard，c09fc110已提交被动持久化/包闭包/迁移；不要重新套combined候选。首编辑自动捕获与所有执行消费者仍未启用，须一起完成。归档只有patch_hash，不能反推或默认为空P。

- 持久来源贯穿原文档 commit/事务、首改/undo/redo、AS/命名稿/版本/多画布、复制与包闭包/完整ref迁移。首改清输出 figureSnapshot 后仍保来源；引用本身不授执行许可。
- renderKey、队列、request/API、回包、latest/recent/exact/mounted/PNG、导出及写回消费者同步同一来源身份；不新建 store/writer，不借旧 manifest 写几何。
- 复用已提交的safe/Workerd/native守卫；接enginesession单一分派和app正常认证/fullref/receipt。归档receipt只有public_facts，不含完整spec/profile；必须与实际live候选receipt逐维匹配，再由原pool/REGISTRY复核同一实例，不能新建路由表或从同名当前图猜归属。服务端核真实 worker/session、execution identity 与 native epoch，parent/Workerd重连/child均不得隐式 build/respawn。不能只加旧端忽略的字段。
- 普通无来源 Python 原路径保持；历史来源不退同名当前图、不把 native 降 safe。来源回包不能从 archived SVG 制造 editable manifest。

**退出**：正式 safe/Workerd/native 加真实认证 HTTP/UI：同次热编辑/undo零重跑；冷、错pj/ref/receipt/basepatch/eid/epoch及迟到回包不隐式执行、不入exact；普通 Python 回归通过；来源首改后保存/重开/包闭包仍完整。来源持久化与真实接纳按完整功能分别提交，不能用 OUT 候选测试冒充产品通过。

前端延期差量见p2-origin-frontend/deferred-from-passive-consistent.patch，仅供审阅接续；full-frontend-wip.patch基于4fe4658e，与已提交被动代码重叠，不能整包重复apply。必须补specfix/update_source、source evidence及替代host等实际消费者，不以候选140项通过替代产品接线。

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

以下仅在用户明确恢复任务后适用；当前暂停，不创建调度或继续运行。

1. 沿当前唯一树/分支，不新建或切分支/worktree；保改动和首红，不 reset/stash/git add .，不 push/PR/合并/发布。按功能定向 commit，状态文档另交。
2. 每批：规则/实际状态→最小实现→必要反证→受影响验证→退出码/源SHA/实物→定向提交→日期checkpoint→下一批。先前绿组源未变不重跑，最终冻结后才做完整链。
3. Python先Ruff再pytest；前端类型走正式build及所需派生产物；不以 tsc --noEmit 代替。新失败先分产品、夹具和观察器，不删断言/放容差消红；同类两轮仍扩散先按第三路径审结构。
4. 已有多代理授权；仅独立任务有收益时并行，每文件一个写入者。root统一合同/浏览器/集成/提交，先核既有代理，避免重复实验。
5. UI期间冻结产品源/build，隔离自建证据与正常认证；只读观察，不注入store/伪造成功。不重用终局session；只关闭核明身份的自有进程，末核HTTP bundle/PID/listener及持久稿。
6. 长检查前后、提交及约30分钟追加 PROGRESS：HEAD/diff、所有权/句柄、命令/cwd/env、真实 fail/skip、实物SHA、首红、未验项和下一条动作。对用户的工作更新不超过60秒失联。
7. 保认证、沙盒、隐私、零PyMuPDF和 config.data_dir；写回仍 prepare→完整重放/几何/像素verify→commit，失败409原件零改。
8. 无人值守指活跃会话自主接续，本任务未创建常驻调度器。预算/时长不替代验收；遇外部条件缺失记录依赖并推进独立就绪项，不反复只写计划。
