# SVG 计算图表编辑：当前效果与下一阶段安排

> 复核时间：2026-11-07 12:17（Asia/Shanghai）。唯一实施树 E:/0 - Project/Tavotto-svg-r00，沿用 zixu.wang/svg-computational-r00。复核起点 HEAD f69fa488，最近产品提交 135aba94；产品前端仍有未提交改动。原 E:/0 - Project/Tavotto 为 clean main@69bcafae，成果尚未合入。本轮未 fetch、切分支、新建 worktree 或远程发布。
> 本文维护当前安排；[PROGRESS.md](PROGRESS.md)保留日期证据，[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)维护 SG00–17 完整范围，[NEXT_PHASE_PROMPT.md](NEXT_PHASE_PROMPT.md)可直接复制接续。恢复以实际 HEAD/diff 为准，不回退到文档 SHA。

## 1. 当前判断

**基础计算编辑、完整项目包安装和历史图显示已取得实际效果；“所有现有功能对等、最终可用”尚未验收。** 校准后的完整 35 成员包已经通过严格初次单 PUT 验证，前轮两 PUT 的夹具问题已闭合。新阻断项是离开项目时的预览归属分叉，不能因为单元测试和构建通过而跳过。

产品方向保持轴模型、显式数据绑定及可信绘图来源恢复。普通 SVG 的图元可拖动，不等于具有原始数据或可恢复 Python 来源；缺少语义的输入仍要能沿现有显式绑定入口完成计算建模。

| 范围 | 本轮实际结果 | 当前边界 |
| --- | --- | --- |
| 完整混合安装 | attempt-05 正式 35 成员、双画布 11 对象、5 个归档引用、2 个 INPUT 图/8 个输入附件；安装 201、严格资源核验 exit0 | 未删旧来源，未做完整新进程冷迁移 |
| 明确采用 | 唯一初次 PUT39；完整正文/revision、真实 panels/磁盘独立核验 exit0；native 与页面放置分开 | 前轮失败保留，未放宽单 PUT 判据 |
| 计算编辑 | Inspector 12 → 撤销 7.8 → 重做 12 pt；三次保存仅目标线宽及 updatedAt 变化；命名保存成功 | 证明声明式计算图，不证明历史 Figure 均能继续计算编辑 |
| 历史显示 | 五个完整引用的 HTTP SVG 与可见 DOM Blob 字节一致；版本/导出预览 14 条 PNG Blob 与原 PDF 独立渲染逐 RGB 通道相等，容差 0 | 原 CDP 七条 response.body 空值仍是观察边界，未伪称恢复原 wire 字节 |
| B 画布导出 | 实际 PDF + PNG + 报告生成；PDF 180×130 mm、三处 value6、红/红/绿；PNG 4252×3071、600 dpi 独立实读通过 | 只覆盖 B 本次 PDF/PNG，不能替代完整 scope×format |
| A 画布导出 | 含 ordinary.svg 的整版 PDF/PNG 实际被现有能力限制拒绝 | 普通 SVG 画布/跨格式合成须补实现，缺实现不能标“不适用” |
| 跨项目最新稿 | 恢复后 named 全文保留、AS 只变 writer 时间戳；最终 PUT364 与磁盘全文/revision 闭合；旧稿保留、计数 4/4 | A→B 两次各三条预览错用 B 并返回 422，尚未修入生产 |
| 已有验证 | 前端 66 文件 1273 pass/0 fail/error/skip；正式和派生构建/check 全 exit0，1500 前端源零漂移 | 受影响联合，非完整回归；重叠历史组不相加 |
| 外部原型 | 历史来源/guarded dispatch、N5 S/F 候选已有实验；P0 候选原消费者链由红转绿 | 候选/原型尚未生产落地 |
| 总体 | 后端归档预览已 135aba94；前端归档、N3e 安装采用尚未功能提交 | N3f 完整冷迁移、N5/N4、M1/M2/M3 未退出，未发布 |

