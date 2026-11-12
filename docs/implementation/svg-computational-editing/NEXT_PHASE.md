# SVG 计算图表编辑：提 PR 前的收敛计划

> 更新：2026-11-12（Asia/Shanghai）。本轮完成 T0 终局归因与 T1/T2/T3/T4 的实施收敛（历史来源 P1、N5 六步、N4 C1–C5、测试红项），T5 冻结验证进行中；未创建远端 PR。
> 目标：在当前分支准备一份功能范围清楚、实现闭合、可审查且具备合并验证条件的 SVG 计算编辑 PR。完整产品范围仍以 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) / ADR 0166 为准；历史证据按日期保留在 [PROGRESS.md](PROGRESS.md)，执行提示词见 [NEXT_PHASE_PROMPT.md](NEXT_PHASE_PROMPT.md)。

## 0. 本轮执行结果（2026-11-12，本地）

- **T0 终局归因**：e2e-full-isolated 526 项 **470 passed / 18 failed / 29 skipped / 9 did not run**；backend-full **82 failed / 27334 passed / 929 skipped / 13 errors**（95 条失败逐项归因全部为 Windows 环境类，无一触及 SVG 计算编辑路径）。首跑冷重放回归（`first-run-qualification`）定为分支 P1。
- **已完成并提交（19 提交，见 PROGRESS 2026-11-12 检查点）**：T1a/T1b 历史来源；N5 显示框 F 六步；N4 声明式图例 C1–C5；T4 红项修复（mcp-canvas crypto 兜底、三处 e2e 判据、前端三红项、CompatBench 环境复验）。
- **验证**：Ruff check/format、agents_budget、cargo test/clippy/fmt、playground/widget 产物重建+check、smoke、`first-run-qualification`/`mcp-canvas`/`playground`/`ux-consistency`/`updater` e2e、CompatBench 窄复验（4 项 product_bug→full_support、product bugs 0）均通过；全量 pytest 6 片与全量 Vitest 的终局数字见证据根 `t5-final/`。
- **N5/N4 口径**：六步与 C1–C5 的生产消费者已闭合（含真实 worker 测试与生产导出 job）；/2 的 UI 采用由成功渲染的自动采用完成（native 保比例 + originPt 同事务），显式 `actions.adoptComputedFrame` 保留；`computed-frame.spec.ts` 覆盖真实浏览器全链。
- **剩余外部步骤**：线上 `full-ci`/required Gates、macOS/Linux 真产物与安装链、真宿主 MCP/桌面壳；0xC0000409（Playwright Node worker 原生退出）根因未定、需管理员 LocalDumps 采集；未 push、未创建远端 PR、未合并或发布。

## 1. 当前状态与 PR 的完成口径

### 1.1 本次实际核对

| 项目 | 2026-11-11 盘点结果 |
| --- | --- |
| 唯一实施树 / 分支 | `E:/0 - Project/Tavotto-svg-r00` / `zixu.wang/svg-computational-r00`；盘点时工作区干净 |
| 当前 HEAD（本次分支补记前） | `23c1871739b7422845e3f2cc8ac2ae09f144c878`：PR收敛计划与提示词；本次补记会再产生文档提交，接续以 `git log -1` 为准，不 reset 到本文SHA |
| 最新产品代码提交 | `71ee8c78`：写回验证/备份期间保护外部新原件；之后截至补记前的提交均为状态文档 |
| 远端与跟踪 | 本次 `git ls-remote --heads origin` 对指定main/功能分支查询实际exit0：main=`2bc94f7f`，未发现 `refs/heads/zixu.wang/svg-computational-r00`；当前分支未配置upstream，不能当作已有远端功能分支 |
| 当前主线关系 | 本次补记前 `origin/main...HEAD` 为主线独有0 / 本分支独有181提交；尚未合入 `origin/main`，本次没有push/合并 |
| 原功能范围盘点基准 | `94ef609f7d558bec43013e2e37d2c3a6042625e9`；下方180提交/412文件数据属于该历史盘点，不是当前HEAD计数 |
| 主线新鲜度 | 本次 `git fetch origin` 实际 exit 0；`origin/main` 与 merge-base 均为 `2bc94f7fd9b13276f92d100fcd1b0e7075f49a93` |
| 提交与差异 | 文档更新前 `origin/main...HEAD` 为主线独有 0 / 本分支独有 180 个提交；412 文件、85633 行增加、2153 行删除。该数字描述盘点范围，不代表验收完成率 |
| 原用户目录 | `E:/0 - Project/Tavotto` 仍为干净 `main@69bcafae`，不是本功能实施树；不在该树切分支或覆盖稿件 |
| PR 风险 | 按 `docs/ci/pr-review-tiers.md` 应为 `risk:high`：涉及执行协议、文档/包格式、写回事务、RenderCore、前后端状态及 Rust 控制面 |
| 本轮未做 | 未重跑产品测试、未应用 OUT 候选、未恢复代码开发；未查询远端现有 PR/检查回执或实时仓库保护设置，不能声称线上 CI 已通过 |

