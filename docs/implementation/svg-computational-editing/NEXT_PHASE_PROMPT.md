# SVG 计算图表编辑：提 PR 收敛执行提示词

更新：2026-11-12。**本轮收敛（T0–T5）已完成实施：历史来源 P1、N5 六步、N4 C1–C5、测试红项修复与文档同步均已提交；当前分支可准备 risk:high 的 PR 草稿，仍未 push/建 PR。** 下方 2026-11-11 的提示词正文保留为历史阶段记录；新的接续提示词见文末「2026-11-12 接续」。详细任务、PR范围和退出条件见 [NEXT_PHASE.md](NEXT_PHASE.md)，现状/首红/证据见 [PROGRESS.md](PROGRESS.md)，完整需求见 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。

分支现场：`zixu.wang/svg-computational-r00`，目录 `E:/0 - Project/Tavotto-svg-r00`；本轮起点 `0ad27234`，收敛提交见 PROGRESS 2026-11-12 检查点（19 提交）。未配置 upstream，尚未合入 main；远端同名功能分支仍未发现（2026-11-11 查询）。恢复以实际 HEAD/diff 为准。

~~~text
【历史阶段：2026-11-11 提示词，保留不改】恢复 Tavotto 当前 SVG 计算图表编辑的 PR 收敛工作。目标是在不影响现有 Python/旧项目功能和逻辑的基础上，完成 NEXT_PHASE.md 中 PR00–PR07 的必做项，产出与最终源码匹配的测试证据和可审查 PR 标题/正文草稿；不能只写计划或以草稿状态代替功能闭合。最大化复用原功能，使用有独立收益的多子代理并行，按功能分别 commit，常规可逆步骤自主完成，不反复问确认。

唯一实施树 E:/0 - Project/Tavotto-svg-r00，分支 zixu.wang/svg-computational-r00。不新建/切换分支或 worktree，不 reset/stash/git add .、不重写既有提交。原 E:/0 - Project/Tavotto 的 main 与全部用户项目、data/config、INPUT、AS/命名稿必须保留。先读根和受影响层 AGENTS、对应规则/ADR及最新 PROGRESS，核实际 HEAD/diff/身份；重新 fetch 核主线。2026-11-11盘点时HEAD=94ef609f（后续文档提交另计），origin/main与merge-base=2bc94f7f，主线独有0/本分支180提交，412文件；这是历史盘点，不是永久基线。当前单分支已从主线派生，不能因原main工作区落后就切回那里开发。

最新分支补记：上述94ef609f是功能范围盘点基准；PR计划已提交到23c18717，本次补记前该分支181条独有提交，最新产品代码仍为71ee8c78。本次origin远端heads查询没有同名功能分支、upstream为空，未合入main。恢复先核实际最新文档HEAD，不能仅checkout远端main或假定功能分支已推送；本次补记后的文档提交同样应保留。

先做PR00：按完整A/B/C需求和当前公开承诺建立需求→入口→实现→用例→证据表，建立大diff的审查地图。默认完成可信Figure来源编辑、普通SVG显式轴/系列/数据绑定、自包含项目恢复，不猜科学数据、不削减既有功能。区分本地可审查、线上可合并、可发布；未曝光的通用普通PDF→SVG是否属于本PR，须以基线/当前UI/ADR和真实必经路径判定，不能无限扩大范围，也不能把回归改叫非目标。

PR01先解决正常render同次执行证明，再做PR02全部前端消费者。当前 figure_execution/include_figure_execution 只有只读提案，尚未实现，参考 E:/0 - Project/svg-review-20261109-resumed/p2-live-render-proof-design/REVIEW.md。正证必须来自本次child实际eid并匹配同版receipt/owner/实例和本次原序P；当前receipt不能给旧响应补签，撤销通知不能授正向权限。可选字段缺省保持原shape/命令/冷build/Workerd透明恢复；缺证据只省略额外正证，不能把普通成功改409。不另建runner/会话账本，不为未证场景扩敏感协议。

前端41路径候选在 E:/0 - Project/svg-review-20261109/p2-app-frontend/pause-closeout，核完整补丁SHA和当前源码后再集成，不套早期WIP。修真实缺口：输出figureSnapshot不能自动成为显式figureOrigin；首次P捕获需匹配本次exact正证；明确Run成功后通过原文档事务采用新来源，保Q/布局，修自动继承把旧origin补回；失败/取消/迟到保原正文。修script=null有效来源的入口，以及同名当前B的baked/profile误判历史A。render/缓存/exact/PNG/specfix/写回/记录/history/sync/原图整版批量导出一并核来源/P/Q/项目/回包，保普通Python冷开、运行和写回。有效历史来源不得静默借当前同名脚本。

重要纠正：真实native描述符project_root=None，无磁盘原件绑定；合法native来源是runtime，普通磁盘FileAsset固定safe。因此“普通native磁盘origin写回回归”的旧推断已撤回，不恢复OUT六文件未联验WIP，不扩不存在的原件能力。runtime无原件的原拒绝保持；native现有热编辑/预览/导出必须保留。