已实际查看的截图：[12 pt 图内编辑](<E:/0 - Project/svg-review-20261106/n3f-ui-calibrated/output/playwright/calibrated-edit-12pt.png>)、[历史 B 红/红/绿](<E:/0 - Project/svg-review-20261106/n3f-ui-calibrated/output/playwright/calibrated-history-canvas.png>)。左/下刻度及部分标题裁切仍可见，不能宣布视觉验收通过。

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
| n3f-owner-second-review/REVIEW.md、validation.json | 原 adopt→writer→Moment→renderer→API 首红 exit1；外部四行候选 exit0、原 PUT 全文不变，尚未应用 |
| n3f-edit-source-prototype/REVIEW.md | 同 receipt 活 Figure 可用原 manifest/SVG 编辑/撤销而不重跑；peek 后 lazy build/重连仍须结构守卫 |
| n3f-preview-final/status-review-source-qualification.json | 1273 受影响联合、正式及 playground/widget 构建/check 的实际退出和源码 SHA |
| n3f-ui-calibrated/review-closed-window.json | 最终 AS 全文/revision 对应 PUT364；恢复稿仅时间戳变；服务/浏览器正常退出，父子 PID 消失、24079 无监听 |

**当前没有待续的本轮自有浏览器/服务。** CLI session n3f-calibrated-20261107 已 close exit0；原服务 exec28051/child4464 均终局 exit0，launcher55348/child4464 不存在。停前实际 HTTP 11 bundle 已重读，1789 源零漂移，见 stop-bundle-verification.json、service-exit.json。源码冻结可解除，不能续等终局句柄。

attempt-05 新根和原 UI data 已存在，须保留。旧 fixture.py serve 要求目标不存在，**不能直接重运行**。修复复验须创建新生命周期目录和源冻结，明确从已有安装/原持久状态继续；不能重装覆盖、重新采用原包正文或清状态掩盖问题。只有独立 fresh 安装测试才用新目标。

## 3. 下一阶段批次

最近可交付目标：**项目切换无错源、已安装新稿无损继续，归档预览与 N3e 安装采用分别完成本地功能提交。** 随后闭合真实历史计算编辑、冷迁移和混合导出。无人值守按依赖连续推进，不以一晚时长代替验收。

| 顺序 | 交付物 | 退出条件 | 独立提交边界 |
| --- | --- | --- | --- |
| P0 | 离开 Moment 与原 writer 同源项目地址 | 原链先红后绿；保存/命名/冲突/空稿/迟到/ABA 回归；真实 A→B→A 无错 pj、离开图正常、正文不丢 | fix(previews): bind save moments to the document owner，修复/回归/受影响规则 |
| P1a | 归档前端收口 | 保同名 A/B、完整 ref、缓存/迟到隔离、SVG 图片上下文及全部预览消费者；P0 后新源资格与 UI 通过 | fix(previews): display explicit archived Figure references；不混 N3e wrapper |
| P1b | N3e 安装/采用/继续编辑收口 | 保严格 1 PUT；当前源验取消/保存确认迟到/重开/切项目返回最新稿/旧稿无损；不重复建立 adopted 工作稿 | feat(packages): adopt installed charts through the document writer，带其 ADR/规则/翻译 |
| P2a | 历史编辑来源及执行守卫 | fullref、同 receipt 活 Figure、parent/child/重连零隐式 build；来源贯穿首改/撤销/保存/包/重开，旧 A 不借 B | 来源合同、guarded render、真实消费者分功能；镜像合同与消费者同交 |
| P2b | 原 INPUT 准备服务冷重建 | 精确 source/data/argv/rc、原 safe/native 路由和当前授权；真实 pair 几何/像素合格再进入原编辑，取消回收 | 原管线扩展及回归，不建第二 runner/store/writer |
| P2c | N3f 冷迁移/真 runtime | 核明自建路径后移除旧源/派生 cache，新父/新浏览器；双画布历史、编辑/undo/保存/再开/版本恢复及已支持导出，真 runtime 另验；A 导出待 P3 后联合退出 | 实际缺陷各自 fix，完整迁移资格另交，不提前标 N3f 全过 |
| P3 | 普通 SVG 画布/跨格式导出 | 实际 A 混合画布 PDF/PNG 由拒绝变可用；保文字/几何/clip/透明/资源，适用格式与多页核真产物 | feat(svg): compose SVG assets through the shared export pipeline，保原冻结来源/job |
| P4 / N5 | 刻度裁切修复 | S/F 同源合同接全消费者；字形完整、数据锚点/单位/命中/缩放不漂，热冷/导出一致，旧 native1/Python 保持 | 合同/兼容和消费者按完整可用功能提交 |
| P5 / N4 | 图例绑定 series/custom | 同名同色不串，复合句柄/手工文字/未绑定注释保留；编辑/撤销/保存/冷开/导出一致 | feat(svg): bind legend entries to computational series 等独立功能 |
| P6 / M1 | 双系列+图例+注释最小完整闭环 | 数据/轴范围/样式→undo/redo→保存→空派生缓存冷开→适用 scope×format 真产物；画布 EPS 仍 eps_not_for_canvas | 缺口各自修复，完整 M1 资格另交 |
| P7 / M2 | SG00–17、A/B/C 能力对等 | 原功能登记逐格映射；图族/多轴/投影、文字/组/use、写回/出版/预检、全部入口有证据 | 按独立能力提交；缺实现不标不适用 |
| P8 / M3 | 最终兼容/交付资格 | 冻结后完整 Python/旧项目回归、等价性/CompatBench/E2E/性能、支持平台实际安装物与进程清理 | 最终验证/交付文档，未授权自动 push/合并/发布 |