当前远端主线就是本分支祖先，没有观察到需要先合并的新主线提交。恢复开发或准备推送前仍须重新 fetch 核对，不能把今天的结果永久当成最新状态。

### 1.2 已完成的工作不要重做

| 已提交内容 | 可使用的历史证据 | 不能外推的结论 |
| --- | --- | --- |
| SVG 场景、显式轴/系列/数据绑定、声明式计算、部分图内编辑、保存/版本/恢复 | 既有实际 UI 与专项测试，见 PROGRESS 各批次 | 不等于全部图族、图例、复杂几何和冷迁移均已完成 |
| 静态 SVG 矢量合成、普通位图原图 SVG、精确来源检查、图片重复 clip 修复 | `4985938c`、`96cbbb33`、`50e3817f` 及对应实物 | 不等于任意普通 PDF 已可导出 SVG |
| 历史来源后端接纳及消费者 | `206d3cdf`；原窗口 583pass/6skip，含真实 Python/Workerd/native | 完整前端消费者未启用；仅有输出快照不等于有当前执行权限 |
| PDF 内容流内部适配 | `be766286`；原窗口 1320pass/68skip | 未接有界 child/facade/HTTP；通用字体和部分文本位置仍有残差 |
| PDF 隐藏图层保护 | `8129b8d6`；77 项定向通过、两项 AST 反证有效 | 对未表示的 `/OC` 明确 unsupported，未实现 OCG |
| 写回验证/备份期间保护外部新原件 | `71ee8c78`；67pass/5skip，另15项来源/归属兼容通过 | 最终检查到 OS replace 之间不是针对任意外部 writer 的原子 CAS |

以上分别属于不同源码窗口，不能相加作为最终全仓通过数。当前前端候选仍只在仓库外；旧候选 1052 项通过或某次 build 通过，不能用作未来集成源码的最终资格。

### 1.3 区分本 PR 必做项与发布/后续能力

默认按用户原要求交付 SVG 计算编辑的 A/B/C 三条路径，不通过删按钮、降低保真或改文档缩减既有功能来凑完成。

| 类别 | 处理口径 |
| --- | --- |
| 本 PR 必做 | 可信来源热编辑完整消费者；普通 SVG 显式绑定后的计算和图例/注释；保存重开/版本/包与承诺的冷恢复；本 diff 相关的裁切、身份串用、丢编辑和导出问题；既有 Python/旧项目不回归；最终构建、测试与审查材料 |
| 需先判定，不能自动扩大范围 | 通用普通 PDF→SVG。当前无配对 PDF 的 SVG 导出在基线也不是通用解析能力，新增内部适配器还没有产品入口。先对照基线、当前 UI/文案、混合导出与 ADR：若是本 PR 公开承诺或必经路径，就闭合 child/facade/字体/精度；若确为未曝光的独立新增能力，保持原明确失败合同并列后续，不谎称已实现、不影响已支持格式 |
| 需保留根问题 | 最终 check→replace 外部写入窗口。评估本 diff 是否引入/扩大可达丢数据风险，记录平台/协作约束与结构性替代；新回归或可达静默覆盖仍阻塞。现有窄修复不能关闭“任意外部 writer 原子 CAS”的根问题，也不能靠再读一次 hash 宣称彻底解决 |
| 发布级验收 | 全新安装/升级/卸载、签名/公证、所有支持平台真产物、真宿主与人工清单。按支持矩阵与 CI 原拓扑完成；不要把所有发版人工项都当成打开 PR 的本地前置，也不要把浏览器通过当成真桌面/跨平台通过 |

