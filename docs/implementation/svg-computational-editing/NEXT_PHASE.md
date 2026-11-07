# SVG 计算图表编辑：当前效果与下一阶段安排

> 当前复核：2026-11-07 16:30（Asia/Shanghai）。**按用户要求，本轮修复完成后暂停；本文不授权自动继续。** 实施树 E:/0 - Project/Tavotto-svg-r00，沿用 zixu.wang/svg-computational-r00；产品HEAD2c4d5aaa，后续文档提交以实际HEAD为准。原E:/0 - Project/Tavotto保留main@69bcafae、未合入；本轮未新分支/fetch/远程写入。
> [PROGRESS.md](PROGRESS.md)保留日期证据，[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)维护SG00–17完整范围，[NEXT_PHASE_PROMPT.md](NEXT_PHASE_PROMPT.md)仅供明确恢复后复制使用。

## 1. 当前判断

计算编辑和项目持久化的核心链路已具备，P3静态SVG与本轮批量修复已提交并经真实产物验证；**完整产品尚未验收**。方向仍是轴模型、显式数据绑定和可信绘图来源。普通SVG未包含的科学语义须显式绑定，拖动文字不等于恢复数据或Python来源。

| 范围 | 当前已实现/已验证 | 仍待解决 |
| --- | --- | --- |
| 计算编辑 | Inspector真实属性编辑、undo/redo、保存、命名稿及声明式冷恢复已有证据 | 历史Figure全链热/冷编辑、图例联动、完整图族 |
| 包与历史显示 | 35成员混合包、双画布11对象、5完整ref、2INPUT图/8附件；取消/迟到确认/最新稿恢复已提交 | A/C/D History render500来源缺口，完整删源冷迁移、真runtime包 |
| 静态SVG合成 | 1def88dc：矢量合成、overflow物理裁切、原图单scene；专项及Chrome29点 | gradient/mask/pattern/marker等完整矩阵、Office/Inkscape |
| 批量任务 | 1b7c5006：旧终态不推进新批，旧prepare不污染新批；runtime有合法默认名 | 默认输出名不是恢复原科学来源名，完整命名/host矩阵仍待 |
| 精确来源/尺寸 | 2c4d5aaa：隐藏PNG可用原endpoint取得正证；原像素/密度保持，PDF位图mm据真实页面 | 检查消费者仍误报missing-asset；旧文档native与源页面/显示框口径仍需统一 |
| 实际逐图批次 | 六图顺序、5冲突另存续行；计算SVG/普通SVG/两历史图各四格式成功，PNG/PDF各三格式成功 | PNG/PDF各自SVG不支持；27成功含5报告，2失败，不能称全格式通过 |
| 实际整版 | job250a2e1ce97748a4 PDF/PNG/TIFF；独立180×130mm、600dpi、12点、文字、像素及来源保持 | ordinary.pdf无可信SVG配对使整版SVG partial，未关闭能力缺口 |
| 本轮检查 | 前端76文件1726pass；后端最终定向56pass；扩大194pass/1skip；夹具253pass/25skip；构建/check均0 | 不累计窗口；未重新跑完整109/全仓回归，不等交付/发布资格 |
| 视觉与入口 | 普通SVG四格式独立尺寸/文字/颜色正确；旧稿/引用保持 | 刻度/标题裁切N5、图例N4、替代host/桌面/发行与平台资格 |

后端原109文件首红仍为3539pass/16fail/128skip、exit1；4条旧SVG判据及12条夹具已分别修复/正式复验，未把原首红覆盖为绿。原128skip的84平台条件/41缺Inkscape/旧GS/pdfminer/Poppler各1仍是该历史窗口事实；本轮夹具25skip也不算通过。

## 2. 本轮证据与暂停现场

证据根E:/0 - Project/svg-review-20261106。不同源码窗口的测试数不累计。

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

收尾审计见status-review-round-pause-20261107/audit.json：当前源/bundle与8份正式回执一致；正文/绑定/计数及12份非config文件SHA不变。隔离验证环境config.json哈希有变化，未捕获旧正文，具体字段差异未知，未恢复/覆盖配置；保audit-first-red，不称整个配置完全不变。未来UI先保存可脱敏比较的配置基线。文档门禁41pass，预算检查exit0（既有OVER提示保留）。

## 3. 用户恢复后分批安排与退出条件

恢复顺序：**补齐P3剩余检查/格式合同 → P2a来源与热编辑 → P2b原INPUT冷恢复 → P2c完整迁移 → N5裁切 → N4图例 → M1/M2/M3**。本轮不执行下面任务。

### 批次 1：P3剩余一致性与能力合同

1. 复用现有来源正证和完整归档引用，修问题面板/样式报告的missing-asset误报；不把只读正证变执行许可，不把隐藏PNG加回素材清单，不忽略真实缺文件。补刷新/切项目/源删除/负缓存反例与真实UI报告。
2. 独立核旧文档native40/48、源PDF35.278/源SVG42.333mm与放置/框的合同。当前只修了Produced测量值，不强改文档native或来源字节；接N5时贯穿显示/请求/实物一致性。
3. PNG/PDF原图→SVG和含无配对PDF的整版SVG仍未实现。按原矢量契约与第三路径规则做有界结构实验；不伪配同名SVG、不删对象、整页栅格化或只改提示关闭缺口。属于需求的格保留M2未完成。
4. 补普通SVG标注/留白、透明/复杂组及scope×format的实际UI/独立实物矩阵；复用已绿专项，不无限重复原普通六图绿流程。

**退出**：正负来源/报告一致，所有要求格式格有实现或明确未完成记录，保原编辑/文件/事务。已提交a4ffd5f3/1def88dc/1b7c5006/2c4d5aaa不要重套候选或重做；新增修复各自commit，不以局部绿宣布P3/总目标完成。

### 批次 2：P2a 历史来源和同次热编辑

沿 p2-integration-map 先冻结闭式合同，再实施。候选 figureOrigin = {version:1, snapshot:<完整ref>, patches:<基准有序patches>}；当前 overrides 单独代表本次完整列表。归档只有 patch_hash，不得反推或默认为空基准。

- 持久来源贯穿原文档 commit/事务、首改/undo/redo、AS/命名稿/版本/多画布、复制与包闭包/完整ref迁移。首改清输出 figureSnapshot 后仍保来源；引用本身不授执行许可。
- renderKey、队列、request/API、回包、latest/recent/exact/mounted/PNG、导出及写回消费者同步同一来源身份；不新建 store/writer，不借旧 manifest 写几何。
- 应用已有 safe 守卫及 native 增量，或 combined 二选一；接 enginesession 单一分派和 app 的正常认证/fullref/receipt。服务端核真实 worker/session、execution identity 与 native epoch，parent/Workerd重连/child均不得隐式 build/respawn。不能只加旧端忽略的字段。
- 普通无来源 Python 原路径保持；历史来源不退同名当前图、不把 native 降 safe。来源回包不能从 archived SVG 制造 editable manifest。

**退出**：正式 safe/Workerd/native 加真实认证 HTTP/UI：同次热编辑/undo零重跑；冷、错pj/ref/receipt/basepatch/eid/epoch及迟到回包不隐式执行、不入exact；普通 Python 回归通过；来源首改后保存/重开/包闭包仍完整。来源持久化与真实接纳按完整功能分别提交，不能用 OUT 候选测试冒充产品通过。

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