### 3.1 P0：先修已复现的正确性问题

原 projectStore.adoptSteps 已在切全局项目之前 pinDocumentOwner；writer 正确用 projectOfDoc。documentStore.flushAutosaveNow 的 captureDocumentMoment 却仍读全局 currentProjectId，把 A 正文配上 B 地址。不能修下游 API 或放松后端 422 遮住它。

先审 n3f-owner-second-review/moment-owner-candidate.patch：四处 Moment 分别消费该次保存已冻结的 owner、address.projectId、scope.pj、scope.pj。外部受控回归只实测离开 autosave 三条预览，后三处正式落地须覆盖原冲突/空稿等专项；apply-check=0 不是产品已修。

保原项目切换顺序、恢复门、writer/queue、timeline gen 和 Promise 生命周期。回归执行原 adopt/writer/Moment/renderer/API 链，量离开 A 那次保存的正文与捕获地址，而非仅 mock 正确 pj。候选 exit0 不能移作生产修改后的资格。

主代理审查并应用最小修复，再按新 SHA 补受影响联合及正式/派生构建。新生命周期从已有安装继续，真实验 A→B→A、最新稿/旧稿、原离开图及零隐式科学执行；首红和目标保留。通过后 P0/P1 定向分别提交，api.ts/规则混合 hunk 逐项核对。

P2c 若仅剩 A 的普通 SVG 导出缺口，保留该失败并直接推进 P3，不循环重跑冷开或宣称 N3f 全过。P3 完成后回验冷态 A 整版产物，再联合关闭完整迁移出口。

### 3.2 P2：显示与真实编辑来源分别闭合

归档显示保持原 PDF/SVG 配对及 SVG 图片上下文，不制造 editable manifest。历史来源原型已证明同 receipt 活 Figure 能复用原 renderer 编辑/撤销而不重跑；也已反证 peek 后直接 override：built=False 会 lazy build，workerd unknown_session 可能透明重连。

生产守卫须贯穿 parent 实际 dispatch/worker 实例、workerd 重连及 child dispatch，核真实 execution identity。不能只在 Flask 核一次，或传目前不生效的 require_built 字段；safe/native 的原路由、锁/屏障及 legacy 无显式来源路径保持。

持久编辑来源与当前输出 figureSnapshot 是不同角色。首 override 清输出后仍须保来源，不能退同名当前源。优先复用合同；扩展时同步前后 schema、包闭包/ref 遍历/迁移，贯穿 request/renderKey/want/last/latest/recent/mounted/exact。持久引用不是运行授权。