“本地可交审查包”“线上可合并”“可发布”分别记录。现在可以讨论/创建草稿，但本计划的完成目标是准备可评审、可走合并门禁的功能 PR，不靠草稿状态代替收口。

## 2. 分批任务与退出条件

每批先核受影响规则与单一权威，按功能独立 commit；下列提交名只作建议，不重写已有 180 个提交，不新建分支。任一批遇到真实问题，修复和复验完成后再进入依赖它的批次。

### PR00：冻结范围、基线与审查地图

- [ ] 重新核当前 HEAD、工作区、身份和 `origin/main`，保留用户改动；主线确实前进时在当前分支集成并核冲突，不 reset/rebase 丢历史，不改原 main 工作区。
- [ ] 沿 SG00–17/A/B/C 建一张需求→当前入口→实现→最终用例→证据表，区分已实现、候选、缺失、环境未验；不能用 planned / helper exists 代替入口可用。
- [ ] 给 412 文件差异建立审查顺序：契约/数据模型→来源与执行→文档与包→编辑与导出→平台/生成物/测试；解释每个目录为何属于 SVG 唯一目的，意外变动单独核查。
- [ ] 核 UNRELEASED、feature registry、支持矩阵镜像及 UI 的公开承诺；列清普通 PDF→SVG 和剩余写回竞争的处置理由，不先关根问题。

**退出**：有准确范围表、风险分档、主线关系、需修反例和必须运行的验证清单。此时不做大规模代码搬家或扩协议。

### PR01：同一次正常渲染的执行证明

优先解决后续前端接线的前提。复用原 resolver、receipt、execution ledger 和 runner，不引入第二套会话/权限账本。

- [ ] 首先核是否能复用原事实；若采用 `include_figure_execution` / `figure_execution` 提案，先冻结最小可选合同。该提案目前没有实现，不能直接据此写消费者。
- [ ] 正证来自本次 child 实际响应，与同版实例、owner、receipt 和请求原序 P 绑定；不能渲染后读取“当前 receipt”给旧响应补签，不能用撤销通知作正授权。
- [ ] 无参数保原调用形状/响应/冷 build/错误语义。缺正证只省略额外字段，不把普通成功变409；Workerd透明重开、native继续/新barrier/EOF和迟到均不得错签旧引用。
- [ ] 覆盖 A返回后B重跑、同eid不同P、错误owner/receipt、hash mismatch、缺元数据与未知输入；新核心判据做有界反证。

涉及位置以实际代码核对：`src/tavotto/app.py`、`engine/pool.py`、`engine/nativesession.py`、`engine/bridge_runner.py`。不要预先扩大 Rust/worker 协议；只有真实同源依赖需要时才同步。

**退出**：Python池、Workerd、native 的正常原路径不变，只有真实同次执行能产生匹配证明；默认形状/冷恢复及反例通过。建议提交 `feat(engine): expose verified render execution evidence`。

### PR02：接通完整前端消费者与明确来源采用

- [ ] 审阅并核 SHA 后再应用完整 41 路径候选，逐段对照当前源码；不能把早期 WIP 或已纠正的 native磁盘假设直接套回。
- [ ] `figureSnapshot` 是输出归档，不能自动升级为历史执行输入；显式 `figureOrigin` 才表示历史意图。首次捕获基准P要与 PR01 的本次 exact 渲染正证匹配；坏显式 origin 不借当前同名脚本兜底。
- [ ] 用户明确重新运行/采用新来源时，成功后沿原单次文档事务更新来源，保 Q/布局；失败、取消或迟到保原正文。修掉原自动继承把旧 origin 又补回的问题，undo/redo与版本恢复保持。
- [ ] 完整覆盖 render、exact/cache key、临时PNG、specfix、写回/记录、history、sync、原图/整版/批量导出；请求和回包核同一完整来源、项目与P/Q。
- [ ] 修 `script=null` 有效来源被上层入口拦截、同名当前B的 baked/profile错误决定历史A资格。普通无origin行为保留，runtime native原本无原件的拒绝保留。
- [ ] 集成测试经实际入口和 store 事务；补普通safe保存→冷开→首改→明确运行、A/B同名不同来源、项目切换/迟到、坏echo零提交。

