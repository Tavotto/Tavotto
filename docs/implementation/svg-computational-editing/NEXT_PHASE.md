# SVG 计算图表编辑：当前效果与下一阶段安排

> 当前 checkpoint：2026-11-07 14:05（Asia/Shanghai）。唯一实施树 E:/0 - Project/Tavotto-svg-r00，沿用 zixu.wang/svg-computational-r00。产品 HEAD 35adee6d 已独立提交 N3e 安装采用；原 P0/归档提交保留。P3 普通 SVG 画布及原图转换已应用工作区：新专项28项、越界裁剪回归2项、前端70文件1514项通过。带留白/标注的多格式来源一致性仍是正式代码阻塞，OUT修复9项通过、原相关合同55通过/1跳过，但尚未应用；广泛后端旧契约、新正式构建/HTTP未验证。原 E:/0 - Project/Tavotto 为 clean main@69bcafae，未合入；本次未新建分支/worktree或远程发布。
> 本文维护当前安排；[PROGRESS.md](PROGRESS.md)保留日期证据，[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)维护 SG00–17 完整范围，[NEXT_PHASE_PROMPT.md](NEXT_PHASE_PROMPT.md)可直接复制接续。恢复以实际 HEAD/diff 为准，不回退到文档 SHA。

## 1. 当前判断

**安装、计算图内编辑、保存与归档预览已有真实效果；完整 SVG 交付仍未完成。** N3e 已真实验证写入成功后取消迟到确认、同项目继续保 undo、跨项目恢复最新稿及磁盘/修订/原稿/计数，正式提交35adee6d。当前P3实现已接原FrozenSource/IR/RenderCore，正式专项通过；独立审查确认两处问题，面板越界已正式修复，带留白/标注的多格式来源混用仍待正式修复。接下来先解决该阻塞，再验证旧契约、实际HTTP导出和产物；专项或OUT候选成功不等于完整导出已验。

产品方向保持轴模型、显式数据绑定及可信绘图来源恢复。普通 SVG 的图元可拖动，不等于具有原始数据或可恢复 Python 来源；缺少语义的输入仍要能沿现有显式绑定入口完成计算建模。

| 范围 | 本轮实际结果 | 当前边界 |
| --- | --- | --- |
| 完整混合安装 | attempt-05 正式 35 成员、双画布 11 对象、5 个归档引用、2 个 INPUT 图/8 个输入附件；安装 201、严格资源核验 exit0 | 未删旧来源，未做完整新进程冷迁移 |
| 明确采用 | 唯一初次 PUT39；完整正文/revision、真实 panels/磁盘独立核验 exit0；native 与页面放置分开 | 前轮失败保留，未放宽单 PUT 判据 |
| 计算编辑 | Inspector 12 → 撤销 7.8 → 重做 12 pt；三次保存仅目标线宽及 updatedAt 变化；命名保存成功 | 证明声明式计算图，不证明历史 Figure 均能继续计算编辑 |
| 历史显示 | 五个完整引用的 HTTP SVG 与可见 DOM Blob 字节一致；版本/导出预览 14 条 PNG Blob 与原 PDF 独立渲染逐 RGB 通道相等，容差 0 | 原 CDP 七条 response.body 空值仍是观察边界，未伪称恢复原 wire 字节 |
| B 画布导出 | 实际 PDF + PNG + 报告生成；PDF 180×130 mm、三处 value6、红/红/绿；PNG 4252×3071、600 dpi 独立实读通过 | 只覆盖 B 本次 PDF/PNG，不能替代完整 scope×format |
| A 画布导出 | 旧正式HTTP含ordinary.svg整版PDF/PNG被拒；P3现已补普通SVG接线和专项 | 新源码正式HTTP尚未复验，不能沿用旧失败为新实测，也不能用专项代替完整混合画布 |
| 跨项目最新稿 | 原 422 已修：真实 A→B→A，close8/9/10 与 AS13/14/15 均正确 A/200，writer17 及最终磁盘全文/revision 闭合，12 pt/旧稿/计数保持 | 原失败保留；本轮保留缓存，不是 N3f 完整删源冷迁移 |
| 当前前端验证 | 70文件1514 pass/0 fail/error/skip，28.43秒、1502前端源与当前逐SHA相同 | 受影响联合非全量；P3正式/派生构建尚未跑，原1320与构建属于N3e源窗口，不能累计或移用 |
| 历史分派候选 | safe Python24项/Rust3项及真实safe实验；native30+wire7项、四反证及真实native实验通过 | 尚未应用；app/fullref/持久编辑来源与冷准备仍缺，候选测试不充作产品历史编辑通过 |
| 取消确认场景 | D 安装90/201、唯一初次PUT137/200；真实确认GET200暂停→UI取消→原样释放，后续编辑/undo/redo/命名/两种继续均通过 | C 首次工具命中前置404没有构造成功，原失败保留；两个安装目标均已存在，不能重装冒 fresh |
| 普通 SVG 合成 | 完整候选已入工作区，新28专项、CSS overflow裁剪2专项通过 | 尚未提交；旧后端合同/正式A HTTP/独立跨renderer待验，gradient/mask/pattern/marker等仍未齐 |
| 原图多格式一致性 | 真实留白导出可出现PDF红、PNG蓝；OUT最终9回归通过，原静态7红及chart混合EPS1红保留 | 正式app仍未修；先统一同次冻结/scene job，保原尺寸、xy DPI、标注顺序和EPS条件，再扩大验证 |
| 总体 | 后端 135aba94、归档前端 e2c026fa、Moment 归属 c9e2980d、N3e 35adee6d 已提交 | P3 工作区、历史计算编辑/N3f 完整冷迁移/N5/N4/M1/M2/M3 未退出，未发布 |

