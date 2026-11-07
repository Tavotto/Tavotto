# SVG 计算图表编辑：当前效果与下一阶段安排

> 当前复核：2026-11-07 15:18（Asia/Shanghai）。实施树 E:/0 - Project/Tavotto-svg-r00，分支 zixu.wang/svg-computational-r00；复核基点 HEAD c85c96c7，最近产品提交 35adee6d。P3 源码与必要测试已在工作区，尚未提交。原 E:/0 - Project/Tavotto 为 clean main@69bcafae，未合入。本轮未新建分支、fetch 或远程写入；以实际 HEAD/diff 为准。
> 本文维护当前安排；[PROGRESS.md](PROGRESS.md)保留日期证据，[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)维护 SG00–17 完整范围，[NEXT_PHASE_PROMPT.md](NEXT_PHASE_PROMPT.md)提供可复制的无人值守接续提示词。

## 1. 当前判断

**已具备计算编辑与项目持久化的核心链路，完整产品尚未验收。** 声明式计算图的真实属性编辑、撤销重做、命名保存、跨项目恢复，以及完整混合包安装和历史显示已有证据。普通 SVG 合成与原图多格式共享来源已实现并通过专项，但本轮真实批量导出暴露了文件名冲突、准备判定和 runtime 命名问题，P3 暂不按完整退出条件提交。

产品方向保持轴模型、显式数据绑定与可信 Python/绘图来源恢复。缺少科学语义的 SVG 仍须经显式绑定补充；可拖动图元不能证明已恢复原始数据。

| 范围 | 已有结果 | 尚未覆盖或未通过 |
| --- | --- | --- |
| 计算编辑 | 真实 Inspector 编辑、undo/redo、三次保存、命名保存；native 尺寸与页面放置分开 | 历史 Figure 全链编辑、图例联动及完整操作矩阵 |
| 包与持久化 | 35 成员混合包、双画布 11 对象、5 完整历史引用、2 个 INPUT 图/8 附件；N3e 取消/迟到确认及最新稿恢复已提交 | 保留缓存的重开不等于完整删源冷迁移；真 runtime 包仍须单独验证 |
| 历史显示 | 五引用 SVG 与可见 DOM 一致；14 条 PNG Blob 与归档 PDF 独立渲染逐通道一致 | 显示不授予 editable manifest；A/C/D 的 History render500 来源缺口未关闭 |
| 普通 SVG 合成 | 正式 28 项、overflow 两项、原图统一来源 9 项通过；独立 Chrome 两页 29 手算点通过 | gradient/mask/pattern/marker 等能力矩阵仍待；Inkscape/Office 未验证 |
| 最新 A 整版导出 | 正常认证 UI 作业 6a452aab0ee94e38：PDF/PNG/TIFF 成功；独立尺寸、文字、12 点颜色和 PNG/TIFF 像素一致通过 | 同一完整画布 SVG 因无配对 SVG 的 ordinary.pdf 返回 svg_source_unsupported，作业 partial |
| 最新逐图导出 | chart.svg 四格式作业 done；History.pdf 四格式及报告 done | ordinary.svg 作业因同名输出 conflict 未完成；普通 PNG 被报来源缺失；runtime 名称含冒号被拒 |
| 视觉效果 | 曲线颜色、普通 SVG 图形和历史内容正常显示 | 适应画布后仍可见左/下刻度、部分标题裁切；N5 未进入产品 |
| 当前前端与构建 | 70 文件 1514 项通过；正式 build、playground/widget 构建与 check 均 exit0 | 受影响集合不是最终全量，也不是发布安装物资格 |
| 扩大后端 | 109 文件 3683 项：3539 pass / 16 fail / 128 skip，exit1，源零漂移 | 4 条过时 SVG 判据已修，对应三文件 76 pass/0skip；另 12 条夹具候选待正式应用/验证 |