重点目录：`web/src/lib`、`web/src/store`、Inspector/问题修复/导出入口与 `types/document.ts`；按最近 AGENTS 读规则。不要新建独立来源 store 或第二正文 writer。

**退出**：前端已在正式源码启用，所有消费者核同一来源；旧普通Python冷开/运行/写回不回归。API证明与首个消费者一并集成验证，不能只交一个wrapper。建议分“来源采用事务”“完整消费者与入口修复”两项 commit。

### PR03：INPUT 冷恢复、自包含保存与真实迁移

- [ ] 复用原 Preparation、INPUT附件、execspec/runconfig、safe/native runner及资源租约。归档可读不等于运行授权；显式运行沿既有授权链。
- [ ] 原脚本/数据缺失时按已保存角色恢复正确来源、cwd、argv、rc与remap；不得猜 terminal 参数、把当前文件SHA冒原compile事实，或把输入口令写入公开事实。
- [ ] 新执行用新receipt：先重放P并核几何/像素/文字，再接Q，通过原来源采用事务落正文，不伪造旧receipt。失败/取消/过期零正文改动且回收自有租约/worker。
- [ ] 验三条路径：A可信来源恢复；B普通SVG+显式数据绑定；C自包含声明式模型冷重放零执行。分别记录科学执行次数，不能混用一种路径的资格。
- [ ] 用自建项目隔离旧源和派生cache，在新父进程/新浏览器验证双画布、同名历史、真正runtime家族、保存再开/命名版本/包迁移；保原包、INPUT、AS/命名稿、config和用户数据，不全清 data/cache。

**退出**：承诺的冷开/跨目录迁移真实走通；缺来源提示与明确恢复正确，零隐式执行，失败不丢编辑。冷恢复与迁移修复分别 commit。

### PR04：计算编辑与可见内容保真

- [ ] 普通SVG+双系列数据，修改数值/轴范围/线宽颜色/图例/注释，检查数据位置、刻度及图例联动；已支持bar/errorbar/heatmap等按能力矩阵补实际生命周期。
- [ ] N5：修实际刻度/标题裁切，统一源坐标S、显示框F、命中、拖拽换算和导出；已有OUT原型未入产品。不能只扩viewBox或缩小数据/字体掩盖问题。
- [ ] N4：复用原legendmodel，覆盖同名同色、复合句柄、手工文字、未绑定注释；undo/redo、保存、冷开、导出结果一致。
- [ ] 按已声明支持范围核group/use、clip、opacity、文本/转曲、多轴、缩放平移、成员层序；未建模装饰保持。坐标数字能拖动不等于轴/数据绑定已恢复。

**退出**：真实UI和独立实物均能证明计算/显示/冷重放一致，无可达静默错图。裁切、图例、其他具体修复各自 commit，不把每个边界塞入一次重写。

### PR05：导出、文件事务与剩余范围判定

- [ ] 沿原后端路由逐格验当前要求的scope×format：SVG/普通位图/静态PDF/可信历史/runtime/混合画布，原图/整版/逐图批次；含标注、留白、透明、取消/partial、命名冲突continuation。不能因单一格式失败误报整批成功或污染前一job。
- [ ] 所有成功产物独立核来源SHA、页框/mm/dpi、文字、固定色点/像素及资源；PDF容器字节本来可变化时，按已有合同核独立读取后的内容，不能随意放宽容差。
- [ ] 复验现有写回的prepare→完整重放+几何+像素verify→备份/commit/回滚、历史与同stem归属。保 `71ee8c78` 的两个入口/两载体/两个窗口反例，不为此放宽事务。
- [ ] 对最终check→replace残余竞争做第三路径审查：区分应用内协作写入与任意外部writer，实验验证平台可行的结构方案；不得以一次额外stat/hash冒原子性。若只能临时缓解，PR正文明确残余风险、根问题及退出条件，不使用关闭根问题的关键字；未授权时不创建外部issue。
- [ ] PR00若确认必须包含普通PDF→SVG，才完成原RenderHost有界child→pdfbackend/facade→IR/SVGwriter，补字体/多Tj/复杂资源/预算和HTTP/UI。仍未支持的语义明确失败，不在Flask parent解native、不加PyMuPDF、不整页栅格冒矢量。若判为独立后续能力，须证明原支持路径和失败合同未退步，不能把真实回归改称“非目标”。