本次重新查看[最新跨项目继续后的12.47pt图内编辑](<E:/0 - Project/svg-review-20261106/n3e-ui-cancel-confirm/output/playwright/d-cross-project-latest.png>)及[原A的12pt图内编辑](<E:/0 - Project/svg-review-20261106/n3f-ui-owner-resume/output/playwright/owner-resumed-12pt.png>)。曲线、属性和标题恢复可见；左/下刻度及部分标题裁切仍在，属于待办N5，不能宣布视觉验收通过。N5可见内容裁切与本轮已修的CSS越界是不同问题。

## 2. 已冻结证据和运行状态

证据根 E:/0 - Project/svg-review-20261106。不同窗口分别保留；已实现、当前源码已验证、交付资格分别记录。

| 证据（相对证据根） | 支持的结论 |
| --- | --- |
| n3f-mixed/attempt-05/ready.json、n3f-mixed/calibrated-freeze.json | 正式包 65661 B，SHA256 381625a558caa6ed62a0841bf8fc1505070567d1bb25bf082e249949ff865f2c；原 attempt-04、167 件及默认 oracle 保留 |
| n3f-mixed/installed-calibrated-live.json、initial-calibrated-live.json | 新根资源及唯一初次 PUT 全文/revision 严格通过；新 pj=1f4c864a2576、working=d_u1yy7f6i |
| n3f-ui-calibrated/ui-readonly-proof/qualification.md、freeze.json | 五引用→原 SVG→可见 DOM、三次编辑全文/revision、旧稿/计数、B 导出实物；七个反证目标失败 |
| n3f-ui-calibrated/png-dom-proof/README.md、validation.json | 五引用/14 条 DOM PNG，容差 0；引用/颜色/位置三条反证实红 |
| n3f-ui-calibrated/png-observer-review/REVIEW.md | Playwright POST 在 CDP 空 body 下返回空 Buffer 的工具边界；不算产品零字节，也不冒称原 body 补齐 |
| n3f-ui-calibrated/output/playwright/135-after-project-switch.json、148-review-repeat-switch-requests.json | 两次离开 A 各三条 preview_png 携错误 B 地址、422；同次 writer 正文地址仍 A |
| n3f-ui-calibrated/output/playwright/149-review-repeat-initiators.json | 第二次现场实际 CDP 发起栈，为冻结压缩 bundle 坐标，不直接当源码函数名 |
| n3f-owner-second-review/REVIEW.md、validation.json | 原adopt→writer→Moment→renderer→API首红及四行候选历史证据；正式修复已在c9e2980d，不再套该候选 |
| n3f-edit-source-prototype/REVIEW.md | 同 receipt 活 Figure 可用原 manifest/SVG 编辑/撤销而不重跑；peek 后 lazy build/重连仍须结构守卫 |
| n3f-preview-final/status-review-source-qualification.json | 1273 受影响联合、正式及 playground/widget 构建/check 的实际退出和源码 SHA |
| n3f-ui-calibrated/review-closed-window.json | 最终 AS 全文/revision 对应 PUT364；恢复稿仅时间戳变；服务/浏览器正常退出，父子 PID 消失、24079 无监听 |
| n3f-owner-production/AFTER_CONTRACT.md、source-release-after-contract.json | 新 6 条回归、原修复前 2 红、撤掉 owner 反证再 2 红；修复后 8 文件 138 绿；无参 prepareDocument 合同及真实 ready/prepared 门断言 |
| n3f-owner-final/source-and-ui-qualification.json | 68 文件 1320 绿、六组正式/派生回执、1790 源与 11 bundle、原父子 PID 消失及正常终局 |
| n3f-ui-owner-resume/ui-owner-proof/ | 实际 close/AS 六条正确归属、完整 ref/patch/model、A→B→A、原稿/磁盘正文和原 422 反证；实际离开图 Blob 限定资格见该目录报告 |
| n3f-mixed/n3e-cancel-confirm-proof/FINAL_REPORT.md、qualification-final.json | D真实取消/迟到确认、单初次PUT、编辑undo/redo、同项目及跨项目最新稿、AS/named字节与修订；C首次前置404失败保留 |
| n3f-svg-compose-review/original-qualification.md/.json、production-targeted-first.xml | 16文件完整候选已应用，正式新28项通过；原writer PDF golden及候选共享预算反证保留，正式A HTTP未验 |
| n3f-svg-compose-independent-review/REVIEW.md、production-overflow.xml | 两条实际P1；CSS越界已入正式工作区、两回归通过；原图多次冻结问题尚未正式修复 |
| n3f-svg-production/frontend-first-exit.json、frontend-first.xml | 正式70文件1514通过，无fail/error/skip；1502源与本次审计当前字节一致 |
| n3f-original-source-consistency/candidate-final-nine.xml、existing-related.xml | OUT新9项通过；原annotations/pad/chart EPS合同55通过/1跳过；补丁已交接、root apply-check0，待审查及正式应用 |
| n3f-edit-guard-candidate/REVIEW.md、source-release.json | 独立 render_existing 分派、可移植 24 项/Rust 3 项及 safe worker 实验；native/app/来源持久化仍待实施 |
| n3f-edit-native-guard-candidate/REVIEW.md、handoff.json | native锁/屏障/真实execution候选37项、四反证及真实native实验；源/进程均封口，尚未应用 |
| n3f-svg-export-ui/PREPARATION.md | 新24109生命周期8工具准备；保原A/B/C/D和counter4，尚未freeze/serve/HTTP/browser |
| status-review-20261107-1400/audit.json | 本次只读核HEAD/diff、1514XML及1502源、28/2正式专项、OUT7红/7绿、原main与关闭证明；不是新产品测试 |