128 个 skip 为 84 条平台文件系统条件、41 条缺 Inkscape、1 条旧硬编码 Ghostscript 路径、1 条缺 pdfminer.six、1 条缺 Poppler pdftotext；不算通过。独立 Ghostscript 与 Chrome 实验已经运行，不能因此把所有 reader/平台格标绿。

[当前界面截图](<E:/0 - Project/svg-review-20261106/n3f-svg-export-ui/output/playwright/current-A-fitted.png>)能直接看到尚存的刻度/标题裁切。[批量导出详情](<E:/0 - Project/svg-review-20261106/n3f-svg-export-ui/output/playwright/batch-partial-details.png>)保留实际失败；不隐藏普通 PDF 或删掉对象来构造全绿。

## 2. 证据与恢复状态

证据根 E:/0 - Project/svg-review-20261106。各窗口的测试数不累计成一次全绿结果。

| 相对证据路径 | 能支持的结论 |
| --- | --- |
| n3f-svg-production/backend-first-exit.json、backend-first.xml、backend-first-analysis.json | 原 109 文件终局与 16 失败/128 skip；原 PTY91338 已结束，不再续等 |
| n3f-svg-production/contract-repairs-production-first-exit.json | 三个正式 SVG 测试文件 76 pass/0skip；保执行来源拒绝、真实 EPS partial 与像素内容断言 |
| n3f-svg-production/consistency-production-first-exit.json | 原图标注/留白各格式同一次 scene；正式 9 pass/0skip |
| n3f-svg-production/frontend-first-exit.json、packaged-first-exit.json 及派生构建回执 | 当前 1514 受影响前端与新正式/派生构建；1502 源零漂移 |
| n3f-static-svg-chromium-inputs/root-check/qualification-first.json、close-proof.json | Chrome 29 点、尺寸/文字/ID/CSS/overflow/PAR/clip/alpha/变换；正常关闭；不等于 Office 互操作 |
| p3-platform-fixture-review/REVIEW.md、portable-fixtures.patch、release.json | 原 12 失败复现；仅两个测试文件候选，生产判定未改；尚未应用 |
| n3f-svg-export-ui/output/playwright/35.json、review-actual-jobs.json | 正常认证 UI 发起的原 validate/start 请求及真实作业；普通 SVG 原图 conflict，不能称四格式通过 |
| n3f-svg-export-ui/review-artifacts-final.json | A 完整请求与保存稿对象/patch/fullref 对齐；实际 PDF/PNG/TIFF 物理尺寸、文字与颜色；PNG/TIFF RGBA 相同 |
| n3f-svg-export-ui/service-exit.json、stop-http-verification.json、root-close-proof.json | 新 UI 源冻结 1791 份/11 bundle 末核；浏览器 36 close0；原 exec66615/child 正常 exit0，24768/50148 消失、24109 无监听；A/B/C/D 原稿和计数4保持 |
| p2-integration-map/IMPLEMENTATION_MAP.md、source-map.json | 只读 P2 接线图与缺口，不是产品实现或测试通过 |

平台夹具候选 SHA256 为 2d631dbed464b6c720b3331406aa05acb1a732f352f8bc3bb474c6f2d935dbf3：wheelhouse 用 file URI；两项 fresh venv 排除本机额外 .pth；一项离线 base 解释器规范化真实路径；目录链接复用既有精确 skip。原断言 AST 保留。候选轮为 10 pass/1 fail/1 skip，最后唯一失败的修复单项 1 pass；**不是一次最终 12 项全绿**。符号链接场景本机缺权限，仍未验证。

当前没有本任务自有 live 浏览器、服务或测试句柄；三名既有子代理均已交付。旧一次性 serve/安装目标不能重用为 fresh。下一轮新建证据生命周期、继续现有 data/config，不重新安装/采用正文掩盖缺口。

## 3. 下一阶段分批任务与退出条件

执行顺序：**P3 测试夹具 → P3 真实导出收口 → P2a 历史来源与热编辑 → P2b INPUT 冷恢复 → P2c 完整迁移 → N5 裁切 → N4 图例 → M1/M2/M3**。不因某批有外部条件缺失而停止全部独立工作。