**退出**：公开承诺的导出格闭合；所有旧格式和事务不回归；PDF新增能力与残余并发问题的处置有代码/实物依据，未实现或未验项如实保留。

### PR06：冻结最终源码，完成完整验证

- [ ] PR01–05的必做项完成后记录HEAD/diff与源SHA，冻结生产源码和构建；测试与UI观测期间不并行改被测树。
- [ ] 先受影响验证，再跑本分支最终完整后端、前端、Rust、构建和下面的集成链；前轮局部绿不能替代。新改动只使受影响资格失效；修后补相关组，最终全集需有一份与最终源码一致的结果。
- [ ] 正常认证真实UI运行M1，网络回包/正式HTTP bundle/持久正文/独立产物对齐；不注入store，不绕认证，不测到旧打包前端，不重启旧终局生命周期。
- [ ] 每个skip注明环境和应跑的实际CI腿；已知平台能力缺失、产品失败、夹具/观察器错误分别处理，不把未运行当通过。只关闭确认归属的自有PID并核回收。

**退出**：最终本地资格有准确命令、cwd/env、真实退出码、pass/fail/skip、源SHA、产物与反证；产品P1及范围内必须修的P2为0。本机没有的CI/平台条件单列待线上验证，不伪造完成。

### PR07：审查包、文档与 PR 就绪交付

- [ ] 同步用户可见能力的 feature registry、规则/ADR、同源镜像、迁移与UNRELEASED；文案只写最终实现，支持范围沿 `docs/support-matrix.json`。
- [ ] 按 `.github/pull_request_template.md` 准备标题/正文草稿：唯一用户问题、前后行为、base/head、`risk:high`、契约/审查地图、验证/反证、未验平台、回滚和必要Third-Path Review。不把OUT草稿、凭据、绝对用户路径或缓存构建产物塞进提交。
- [ ] 完成一次覆盖当前完整diff的独立审查，报告有触发场景的正确性/回归/数据风险；一次收齐修完并定向复验。未来线上Codex完整评审与定向复核按仓库预算，不无止境重复同一绿组。
- [ ] 核git状态/提交分类/无意外产物；保存可审查的PR草稿与准确剩余外部步骤。用户未授权远端动作前，不push、不创建PR、不发评论、不触发review、不merge或发布。

**退出**：本地审查包就绪，剩余仅线上CI/维护者审查等已明确外部动作；不得把“草稿写完”记为线上可合并。

## 3. 必须跑通的实际验收场景

| 场景 | 必须观察到的结果 |
| --- | --- |
| 普通Python磁盘图保存→冷开→编辑/写回 | 保原正常冷构建，不因输出快照旧receipt误409；写回重放两门保持 |
| 历史A与当前B同fileId、不同数据/receipt | 预览、修复、导出、记录/同步均指向A；B的baked/profile不能替A判资格 |
| 明确运行并采用新来源 | 成功才更新origin/ref；失败/取消/迟到保旧正文，单次事务可撤销 |
| script=null但有效来源 | 原支持的编辑、问题修复、原件动作从真实入口可达；runtime仍遵循原无原件边界 |
| 普通SVG显式绑定双系列 | 改数据/轴/样式联动正确，图例/注释保留；undo/redo→保存/版本→冷开一致 |
| 真正源缺失的新父进程迁移 | 从包INPUT按明确授权恢复正确角色；声明式冷重放零用户代码执行；不借当前同名源 |
| 标题/刻度靠边、复杂clip/use和缩放拖动 | 视觉、命中、源坐标与导出一致，不缩小科学内容遮问题 |
| 混合画布多格式/批量/命名冲突 | 来源版本一致，格式级失败与partial如实，成功实物尺寸/像素/文字正确 |
| 事务失败与外部原件变更 | 必须拒绝的场景409、原件/外部新字节/历史保留、无staging残留、自有worker回收 |

## 4. 验证入口与证据规则

所有命令以当前package/脚本/CI为准，未来改名先核源码，不另造第二测试runner。Python使用本树 `.venv/Scripts/python.exe`；OUT使用仓库外本任务目录。逐条核命令本身的退出码，不靠shell最后一条echo/管道伪绿。

