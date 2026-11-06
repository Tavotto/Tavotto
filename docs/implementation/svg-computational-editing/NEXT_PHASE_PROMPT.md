# SVG 计算图表编辑：下一阶段无人值守提示词

可直接复制以下提示词。2026-11-07（Asia/Shanghai）97e75d41实际输入字节封存提交后接续，实际工作树优先。详细任务：[NEXT_PHASE.md](NEXT_PHASE.md)；进度唯一台账：[PROGRESS.md](PROGRESS.md)；最终范围：[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。

```text
继续完成 Tavotto 的 SVG 计算图表编辑，保留现有 Python 绘图、静态素材和其他已有功能。最大化复用现模型、编辑器、事务、writer、RenderCore/pdfbackend 和 legendmodel；持续自主实现、验证、修复，在同一分支按功能分别 commit。常规可逆步骤自行推进，不逐批等待确认。已授权多子代理，只分配独立且有收益的任务；共享合同、app、Schema、规则、文档、集成和提交由主代理唯一写入，同一文件同时只有一个写入者。

一、恢复真实状态，沿用当前分支

实施树：E:/0 - Project/Tavotto-svg-r00
唯一实施分支：zixu.wang/svg-computational-r00
原目录：E:/0 - Project/Tavotto，仍为 clean main@69bcafae，未合入成果。
稳定文档：docs/implementation/svg-computational-editing/{PROGRESS,NEXT_PHASE,NEXT_PHASE_PROMPT}.md
证据根：E:/0 - Project/svg-review-20261106

复核时最新功能 HEAD=97e75d41：实际输入原字节会话封存、私有opaque descriptor、safe/native接线及父receipt核验已提交。3c64195b/d8416d4e捕获/准备、11c67ab1 safe/workerd、b5f9f557成员及N3b三项1795f244/d1389db6/5e000c6f保留。各源码子任务已冻结释放，无在途写入/测试；后续durable预算只读设计已经完成。随后可能有文档或实现提交，以实际 HEAD/index/diff 为准，禁止 reset 到此 hash。不按旧23/15文件候选清单覆盖新稿，不重做会话封存/native/准备。

先核分支、提交身份、Python 实际 import 路径、自有进程，再读 AGENTS 和涉及层的规则/ADR。沿用当前树/分支，不新建或切换分支/worktree，不 reset/stash 覆盖已有改动，不 git add . 混批，不 push/PR/合并/发布。复用 venv/node_modules；测试 config/data 隔离、遥测关闭，运行可写数据沿 config.data_dir()。

注意共享 venv 的裸 Python 默认 import 另一旧树。pytest.ini 的 pythonpath=src 已经独立 collection 核实正确；手工 Python/服务/子进程须显式令 PYTHONPATH 指向本实施树 src（隔离模式用既有显式源码入口），在实际进程核模块 __file__。不得仅凭 venv 路径判定测试/服务源码。

二、保留已完成，不重复实现

- 既有显式 SVG 绑定、计算编辑、精确 INPUT 附件、v4 正文/冷恢复、EPS 完整接线、命名关联/冲突恢复均保留。原图五格式已有单曲线真 UI 和独立产物证据，完整双系列 M1 尚未通过。0b611411 预检、d7a73ce1 保存、e468d516/336783bf 旧包、71e637c8 原 writer 回执、206a1c34 预算工厂不重做。
- 9a9843d2 统一目标计划、8ed50616 实际 POST /api/documents/v4/package 和项目菜单已提交。旧只读 schema2/3 open/relink 保持；不能让 v4 走会清 edits 的旧 relink。
- 75b09b57：runconfig.import_portable 保原 rc_ID/精确 argv，全部新版配置 writer 共用跨进程 guard；145 通过、0 skip，真实双进程/冷进程已验。不复制默认/成功事实/授权，不是跨版本 CAS；atomicio 替换后目录 fsync 失败也不保证字节回滚。
- be84ba33：execution v1、已知登记/脚本/CSV/helper/参数/内部精确 file remap 已接正式导出和准确 coverage UI。最终联合 718、另 8 新 HTTP、56 前端各范围通过，不相加或称全分支回归。真菜单/ZIP/实际整份请求/输入字节/rc_ID/argv 已核，计数仍 1；未声明完整输入时有脚本包准确 incomplete。
- 13720bc4：旧图幅迁移只有实际追加 FRAME 才撤旧快照引用，其余路径保留。1c9da4ca 只将旧测试改等真实持久化回执，未改生产时序。100 项、lint/build/playground/check 均通过，指纹 9c11afde28799951。新鲜浏览器旧文档立即菜单导出 200，ZIP 仅补正确 FRAME 引用，原编辑/参数保持，计数仍 1。
- 1795f244：DocumentPackage.copy_required与原reader共享CRC/预算循环，普通file/script/figure_snapshot有界流式暂存与复核；326通过/0skip、4反证、旧ZIP整份字节相同。d1389db6：svgstore.inspect_portable_store核全部档案/marker/entry/blob，包含未引用blob；316通过、31 Win1314权限skip、6反证及5组旧字节对拍；历史归档不变当前授权。
- 5e000c6f：POST /api/documents/v4/package/inspect真实消费者已接，输入预算由packagebudget.input_budget统一；全部资源/精确ref/source/patch、安全SVG/单页PDF、原声明与实际缺件分别核对。最终consumer430ba6…的68项HTTP通过/0skip，全仓Ruff及格式1442文件通过；真实认证网络401/200/409/422，整份doc/manifest相同、14资源/1快照，脚本计数1。审查唯一P2（helper/runtime明确脚本漏报）已补15回归并收口。较早consumer6a6fbb…的511联合仅记原源码时刻，不冒充最终又跑511。尚无生产安装API/UI资格。
- b5f9f557：原PackageError/成员验证移到packagemembers单一stdlib叶子，旧公开API同对象重导出；637通过/0skip、旧ZIP整份字节/七定义AST保持，恢复旧反向依赖确实触发循环失败。不要重新从documentpackage引入运行配置/执行闭包循环。
- 11c67ab1：declaredinputs私有CAS与认证GET/POST/DELETE API、inputdeclaration唯一stdlib freezer、实际safe/workerd起止捕获及冻结captured_build、receipt可选无路径revision已接；无声明旧JSON/identity/调用保持。cached Figure不重签，实际source/id/scope/PID不一致不授证；Rust supervisor实际请求锁内提供可信child PID，runtime自报不能代替。DELETE可撤销已删除源码的声明，不借执行resolver阻塞撤销；400/409/503保持。前端仅增加receipt类型/错误码，尚无声明UI；完整图/资源未入包，execution v1仍准确incomplete。
- 该批证据n3c-capture-staged-final.json：最终核心24/HTTP21、receipt43/settings30、Python worker137/真实workerd23/Cargo62/Python3.10实际worker23、错误/i18n366各自通过；发行147通过/9 Linux/POSIX skip，非真安装物。173根联合早于DELETE末修与新增link测试，delta已由21/24覆盖，不说最终又跑173。本次Ruff/format/diff、playground与插件画布build/check通过；此前最终pnpm build工具d7e489 exit0。不同组不相加，旧suite不为状态询问重跑。
- 3c64195b：候选沿原认证ACK私有传入native runner，源码读取/实际compile bytes、start/barrier/continue谱系按同一freezer核验；NativeSession按execution_id/epoch保存，重复barrier不补证，ensure_built与原render/continue共Figure锁并在最终EOF后核身份。receipt两native入口共用投影；准备收口from_worker仅接同次build的本地execution_id marker，默认字段/instance保持。实际native22、受影响184通过/1旧Win权限skip；末次会话41/0skip，3.10真实入口5/0skip。固定旧d946225f五情景的JSON/identity逐字相同，见n3c-native-default-oracle.json。未知参数/-m归属仍准确未知，原native执行编辑不禁用。
- d8416d4e：Plan私有选择token区分legacy未观察/真实absence，执行前、retry、执行后核同一scope，replan不吸收声明变化。launchctx仅存实际receipt摘要，当前资源比较复用唯一freezer；旧记录不自动升级。末次23/0skip含真实safe缓存计数1及native PreparationService→receipt→成功记录→资源变更拒旧，6项实际反证；前阶段161/1旧symlink skip不冒最终同组重跑。
- U09规定94个不同用例全通过/0skip：90项receipt/runtime/trace/export，加FO32已有环境/错误选择/受管环境及FO30共4。实际3.10.20与锁内3.13.15均到RenderCore PDF/PNG/TIFF，两出口独立回执保留。首轮cp936输出和pip空格wheelhouse夹具失败保留，仅修隔离测试环境，没改源码/测试/产品锁/全局依赖；见n3c-u09-validation.{md,json}。额外existing单例只保回执，不新增覆盖数。
- 扩大集成263收集=254通过/7平台skip/2 Win1314失败、exit1。两失败在旧d946225f同一测试函数也实复现，属未进入产品的符号链接权限失败；不删、不伪skip、不说全量全绿。较早root142不是末次源码资格。n3c-native-staged-final.json、n3c-preparation-staged-final.json与各原始JUnit记录时刻/范围；本轮未改前端，不虚报新build/UI。M1/M2/M3均未通过。
- 97e75d41：原Capture.finish同流封存源码/files/prefix bytes，原图保空目录；InputArchive(graph_bytes:bytes,resources:tuple[InputResource(sha256,size_bytes,path)])为父核验后的私有对象。wire只有{version,key,revision}，仅从父已知out_dir/captured-inputs读取，不能接任意回复path；同lineage复用nonce，新barrier仍显式同版传递。receipt两入口IO后复核eid/epoch/fact/descriptor，safe unknown拒当前证据；附件不进入任一JSON/public_facts/identity，默认无声明源IO保持。仍只是会话cache，svgstore/app/完整包未接。
- 该片精确资格见n3c3b-stage-final.json及core/native/safe/review报告：核心55、native末次84、root268、Engine24、真实workerd6、3.10实际safe/native两例各自通过/0skip；root含U09 receipt/runtime/trace/export90与准备API39，FO32/FO30原4另实跑通过。旧worker136先129通过/7缺可发现binary skip；同已核binary临时无覆盖复制后原7全部实际通过，已删自有副本。原first红灯保留；root268早于唯一bridge mkdir小修，末次native84/3.10两例覆盖delta，不冒重跑。核心8/safe3/native2/receipt3内存反证实际断言exit1；独立10状态/IO探针无具体新缺陷；e986默认fact字节+60源IO序列对拍同。不同组不累计，旧Win1314仍未解决，不外推全产品/平台。

读 n3c-execution-export-staged.json、n3c-real-ui-correct-kind-qualification.json、n3c-frame-ui-qualification.json、n3c-compat-frontend-build.json。原失败、夹具错误、超时和权限 skip 保留，不改写历史。

三、当前第一批：把已有封存接到正式冷store，再接完整包

最近交付目标是“包带到中文新目录，原目录不存在、缓存清空、新父进程/浏览器下，仍可恢复并继续计算编辑”。N3b、N3c-3a与3b-1会话封存已完成；首项是3b-2 svgstore正式冷存，再3b-3 app热/冷staging、3b-4完整包/UI/ref及N3d安全安装→N3e原writer UI→N3f整链。共享合同/app由主代理统一接线，冻结后并行独立任务，文件所有权互斥，不重复相同昂贵套件。

本次执行复核基线为0f063765（文档）/97e75d41（功能），工作树干净；15份功能文件、7份报告和9份JUnit摘要均已与原SHA核对。这是证据复核，不是新一轮产品/浏览器通过。最新安排见NEXT_PHASE第2.1节，详细验收仍以第4节为准，不改写历史冻结索引。

直接从可验收功能3b-2开始：把receipt已有的私有InputArchive接进svgstore的同锁冷存与严格读取；同一功能内确定新增累计预算，复用已完成预算probe，不再重复会话封存或以新研究文档替代实现。随后分别提交3b-3真实保存/冷暂存、3b-4显式execution v2的导出与严格检视；前三片之后紧接声明UI、精确ref迁移和安全安装，不把“资源已入缓存”当搬迁完成。计算SVG既有精确INPUT冷恢复继续保留，与待接的Python执行图完整捕获附件区分。

第一段真实验收必须先保存一次实际执行图，再改/删本任务自己的原输入，清派生缓存并启动新父进程，仅从正式项目资源读取并打包原内容；独立核摘要和执行计数。覆盖合法大graph、超过4096成员、零字节/去重/空目录、错scope/跨run、篡改、断写和容量不足，失败后旧版本仍可读。不能把单声明2GiB误作累计store或整包容量；不能以放宽旧门禁让新测试绿。图例和裁切可由独立写入者提前定位，实际修复及完整M1仍必须完成。

先读n3c3b-stage-final.json、n3c3b-archive-core-validation.md、n3c3b-safe-validation.md、n3c3b-native-freeze.md、n3c3b-receipt-integration-review.md，再读n3c3b-durable-budget-design.md/probe.json。旧cold/drift probe证明正式snapshot只留摘要；会话原字节已由97e75d41封存，当前svgstore/app仍未接，不重复旧缺口实现。新probe实证4097触旧census、50000逻辑成员graph达5700047B、同长cache篡改流式copy拒绝。后续冷存/包不能从当前源文件重造历史；store接口是待冻结建议，检视/成员/声明/资源继续复用。

根因已经实测：旧binding在导出时重算且不进receipt identity，不能证明旧Figure原输入；partial observer漏外部读取；remap fingerprint含绝对to，换根使旧ref精确失配。现实际fact与原字节已进私有receipt附件，但app/svgstore仍仅转公开revision，正式冷存/冷包会丢完整附件。判据主语仍是“实际某execution_id/native epoch的明确声明及当时字节”。不能当前补签或降低loader门。按下列边界接真实消费者：

1. N3c-3b分四片，每片接真实消费者、分功能commit：
   a. 原bytes封存已完成97e75d41，按上方冻结API与证据保留；不重新实现或覆盖已核源码。InputArchive底层Path不是永久租约，后续存储边界仍须流式重核字节。
   b. durable：svgstore可选新capture metadata变体，图canonical bytes由原声明权威提供，输入流经同一有界发布/配额/核验。仅有capture时在_snapshot_id加入其digest；旧无capture公式、refs/marker版本及旧archive字节保持，不原地补签。不要整体升级svgstore.VERSION，它还管理refs/markers。load/inspect全量核图/每份bytes，冷父进程不依赖settings/worker/科学栈；缺件/篡改/错scope/跨run/磁盘失败反例后提交feat(snapshots): persist captured input graphs and resources。
   c. 实际接线：app._execution_source/_capture_svg_snapshot_best_effort/_bind_svg_asset到同一_save_svg_snapshot只转实际receipt私有附件，_package_svg_assets冷staging也完整转发。真实capture→保存→改/删原源→全新父进程仅archive打包仍带原bytes，counter仍1；历史无bytes准确incomplete，不偷偷重跑脚本。提交feat(svg): carry captured inputs through snapshot staging。
   d. 版本化包：packageexecution显式v2，旧闭式v1/默认字节保持；collector、正式export/inspect同批消费。用原manifest/ResourceFile/stream writer建立snapshot/ref↔graph↔script/file/prefix成员映射，精确SHA/尺寸/scope/rc_ID/argv交叉核验，空目录保留，不复制一套SHA或授权表。同名历史不同内容须显式映射，不能拿当前文件覆盖；坏图/换bytes/漏增成员/错scope/跨run与真冷包验证后提交feat(packages): carry verified declared inputs and prefix resources。
2. 附件/wire已实现；b前冻结durable累计预算：现2GiB/50000仅每graph选择预算，不是已有store累计政策。优先同store原锁下typed input子树，旧render512MiB/4096约束保持；graph必须外置，新树census按输入维度有界。复用原常量，caller单一budget明确累计字节/成员/graph元数据，不能随意承诺256×2GiB、静默放宽旧限额或套错4096丢资源。整包2GiB总额与输入选择是不同主语，3b4精确计资源+元数据，不擅扩packagebudget。完整graph的mappings.from可含绝对请求，属于私有来源事实，不进普通receipt/遥测、不授本机读权。外部file/prefix沿现明确选择；相对输入视图实测open/exists/glob/native库，绝对原生路径先实验结构适配，不把open hook正例称全部支持，不禁用原native功能。
3. N3c-3c 声明UI：复用准备/配置/选择器/i18n，显示输入集与未知通道；用户确认后沿现授权真实新执行取得捕获事实。改清单撤旧资格，旧图仅作对照；取消/切项目/迟到/失败保原稿。实际浏览器声明→新执行→正式包/inspect联验。未声明仍incomplete，已核完整项目须能继续搬迁，不永久只报待补。建议commit feat(inputs): declare portable inputs through the preparation flow。
4. N3c-3d 引用搬迁：经核capture声明+旧完整ref+新已核资源/实际remap规则，复用public svgstore.save在自有stage生成oldRef→newRef。保source/rc_ID/精确argv/patch/receipt/配对字节；snapshotId随内容身份正确改变，不硬保旧ID。历史多版本按完整ref映射，archive-only不装成当前marker。新根新进程精确loader查询和篡改反证通过后接N3d事务；旧无capture证据包不自动升级。建议commit feat(packages): plan verified snapshot references for relocated inputs。

保持Registry/runconfig/launchctx/workdir/inputremap/databinding唯一权威；显式声明文件闭包与任意Python全通道执行依赖分开，成功receipt/partial观察不证明后者。外部读取须来自现明确授权，不从argv猜权限；敏感参数、机器环境/cwd授权/成功信任不复制。真实消费者接线需要跨批调整时保可审查边界，不提交永久未使用helper。

四、随后接通安装与整链

N3d：独占创建新项目根，保留相对sourceId/script/runtime及原运行配置；remap变化时用已核oldRef→newRef更新内容寻址快照。projects/open create=true会复用目录，copy_project可skipped>0成功，都不能直接当严格安装器。复用排他reserve/原子发布，逐份摘要/语义核对及必要受控重放；同名/并发/断写/源变化保旧目标和正文，不以改panel.fileId规避冲突，不继承发送者本机绑定/绝对路径。检视/采用不自动执行陌生Python，需执行沿现授权/worker入口；完整闭包和恢复资格分别表达。后端独立commit。

N3e：复用 projectStore.adoptOpenedProject(status,{prepareDocument})、switchTransaction、openDocumentAs；最终正文在 beforeInstall 前固定，前后核 pj/format/documentId/loadSeq/意图。waitForWorkingDocumentSave 已存在：非空只等待原 switchDocument 排队的一次正文写及实际回读，不补第二 PUT；空稿显式 persistEmpty 一次，await switchDocument/一轮微任务不代表落盘。取消/切项目/迟到/超时/资源成功但正文失败/正文成功但元信息未确认准确分列，旧稿保留。中文/i18n/真 UI 验完独立 commit。

N3f：冻结自建混合 SVG/普通素材/Python runtime/快照、多画布、中文目录夹具。仅删除自建测试原目录，清缓存，新父进程/浏览器开新项目；实际编辑、undo/redo、保存、冷重开、版本恢复、全部适用 scope×format。独立核模型/完整 INPUT/资源/参数/几何/文字/PPI，补旧 schema2/3 和原 Python 受影响回归。实际缺陷分别 fix，资格记录独立 commit；N3a–f 全退出才标完整迁移，不以一个菜单成功或一夜结束作完成条件。

五、N3 后的完整队列

N4：显式 SVG 图例条目→系列/custom 关系和持久化合同，再复用 legendmodel 跟随/脱开/handle_* 与原 paint slot。曲线改色而旧图例不变是已证实缺陷，不按颜色/同名/顺序猜。验同色同名、多系列、复合句柄、范围/数据变更、撤销、冷开、导出；重绑迁移保科学身份，未匹配项显式可撤销。

N5：成员 paint slots、共享 SVGIR Path/ClipUnion 的祖先 AND/子形状 union/evenodd/objectBBox/运动。命中距离/框选/高亮/宽描边共用几何，圆外/孔洞实际拒绝。截图刻度/文字边缘仍可见裁切，先比原 SVG、预览和产物确定根因，再修对应层。

N6/M1：纯外观分享不暗带模型/数据，完整分享含全部编辑/模型/数据；真实双系列+图例+未绑定注释→绑定/数据/范围/样式→精确选择/undo/redo→正式保存→空缓存新父进程/浏览器→原图/标注/混合画布/多页全部适用 scope×format 的独立产物核对。画布 EPS 保持既有 eps_not_for_canvas。

N7/M2：SG00–SG17 不减，A 可信来源/C 自包含、文字/分组/use/图元、适用科学族/多轴/投影、事务写回、出版/预检/样式、CLI/MCP/playground/桌面/发行入口逐格验。普通 SVG 不反推原 Python；写回 prepare→verify 全量冷重放+几何+像素→commit，失败 409 原件零改。

N8/M3：冻结候选做完整旧功能/等价性/不变式/CompatBench/E2E/性能/支持平台真安装物。既有 Windows desktop PID 回执失败需处置，两个基线Win1314用例仍须在具备权限的受支持环境取得资格；缺平台列未验证。SVG专项全绿不证明全部现有功能未受影响。

六、无人值守执行合同

每批：读真实 checkpoint/选就绪任务 → 明确不变量和可观测退出条件 → 实现并接真实消费者 → 必要反证/受影响验证 → 核 diff/源码身份/真实退出码 → 定向 stage/功能 commit → 同步稳定台账 → 继续下一就绪任务。无新增疑点不重复昂贵检查，不以反复写计划代替实现。

Python 先 Ruff 再针对性 pytest；前端专项 Vitest 后正式 build，按影响补 i18n/playground/真 UI；最终候选才完整回归。skip/未执行/平台缺口照实记，重叠组不累计，不删测试或放宽容差。新增核心不变量做实际产品落点反证；同类两轮仍出边界，先查共同结构/单一权威。源码结构用 AST，热态/落盘/冷开/导出分别量。

每次功能提交、长检查前后、约 30 分钟安全边界更新 PROGRESS：HEAD/index/diff、所有权、源码 SHA/命令/cwd/env/退出码、失败/skip、下一动作。NEXT_PHASE/PROMPT 只维护当前队列，保留历史证据。阻塞单项先转独立就绪工作；仅缺失信息实质影响结果或超授权时询问。日志/临时 fixture 留外部证据根；Playwright CLI 沿 run-playwright-captured.py 原始 UTF8 捕获，走真实 DOM，不直接改 store 伪造用户操作。

服务线索须重新核PID/创建时间/命令/实际import后使用：64860（2026-11-06T23:26:50.033107+08:00，n3c-browser/server.py，23989）是be84时刻后端并服务既有web/dist，未加载新检视API，禁止重新seed覆盖证据。浏览器tavotto-execution-v4=39048（23:40:27.365023）、tavotto-frame-legacy=75500（2026-11-07T00:01:17.572366）也只作线索；working d_n3c_legacy_frame_fix是fixture durable last，原d_n3c_browser与named保留。旧N3a后端75464/23988与浏览器66204均属旧资格。本轮独立socket服务已正常结束。只结束精确身份已核的自有进程，不按模式广杀。

未创建定时任务；无人值守指活跃会话持续迭代，休眠/会话结束后按 checkpoint 接续，不宣称提示词能唤醒机器。M1/M2/M3 均未通过，goal 保持 active；状态询问简短答复后继续原目标。

现在核真实HEAD/diff及97e75d41十五文件冻结记录，读durable-budget设计/实际probe，从svgstore新metadata变体、typed输入树、同锁预算和严格冷reader开始实现3b-2。然后接app热/冷staging和版本化真实冷包，再3c声明UI/3d引用迁移/N3d–f。N4图例/N5裁切可并行独立定位，M1前必须修好。不重做会话原字节封存、safe/native捕获、准备核验、N3b检视、配置导入、已知执行导出或FRAME修复，不停在新的盘点报告。
```