### 批次 1：完成 P3 当前回归与真实导出

1. 复核并应用上述仅两文件夹具候选，先 Ruff 再正式运行两个完整测试文件。保实际 pip/旧代保护断言，不改生产授权/来源完整性；WinError1314 精确 skip 如实报告。单独提交测试夹具修复。
2. 按原 UI 请求复现普通 PNG 准备失败。文件本轮实际存在且为 130 B、画布中成功使用；先核原图尺寸/来源事实与准备请求，不能把“准备失败”直接解释为磁盘不存在，也不能绕过原 SourceResolver。
3. 接原导出文件名权威和作业协调器，修同名多扩展素材批量输出。来源身份与显示名分开；同批、并发与已有磁盘冲突均走原重命名/替换/取消合同。ordinary.pdf 与 ordinary.svg 均产出，不静默覆盖/丢掉 conflict 作业。runtime 名称经原合法文件名消费者处理，前后同源；不另建命名规则。
4. 独立核对普通 PDF 原图尺寸口径：本次请求40×40mm，而 PDF 为约35.278mm、833px位图对应原生尺寸；区分原生图幅和页面放置，修展示/请求/产物的实际合同差异，不强改素材或 golden。
5. 正式 UI 重新验证 A 全对象混合画布和逐图批次，含 ordinary.svg 原图四格式、无/有留白及标注；独立读实物尺寸、文字、颜色、透明、源 SHA/fullref、原稿与计数。保本轮 partial/conflict 首红，不覆盖证据。
6. 整版 SVG 的 ordinary.pdf 限制另有确切原因：静态 PDF 缺可信同次 SVG。先对照现有能力矩阵与第三路径规则做原矢量契约的有界实验；不得按同名外部 SVG 伪造配对、删 PDF、整页转位图或只改提示关闭缺口。要求支持而未实现的格留 M2 待完成。

**退出**：正式失败文件复验、批量来源/命名与 P3 SVG 原图流程通过，现有对象/编辑/原稿保持；当前支持与未实现格式逐格记录。正常结束所有自有进程后，按完整依赖提交 P3 画布/原图功能；独立命名修复另 commit，共享前置不拆成不能运行的提交。受影响规范/ADR/待发说明同步。P3 未完整退出前不提前启动删源冷迁移。

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

1. 沿当前唯一树/分支，不新建或切分支/worktree；保改动和首红，不 reset/stash/git add .，不 push/PR/合并/发布。按功能定向 commit，状态文档另交。
2. 每批：规则/实际状态→最小实现→必要反证→受影响验证→退出码/源SHA/实物→定向提交→日期checkpoint→下一批。先前绿组源未变不重跑，最终冻结后才做完整链。
3. Python先Ruff再pytest；前端类型走正式build及所需派生产物；不以 tsc --noEmit 代替。新失败先分产品、夹具和观察器，不删断言/放容差消红；同类两轮仍扩散先按第三路径审结构。
4. 已有多代理授权；仅独立任务有收益时并行，每文件一个写入者。root统一合同/浏览器/集成/提交，先核既有代理，避免重复实验。
5. UI期间冻结产品源/build，隔离自建证据与正常认证；只读观察，不注入store/伪造成功。不重用终局session；只关闭核明身份的自有进程，末核HTTP bundle/PID/listener及持久稿。
6. 长检查前后、提交及约30分钟追加 PROGRESS：HEAD/diff、所有权/句柄、命令/cwd/env、真实 fail/skip、实物SHA、首红、未验项和下一条动作。对用户的工作更新不超过60秒失联。
7. 保认证、沙盒、隐私、零PyMuPDF和 config.data_dir；写回仍 prepare→完整重放/几何/像素verify→commit，失败409原件零改。
8. 无人值守指活跃会话自主接续，本任务未创建常驻调度器。预算/时长不替代验收；遇外部条件缺失记录依赖并推进独立就绪项，不反复只写计划。