**当前没有待续的本轮自有浏览器/服务。** 最新 n3e-cancel-confirm-20261107 已 124-normal-close exit0；原 exec50760/child76164 终局0，launcher23968/child76164 均消失、24099 无监听，停前1790源/11HTTPbundle末核通过。见 n3e-ui-cancel-confirm/service-exit.json、root-close-proof.json；完整场景证据见 n3f-mixed/n3e-cancel-confirm-proof/FINAL_REPORT.md、qualification-final.json。旧 owner/calibrated 窗口也均终局，不能续等旧句柄。新 P3 生命周期准备仅 OUT，不能借旧1790源资格启动已改 P3。

attempt-05 新根和原 UI data 已存在，须保留。旧 fixture.py serve 要求目标不存在，**不能直接重运行**。修复复验须创建新生命周期目录和源冻结，明确从已有安装/原持久状态继续；不能重装覆盖、重新采用原包正文或清状态掩盖问题。只有独立 fresh 安装测试才用新目标。

## 3. 下一阶段批次

最近可交付目标：**P3 普通 SVG 画布及原图跨格式导出正式落地、核验并分功能提交。** N3e 已退出，不重新实施安装。随后推进历史来源/执行守卫与完整冷迁移；P2/P3 保留原任务编号，以下表格位置表示实际顺序。先消除已知导出阻塞，减少冷迁移重复验证，不缩减任何验收项。