随后PR03：复用原Preparation/INPUT/execspec/runconfig/runner/租约做明确授权冷恢复；新执行用新receipt，先P核几何/像素/文字再接Q，不伪造旧身份、不猜argv/cwd/env/口令。新父进程/浏览器在自建旧源和派生cache被隔离后验完整迁移，保原包和所有用户稿；声明式自包含冷重放须零用户代码执行。

PR04可独立推进：完成双系列数据/轴/样式/图例/注释联动和保存/undo/版本/冷开；修实际N5标题/刻度裁切与S/F坐标、命中、拖拽、导出一致；N4复用原legendmodel。不得缩小科学内容或改文字掩盖数据/几何错误。复杂SVG/图族按需求矩阵验证，未知科学语义由用户显式绑定。

PR05：完成公开承诺的scope×format与混合/批量导出，独立核源SHA、mm/dpi、文字/色点/像素；正确处理partial/命名冲突/取消与job归属。若PR00认定通用PDF转换为必做，走原有界RenderHost child→pdfbackend/facade→IR/SVGwriter并补字体/文本精度/资源/HTTP/UI，禁止Flask parent native解码、PyMuPDF、伪配SVG或整页栅格冒矢量；否则保原失败合同且不宣传已实现。

已交付不要重做：8129b8d6隐藏图层保护，77项定向通过；71ee8c78写回verify/备份期间保护外部新原件，67pass/5skip，另15项兼容通过；206d3cdf历史来源后端接纳、静态SVG/位图SVG/来源报告和clip修复保持。历史组有不同源码窗口，不累计为最终全仓绿。写回仍prepare→完整重放+几何+像素verify→备份/commit/回滚，冲突409清staging保原件。最终check→OS replace并非任意外部writer原子CAS；做第三路径审查，真实回归必修，临时缓解不关闭根问题、不靠再hash一次冒彻底解决。

PR06最终冻结源码后完成：Python先Ruff再受影响pytest、最后完整后端；web正式vitest/lint/build及所需i18n（Windows设NODE_OPTIONS=--no-experimental-webstorage；不用tsc --noEmit）；本分支已改Rust，跑cargo test/clippy/fmt；按规则更新包内前端/playground/widget并检查。跑equivalence/invariants/CompatBench、smoke_app、正常认证真实UI/E2E与独立产物验收。E2E前先build_frontend，核实际解释器与继承TAVOTTO_EXE，防测旧包。记录真实退出码、命令/cwd/env、源SHA、pass/fail/skip、首红和反证；新核心判据需有界去修复反证，不删断言/放容差。平台缺失记准确待CI腿，不伪造macOS/安装物/宿主通过。

PR07按 .github/pull_request_template.md 产出risk:high审查包：唯一用户问题、前后行为、base/head、完整diff审查地图、同源契约/迁移、最终验证和反证、未验范围、残余风险/必要Third-Path Review、回滚。同步feature registry/UNRELEASED/规则/ADR与支持矩阵镜像。最终做一次完整独立审查，收齐可复现发现修复并定向复验；不得无限重复已有绿组，线上Codex预算按仓库规则。

每文件一个写入者，主代理协调app.py/共享合同、构建、浏览器和commit；冻结测试时停止写被测树。每60秒内给实质进展，批次结果追加PROGRESS；只回收核明身份的自有PID/句柄。外部平台缺失时记录依赖并推进独立就绪工作，不创建常驻调度器，不把时间耗尽当完成。用户再要求暂停时安全收尾并停止。

本提示词授权本地实现/验证/分功能commit与PR草稿准备，不授权push、创建远端PR/issue、发评论/触发review、合并或发布。线上高风险full-ci、当前required Gate/重腿与维护者审查单列待执行；不能声称PR创建前已有远端绿结果。达到本地PR就绪条件后给出完整交接及剩余外部步骤，不擅自扩大范围。
~~~

## 2026-11-12 接续（当前阶段）

~~~text
本轮收敛已完成实施与本地验证（见 PROGRESS 2026-11-12 检查点与证据根
E:/0 - Project/svg-review-20261111-verification/t5-final/）。剩余工作只有两类：

1) 外部/远端项（需用户明确授权或外部环境，不在本地可完成范围）：
   - push、创建远端 PR（risk:high）、线上 full-ci / required Gates / 重腿；
   - macOS/Linux 真产物与安装链、签名/公证；
   - 真宿主 MCP / 桌面壳手测；
   - 0xC0000409（Playwright Node test-worker 原生退出）根因：需管理员
     LocalDumps 或受控 dump 采集；当前证据为「安静单组可复现、无 WER 事件、
     失败用例 trace 无法落盘」，不得归为产品缺陷。
2) 可选后续：把 N5 的 /2 坐标合同接入真实用户入口（当前 /2 面板经持久化
   chartFrame 进入；绑定/导入 UI 默认 /1 未变），以及普通 PDF→SVG（原失败
   合同保持，不在本 PR）。

纪律不变：唯一实施树/分支、不新建 worktree、不 push/PR/merge/release（未授权）、
不弱化断言/容差/安全边界、按功能 commit、文档历史按日期保留。
~~~
