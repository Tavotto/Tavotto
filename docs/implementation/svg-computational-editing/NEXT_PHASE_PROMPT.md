# SVG 计算图表编辑：下一阶段无人值守提示词

2026-11-09 本轮收口后按用户要求暂停。下面只供用户明确恢复后复制，不会自动执行。结果与未验边界见 [PROGRESS.md](PROGRESS.md)，详细安排见 [NEXT_PHASE.md](NEXT_PHASE.md)，完整范围见 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。

~~~text
恢复 Tavotto 的 SVG 计算图表编辑工作，以完整功能可提 PR、既有 Python/旧项目不回归为标准。在当前唯一实施树 E:/0 - Project/Tavotto-svg-r00、分支 zixu.wang/svg-computational-r00 上继续；先核最新 PROGRESS、HEAD/diff、规则与 ADR。不要新建/切换分支或 worktree，不 reset/stash/git add .，不 push/PR/合并/发布。原 E:/0 - Project/Tavotto 的 main 和已有 A/B/C/D、data/config、INPUT、自动保存/命名稿全部保留。按功能定向 commit，必要验证后再接下一批。可以对独立任务使用多子代理，每文件一个写入者，根代理统一集成/提交。常规可逆步骤自行推进，不反复请求确认。

本轮已交付的 app 接纳和底层 execution-id 守卫不要重做：完整 figure_origin={version:1,snapshot:fullref,patches:P} 只绑定已有真实执行；当前 overrides 为全量 Q。复用原 enginesession.resolve、pool/REGISTRY、实际 receipt 和 execution ledger；冷/错 ref/P/receipt/eid/epoch 不隐式执行或换来源。新 PDF 内容适配只在内部模块，尚未接产品入口，不称普通 PDF→SVG 已完成。各功能提交、准确测试及源码冻结窗口以最新 PROGRESS 为准，不累计历史重叠结果。

第一优先修 native 磁盘图写回，再原子恢复前端自动来源消费者。前端 41 路径完整候选在 E:/0 - Project/svg-review-20261109/p2-app-frontend/pause-closeout/，审阅最终 patch/manifest；早期 WIP 不重复套。它因确认会让普通 snapshot-only native 磁盘图自动进入未完成写回验证而延期，生产前端已恢复已提交基线。不能把该问题只写成“新历史功能缺失”，不能置灰、移除 origin、改用当前同名 safe 图或跳过几何/像素门。

native 热图保权威；pool.one_shot 可作为独立候选验证器的第三路径已有 OUT 单例。阅读 p2-app-backend-tests/native-writeback-third-path.md：固定真实 interpreter/argv/cwd/env/输入回答/源修订，native Q 同次 manifest+PNG 强制原几何及像素两门，不能 fresh_only；候选不冒用 native receipt。当前 one_shot 不收通用显式 cwd/env，terminal 未知参数不能猜空；原 native preparation 会替换热态，直接 native 重跑 savefig 又可能在 verify 前写原件，不能直接复用。先实现结构与真实失败零改反例，再恢复全部自动捕获/缓存/回包/PNG/导出/specfix/写回/版本/sync 消费者，并正常认证 UI 验证既有 Python 不回归。

并行独立支线 P3：审核已提交 pdfcontent 与适配专项，沿原 RenderHost 有界 child→pdfbackend/facade→既有 IR/SVGwriter 接普通 PDF 导出。不得在 Flask 父进程直接执行 native 解码，不引入 PyMuPDF，不伪配同名 SVG或整页栅格化。受控 approved-font ordinary 两路线零差只证对应实物；未批准字体轮廓与 PDFium hinting 有残差，连续多 Tj 的隐式起点也有精度缺口。保首次失败与 budgets/unknown-operator structured failure，补通用字体/资源/透明及实际 HTTP/UI 后才关闭 P3。

后续依次 P2b 原 Preparation/INPUT 明确授权冷重建→P2c 真删源新父进程迁移→N5 刻度/标题裁切→N4 图例→M1/M2/M3。沿完整 SG00–17/A/B/C，保轴模型、数据绑定和可信来源，补复杂 SVG/图族、全部 CLI/MCP/playground/桌面/发行入口、完整原 Python/旧项目与平台安装物资格。新执行用新 receipt，不伪造旧身份；缺实现不标不适用，未验不标通过。

每批先说清判据主语，Python 先 Ruff 再针对性 pytest；前端类型走正式 build，按规则补派生产物及 i18n。测试取命令实际退出码，留命令/cwd/env、源 SHA、实物和首次失败，尤其不得让 shell wrapper 吞掉失败。验证充分后停止扩测；不放宽容差、不删断言消红。每60秒内给简短实质进展。只关闭核明身份的自有进程，不碰用户其它服务。没有创建常驻调度器；用户再次要求暂停时收口当前安全批次、记录真实未完成项并停止。
~~~