| 顺序 | 交付物 | 退出条件 | 独立提交边界 |
| --- | --- | --- | --- |
| P0 / 已退出 | 离开 Moment 与 writer 同源地址 | 新 6 回归、138 专项、1320 联合、正式/派生构建、真实 A→B→A/离开图均已验 | c9e2980d，3 文件；N3e 规则段未混入 |
| P1a / 已退出 | HTTP 归档前端收口 | 同名 A/B、完整 ref、图片上下文及原预览消费者资格保留；当前窗口通过 | e2c026fa，17 文件；N3e API wrapper 未混入，替代 transport 仍列 M2 |
| P1b / 已退出 | N3e 安装/采用/继续编辑收口 | 严格初次1PUT、取消/迟到确认、同项目undo及跨项目最新稿、原稿/磁盘/修订实际通过 | 35adee6d，17路径，含 ADR/规则/翻译/待发说明 |
| P3 / 进行中 | 普通 SVG 画布及原图跨格式导出 | 先修原图多次冻结；正式A PDF/PNG/TIFF/适用SVG及原图实物、旧合同/资源/裁剪/透明/同基准验证通过 | 画布合成、原图转换按完整依赖分功能commit；复杂SVG继续M2，不提前宣称全部表达支持 |
| P2a | 历史编辑来源及执行守卫 | fullref、同 receipt 活 Figure、parent/child/重连零隐式 build；来源贯穿首改/撤销/保存/包/重开，旧 A 不借 B | 来源合同、guarded render、真实消费者分功能；镜像合同与消费者同交 |
| P2b | 原 INPUT 准备服务冷重建 | 精确 source/data/argv/rc、原 safe/native 路由和当前授权；真实 pair 几何/像素合格再进入原编辑，取消回收 | 原管线扩展及回归，不建第二 runner/store/writer |
| P2c | N3f 冷迁移/真 runtime | 核明自建路径后移除旧源/派生 cache，新父/新浏览器；双画布历史、编辑/undo/保存/再开/版本恢复及已支持导出，真 runtime 另验；A 导出待 P3 后联合退出 | 实际缺陷各自 fix，完整迁移资格另交，不提前标 N3f 全过 |
| P4 / N5 | 刻度裁切修复 | S/F 同源合同接全消费者；字形完整、数据锚点/单位/命中/缩放不漂，热冷/导出一致，旧 native1/Python 保持 | 合同/兼容和消费者按完整可用功能提交 |
| P5 / N4 | 图例绑定 series/custom | 同名同色不串，复合句柄/手工文字/未绑定注释保留；编辑/撤销/保存/冷开/导出一致 | feat(svg): bind legend entries to computational series 等独立功能 |
| P6 / M1 | 双系列+图例+注释最小完整闭环 | 数据/轴范围/样式→undo/redo→保存→空派生缓存冷开→适用 scope×format 真产物；画布 EPS 仍 eps_not_for_canvas | 缺口各自修复，完整 M1 资格另交 |
| P7 / M2 | SG00–17、A/B/C 能力对等 | 原功能登记逐格映射；图族/多轴/投影、文字/组/use、写回/出版/预检、全部入口有证据 | 按独立能力提交；缺实现不标不适用 |
| P8 / M3 | 最终兼容/交付资格 | 冻结后完整 Python/旧项目回归、等价性/CompatBench/E2E/性能、支持平台实际安装物与进程清理 | 最终验证/交付文档，未授权自动 push/合并/发布 |

### 3.1 P0 与 N3e 已收口

原 projectStore.adoptSteps 已在切全局项目之前 pinDocumentOwner；writer 正确用 projectOfDoc。documentStore.flushAutosaveNow 的 captureDocumentMoment 却仍读全局 currentProjectId，把 A 正文配上 B 地址。不能修下游 API 或放松后端 422 遮住它。

四处 Moment 现已消费本次保存已冻结的 owner、address.projectId、scope.pj、scope.pj。新增真实原链回归覆盖 autosave/迟到 ABA、persistEmpty、named 空稿、overwrite/reload 冲突；原 2 红和 AST 撤修反证保留，正式 138 专项与 1320 联合通过。不重套旧外部候选 patch。

保原项目切换顺序、恢复门、writer/queue、timeline gen 和 Promise 生命周期。回归执行原 adopt/writer/Moment/renderer/API 链，量离开 A 那次保存的正文与捕获地址，而非仅 mock 正确 pj。候选 exit0 不能移作生产修改后的资格。

主代理已完成当时新SHA构建与正常认证UI；archive→P0顺序由完整引用回归依赖归档消费者决定。api.ts归档hunk入e2c026fa，writer地址规则入c9e2980d；N3e余下实现及文档均已入35adee6d，不再保留该批未提交任务。