| cwd | 验证入口 | 使用时机 |
| --- | --- | --- |
| 仓库根 | `.venv/Scripts/ruff.exe check .`；`.venv/Scripts/ruff.exe format --check .` | 改Python后先受影响Ruff，最终提交前全仓 |
| 仓库根 | `.venv/Scripts/python.exe -B -X utf8 -m pytest` | 最终完整后端；必要字体先走 `scripts/fetch_fonts.py`，字体不进git |
| 仓库根 | 定向pytest：`test_figure_origin_execution.py`、`test_figure_origin_consumers.py`、`test_figure_origin_persistence.py`、SVG/INPUT/包/导出组、`test_writeback_concurrent_source.py` 与原写回组 | 对应批次先跑；用例路径均在 `tests/`，按实际影响去重 |
| web | PowerShell先设 `$env:NODE_OPTIONS='--no-experimental-webstorage'`，再 `pnpm exec vitest run`；`pnpm lint`；`pnpm build`；文案受影响时 `pnpm i18n:check` | Windows等价执行原test script，正式build承担类型检查；不用 `tsc --noEmit` 冒通过 |
| workerd | `cargo test`；`cargo clippy --all-targets -- -D warnings`；`cargo fmt --check` | 当前branch已有Rust差异，最终必须验，不以最后一轮未改Rust豁免 |
| 仓库根 | `scripts/build_frontend.py`、`scripts/build_browser_playground.py` 及 `--check`、`scripts/build_mcp_widget.py` 及 `--check`，使用本树Python | 前端/规定引擎源变化后生成相应产物；widget仅本地，派生物按既有规则不乱提交 |
| 仓库根 / web | 本树Python运行 `scripts/smoke_app.py --python <本树Python绝对路径>`；web中 `pnpm e2e` | E2E前先构建包内前端；显式核 `TAVOTTO_PYTHON`、worker解释器及继承的 `TAVOTTO_EXE`，防测旧产物 |
| 仓库根 | `tests/test_equivalence_matrix.py`、`tests/test_invariants_engine.py`；`scripts/ci/compat_matrix.py --smoke --gate pr --out <OUT>`；必要受影响保真/性能腿 | 互不替代；CI快速nofidelity不代替本功能独立像素保真 |
| 仓库根 | pytest `tests/test_agents_rules_index.py tests/test_docs_references.py`；Python `scripts/dev/agents_budget.py --check`；`git diff --check` | 文档与提交收尾 |

命令输出用JUnit/JSON/日志保原样，记录首次失败与修复落点。新核心判据做一次有界AST/去修复反证；不放宽容差、不删除断言凑绿，不在未授权路径清理用户数据。功能运行时写入沿 `engine/config.data_dir()`，独立测试/证据使用隔离目录。

## 5. 依赖、并行与停点

顺序：`PR00 → PR01 → PR02 → PR03`；`PR04`、`PR05`在范围冻结后可作独立支线；必做项汇合后 `PR06 → PR07`。

恢复后可复用既有子代理：执行/证明、前端来源、导出/几何三个独立方向；每文件仅一个写入者。app.py/共享协议/构建/浏览器和commit由主代理统一协调；源码冻结验证时各代理停止写入被测树。分批能无人值守接续，不承诺一夜完成所有平台资格，不创建常驻调度器。

- 用户尚未明确恢复实施：只保存本计划与提示词，产品目标保持暂停。
- 用户恢复后：在授权范围内完成实现、受影响验证和分功能提交，遇外部平台/CI条件缺失先记录并推进独立就绪项，不用反复问常规选择。
- 可交本地审查包：PR00–07必做项完成，最终源码资格完整，范围内发现已处理，PR草稿可供审查。
- 线上可合并：获得用户远端授权后，针对实际base/head核当前required checks；按高风险规则运行 `full-ci`、现役三个Gate/重腿成功及审查线程处理。不能在PR创建前伪称这些已完成。
- 可发布：另依支持矩阵完成Windows/macOS相应真产物/安装链和人工项；未经授权不merge/发布。部分未验平台会阻止合并或发布时据实标记，不从计划文本推断其豁免。