冷态沿 load_input_archive、PreparationPlan/PreparationService、safe spec/runconfig/工作目录及原 runner。INPUT 比对视图不是可执行目录，rc/argv 不继承本机授权。实际 pair 独立几何/像素对照合格再交原编辑器，不把图片显示当可运行来源。

冷迁移仅清绝对路径/归属/清单核明的自建旧源和派生缓存。保新根、包、INPUT、AS/命名稿、runconfigs/config、绑定/打开意图和 layout-sessions(.bak)；data/cache 含 AI history，不能全清。先结束旧父/浏览器再清清单路径，启新父/新浏览器。真 runtime 附加包 n3f-runtime-kind/attempt-02/真正runtime家族.tavotto 为 12 成员，本轮混合包 runtime 来源对象实际 pdf，不能替代其 fileKind/fallback 资格。

### 3.3 P3/P4：混合导出与真实裁切

A 画布普通 SVG 被画布/跨格式门拒绝属于最终目标实现缺口。先用原 ordinary.svg 与复杂 SVG 语料有界实验，接原 SourceResolver/FrozenSource/IR/ExportRequest/job，保静态 SVG 校验权威及字体/资源策略。不得要求用户预转 PDF 作为终点，不得静默栅格化降保真。B 本次 PDF/PNG 通过不能关闭此项。

N5 外部 n5-tick-clipping-review/prototype/IMPLEMENTATION.md、validation.json 有 14 文件 overlay、13 后端/92 前端及反证，尚未接产品。保 S 坐标、派生 F，同次 draw render_frame/native-manifest/2 与旧 /1 兼容；补 origin/profile schema、全部请求/cache/exact、frame 采用事务、F→S 写入、C 确认、原图/画布/多页导出及真实 ink/page 策略。

沿原 SVGIR winning declarations 处理 CSS/percent/clip/use/PAR/旋转/非零原点。不能只扩 viewBox 使数据缩短，也不能缩字体/轴、改用户页面掩盖裁切。x10 负框不证明 glyph 越页，x5 clone 才有真实字形越界反证；布局变化沿原明确用户事务。

## 4. 无人值守执行与恢复

1. 沿当前唯一树/分支；保所有改动/证据，不新建或切分支/worktree，不 reset/stash、无审查 git add .。本地按功能 commit，状态文档另交。
2. 依赖 P0→P1→P2→P3→P4→P5→M1/M2/M3。已有多代理授权，仅独立有收益任务并行，每文件一个写入者，主代理统一合同/浏览器/集成/提交；先核现有代理，不重复派工。
3. 每批核状态/规则→实现真实消费者→必要反证→受影响验证→实际退出码/源 SHA/diff→定向 commit→日期 checkpoint→下一项。未改源不重复跑绿组；失败先分清产品/夹具/观察器主语，不削弱验收。
4. Python 先 Ruff 再定向 pytest；前端正式及 playground/widget 按要求验证。最终候选再跑完整链，1273 专项不代全量。
5. UI 期间冻结产品源/build；正常认证、隔离 data/config/证据、关闭遥测。只关闭核明 PID/创建身份的自有进程，停前核实际 HTTP bundle、终局核 listener；不以已有目标冒 fresh。
6. 长检查前后/提交/约30分钟更新 PROGRESS：HEAD/diff、所有权/句柄、命令/cwd/env、actual exit/fail/skip、SHA/实物、首红/下一项。中断从最新 checkpoint 续，不能复用终局 session/旧 bundle。
7. 局部失败修复并继续；缺平台条件精确保留未验格，推进独立就绪项。写回仍 prepare→完整重放/几何/像素 verify→commit，失败 409 原件零改；认证/沙盒/隐私/零 PyMuPDF 不放松。
8. 无人值守指活跃会话自主接续，当前未创建常驻调度器。不以一晚时长或测试数量判完成；M1/M2/M3 全退出前总目标保持未完成。