N3e 已在两个独立新目标完成真实场景并提交，不再重做。首次 C 只命中保存前404，准确保为时序工具前提未成立；第二次 D 工具只在实际PUT200后延迟同scope确认GET200，取消后原APIResponse原样交付。D 唯一初次PUT137、native5→8→5→8→5→8、命名保存及同项目/跨项目继续均核完整正文/修订；A/B原稿、命名文件和执行计数4保持。13pt超原生max8换算后页面上限，正常钳为12.47pt；不是13pt原样接受，也不是新数值bug。源冻结解除于正常终局后，当前P3需新资格窗口。

P2c 在 P3 和历史来源就绪后开始完整冷迁移；此前保留的 A 导出失败要在原对象/引用完整的画布上由正式请求复验，不能用单 SVG 实验替代。出现独立缺口时保失败、修对应能力，不循环重跑相同冷开或宣称 N3f 全过。

### 3.2 接下来三批的具体执行安排

1. **P3a：关闭多格式来源阻塞并完成后端资格。** 来源一致性补丁已在n3f-original-source-consistency/original-composition-one-scene.patch交接（SHA256 34272c622af365f91a142114952594aa4c6b868532bfff6f8d624a752515b6de），root核当前app基线与apply-check0。下一动作审查该单函数及tests/test_original_composition_sources.py，再正式应用。正式应用后先Ruff，再跑新9行为、原标注/留白/尺寸/xy DPI/EPS组；同次原图所有非EPS产物共用冻结来源和scene job，需要chart EPS内部SVG时在同次产物中取得并过滤内部输出。源在第一份canonical后被合法改写时，PDF/PNG/TIFF/SVG仍来自同版本，或明确失败，不能成功混交。随后冻结后端源，跑RenderCore全组、旧facade/export/compose/SVG安全/配对/退休扫描；保真实失败与skip，不用调用次数替代图像/几何结果。已应用16文件完整候选及overflow补丁，不再叠套旧15文件补丁。
2. **P3b：正式构建、真实导出及提交。** 当前前端70文件1514通过；源未变不重复跑。后端稳定后运行正式build_frontend及playground/widget构建/check，按新源SHA取得真实回执。沿n3f-svg-export-ui/PREPARATION.md新24109生命周期，使用现有A及原data/config，经正常UI validate→start→job导出完整混合画布和ordinary.svg原图的PDF/PNG/TIFF/适用SVG；覆盖留白/标注、多实例CSS/ID、crop/rotation/opacity。独立PDFium/PIL核物理尺寸、文本、点位、颜色、透明、全对象/fullref和counter4；新增SVG分支用可用Chromium/Inkscape独立核验，未运行项明确留空。补规范/ADR/待发说明，正常终局、末核HTTP/源/PID后按完整功能依赖commit。gradient/mask等仍缺的族继续列M2，不能静默整页栅格化或空白成功。
3. **P2：历史计算编辑与冷恢复。** safe/native候选均已完成OUT实验，读各自REVIEW/source-release；两份native补丁是delta或combined二选一，不能同时应用。正式接parent/child/重连执行守卫、app正常认证/fullref/receipt及持久编辑来源，再接原Preparation/INPUT。分别核safe/Workerd/native同次热编辑零重跑、冷/错来源零隐式执行、取消/迟到及普通Python原样重建。新A/C/D的History render500缺来源仍属未解决项，归档显示成功不能关闭。只有应用层、持久化和准备链均闭合，才做P2c双画布完整冷迁移及真runtime附加包。

UI/构建由root串行控制；独立协议或格式实验可在OUT并行，每文件一个写入者。来源一致性候选也已完成交接，无本轮子任务测试/服务live句柄；恢复先核状态，不重复派同一实验。失败先分清产品、夹具与观察器主语；相同问题修过两轮仍扩散，按第三路径规则审结构，不靠更宽容差或删用例收口。

### 3.3 P2：显示与真实编辑来源分别闭合

归档显示保持原 PDF/SVG 配对及 SVG 图片上下文，不制造 editable manifest。历史来源原型已证明同 receipt 活 Figure 能复用原 renderer 编辑/撤销而不重跑；也已反证 peek 后直接 override：built=False 会 lazy build，workerd unknown_session 可能透明重连。

生产守卫须贯穿 parent 实际 dispatch/worker 实例、workerd 重连及 child dispatch，核真实 execution identity。不能只在 Flask 核一次，或传目前不生效的 require_built 字段；safe/native 的原路由、锁/屏障及 legacy 无显式来源路径保持。

持久编辑来源与当前输出 figureSnapshot 是不同角色。首 override 清输出后仍须保来源，不能退同名当前源。优先复用合同；扩展时同步前后 schema、包闭包/ref 遍历/迁移，贯穿 request/renderKey/want/last/latest/recent/mounted/exact。持久引用不是运行授权。

冷态沿 load_input_archive、PreparationPlan/PreparationService、safe spec/runconfig/工作目录及原 runner。INPUT 比对视图不是可执行目录，rc/argv 不继承本机授权。实际 pair 独立几何/像素对照合格再交原编辑器，不把图片显示当可运行来源。

冷迁移仅清绝对路径/归属/清单核明的自建旧源和派生缓存。保新根、包、INPUT、AS/命名稿、runconfigs/config、绑定/打开意图和 layout-sessions(.bak)；data/cache 含 AI history，不能全清。先结束旧父/浏览器再清清单路径，启新父/新浏览器。真 runtime 附加包 n3f-runtime-kind/attempt-02/真正runtime家族.tavotto 为 12 成员，本轮混合包 runtime 来源对象实际 pdf，不能替代其 fileKind/fallback 资格。

### 3.4 P3/P4：混合导出与真实裁切

A 画布普通 SVG 被画布/跨格式门拒绝属于最终目标实现缺口。先用原 ordinary.svg 与复杂 SVG 语料有界实验，接原 SourceResolver/FrozenSource/IR/ExportRequest/job，保静态 SVG 校验权威及字体/资源策略。不得要求用户预转 PDF 作为终点，不得静默栅格化降保真。B 本次 PDF/PNG 通过不能关闭此项。

N5 外部 n5-tick-clipping-review/prototype/IMPLEMENTATION.md、validation.json 有 14 文件 overlay、13 后端/92 前端及反证，尚未接产品。保 S 坐标、派生 F，同次 draw render_frame/native-manifest/2 与旧 /1 兼容；补 origin/profile schema、全部请求/cache/exact、frame 采用事务、F→S 写入、C 确认、原图/画布/多页导出及真实 ink/page 策略。

沿原 SVGIR winning declarations 处理 CSS/percent/clip/use/PAR/旋转/非零原点。不能只扩 viewBox 使数据缩短，也不能缩字体/轴、改用户页面掩盖裁切。x10 负框不证明 glyph 越页，x5 clone 才有真实字形越界反证；布局变化沿原明确用户事务。

## 4. 无人值守执行与恢复

1. 沿当前唯一树/分支；保所有改动/证据，不新建或切分支/worktree，不 reset/stash、无审查 git add .。本地按功能 commit，状态文档另交。
2. 实际顺序P3来源一致性/旧契约→P3构建/真导出/提交→P2a/b历史来源/重建→P2c冷迁移→P4/N5→P5/N4→M1/M2/M3；P0/P1a/P1b均已退出。已有多代理授权，仅独立有收益任务并行，每文件一个写入者，主代理统一合同/浏览器/集成/提交；先核现有代理，不重复派工。
3. 每批核状态/规则→实现真实消费者→必要反证→受影响验证→实际退出码/源 SHA/diff→定向 commit→日期 checkpoint→下一项。未改源不重复跑绿组；失败先分清产品/夹具/观察器主语，不削弱验收。
4. Python先Ruff再定向pytest；前端正式及playground/widget按要求验证。最终候选再跑完整链，1514受影响联合不代全量；旧1320/新1514及候选组不相加。当前源码未变不为状态复核重复构建。
5. UI 期间冻结产品源/build；正常认证、隔离 data/config/证据、关闭遥测。只关闭核明 PID/创建身份的自有进程，停前核实际 HTTP bundle、终局核 listener；不以已有目标冒 fresh。
6. 长检查前后/提交/约30分钟更新 PROGRESS：HEAD/diff、所有权/句柄、命令/cwd/env、actual exit/fail/skip、SHA/实物、首红/下一项。中断从最新 checkpoint 续，不能复用终局 session/旧 bundle。
7. 局部失败修复并继续；缺平台条件精确保留未验格，推进独立就绪项。写回仍 prepare→完整重放/几何/像素 verify→commit，失败 409 原件零改；认证/沙盒/隐私/零 PyMuPDF 不放松。
8. 无人值守指活跃会话自主接续，当前未创建常驻调度器。不以一晚时长或测试数量判完成；M1/M2/M3 全退出前总目标保持未完成。
