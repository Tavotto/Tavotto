# SVG 计算图表编辑 — 实施进度（稳定 checkpoint）

> 本文件是长周期稳定文档：每轮只更新状态表与 checkpoint 追加节，不重写历史。
> 计划全文：[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。历史实施证据目录：`/tmp/svgplan-evidence/`（原执行会话记录，本轮未重新取得全部日志）。
> **当前结论（2026-11-07 native与准备接线收口）：最新功能HEAD=d8416d4e；3c64195b补真实native读取/编译、屏障与续跑lineage，d8416d4e补准备/启动/重试声明过期与实际成功记录；11c67ab1 safe/workerd及N3b资格保留。新增实际native、终端已知无参、Python3.10和U09规定94项均有验证；无声明native两回执入口与旧git对象的JSON/identity逐字相同。完整声明图和输入字节尚未持久冷存/入包，声明UI、安全安装和迁移仍缺。图例/裁切/几何与最终资格未完成，M1/M2/M3均未通过。扩大集成保留2个基线Win1314失败和7项平台skip，不报告全分支全绿。**
> 最新任务顺序和验收条件见 [NEXT_PHASE.md](NEXT_PHASE.md)，可直接执行的接续提示词见 [NEXT_PHASE_PROMPT.md](NEXT_PHASE_PROMPT.md)。下方旧 checkpoint 保留为历史，不再作为当前接续指令。

## 环境（2026-11-07 当前快照）

- 当前实施树 `E:/0 - Project/Tavotto-svg-r00`，分支 `zixu.wang/svg-computational-r00`；最新功能HEAD=`d8416d4e`，前项`3c64195b/11c67ab1/b5f9f557/5e000c6f`。native捕获与准备过期核验分别commit，稳定文档另提交；各实现文件已释放，后续资源设计只写外部证据。沿用当前分支/工作树。原目录 `E:/0 - Project/Tavotto` 的 clean `main@69bcafae`未合入成果，不能在原目录启动旧版本判断新功能。
- 原层序/请求/来源/包核心提交全部保留；后续输入合同f6c4a13d、转换3ed06553、共享导出5bc82a81、恢复82b142d2与上述新批次均独立提交。实际HEAD可能继续前进，续接须先核对，不reset到文档快照。
- 本轮未 fetch，不把本地 origin/main 当远端新鲜度证明；当前任务未授权 push/远程 PR/合并/发布。
- 本轮证据根 `E:/0 - Project/svg-review-20261106`；已有 venv/前端依赖复用，实际 Python 源码 import 已核对为当前树。测试配置/数据隔离，遥测关闭；前端按 package.json 等价 CLI 验证。
- 最新冻结索引`n3c-native-staged-final.json`与`n3c-preparation-staged-final.json`记录源码SHA/原始检查和局限；前批`n3c-capture-staged-final.json`保留。既有UI资格见`n3c-real-ui-correct-kind-qualification.json`、`n3c-frame-ui-qualification.json`；本轮没有新增声明UI/浏览器验收，旧裁切/图例缺陷未修。未启动通宵调度器，历史后台状态须重新核实。
- 最新 NEXT_PHASE 和提示词直接在当前实施分支维护；不再依赖历史 review 文档分支才能取得新计划。

## 批次状态（2026-11-07 复核后；按完整退出条件）

| 批次 | 状态 | commits | 证据 |
| --- | --- | --- | --- |
| SG00 | baseline_recorded | a046b799（计划）、9a271f9f | 能力清单和历史基线已有；远端新鲜度及最终候选回归需补证 |
| SG01 | partial / revalidated_core | ADR 0166、e836a8de、3ed06553、f04d8f63 | 层序、ClipUnion及连续文字实验/ADR已同步；最终全范围复核待补 |
| SG02 | partial | ac060053 | 基础模型/绑定身份已补修；持久化来源/资源、实际环境与元数据恢复合同未闭合 |
| SG03 | core_partial | f1406038+fmt | 场景盘点与 inspect 已有；资源生命周期及所有入口未接通 |
| SG04 | core_partial | d0c0d898 | 采用判定 helper 已有；生产来源采用、语义元数据往返未接通 |
| SG05 | core_partial | 451d8490、4c2ca1f2、f04d8f63 | 坐标与连续文字转换已有独立回归；精确命中/文字/分组交互未完成 |
| SG06 | interaction_verified / core_visible | 133d2bf7、cbd8d95e、e836a8de、f6c4a13d、82b142d2 | 两系列/C恢复/确认撤销及类别与quoted CSV真实流程通过；完整图例/模型验收待补 |
| SG07 | core_partial / native_edit_verified | 4c2ca1f2、242cabc0、8e6c399a | 真实图内范围/线宽及多成员预览已验证；完整模型族与M1未完成 |
| SG08 | partial | 4c2ca1f2 | bar 附属误差基础修复已有；完整族与 UI/导出追验待补 |
| SG09 | partial | ac060053 | 色条基础所有权/镜像已修；复杂族和导出图像资源未验收 |
| SG10 | partial / product_cold_edit_verified | cbd8d95e、242cabc0、53393810、8e6c399a、264ce59c、a06cc3d3 | 真UI保存、零缓存新进程恢复、继续编辑及五格式原图导出已验；图例/复杂clip/成员层序仍缺 |
| SG11–SG17 | partial / pending | cae96f89、5bc82a81、5abb9a7d、95684f89、a7008840、d7a73ce1、ffe2fb32、c40805bf、e468d516、336783bf、71e637c8、206a1c34 | 正文/附件/命名关联与恢复已验，旧包原子输出已接；v4完整安装/写回/入口/平台和最终资格待补 |
| R00 | baseline_and_regressions | 5a4f5c2c | 基础反例保留；本轮用户流程和导出反例待随修复入库 |
| R01/R02 | core_partial / multi_target_verified | 3236307c、8e6c399a | 跨父/split/errorbar/colorbar共同目标与预览通过；任意clip、欠定成员paint slots和图例仍缺 |
| R03 | runner_and_source_consumers_integrated | 029dfac2、242cabc0、c2fd47d5、5b2b1403、2452d8fc | 受控runner与精确INPUT附件接真实API/UI/导出；不宣称全产品总进程cap或最终资格 |
| R04 | affected_checks_passed | a8a088cf、18907d65、cbd8d95e | 请求序号/表单/项目切换、late response 和单项 drop 均有受影响测试；Dialog 已可发起真实绑定；完整编辑器生命周期在 R06 追验 |
| R05 | binding_and_recovery_ui_verified / package_inspection_verified / execution_capture_verified / installation_pending | 5abb9a7d、95684f89、264ce59c、a7008840、d7b6eef9、d7a73ce1、ffe2fb32、c40805bf、e468d516、336783bf、71e637c8、206a1c34、9a9843d2、8ed50616、75b09b57、be84ba33、13720bc4、1795f244、d1389db6、5e000c6f、b5f9f557、11c67ab1、3c64195b、d8416d4e | 保存/恢复、v4导出/检视、safe/workerd/native捕获与准备过期核验已验；当时字节冷存、完整资源入包、安装API/UI仍缺 |
| R06 | product_editing_and_cold_restore_verified / partial | 242cabc0、53393810、8e6c399a、264ce59c | 冷开后Inspector编辑、undo/redo和exact显示已验；图例、完整操作及复杂几何仍缺 |
| R07 | partial / original_five_formats_ui_verified | 5bc82a81、c2fd47d5、2a40fec9、c9fa29a4、8fa84f5d、a06cc3d3 | 原图五格式真实UI及独立产物已验，EPS接线已提交；分享与完整双系列M1未完成 |
| R08 | full_edit_restore_verified / partial | cae96f89、029dfac2、53393810 | 生产v2导出与真UI完整编辑恢复通过；A采用、分享选择和正式项目迁移待补 |
| R09–R13 | pending | — | 见 NEXT_PHASE.md 第 4 节 |

## Checkpoint 追加（保留原记录；最新记录在前）

### 2026-11-07 执行情况复核与下一阶段四片安排

- 用户要求查看当前效果并梳理下一阶段。核对实施树HEAD=d8416d4e、两功能commit、现有JUnit/独立回执及旧对象对照；原main仍clean@69bcafae，未建分支或切树。各子任务均已结束，无在途源码写入。本次仅同步三份稳定文档，不新增生产实现，也不重复已有昂贵套件。
- 直接复看00:01既有截图：计算SVG与运行图可见，打包提示准确；坐标/标题裁切仍在。没有新UI/build资格，图例与几何缺口保留。U09规定94项通过、最终native41、准备23与3.10真实五例按各自范围记；扩大集成254通过/7skip/2基线Win1314失败仍为exit1。
- 两个实际只读probe均exit0：真实Worker→同次PDF/SVG→冷父进程inspect仅见public capture摘要；随后修改CSV，旧ref可读、执行计数仍1，但durable库没有原CSV字节。报告`n3c3b-durable-capture-wiring-review.md`（SHA A83595871F2D279ED0D168D00EE00E649CC5D3F3C2EAD0302E07D937EFC286F9）给出精确丢失点，属于设计/缺口证据，不是实现通过。
- 下一阶段改为3b-1同流封存实际bytes/私有附件→3b-2新metadata变体与条件capture身份→3b-3热保存/冷staging完整传递→3b-4显式execution v2+真实export/inspect。先冻结wire、canonical graph和50000成员/2GiB选择与旧4096项/512MiB Figure库的预算关系；旧refs/JSON/身份保持，历史无bytes不补签，包中旧路径不授读权。之后声明UI/ref映射→N3d/e/f→N4–N8完整保留。
- 更新NEXT_PHASE和可直接复制的PROMPT，删去当前队列中过时的“native/准备尚待实现”。仍在原实施分支按功能commit；goal active，M1/M2/M3未通过，无定时调度或发布。本次现有指导文档结构检查36项通过/0skip/exit0（`status-20261107-native-docs-final.xml`），UTF-8/相对链接/代码围栏、原始JUnit计数和冷probe对照通过，`git diff --check`为0；未把旧产品套件记作本次重跑。

### 2026-11-07 native与准备接线分功能提交，继续完整声明资源冷存

- 上一目标轮有实质进展：提交11c67ab1及同步文档d946225f。本轮继续实施，不重建分支。`3c64195b`提交17个native文件/测试/规则；`d8416d4e`提交准备/launchctx/会话指纹和专项共5文件。原main未改，未fetch/push/PR/合并/发布；不以完成两commit标目标完成。
- native生产链已接：实际desktop cwd/原rc_ID与terminal正面已知无参匹配，候选仅经原认证ACK私有传输；relay通用字段搬运不解析引擎策略。runner读取源码前begin，用实际compile bytes核SHA、真实start绑定eid、barrier前finish、continue只resume原baseline；变化后谱系不重开。-m/未知参数无正面归属保持未知，正常native执行/编辑不禁用。会话私有fact按execution_id/snapshot_epoch保存，重复barrier不补证，EOF/释放复用原资格状态机。
- 实际只读复核发现并修复两个接线缺口：原准备收口走from_worker丢native正证；现两native回执入口共用唯一投影，保默认字段/instance。旧build body可在继续后错误取得新屏障正证；现ensure_built与render/continue共Figure锁，仅有正证时在本地响应盖请求eid，并在EOF后末核；from_worker拒未盖章/过期body。新socket41及真实PreparationService链分别证明这两项，未重写执行路由或释放状态机。
- 准备计划区分legacy未观察与真实absence，精确scope metadata token进入私有快照；执行前、重试、执行后变更均拒旧计划，replan不吸收。launchctx只存实际receipt摘要，当前批准资源对照复用唯一freezer，比较不制造执行事实；native经实际result.receipt传NativeBasis。旧记录不自动升级，热Figure保原证据。safe真实缓存复用执行计数1、native真实Service→receipt→成功记录→资源变化拒旧均已验。
- 资格分组：producer新增22全部通过；受影响185收集=184通过/1旧Win filelink权限skip。最终会话41/0skip；准备23/0skip，前阶段161/1旧symlink skip。Python3.10实际native5/0skip（含真实版本/PID与from_worker摘要）；冷父进程import拒科学栈/Flask通过。固定旧d946225f的owned无参/有参两入口与terminal入口共5情景，public/private/facts JSON及三身份逐字相同；见`n3c-native-default-oracle.json`。不同源码时刻/重叠组不相加。
- U09规定94个不同用例真实全通过/0skip：90项receipt/runtime/trace/export身份，加FO32两个出口/错误选择及FO30共4。已有环境3.10.20、受管环境锁内3.13.15均实际到RenderCore PDF/PNG/TIFF，独立原回执分别保留；managed归档47131996字节/SHA与锁一致。首轮3fail是夹具输出cp936及pip离线wheelhouse空格路径；仅本次PYTHONUTF8与逐轮包SHA相同的自有无空格Temp副本修测试环境，没有改测试、产品锁、默认enabled或全局安装。额外existing单例只为保被同名覆盖的回执，不增加覆盖数。见`n3c-u09-validation.{md,json}`。
- 扩大根集成263收集：254通过、7平台skip、2 Win1314失败、实际exit1；两失败在旧d946225f完全相同测试函数也实际复现，尚未进入产品路径。原脚本/锁源无改，不删用例、不伪标skip或绿；`n3c-runtime-permission-baseline.json`保旧对象反证。较早root142/0skip不冒最终源码重跑；最终集成、Service23、socket41、3.10五例覆盖后续hook/marker差异。全仓Ruff/format、规则41、budget、diff各自exit0；无前端改动不虚报新build/UI。
- 原红灯均保留：新测试误判stems/render inline、CLI fixture未显式上层project、native不可入池却假造复用；新增socket actor漏参数造成等待，精确终止自有pytest、修fixture有界握手后41通过，没有生产死锁证据。runner3、会话5、准备6等内存反证均在实际判据落点触发断言失败/exit1，磁盘源码不变；不把定位器或夹具错误计为反证通过。
- 下一实际工作为N3c-3b：小量真实Worker→同次PDF/SVG→durable save→新父进程inspect已证仅public revision冷存，完整graph及当时输入字节都没有。先在实际capture/freeze边界有界物化不可变资源，再扩durable metadata与严格reader/collector/版本化execution；export时不能从当前声明补签。完整graph的mappings.from可能含旧绝对请求，属于私有来源事实，不能授本机读权。无capture旧metadata/ref保持，旧archive不回填。声明UI/精确ref迁移→N3d/e/f→N4–N8队列完整保留，goal active。

### 2026-11-07 safe/workerd输入捕获已提交，下一阶段从native与过期核验接续

- 用户要求查看执行情况、效果并安排下一阶段。本次核对实际HEAD/index/diff、现有子代理结束状态和原始JUnit；原目录main未变，未建分支/worktree、fetch、推送、合并或发布。将已完成验证的23文件捕获功能提交为`11c67ab1`，稳定文档单独同步；未继续扩大本次生产代码范围。
- `b5f9f557`先修正实际`documentpackage → packageexecution → launchctx → runconfig → documentpackage`循环：纯stdlib叶子`packagemembers`持有原错误/成员权威，旧公开API同对象重导出，不复制路径规则。637通过/0 skip；严格与legacy ZIP整份字节、七个原定义AST与旧git对象一致；内存恢复旧依赖后门禁准确失败，见`n3c-package-members-freeze.{md,json}`。
- `11c67ab1`实现`declaredinputs`私有CAS存储、纯stdlib `inputdeclaration` freezer、实际Worker起止捕获和`captured_build`冻结胶囊；缓存ensure_built不重签旧图。父进程只接同一胜出execution、scope、source SHA与真实PID核验通过的事实；Rust supervisor在真实请求锁内提供可信child PID，解决同session换child后的陈旧PID。receipt仅公开无路径`{version,revision}`，缺省字段完全省略，旧JSON及identity独立对拍不变。显式文件/批准prefix起止清单核验不等于任意Python完整IO追踪。
- 认证`GET/POST/DELETE /api/engine/input-declarations`已接，409为CAS冲突、400为无效声明、503为存储不可用。POST不执行脚本；DELETE撤销元信息不要求原脚本还存在。实际选择→worker→receipt→v4导出/严格检视证明公开revision被保留，尚未携带完整声明图及全部资源；execution v1依赖覆盖仍准确incomplete。三错误码、前端receipt可选类型、发行源码datas、规则/ADR同功能提交。
- 已有末次证据逐组核对，不累计：核心24/0skip；HTTP21/0skip；receipt43/0skip；settings30/0skip；Python worker137/0skip；真实workerd23/0skip；Python3.10实际worker23/0skip；Rust62/0ignore；错误码/i18n366/0skip；发行检查147通过/9个POSIX/Linux条件skip。根联合173通过早于DELETE末修和新增目录link用例，相应delta由HTTP21与核心24覆盖，不冒称173在最终SHA重跑。见`n3c-capture-staged-final.json`、`n3c3a-worker-capture-validation.{md,json}`及各原始XML；3.10只验证受影响实际worker路径，非全套最低版本资格。
- 本次新增实际检查：全仓Ruff、format、diff、playground build/check、插件画布build/check均exit0，源码23文件SHA构建前后不变；命令/原始日志见`n3c-capture-review-*.log`及冻结索引。此前正式`pnpm build`最终exit0（工具`d7e489`）仍按原执行时刻记录。本次文档/规则门禁41通过、0失败/0skip/exit0，见`n3c-capture-review-docs.{json,log,xml}`，不把历史检查冒称新跑。
- 原红灯保留：Windows path stat与fd stat的ctime跨API差异，现分别完整基线、仅跨API核共同身份字段；真实Rust换child PID错配，现用可信supervisor字段；遗漏发行datas及错误码表；删除源码不能撤销声明P2。各项修后受影响验证通过，P2只读定向复核已关闭。核心、receipt、settings、worker及循环门禁反证均是实际落点失败，没有删测试或放松安全边界。
- 效果边界：计算编辑/保存冷恢复/单曲线五格式旧用户证据保留；本次查看`output/playwright/n3c-ui-legacy-frame.png`仍见刻度和文字边缘裁切。已证实旧SVG图例不跟随；本批没有实现声明UI、安全安装、完整搬迁或这些视觉修复。M1/M2/M3均未通过，完整旧Python功能及支持平台安装物尚未最终验收。
- 下一动作：沿`n3c-native-capture-lifecycle-design.md`冻结真实native cwd/known run候选，在runner真实源码读取与用户执行之前begin、每次barrier前finish，按execution_id/snapshot_epoch/初始lineage消费；初次无证据或中途失配不能continue补签。再补preparation/launch过期声明核验，继而N3c-3b完整图/资源→3c声明UI→3d精确ref迁移→N3d/e/f安装与冷开。N4图例、N5几何、N6/M1、N7/M2、N8/M3完整保留，不从safe捕获或N3b重新实现。

### 2026-11-07 N3b正式收口与下一阶段复核

- 新功能提交为`1795f244`（有界流式暂存与校验）、`d1389db6`（完整portable Figure档案检视）、`5e000c6f`（实际v4检视API/资源语义/HTTP回归/规则）。沿用同一实施树和分支，未推送/合并/发布。API提交只含6个对应文件，稳定进度文档未混批；各子代理已释放文件，没有在途代码写入任务。
- 当前API源码SHA：app=`7f3c2ba6…7655d88`，packageinspection=`430ba6f1…635902`，packagebudget=`e2acbb4e…91468f`，新HTTP测试=`16f4d476…ff922f3`。最终68项HTTP通过、0失败/0 skip/exit0，26.50秒；全仓Ruff/格式检查1442文件通过。原始命令、七源SHA、首红及反证见`n3b-inspect-http-missing-candidates-validation.{md,json}`和`n3b-inspect-http-68-final.{log,xml}`。
- 只读审查的唯一P2：明确缺失helper及runtime_assets中明确脚本漏报。已统一为声明候选与实际目标资源核对，已携文件清旧缺件；完整INPUT仅替代chart-only原件，不吞普通/显式script职责。不安全/opaque旧声明保留且警示，不读取外部路径、不授予执行。新增15项回归包含真实首红与去掉候选收集后再次漏报的有效反证；独立审查核读同SHA源码及68项原始日志后收口，未冒称自行重跑。
- 独立真实网络验证在最终consumer上取得401/200/409/422：认证、完整包、未知项目、坏ZIP均符合预期；整份doc/manifest与真实UI导出的包相同，14资源/1快照、精确rc_ID/argv保持，源脚本计数仍1、未执行。见`n3b-network-inspection-result.json`及command/log；此资格不包括项目安装或全量搬迁。
- 验证集合不相加：流式326通过/0 skip；完整store316通过/31 Win1314权限跳过；较早consumer `6a6fbb…`的511联合通过仅适用该时刻，不能写成最终`430ba6…`又跑511。最终变更由68项及定向复核覆盖。此前53项/首轮夹具错误/请求流边界失败/缺件首红均保留。无前端代码改动，不无理由重跑旧build；其最近资格指纹仍`9c11afde28799951`。
- 效果复核：既有截图`output/playwright/n3c-ui-legacy-frame.png`可见计算曲线/运行图及准确不完整依赖提示，但坐标数字、标题边缘仍有裁切；这只是历史UI状态，不冒充新API后的浏览器重验。图例不跟随样式仍是已知缺陷。完整双系列M1、所有科学族/交互与最终旧功能/平台资格继续pending。
- 下一动作已改为N3c-3：先冻结捕获时输入声明事实，接真实safe/workerd/native执行与原receipt，再接可携声明/外部/prefix资源和实际UI；之后N3d独占安装→N3e原writer采用→N3f删自建原目录/清缓存/新进程的整链。`n3c-input-portability-design.md`与三个真实probe只支持设计/机制，不是已实现产品；输入当前binding在导出时重算且不进receipt identity，绝不能给旧Figure事后补证。无声明默认wire/identity保持，成功receipt/partial observer不证明任意Python输入完整。
- 本轮复核未新建调度器，goal继续active。长驻UI服务的PID和旧源码时刻仅作线索，复用前核创建时间/命令/实际import；本轮独立socket服务已结束，不把旧23989后端称为已加载5e000c6f。
- 台账收口验证：41项文档/规则检查通过、0 skip/exit0，UTF-8/围栏/相对链接与源码身份已核；agents_budget退出0，原有体积建议超额保留。冻结索引`n3b-inspection-staged-final.json`记录API实际6文件提交、七源SHA及新旧验证边界；仅三份稳定文档另行提交。

### 2026-11-07 N3b实施安全checkpoint：流式与完整归档已提交，API收口中

- 上一goal turn为progress（复核改变了稳定台账并定位共享venv裸import边界）；本turn继续实际实现，无新增分支/worktree/fetch/发布。实施分支新增`1795f244`流式暂存/验证（2生产+新测试）、`d1389db6`完整portable Figure检视（svgstore、2新测试、导出规则）；root定向stage，其他候选未混入。原main未改。
- 流式候选：普通file/script/figure_snapshot各20MiB实际ZIP与stage读取tripwire，默认bytes reader/旧writer保持；326项/0skip/exit0、4实际内存变异、旧strict/legacy_names完整ZIP字节相同，详见`n3b-package-streaming-validation.md`。完整store：所有metadata/blob（含未引用）、marker-only/archive-only及真实junction覆盖；316通过/31 Win1314跳过/exit0、6变异、5组原/新生产树字节相同，见`n3b-svgstore-full-freeze.md`。不把31跳过写为通过。
- 新API候选`POST /api/documents/v4/package/inspect`已接同一认证/格式域，有限实际上传/字段内存/容器预算，隔离data_dir请求暂存，完整INPUT/资源/Figure语义与SVG安全/PDF可读单页检查，零用户脚本/项目/正文/配置写入。初47项有4个fixture错误和一个真实坏pj漏守卫；修正公共_request_ctx并保留原红。49绿后实际总body等号边界2红，+1哨兵修后53项/0skip/exit0，四有效反证保留；`n3b-inspect-http-validation.md`。最终联合511/0skip/exit0（`n3b-final-integrated-command.json/.xml/.log`，7源无漂移），全Ruff/format1442文件及agents_budget --check通过；不是全分支回归。
- 独立新父进程/真实认证本机socket读先前真UI包b6a024…：401/200/409/422均预期，整份doc/manifest、14资源/1snapshot、原rc/argv不变，计数文件SHA仍1；准确incomplete与remap须重连，见`n3b-network-inspection-result.json`。首socket夹具误把port当SessionState构造关键字，发请求前TypeError；修正真实port属性后通过，首错另存未隐藏。该父进程/服务/child均正常关闭，不重启旧浏览器夹具服务。
- 独立只读consumer审查又证实两个同根报告缺陷：正式删helper包的声明missing被inspect漏掉；旧v4仅runtime_assets声明script而scripts/execution省略时也漏其缺件。外部实际两探针红已保留；self正只改新增HTTP测试补维护回归，root将统一验证声明候选并用实际plan重新计算，不给descriptor运行权，也不把完整chartSource缺原SVG当缺件。该修复尚未落源码，53/511不得冒称包含它的最终资格。
- 当前未提交：root接管`app.py`、`engine/packagebudget.py`、新`engine/packageinspection.py`、布局规则/ADR与稳定文档；self仅新`tests/test_chart_package_inspect_http.py`（已53绿、现补缺件回归）。index空，前所有叶子已提交。export_adapter_review只读检查消费者；svg_ir_fidelity只做仓库外N3c-3设计/搬迁实验；无root测试句柄在途，71588/78728均exit0。
- 下一动作：取得声明缺件新增红→统一修候选计算→当前HTTP文件末验/必要反证→复核共享契约并提交API，不重跑无关旧套件；随后N3c-3。外部已证明remap绝对路径指纹搬迁失配，以及public save在有捕获时完整输入证据下可生成新ref、保sourceId/rc/argv/patch/字节；仅捕获后声明不能签旧图，receipt binding在导出时重算的时刻问题仍须结构解决。完整N3d–N8继续待实现/验收，goal active。

### 2026-11-07 00:07起 执行效果复核与下一阶段安排

- 用户本轮要求复核执行情况/效果并安排下一阶段；未新增功能或改生产代码。核实实施分支仍为`zixu.wang/svg-computational-r00@13720bc4`；原目录仍clean main@69bcafae，成果未合入。当前未提交代码仅svgstore及新检视测试，原SHA与冻结报告一致；此前三个子代理均completed，无在途写入任务。未fetch、建分支、合并、发布或新建调度任务。
- 对照原始JUnit：718联合/8新HTTP/145配置/56前端/100图幅各自0fail/0skip；N3b叶子274收集=243通过/31权限skip。核7个执行源码SHA与冻结资格一致，两个真UI包归档SHA与记录一致，追加式计数文件仍1且SHA不变；本轮没有重跑产品测试或浏览器操作。实际查看最终旧图幅截图：曲线和runtime可见，提示准确说明依赖不完整，坐标数字/文字边缘裁切仍可见，不能称整体最终可用。
- 新跑现有`test_agents_rules_index.py`与`test_docs_references.py`共41通过/0fail/0skip/exit0，见`status-20261107-docs-command.json`与`status-20261107-docs.log/.xml`。三份稳定文档同步：当前状态、最近N3b/c-3→N3d/e/f依赖与功能提交边界、N4–N8后续、可直接复制的无人值守提示词；清除旧23:05在途所有权/“配置和事实收集未实现”指令，历史进度保留。
- 导入身份另有实际核对：共享venv裸`python -c import tavotto`默认指向另一旧树；当前pytest.ini的`pythonpath=src`在collection前正确选择本实施树。独立collection探针确认tavotto/packageexecution/runconfig/chartpackage均来自当前src，95项仅收集、未运行，见`status-20261107-pytest-import.json`。当前浏览器server显式设置sys.path/PYTHONPATH，原冷子进程测试也显式绑定当前src。后续手工Python/服务必须指定当前src，不能仅凭venv路径宣称源码身份；不把裸解释器发现误报为既有pytest资格失败。
- 当前自有服务身份重新核对：64860/23989、75464/23988及浏览器39048/75500/66204的创建时间和命令与前记录一致；均未重启/重seed。新N3c服务与旧N3a服务继续区分，服务不是通宵调度器。详细复核与当前差异见`status-20261107-current.json`。
- 下一条实现工作：以活跃Prepared/现svgstore权威接N3b真实只读检视与全资源语义；N3c-3独立输入/资源可并行，主代理唯一写共享合同；随后独占新项目安装、原writer一次正文/回读、删自建测试源/清缓存/新进程整链。图例、裁剪/成员几何、完整M1/M2/M3继续待验；不重做75b09b57/be84ba33/13720bc4，不把叶子或一夜结束当完成。

### 2026-11-07 00:01 旧图幅兼容已收口，接续实际检视与完整输入声明

- `1c9da4ca`单独修正旧测试的观测：switchDocument只保证安装/排队，不能await一轮微任务就要求clean。只读真实回执证据显示原writer恰写一次、磁盘与内存快照均保留，saved是正常1600ms反馈态；因此改等公开onDocumentPersisted，严格核一次PUT及实际盘体，成功态接受saved/clean。生产store/writer/时序一行未改，首红和旧/新对照保留；报告`n3c-save-promise-review.md`。
- `13720bc4`提交2文件FRAME兼容修复与对应规则：迁移真正追加override时撤旧ref，其余三类不动。最终7文件100项/0fail/0skip/exit0；随后lint/build/playground/check均0，最终指纹`9c11afde28799951`。先前99/1为有效历史失败，不能回写成当时通过；最终资格见`n3c-frame-lifecycle-final.xml`与`n3c-compat-frontend-build.json`。
- 真实新鲜浏览器加载经public AS保存且严格回读的旧输入：runtime无marker、空patch、原空patch有效ref。新bundle立即请求中带FRAME/marker且无过期ref；同一菜单导出200，ZIP只补正确FRAME快照引用，原所有编辑/几何/模型和rc/argv保持，两个运行图共用正确新ref，计数文件SHA仍1。`n3c-frame-ui-qualification.json`、`n3c-ui-legacy-frame.json`及`n3c-browser/legacy-frame-regression/`保独立证据，不能以此替代完整M1。
- 当前根接管仅两份未提交代码：`svgstore.py`与`tests/test_svgstore_portable_inspect.py`，SHA和243通过/31权限跳过见23:51记录。它仍未接生产v4检视消费者；不提交成“已安装”。子代理均已释放，没有遗留构建/测试句柄。下一条实际工作是沿该API接严格包检视/全资源语义，再补N3c完整声明与外部/prefix输入，之后N3d–f；不要重做已提交的配置/事实收集/FRAME。
- 新增fresh浏览器`tavotto-frame-legacy` daemon75500，创建2026-11-07T00:01:17.572366+08:00，当前打开working `d_n3c_legacy_frame_fix`，该槽已成为fixture durable last；原working `d_n3c_browser`与named完整保留。仍复用PID64860/23989服务，未重启或重跑脚本。00:01代码分支未改远端/原main，goal active。

### 2026-11-06 23:51 已知执行资源已接真实导出，继续兼容与完整迁移

- 本goal turn为progress：同一实施分支分功能提交`75b09b57`（运行配置恢复/共同存储锁）与`be84ba33`（18文件，闭式执行清单、已知输入收集、真实导出和UI），根精确stage，不混入下批候选；未新建分支、fetch、合并或发布。原main未改。功能详细资格索引`n3c-execution-export-staged.json`；不同范围/时刻用例不相加成全量。
- 配置API保原rc_ID和精确argv，幂等/冲突/敏感占位/坏存储/容量明确处理，导入不设置默认/成功事实；所有新版writer复用同一跨进程guard，旧双进程丢写实际反例已关闭，原无参数成功遇锁不可用仍零登记写继续。最终145项/0skip、4实际反证、旧完整JSON字节对拍通过，见`n3c-runconfig-portable-freeze.md`。原`atomicio`替换后目录fsync失败并不保证回滚，本批文案不伪称所有I/O失败零改；不把咨询锁称跨版本CAS。
- `execution`v1现在被正式manifest与资源reader共同校验，真实导出携登记子集、配置、静态/已观察CSV/helper及可携精确file remap；同脚本的已知无参数与未知参数来源分别保留。没有完整输入声明时coverage必须incomplete，已知缺件单列missing，界面准确区分两者；file计数含数据。新95 validator/34 collector叶子专项通过，真实8HTTP证明坏字段/错basis摘要被正式reader拒绝，删配置/敏感/旧basis/缺helper/混合上下文/旧包路径已验；见对应n3c-execution-*报告。
- 根最终单一联合命令718通过/0skip/exit0，原v4/旧包/容器/目标计划/错误合同/规则均在其实际范围；56前端专项、正式i18n/lint/build/playground/check均0，指纹`7aad37b5d51aebfa`。全仓Ruff与format1438文件通过，agents_budget --check退出0。更早295项初联验XML零失败但观察句柄关闭前未留exit，已由带原始log/exit文件的最终718命令取代，不补写原退出码。
- 新自建中文项目真实菜单200，完整正文等于实际请求，5个file资源/原rc_ID和argv/全部已知输入字节及14项目目标核对通过；FRAME正式保存后runtime预览200且图像完整，脚本计数始终1。`n3c-real-ui-correct-kind-qualification.json`、`n3c-ui-frame-save-continue.json`保真证据。首夹具错误为runtime误写pdf、漏frame marker，后经public接口修kind、fresh浏览器真实F2→AS PUT→CtrlS冻结正确配对；首404/422、错误POST观察超时（实际AS为PUT）、静态CSV文件名假设错误均保留。并发夹具JSONL有交错记录，最终计数证明改用保全的计数文件SHA，不把坏行当没有事件。
- 同时发现并修出一个兼容候选：读档确实追加FRAME时旧figureSnapshot须撤销，其余仅marker/已有FRAME/已marker路径保留，原对象不变。两文件由self冻结，旧行为新回归3红、修后49专项绿、3故障反证有效；较广7文件100项仍99通过/1失败，旧新独立case都在`figureSnapshotRestoration.test.ts:166`得到saving而非clean。export_adapter_review正在只读定位真实保存完成时刻与该断言主语，不能将它写为全绿或擅改产品同步时序。候选尚未build/commit，见`n3c-frame-migration-validation.json`。
- N3b公有`svgstore.inspect_portable_snapshot`及新测试也已冻结未提交，只证明档案内部身份/owner/配对blob，不签当前remap或执行权。新旧联合243通过/31 Windows symlink权限跳过，4反证有效，旧38定义原文保持；API/边界见`n3b-svgstore-portable-freeze.md`。正式v4检视端点/全store语义/安全安装尚未接，不能把此叶子报作完成。
- 当前未提交所有权：根接管`src/tavotto/engine/svgstore.py`、`tests/test_svgstore_portable_inspect.py`、`web/src/lib/figureFrameMigration.ts`、`web/src/lib/figureFrame.test.ts`；索引为空。下一动作先收口保存断言/FRAME候选，实际接N3b消费者及N3c剩余完整声明/外部资源，再N3d–f真实安装/冷迁移、N4–N8。现有项目包不是完整可复现运行项目，目标范围保持，goal active。
- 本轮新增自有服务：PID64860，2026-11-06T23:26:50.033107+08:00，`n3c-browser/server.py`、端口23989、exec93934，元数据见该目录process.json；新fresh浏览器`tavotto-execution-v4` daemon39048（23:40:27.365023+08:00）。旧自有75464/23988与daemon66204在本轮已核精确身份，未终止；更早PID仅历史线索。新CLI产物全在外部证据根，未污染源码目录；服务不是调度器。

### 2026-11-06 23:05 执行情况复核与下一阶段收敛

- 用户要求核对执行情况、效果并梳理后续安排；原实现goal保持active。实际工作区/分支与提交已核对：实施分支HEAD=83ccb6b0，原main clean；本轮未新建分支、fetch、合并或发布。8ed50616与9a9843d2仍分别是实际v4导出和安装目标计划的功能提交，完整检视/安装尚不存在。
- 重新读取最终导出证据与实际截图：v4端点真实返回200，原chart.svg缺席时仍携完整INPUT；缺失普通素材被准确列为missing并由菜单提示。截图中轴刻度边缘裁切仍可见，图例旧句柄不跟随曲线样式的既有反例仍未关闭，不能把备份成功解释成视觉/科学语义全部合格。下一阶段保留原SVG/最终产物对照作为裁切问题的判据。
- N3c已从只读盘点进入实现。`svg_ir_fidelity`独占runconfig.py与新增portable测试，补原rc_ID导入及全部现有读改写入口的共享跨进程锁；`export_adapter_review`独占新packageexecution.py与测试，落实闭式execution清单/资源关系；`svg_self_contained`独占新packageexecution_export.py与测试，将现有Registry/静态绑定/动态依据/参数/cwd/remap事实接到资源回调。根保留app、manifest、契约、UI、规则、三份稳定文档与提交所有权。代理不会自行commit。
- 实际旧writer双进程探针发现：两个put均exit0，但期望3条最终只有2条，第二写入丢失；证据`n3c-runconfig-before.py/.json`。因此新增import不能独占一把其他writer不遵守的锁；共享guard仅约束新版参与者，不声称覆盖旧二进制或外部编辑。修复及针对性/并发回归待完成，此记录不是通过结论。
- 后续按N3c-1配置存储、N3c-2真实导出消费者、N3b严格检视、N3d/e安全安装及原writer UI、N3f移目录冷开验收分功能提交。首先交付一条可复现的实际搬迁流程，再做N4图例、N5几何、N6/M1和N7–N8；完整范围未缩减。具体输入/退出条件在NEXT_PHASE第4节，提示词同步当前未提交状态。
- 本轮状态复核没有重跑昂贵的既有专项/全量套件，也没有替尚在实现的N3c补写绿灯。服务表沿22:50历史线索，未逐一重核实时存活；没有创建整夜调度器。恢复先查实际diff、代理归属及进程身份，不能覆盖这些在途改动。

### 2026-11-06 22:50 v4实际导出与安装目标计划已提交

- 本goal turn为progress：实际新增生产端点/菜单接线、修复真实重复引用缺陷，取得最终独立证据并分功能提交。唯一实施树/分支不变；9a9843d2为安装目标投影，8ed50616为v4导出/入口及所需错误合同。未新建分支/worktree、fetch、推送、合并或发布；原main未动。17文件导出候选已提交，不再保留“6文件待接后端”的旧接续指令。
- `POST /api/documents/v4/package`现在真实存在，沿原认证及v4格式/会话namespace，冻结全部画布的精确INPUT；chartpackage唯一layout/manifest与实际预算工厂、严格检视及不可覆盖发布保持。普通file、登记script、runtime描述符和已核Figure配对携带；不执行用户脚本。原SVG缺席但有效chartSource不算missing；普通缺件/脚本/未具配对执行图明确missing。旧2/3默认wire、只读open及旧helper默认行为保持。
- 前端同一菜单完整深拷贝项目，固定pj/format，操作序+pj/format/documentId/loadSeq防迟到状态，错误格式回执不降旧链；缺件包准确提示需补齐。最新7文件85项/0skip及13条累计前端反证分阶段有据，非全前端回归；最终types/i18n/build均exit0，playground build/check均0、指纹`c95b69046c966033`。`n3a-package-incomplete-frontend-validation.json`及`n3a-final-frozen-build.json`。
- 真HTTP最初43项中发现双面板省略显式ref时只有首面板被回填；保留真实红例，strict去重资源后逐个回填相同实际ref，显式/省略双变体均通过。后续增加越界源在hash读取前403的反例。最终同一命令`n3a-final-integrated.xml`为438通过/0失败/0error/0skip/exit0（18新HTTP、18旧原子HTTP、8旧兼容、358错误合同、36规则）；不是把不同时刻重叠组相加。最初联合401项4fail、首fixture/换行错误及原重复引用红例均保留。
- 错误合同门禁揭出旧93521ce2已经发出却未登记的7个SVG码；补双语/参数表，并用AST正面识别DocumentError/真实响应dict，修复靠字段先后及邻近dict假证明的旧判据。358专项通过；旧通用错误门禁、原漏斗/registry/allowlist逐AST保持，4个实际故障反证命中。`n3a-error-contract-baseline.json`、`n3a-error-contract-freeze.md`。全仓Ruff check与format check均0（1431文件）；未删除或放宽用例。
- 当前app/test源码的3个HTTP反证实际命中丢格式、重复ref漏回填、同名覆盖，pytest各exit1/AssertionError、wrapper0、源SHA前后不变。`n3a-final-http-mutations-summary.json`。完整阶段/提交源清单`n3a-export-staged-final.json`；app SHA=`0fdf5e718d36d4ca51e3386cbdc871d2d77f139772bad43d749ade2fef26e3a4`，新HTTP test SHA=`bfb25aef81144fa75d06753165bc6944c33148e165a155d68833f022d68a1d7c`。
- 新隔离project复制既有证据fixture，未重建原chart.svg；真实菜单分别生成完整单画布包、含非活动缺件画布包。严格reader+独立读ZIP证明整份正文等于实际UI请求，模型/原序编辑/完整INPUT一致，缺件清单准确；`n3a-real-ui-package-qualification.json`。首次HTTP已200但脚本等待了错误中文短语而超时，保留原log，仅观察脚本问题；后续完整/缺件实际菜单通过。最后重启新后端并加载最终bundle再次真导出，`n3a-ui-final-frozen.json`，最终产物/源码绑定在staged-final索引；不将这一小fixture当完整M1。
- 9a9843d2：`plan_project_resources`在活跃Prepared内全核资源/manifest，将assets与scripts投影同项目根；唯一portable规则派生比较键，同拼写同SHA/长度可复用，异内容/case/NFC/祖先目录别名/文件目录冲突零写入拒绝。277项/0skip（新67+计算包82+容器128）、3个AST反证及36规则通过。`n3b-project-targets-freeze.md`、`n3b-targets-staged.json`；当前只有只读计划，检视API/语义闭包和安装均未完成。
- N3c实际worker探针已证静态binding只见固定CSV，真实成功basis另见argv动态CSV/helper，而项目外显式remap实际读入却不在观察basis；`matched=true`与完整receipt不能证明依赖集合完整。保外来rc_id缺公共导入接口；cwd/remap/本机环境与执行许可不能照搬。`n3c-portable-resource-config-review.md/.json`含精确API/夹具/退出0与待实现闭式execution建议。此为实现依据，不是N3c已完成。
- 当前第一动作改为N3c可携上下文/数据与公共配置导入，再接N3b严格检视/语义、N3d/e独立项目安装与原writer真实回执，最后N3f搬迁整链。N4–N8完整范围保留，未做全分支/全平台最终回归，不能宣称所有旧功能已证零回归；M1/M2/M3仍未通过，goal active。
- 新自有后端75464（22:45:34.778615+08:00，23988，`n3a-browser/server.py`，session65556）载入最终app；浏览器tavotto-package-v4 daemon66204（22:13:21.576551+08:00）。旧59244与其renderchild23440已不在，旧78208已读终态；所有本批测试/构建句柄已结束，三代理释放。CLI新产物移到外部`n3a-browser/playwright-cli-cwd-artifacts`，旧07:10 console原字节已还原原处。本批未核旧21:41服务实时状态，先核PID/创建时间/命令再接管。未创建通宵调度器。

### 2026-11-06 22:00 当前执行情况与下一阶段复核

- 应用户状态询问复核实际工作树、提交、源文件、原始验证索引及截图；本轮未新建/切换分支，未fetch、推送、合并或发布。实际实施HEAD=faa07287、索引为空；原main干净。其他历史worktree仍存在，本轮只维护既有r00实施树，不删除历史数据。
- 本次新增且尚未提交的6文件：`web/src/lib/{api.ts,documentFormat.ts,documentFormat.test.ts,packageApi.test.ts}`、`web/src/components/{TopBar.tsx,topBarPackageExport.test.tsx}`。旧三参数打包wire保持；显式v4固定pj/格式、复制全部画布和原序编辑、拒绝错误格式回执，操作序与项目/格式/documentId/loadSeq防迟到串写。代理已冻结释放，根保留集成所有权；尚缺真实后端，不把候选当功能提交。
- `n3-package-export-frontend-validation.json`：最终7文件78通过/0失败/0skip；正式`pnpm build`、`pnpm i18n:check`、`pnpm lint`均exit0。10条内存反证均唯一命中且AssertionError/exit1，wrapper0，源未改变；3次测试fixture失败原日志保留。未跑真实ZIP/HTTP/浏览器导出、安装或此候选的playground，旧448项也未因状态询问重复跑。
- 当前`src/tavotto/app.py`与336783bf资格SHA一致，`chartpackage.py`与206a1c34一致，`documentStore.ts`与71e637c8一致。旧保存报告中的api/store/规则hash差异分别来自当前导出候选、已提交回执抽取及随后规则同步，不把旧报告冒充当前整树资格。已检查原始18新/8旧HTTP和82计算包XML，无失败/跳过；不同范围与源码时刻不累计。
- 源码路由复核：现有`api_package`只接`/api/package`，`api_package_open`只读；v4适配前缀仅layouts/autosave/layout-session/versions，不含package。故真实v4导出缺口成立，前端78项不能证明服务已实现。前一次所记后端资格服务也载入较早app，本次未重启或对旧服务补写新资格。
- 重新查看冷保存与reload截图：确有真实编辑界面，但fixture的轴刻度在面板边缘被截断，仍需与原SVG及实际导出几何对照，不能因保存成功就宣称视觉完全正确。图例曲线红/旧句柄蓝的已证实缺陷仍未修复。全量既有Python能力与支持平台回归未完成，目前不能确认“全部现有功能完全不受影响”。
- 下一阶段收敛为N3a–N3f：真实v4导出及现候选联合接通→严格检视/目标投影→registry/数据依赖/运行配置/快照可携闭包→独占新项目安装→原writer真实回执UI→删源移目录新进程完整验收。具体依赖、改动落点、功能commit与退出条件见NEXT_PHASE最新表；完成后继续N4图例、N5几何、N6/M1、N7/M2及N8/M3，原SG00–SG17范围不变。
- 执行goal仍active；本次是状态/安排复核，没有创建通宵调度器。当前6文件构建窗口已释放，无新服务或测试进程需接管。长期服务的实时状态未在本次逐一复核，不能把旧PID表当当前运行证明。

### 2026-11-06 21:41 保存恢复收口与项目包原子输出

- 上一goal turn为progress：产生统一构建和实际named外改409证据并提交acbaeb82；本轮继续改变权威代码与取得新证据，不把状态复述当推进。沿用唯一实施树/分支，功能HEAD=206a1c34；未新建分支/worktree、推送、合并或发布，原main未合入。
- 六批独立功能已提交：0b611411（预检输入的精确附件/完整render identity）；d7a73ce1（20前端文件+3规则/ADR，命名关联与明确冲突恢复，含404备份失败P1）；e468d516（最终plan预算、ZIP64及旧名兼容）；336783bf（旧schema2/3包HTTP原子输出）；71e637c8（原writer真实工作副本回执）；206a1c34（计算包唯一manifest/实际census预算回调）。每批精确stage核源码SHA，未混下一功能；提交与原始资格范围见各staged.json。
- 从新冻结dist n21-recovery-ui-dist-2056/23987启动全新tavotto-recovery-ui，初始localStorage=[]，实际恢复A绑定/dirty与chartSource。外部改AS后真实编辑触发409 external_change；原冲突脚本最初错误预期stale_write而失败已保留，不称产品失败。真“覆盖”确认发同writer的固定实际revision+完整旧view证明，创建新actor，named原修订与字节未变；下一次普通编辑200且不带recovery证明。n21-recovery-overwrite-qualification.json。
- 真“重新加载”时仅暂停实际GET（不伪造响应），等待期间通过F2新增编辑，再放行真实后端读取。外部正文安装/恢复AS 200，新编辑进入原恢复槽；随后普通编辑仍200。实际proof与外部AS摘要及原view逐项相等，named文件不变。n21-recovery-reload-qualification.json，截图output/playwright/n21-recovery-ui-reload.png。对应首次21文件428阶段现代分支；后续仅404兼容保护与共用回执reader另有最终测试资格，不冒称新bundle所有路径均真浏览器重跑。
- 第二个独立浏览器tavotto-recovery-peer零本机缓存，继承原base_axis；主窗口先编辑200，peer新actor携旧基线编辑409，peer本机稿保留且主AS正文不变，named A也不变。n21-two-window-qualification.json；不是直接写store或fake API成功。
- 独立review实际发现404旧后端fallback reload P1：读AS等待期间新编辑未debounce，最新副本Storage写失败仍applyProject会丢编辑。原探针exit1；只修documentStore两处保护，并加3个对照。最终21文件431通过/0skip、build/i18n/lint0，修后独立探针1pass（41是-t过滤未选中），两反证实际AssertionError/exit1。n21-p1-frontend-validation.json与n21-independent-review.md。原失败log/exit和旧源SHA保留；复跑配置曾覆盖单个landing记录，已在索引明确，不伪造复原。d7a73ce1已包含修复。
- N4窄预检已提交0b611411，原78前端/33Python/49向量、真实HTTP输入和旧128份完整spec对拍证据保留，统一build已过；规则41项通过。图例真实旧蓝/新曲线红问题尚未解决，不把这次提交当图例或广泛readiness完成。
- 包底层e468d516：204通过/0skip（预算22+容器128+当时图表包54），旧小ZIP8组及level0/1/9完整字节相等、3组真实故障反证；ZIP64以缩小阈值实际跑分支，不声称>2GiB物理吞吐。ARCHIVE_CHUNK_BYTES保持原64KiB唯一值；预算覆盖实际目录/压缩读取。legacy_names只由旧writer显式选择，公开reader与默认v4仍严格portable；NFC两种真实本机文件名可保，越界/重复/特殊仍拒。n3-packagebudget-freeze.md与n3-package-budget-staged.json。
- 旧包app已接336783bf：caller在data_dir持快照临时路径至输出结束，完整canonical资源plan及ZipFile原名规范化后推预算，最终ZIP经CRC再不可覆盖发布；同秒名加随机后缀，旧layout/manifest与响应键保留。新18HTTP/0skip及原8相关/0skip均实际通过，含真实worker快照、add/close/publish六故障、最终名未提前出现、并发/强制同名保护、Windows真实junction及改指、NFC双文件。两个API AST反证实际杀出覆盖旧包/读项目外；生产字节未改。n3-atomic-http-validation.md、n3-package-http-staged.json。
- 回执71e637c8：waitForWorkingDocumentSave复用原writer成功记录+实际AS回读，非空不多发PUT，空稿只有显式persistEmpty创建一次；不能把open true或普通空wrote:true当落盘证明。最新22文件448通过/0skip、正式build/i18n/lint0，10内存反证命中。根playground重建/check均0，指纹7f69cc026d69e1f4。n3-working-document-receipt-validation.json、n3-working-receipt-staged.json；包安装消费者仍未接。
- 计算包206a1c34：budget/budget_factory恰一，内部唯一序列化layout/manifest，factory收只读最终实际字节census；逐份验证INPUT不聚集全部内存，回调后仍沿原装载/strict inspect。末次82通过/0skip（原54+新增28），四组旧/新static/新factory完整ZIP与回执三方相等，两AST故障反证；n3-chart-budget-factory-freeze.md、n3-chart-budget-factory-staged.json。不同组重叠与时刻不同，不累加为全分支测试。
- 完整v4包尚无生产导出/安装API/UI；TopBar仍把v4送旧接口，不能称N3完成。独立迁移探针：整树搬移保相对chart/runtime/snapshot ID，但runconfig/workdir配置在新根丢失；快照仍绑定remap_fingerprint。严格包可含assets/same.txt与scripts/same.txt不同字节，ZIP名不撞而安装目标相撞；opaque快照行只核字节，需既有svgstore语义验证；缺registry的runtime只带脚本仍resolve=None。copy_project可skipped>0且成功，不能当完整安装器；projects/open create=true可复用既有目录，不是exclusive reserve。n3-package-install-identity-probe.json与n3-package-install-identity-review.md已交回，探针exit0、无生产修改；不冒称安装/渲染资格。
- 下一项是v4完整资源清单/导出API与同一顶栏入口，再冻结显式检视/安装/目标身份合同：优先研究完整新项目保相对ID，用现projectStore.adoptOpenedProject(status,{prepareDocument})/switchTransaction在ready前装入并走原writer真实回执；不得只改fileId、静默跳普通资源或忽略运行配置/重定向。随后N4图例/迁移、N5几何、N6完整M1、N7/M2、N8/M3保持原范围。M1/M2/M3都未通过，goal active。
- 根独占app/共享契约/规则/三文档/UI/提交；两个写入代理本轮源均已冻结释放，export_adapter_review只读补安装身份报告。当前服务仍是隔离资格用：后端71856/23984载入d7b6eef9时期app，尚未重启载入本轮旧ZIP改动；真实新ZIP资格来自新pytest进程。新冻结预览24652/23987/session46153，浏览器9260/77356；旧Vite10168/23983会随新稿变化。后续先核PID/创建时间/命令，不照数字杀进程。服务不等于无人值守调度器。

### 2026-11-06 21:03 最新统一构建与外改冲突复核

- 根接收已冻结20+2文件后，逐个对照源SHA，实际执行正式`pnpm build`、`python scripts/build_browser_playground.py`及`--check`，三条命令各exit0，前后22文件摘要无漂移。playground指纹`551ca2013f6b078c`；完整报告`unified-current-build-validation.json`和各原始日志。复制到独立`n21-recovery-ui-dist-2056`留作后续真恢复按钮联验，不用旧397项普通bundle冒充新恢复实现。
- 真浏览器额外验证named外改：先备份隔离A实际原字节，外部修改A；真实F2改当前标题为N21_external_conflict_C，AS仍关联A且dirty；CtrlS的实际POST返回409 external_change。独立读盘证明外部A摘要不变、另一B摘要不变，后端绑定仍保原named revision。`n21-ui-external-named-qualification.json`、`n21-ui-external-named-read-final.json`及截图`output/playwright/n21-ui-external-named-409.png`。这条仍用普通冻结bundle，最新恢复按钮验证尚未执行。
- 首次动作脚本已完成409/dirty断言，最后`header`观察因页面有两处header而严格匹配失败；后续只读观察曾将fetch Response.status误当方法，已保留两次失败日志。改用真实`data-topbar`与status属性，只读复核exit0；没有重复覆盖/重导数据来替换失败证据，也没有将脚本失败算产品故障。
- 当前HEAD仍d7b6eef9、索引空；22个功能文件冻结未提交，三份文档本轮更新。下一动作调整为：使用新冻结bundle做明确覆盖/重载真API联验；预检已具备统一构建资格，可独立提交。源不变则不重复刚通过的昂贵检查。N3–N8队列和M1/M2/M3未通过状态保持。

### 2026-11-06 20:56 执行效果复核与下一阶段收口

- 应用户要求核对当前执行情况。实际实施树仍是`E:/0 - Project/Tavotto-svg-r00`、唯一分支`zixu.wang/svg-computational-r00`、HEAD=`d7b6eef9`；原`E:/0 - Project/Tavotto`仍为clean main@69bcafae，未包含这些成果。索引为空；未新建/切换分支，未推送/合并/发布。本次不是最终完成，goal仍active。
- 相比20:02新增五个功能提交：`a06cc3d3`计算原图EPS公共接线；`a7008840`后端命名关联；`ffe2fb32`预算约束ZIP/原子发布；`c40805bf`计算来源包检查、准备和发布；`d7b6eef9`明确覆盖/重载的编辑轴恢复。旧的“app已stage EPS”“HTTP未接”“关联只有37项”的接续指令已过时，不能照旧报告回滚或重做。
- 独立解析实际XML：EPS新HTTP15通过；恢复HTTP74通过；容器121通过；计算包末次专项54通过，均0fail/0error/0skip。这些是不同范围/时刻，不相加为全分支测试数。关联普通旧HTTP另有204通过/19既有skip；模块恢复200通过及3组真实反证有原始记录。报告分别`eps-staged-qualification.json`、`project-file-backend-staged.json`、`project-file-recovery-staged.json`、`package-container-staged-current.json`、`chart-package-staged.json`。
- 普通保存/打开实际UI：working ID `d_d88wfjsq`先打开命名文件A=`N2_正式保存_20261106`；真实F2把标题改成B，另有同名B文件，AS回执仍绑定A且dirty。关闭旧浏览器/重启父后端后，全新浏览器启动前localStorage=[]，仍从后端恢复A绑定/dirty、标题B和精确来源。真实CtrlS只写A，落盘正文等于实际请求；另一B摘要未变，原chart.svg仍缺席。证据`n21-ui-binding-qualification.json`，原始`n21-ui-{open,retitle,cold-lossless,cold-save}*.json`及截图`output/playwright/n21-ui-cold-ctrlS-A.png`。此证据用397项阶段的冻结普通保存bundle，不能冒充最新恢复按钮已验。
- N2.1完整前端20文件正式冻结交回，根逐个SHA核对0漂移：21文件428项通过/0skip，pnpm build、i18n:check、lint exit0；28组实际内存反证全命中产品落点并AssertionError/exit1，wrapper0。`n21-complete-frontend-validation.json`，正式入口`index-BBA0tQCV.js`。包括新分支证明固定、迟到对话框/响应、reload等待期间编辑、恢复副本Storage失败；同一writer/store，只有404回旧wire。尚未commit；同版本真API覆盖/重载、多窗口及最终playground仍由根收口。
- N4窄预检修复两文件冻结交回：有效chartSource的缺素材豁免仅来自同完整输入的exactPanelRender；修改应用判断复用renderRequestIdentity。新19项先红后绿，合组78项前端、33项Python、49黄金向量只读校对通过；真实后端完整response输入两个范围，missing/unapplied误报消失。旧commit c40805bf的64场景×2范围完整spec JSON逐字一致；两AST故障反证实际失败。报告`n4-preflight-freeze.json`与`n4-preflight-and-legend-review.md`。该两文件未提交，正式build/playground尚待根统一检查；不把修预检等同修图例。
- 图例真实缺陷仍在：同一真实渲染的曲线已红，保留旧legend_handle仍蓝且manifest无legend。控制实验明确创建native Legend并交原legendmodel可正确跟随；根因是当前Binding只表达轴/系列，缺显式图例条目与区域所有权。后续先冻结可持久化关系合同，复用现legendmodel/placement；不按颜色或同名文字猜关系，不另写SVG重染。
- 项目包已有严格容器和计算附件叶子，含删原目录后的新worker精确INPUT重放；app仍直接ZipFile写最终路径，package/open仍只读检查。混合普通素材/脚本/快照的安装、显式API/UI及单次正文提交未接，不能把叶子通过当作项目搬迁完成。
- 最近顺序：前端保存关联/恢复与预检分别联验提交→旧ZIP原子输出及v4完整安装→图例/readiness/逐对象迁移→复杂几何→分享与完整M1→M2能力对等→M3冻结全量资格。完整SG00–17不缩减。截图仍可见坐标刻度边缘裁切，归几何/预览与实际产物对照追验，未定性为已修。
- 20:55实际自有进程：后端71856（20:43:13，source-ref-browser/server.py，23984/session33431）；Vite10168（18:51:24，native-edit-browser/vite.mjs，23983/session80916）；普通冻结预览8452（20:33:46，n21-preview.mjs，23986/session38666）；旧EPS冻结预览76372（19:48:20，eps-preview.mjs，23985/session82628）；浏览器tavotto-binding-cold daemon50704（20:43:24）。旧66888已停止。源/构建正在迭代，后续先核归属与实际bundle；这些服务不是整夜调度器。
- 两个代理现均冻结释放；根仍独占共享契约/规则/三文档、app/集成/UI/提交。新工作从当前22个未提交文件继续，保留用户改动，分N2.1的20文件与预检2文件两批，不git add .。本次有两次只读取证命令因路径错误/GBK打印符号失败，后续已用正确绝对路径与UTF-8读取；未把取证失败混作产品失败，也未补写未跑检查为通过。

### 2026-11-06 20:02 当前效果复核与下一阶段安排

- 应用户要求核对真实执行状态。唯一实施树/分支仍是`E:/0 - Project/Tavotto-svg-r00` / `zixu.wang/svg-computational-r00`，功能HEAD=`ec2cb144`；原目录`E:/0 - Project/Tavotto`的main仍clean且未合入新功能。未创建/切换分支，未fetch、推送、合并、发布或创建调度器。已有goal为active；本条是进度审计，不是整项完成。
- 新增已提交功能：264ce59c（N2前端持久化，46文件含规则/ADR）、c9fa29a4（完整矢量场景准备EPS）、8fa84f5d（共享嵌套viewport/clip）、ec2cb144（保存结构化错误）。此前19:40条目和原始报告保留；不把这些提交重新算作未完成。
- EPS能力刷新撤回勾选已按独立backend offer投影修复：routeKey仍严格包含格式，offer只复用同项目/来源/完整chart/ref事实的EPS how/reason；UI与请求构建消费同一投影。冻结8前端文件，429项通过/0skip，正式build exit0，真实旧反例先红后绿；128种来源事实×7格式的896次实际后端route调用证明offer独立于格式，不在前端复制授权规则。报告`eps-offer-freeze.{json,md}`。
- 本轮从外部冻结dist（19:45构建，`index-DVd4oAXS.js`）预览实际操作ExportDialog，EPS保持勾选，PDF/PNG/EPS/TIFF/SVG五格式一起提交。作业`27b3fd4aa3fb4750`实际status=done，六个输出（五图+报告）均done；实际请求带chart、精确chart_source和原序两条编辑，原`chart.svg`仍缺席。`eps-ui-export.js`曾等最终GET超时：完成经SSE送到UI后不再轮询，已保留这次失败，另读同一作业最终状态，没有第二次导出来覆盖证据。
- 独立产物核对`eps-ui-artifact-check.py`真实exit0：SVG v2的模型/绑定/原序edits/374B精确INPUT逐一相等；PDF一页100×100pt且`Source keep`文本保留；PNG/TIFF均833²，TIFF600dpi，红线位置/4.03pt宽度符合预期；EPS由独立Ghostscript实际转833²，线位置/宽度符合预期，EPS头/BB及无整图image包装另核。每个产物字节/摘要与真实作业回执匹配。证据`eps-ui-original-request.json`、`eps-ui-job-final.json`/`eps-ui-job-final-cli.log`、`eps-ui-five-formats-qualification.json`，截图`output/playwright/eps-ui-five-formats.png`。这里只授予单曲线计算原图资格，不能代替混合画布/双系列图例/M1或平台资格。
- 精确解析已有XML而非日志子串：`chart-eps-http-final15.xml`=15通过/0skip；`eps-existing-integrated.xml`=84通过/2skip；N2.2规则41通过。EPS公共接线/route/UI/app/15项测试尚未提交，下一步同步受影响规则/ADR、核SHA和stage范围后独立commit。保存错误的真实422已在ec2cb144和`n22-ui-source-error-result.json`记录，不再列为待实现。
- N2.1关联叶子冻结：layoutsession.py SHA256=`66bb90d4d93f1ad567b3194947c70dd099b4dda10653f6feef8246bf8352d464`，最新37项通过/0skip；集成旧组240项中230通过/10既有Windows skip，5个实际错误实现反证均触发预期失败。前端仍为中间稿，初轮5文件86项通过不代表完整新链；正式build/有状态新feature测试/真实冷开及多tab尚待验证。app尚无project-file HTTP路由，新增HTTP文件只完成Ruff/格式检查，未collect、未pytest，不把预计用例数写成通过。
- N2.1最近集成顺序：补原锁内按当前intent sequence取消的窄叶子；根独占app接GET/reserve/named commit/confirm/cancel及AS写前axis校验；保持旧缺省wire/args，确认实际named revision，元信息失败与已写正文分开；再跑真实HTTP和前端/冷开/外部改A409。外部AS漂移导致unknown时的显式恢复仍需兑现，不能静默洗白同步轴。详细合同见`project-file-binding-api.md`及NEXT_PHASE的N2.1节。
- 索引特别保护：当前仅app.py已stage，EPS候选blob=`e1a9c89c2bd2d759a04153cdc16ccc33cdfbb3f2`，物理SHA256=`bf98579d445fcb68d755944dd0b701db619219b9c4b04f8b4b6c4c2396ecfd1f`；其余binding/package/front中间稿未stage。接N2.1前必须保留这个EPS边界，不可git add .或重新stage混入未验关联代码。文档checkpoint可单独按路径提交，不改变该索引候选。
- 所有权：根独占app/共享契约/规则ADR/三份文档/UI/提交；svg_self_contained继续N2.1前端（不碰EPS）；export_adapter_review的关联叶子及新HTTP测试已冻结交回；svg_ir_fidelity的EPS offer已冻结交回，N3包任务暂停，尚未新增安装实现。下一阶段收口EPS与N2.1→旧ZIP原子输出/v4安装搬迁→N4图例/状态/迁移→N5复杂几何→N6分享与完整M1→N7/M2→N8/M3。
- 实时自有服务复核：后端66888（19:32:55，`source-ref-browser/server.py`，23984/session39115）；Vite10168（18:51:24，`native-edit-browser/vite.mjs`，23983/session80916，现为N2.1中间稿）；冻结预览76372（19:48:20，`eps-preview.mjs`，23985/session82628）；Playwright `tavotto-eps-ui` daemon24696（19:48:46）、`tavotto-v4-cold` daemon71840（18:57:08）。旧后端10460已结束。服务属于隔离联验，不是无人值守调度器；后续凭PID+创建时间+命令再确认，不能照旧数字杀进程。
- 仍可见的产品问题：冷开顶部“未存进项目”；有效附件仍被预检误报missing-asset/unapplied-override，本次五格式显式ack后成功，不能因此称预检已修；导出预览在本截图中刻度文字边缘被裁，须与原预览/实际产物对照后归入几何/预览追验。图例联动、包安装、复杂clip/member placement、剩余能力矩阵和最终旧功能/支持平台资格仍未完成，不给无依据的完成百分比。
- 三份稳定文档已同步本轮实际状态/依赖/退出条件，校验9个本地Markdown链接、UTF-8与diff-check通过；8个EPS offer文件SHA仍匹配，app索引/物理候选保持。报告`status-planning-2002.json`只证明此范围，不把文档检查称分支完整回归；提交时只包含这三份文档。

### 2026-11-06 19:40 N2收口、准确错误与EPS真实UI反例

- 已按冻结SHA定向提交N2：264ce59c，仅43前端文件及受影响ADR/规则/生成web速查表，共46文件。同步schema3/4边界、首次计算fork/事务取消、位置回执、跨格式单队列和活动画布版本合同；未混app/EPS/ZIP。首次规则检查因速查单元151B超150B真实失败，缩短头部索引后重新生成，41项通过（n2-contract-docs-final.xml），未调大预算。
- EPS叶子c9fa29a4（originaleps/epscontainer/独立GS测试/规则）与共享nested SVGIR 8fa84f5d分别提交；后者测试依赖已提交EPS叶子，提交可按顺序独立检出。源码分别对照原冻结SHA；沿用真实281项/两反证及旧IR字节资格，不重复冒称一次新全量。
- N2.2 ec2cb144仅7前端文件+受影响规则，保留有code的后端错误最小描述，经同一backendCodeMsg，不保存整个响应体或发遥测。8文件191项通过/0skip、正式build/i18n/lint exit0、5进程内反证均AssertionError/exit1；真实浏览器打开坏ref→新v4工作ID d_mp1t6u84→autosave422 chart_source_mismatch→界面显示实际chart source unavailable，SaveIssue=backend且本机恢复副本仍在，不再含检查磁盘空间。证据n22-errors-validation.json、n22-ui-source-error-result.json及output/playwright/n22-ui-source-error.png。未实现所有错误码中文化，不把fallback当新增翻译。
- 新app传真实has_chart事实，错误Produced保实际fmt；原EPS HTTP10项扩至15项，补原图/画布route、EPS-only/无SVG的完整标注/非均匀留白与worker失败格式，全部通过/0skip（chart-eps-http-final15.xml）。旧EPS/原图标注/留白/公共契约/ledger合组已执行，计数以eps-existing-integrated.xml为准；本次不将重叠组相加。新测试先Ruff，首次format-check失败修正后再跑语义门。
- EPS route第一稿已通过后端158项、前端247项及Dialog178项、正式build与448组旧响应/kwargs完整对拍；但新后端真实UI EPS复选框虽enabled，勾选后formats变→严格route键失配→fallback无script→checked撤回。eps-ui-first-attempt.json保留真实CLI exit1，不按接口已绿宣称交付。根因和结构候选记录eps-offer-third-path.md；由原代理实现backend offers最小投影按相同scope/pj/完整panel事实独立有效，来源计划仍含formats严格键，UI和请求builder共同消费，不按chart自授权。
- N2.1后端沿原layoutsession/锁/atomicio实现版本化工作副本关联、reserve/commit/cancel和前写prepare，前端唯一编辑轴接线并行中。axis=(editor_id,sequence)只作身份/分支判断，不跨editor取max；每个活跃实例独立editor_id，冷恢复旧axis只供无编辑/derived，首次真实编辑换本实例轴。原正文写前检查actual AS revision/base_axis，成功再记轴；旧回执不可盖另存B，ABA由binding sequence阻断。叶子37项/旧组合230通过+10既有skip为阶段结果，未冻结、无app接线，不能算冷开绑定已完成。
- 当前所有权：根唯一app/ADR/三文档/HTTP/UI/提交；export_adapter_review独占layoutsession与新binding专项；svg_self_contained独占N2.1前端保存/恢复/绑定消费者；svg_ir_fidelity优先修EPS offers/组件，N3只读候选暂停、无生产修改。N2.2已释放给N2.1，不能用旧冻结hash覆盖新工作。
- 后端10460已按确认的自有session18140 Ctrl-C停止（预期exit1），新66888/session39115/23984于19:32后加载新app，隔离原SVG仍缺席；Vite10168/23983、浏览器tavotto-v4-cold仍用原隔离环境。新浏览器目前停在坏ref失败测试稿，后续EPS前须用真实菜单重开合法命名文件，不能直接改store或重seed夹具。playground本次曾构建/check指纹82f672dc17a1e522，后续EPS offer/N2.1再改源码后须重新核，不沿用。goal active，未启动调度器/推送/合并/发布。

### 2026-11-06 19:08 当前效果复核与下一阶段收敛

- 实施树/分支不变，HEAD=95684f89，原main@69bcafae仍干净；未新建/切换分支、未fetch/推送/合并/发布。共享打开意图后端已独立提交。当前未提交内容分别为N2前端43文件、EPS叶子/owned action/app接线、SVGIR嵌套viewport修复和ZIP原子writer，仍须分功能暂存，不git add .。
- N2冻结报告n2-frontend-validation.json：31个生产文件+12个测试文件；阶段36文件810项通过，最后两文件改动对应7文件206项复验通过，正式build通过；i18n/oxlint及23个实际进程内反证有原始日志。两组重叠、源码时刻不同，不相加为1016，不称完整前端回归。43文件已逐个对照报告SHA；本次playground重建及--check均exit0，指纹0d91b54086f7e829，既有dynamic-import提示保留；功能尚未提交。接续先同步实际schema4/格式/回执合同到受影响规则/ADR，再定向提交，不能仅新总计划正确而基层规则仍只写2/3。
- 真浏览器通过实际菜单/表单/Inspector执行，测试不直接写store：打开合法旧稿升级v4工作副本d_kyyf7jn1→命名N2_正式保存_20261106→CtrlS→原SVG已缺席→关闭浏览器、重启后端→全新浏览器启动前localStorage为空→共享意图自动发现同v4 ID→exact恢复模型/绑定/附件及原序编辑。冷恢复后线宽3.02→4.03、undo→3.02、redo→4.03，source引用始终不变。证据n2-ui-{edit,save,cold,cold-edit}-result.json及output/playwright/n2-v4-{save-edited,cold-restored,cold-edited}.png。
- 冷开后真实ExportDialog另名n2_cold_chart输出PDF/PNG/SVG，HTTP/start及state成功，界面显示已导出4文件（含报告）。独立脚本n2-cold-export-check.py exit0：SVG v2模型/绑定、精确374B INPUT及原序edits一致；PDF单页100×100pt且文本Source keep保留；PNG833×833、预期红线点(416,583)=(255,0,0)，中心列红线rows567..598与物理线宽相符。资格n2-ui-cold-export-qualification.json。该单曲线场景不代替双系列/图例/混合画布M1。
- 发现实际冷开后projectFile=null，界面未存进项目。只读旧模块探针确认95684f89^的legacy绑定也只在Map/localStorage，清空后同样null；不是N2新回归，但ADR0108已接受的projectFile迁后端尚未兑现。下一优先批N2.1在原会话权威按(project,format,workingId)存规范目标、revision与用户编辑同步轴，保持ADR0096不塞可携正文；不能按标题/文件名猜绑定。另存为迟到回执、外部修改409、元信息失败须独立覆盖。
- 来源422被diskWriter丢弃后转io，App统一提示检查磁盘空间；旧路径也如此。下一批N2.2保留结构化后端错误，经现有SaveIssue/通知与backendErrorText展示，真正I/O才通用提示；直接命名保存已有正确错误翻译。以上审计不是已实现修复。
- EPS首次真实HTTP10项为6通过4失败，原因是完整标注/留白输出包含nested SVG viewport；未删wrapper或退化栅格。共享SVGIR补齐嵌套viewport/百分比/overflow/clip语义后，新45+旧相关合计281项通过、0skip；原HTTP10项在未改测试和同app SHA下全部通过，0skip、29.339秒。最终nested-viewport-freeze.json已冻结四文件；独立Chrome/PDFium/GS及旧IR五份字节对拍通过，writer/assets/retirement另105通过/41因缺Inkscape跳过，不能称全载体验证。两AST反证均1fail/exit1，原实现新进程2pass/exit0。界面当前仍禁用EPS，须接统一route事实与真实UI后才授予入口资格。初次失败日志保留。
- ZIP staged writer与旧atomicio共121项通过/0skip，实际添加失败即使被helper吞掉也不发布半包；尚未接旧app输出或v4安装/搬迁，不以叶子资格冒充产品功能。当前三文档改为：N2收尾提交→N2.1绑定/N2.2准确错误→N3包；可并行收尾EPS入口，再图例/readiness/迁移、成员/clip、分享/M1、M2、最终M3。完整范围不缩减。
- 本次核实自有进程：后端10460（18:56:58启动，source-ref-browser/server.py，23984/session18140）、Vite10168（18:51:24启动，native-edit-browser/vite.mjs，23983/session80916）、浏览器tavotto-v4-cold daemon71840（18:57:08）。旧25224和旧save浏览器已关闭；新服务复用隔离fixture不重seed。未设置通宵调度器，调试服务不代表无人值守执行器；goal仍active。
- 19:12独立身份复核status-review-1912.json：N2报告43文件、nested冻结报告7文件（含app/EPS依赖）、EPS wiring8文件逐个SHA零漂移；范围有重叠，不按58个唯一文件计。直接读取真实JUnit确认各项通过/skip，未把旧报告重称本轮重跑。原main工作区仍干净；三文档引用/规则索引41项通过/0skip、exit0（status-review-1912-docs.xml），agents_budget和定向diff检查exit0，既有OVER提示保留；文档检查不是功能资格。本次只提交状态/计划/提示词，功能草稿保持分批待提交。

### 2026-11-06 18:38后 共享打开顺序后端闭环与前端乱序修复

- layoutsession在原文件/锁/原子写中实现版本化共享意图、实际双namespace指针witness、显式open_intent、repair与混代读取重试。新增55项和旧namespace/session/recovery组合203项：193通过/10既有Windows条件skip、exit0、22.409秒；真实旧模块八步main/bak/响应字节对拍通过。四个生产AST反证均exit1：重复pending不分序、普通保存洗白stale、漏跨ns稳定检查、repair变新open。源码两文件SHA已逐个对照冻结报告一致。
- 根app仅显式query=1转发include_open_intent，故障才加unavailable元数据；默认旧四键/实际实参数量保持。repair严格布尔且仅True转发，旧错误文案保持。v4布局清单另回后端派生document_dir，legacy清单不变；读目录不创建它。真实HTTP新增15项通过/0skip；此前14项与旧session/recovery同次132项：122通过/10既有skip、exit0、21.653秒。首轮2个新fixture失败因误读ProjectStatus.path，已改用实际figures_dir，没有为此改产品响应。
- 两个HTTP真实函数进程内反证已确认落点：丢query适配使共同快照缺席，丢repair转发使迟到A重占意图，均AssertionError/pytest exit1，源码字节不变。证据session-open-intent-http-negatives.json。未知新账本read unavailable/写503且main/bak零改动亦由实际HTTP覆盖。旧顶层future允许用户显式删正文的合同仍保留，新账本future/非法不触发删除回调；审查曾发现过宽guard，修窄后旧组通过，原失败日志保留。
- 根独立审查又发现前端两namespace独立remoteTail会让旧显式打开A晚到抢B。反例先红后把打开/repair恢复为module级单队列，相关52项通过；保存队列/缓存/revision仍按格式隔离。自动安装须避免订阅重复分序，旧3含chart自动升级fork还需在成功settle后记新v4 ID一次；前端仍变动，阶段810项不作为最终资格。
- EPS公共薄契约和固定pack_eps action已冻结，102项含真实owned child、独立Ghostscript像素和旧probe/scaffold/lifecycle通过；32MiB裸payload加action封套超限时沿原runner cap拒绝，未扩大上限。ZIP staged_package与旧write_package共用收尾，121项通过，添加失败即使helper吞掉也阻止发布；二者均尚无完整app入口资格。
- 接续：先独立提交已验共享后端，再接EPS和旧ZIP原子输出；前端最终冻结后重启真实服务做正式保存冷开。当前未提交不同功能仍按所有权隔离，不能git add .混批。goal保持active，所有SG/M1–M3退出条件不缩减。

### 2026-11-06 18:17 执行效果复核与下一阶段安排

- 已逐项核对N1源码SHA与三组反证记录一致、索引只含六个N1文件，定向diff检查exit0后提交5abb9a7d。未混入前端、ZIP/atomicio或EPS草稿。原main目录仍干净；本轮不新建/切换分支或工作树，不推送/合并/发布。20项及旧312项通过/20skip是既有真实报告的复核，本轮没有重复跑后冒称新全量。
- 再读实际UI截图和产物资格：删原源后现有Inspector可改曲线、undo/redo，PDF文字和100×100pt尺寸保留、PNG833×833及预期红线像素一致、SVG模型/绑定/原序编辑一致。旧双系列截图同时确认图例样式未联动和“仅版面”统计缺口。证据不是当前未提交N2前端的正式保存冷开资格。
- N2阶段报告：已有新API/namespace/version专项及旧保存/恢复组通过，阶段build通过；最终源码仍在变动，不能累计或替代最终全量。缺thumb版本已补真实组件边界测试，但尚非真后端浏览器链。同ID跨格式的恢复备选入口也须按(format,id)区分，已纳入最小接线范围。
- 当前三条工作线：export_adapter_review独占layoutsession.py与新共享意图测试；svg_self_contained独占前端格式/回执/发现/版本消费者；根代理独占app.py、ADR/进度、跨层HTTP接线和提交。包107项/EPS36项叶子继续冻结，待根接入，不把叶子资格称作HTTP安装或EPS入口完成。
- 共享打开顺序合同已冻结：只有显式include_open_intent=1的GET增加open_intent；两格式的snapshot相同才采用latest。普通保存不分新sequence。自动补推显式repair:true，只能确认/晋升可信的当前同地址意图，不能让迟到A抢占B；superseded/unconfirmed保留待确认，未知未来账本仍拒写。旧格式默认请求/响应保持。完整wire及退出条件见NEXT_PHASE，尚未验证生产实现。
- 即时顺序：N1.1共享序与repair→N2真UI正式保存、删源、清缓存/重启冷开→N3包安装搬迁→N4图例/数据/readiness→N5成员/clip→N6分享/EPS/M1→N7能力矩阵/M2→N8冻结候选/M3。完成就绪批次后自动继续，沿用当前分支分功能commit。
- CIM确认后端33104（17:17:32启动、source-ref-browser/server.py、23984监听）仍加载N0；Vite11676不存在、23983无监听。浏览器daemon4468/78620在。端口查询对不存在23983返回非零，实际23984监听已读出，不是产品失败。没有常驻通宵调度器，goal仍active；调试服务不代表无人值守执行器。
- 本次当前状态/下一阶段三文档更新后，文档引用与规则索引41项通过/0skip、exit0（status-review-1817-docs.xml）；agents_budget --check和定向diff --check exit0。预算仍有既有OVER提示，不把文档检查作为功能资格。

### 2026-11-06 18:01后 v4后端正式保存与新父进程恢复

- 显式/api/documents/v4/{layouts,autosave,layout-session,versions}适配现view和namespace；不改认证/锁/冲突/原子写权威，默认旧2/3响应/路径保持。命名、自动保存、last/owner/candidates/summary、版本/草图/thumb/rebuild/清理都选同一格式，v4不借旧历史。正文捕获实际native完整状态及精确INPUT附件，在原锁内验证后单次提交；五字段位置回执不假定ID唯一。
- 真实生产v2外层INPUT正式保存→删原源/外层原件→新-I父进程只获项目路径及文档名→正式读取/编辑/同版PNG预览/生产SVG与PNG导出；逐字验证完整外层、模型/绑定/原序重复编辑、xlim2几何端点及独立像素。并测replace故障保旧正文/revision/引用、坏/缺附件严格失败不借原件、legacy/v4同docId隔离及7类新端点401认证守卫。最后整组20项通过/0skip、exit0（chart-documents-v4-final20.xml），先Ruff check/format通过。
- 较早18项60.178秒及首轮10项39.829秒保留；新增明确legacy session全响应不变断言后focused1项通过。旧命名/文档/恢复/版本组共332项：312通过/20既有平台skip、0失败、exit0、74.960秒（chart-documents-v4-legacy-integrated.xml）；不是全产品/全平台资格。
- 三个进程内反证：去namespace使旧last丢失；把v2只存inner使精确INPUT断言失败；legacy目录发现v4使旧客户端隔离断言失败，均真实AssertionError/pytest exit1，生产字节不改。外部harness首轮误认为JUnit必有type属性，改读实际message后正常收全；不把harness错误当产品修复。
- 独立复核捕获旧2/3合法未知chart扩展字段绕过FigureSnapshot规范化，已把skip限定v4并加schema2/3正式保存回归。定向复核4次真实保存与2452d8fc旧freeze AST正文完全一致，旧未核快照均删除，exit0。另实际验证legacy/v4不同thumb/删除/槽位裁剪互不影响。证据n1-independent-review-fixed、n1-storage-isolation-review。
- 尚缺：无thumb且原件删除时版本行磁盘fallback仍404，而同版正文+ref PNG200，前端D层将按需回建；不把完整模型/edits塞列表。跨namespace at没有共享总序，时钟回拨候选仍需最小共同打开意图结构，不能只选最大at静默恢复旧稿。N2前端仍中间稿，不声称UI正式保存冷开完成。
- 当前Vite已按11676真实命令核验后Ctrl-C停止；后端33104仍加载N0源码，需重启才有本批新路由。N2唯一前端写入者继续四层接线，根代理唯一app/ADR/进度写入。package原子ZIP叶子107项、EPS叶子36项另批冻结，未混本批、未冒充HTTP安装/EPS入口已完成。目标保持active，后续接共享发现序、前端联验及N3/N6胶水。

### 2026-11-06 精确附件消费者完成真实UI联验

- N0前后端消费者达到本批提交边界。冻结31文件SHA仍与source-ref-consumers-validation.json一致，沿用其700项/正式build/i18n/9反证资格；playground已重建并--check exit0，指纹77d32934d571f6a6（早于N2未接入新叶子）。Python先Ruff check/format exit0，随后当前附件专项13项同次通过、0skip、exit0、50.922秒（chart-source-ref-final13.xml）。app工作区v4草稿的三处格式已修，仍不混入本批索引app。
- 新隔离项目source-ref-browser：正式绑定后冻结不可变INPUT，删除原chart.svg，经真实“打开排版”载入带ref消费者夹具→进入既有图内编辑→Inspector改线宽→undo/redo回exact。读取store仅作断言，无直接改store；引用始终相同。面板比例1.98425时用户6pt对应既有两位精度native3.02，文档/实际SVG/撤销重做一致。
- 真实ExportDialog请求含chart_source、完整chart/native profile/原序edits；原图PDF/PNG/SVG和报告均落地。独立检查SVG v2模型/绑定与原序编辑完全相同，PDF 100×100pt且文本层保留Source keep，PNG833×833与预期红线点(416,583)=(255,0,0)。资格记录source-ref-browser/export-qualification.json。坏ref从UI触发422 chart_source_mismatch，最后display=empty、无旧native目标，不冒充exact；截图source-ref-deleted-edit.png/source-ref-bad-refused.png位于证据根output/playwright。
- 夹具是消费者测试输入，不是v4正式保存或冷恢复资格。首次外部脚本误写atomicio函数名、after_request注册时序均已修；坏ref探针先误期望missing（既有权威实际为mismatch），纠正后同身份重复加载命中失败缓存，等待新response超时，转为读取实际错误响应与空显示断言；保留失败记录，不冒充产品修复。
- 实际新缺口：预检仍将有效ref图报missing-asset/unapplied-override，UI导出按现acknowledged策略记录后成功；这属于N4/R12能力事实接线，不能称所有现有功能已对等。EPS生产叶子独立36项含Ghostscript像素已通过但未接app/owned worker；N2新格式/回执叶子66项已通过但未接现store。二者不混本批。
- 旧后端43356已核PID/命令后Ctrl-C结束；新后端33104/session79201/23984加载当前app，Vite11676/session67246/23983；source-ref浏览器78620。前端既有文件在本批提交前保持冻结，N0提交后释放给N2唯一写入者；根代理继续唯一app/ADR/进度写入。后续N1保存端点/新父进程与N2前端持久化并行，目标仍active。

### 2026-11-06 16:53后 执行状态、实际效果与下一阶段复核

- 复核HEAD=83f4ffbf626e0eab1b7bd04df07e8f9fe8be04cc；唯一实施分支/树不变，原main目录仍干净。本轮是状态与任务安排复核，未更改生产代码，未提交未完成功能、合并或发布。三份接续文档及总计划Git条款同步当前状态，不把15:32快照当作最新。
- 31个精确来源前端文件逐个SHA256与source-ref-consumers-validation.json一致（0漂移）；实际原始日志为18文件700项通过，0失败/skip，47.95秒，正式build/i18n/oxlint/diff均exit0，14条既有lint warning保留。9组反证含一次过窄筛选的更正记录；不把初次skip当反证成功。未重跑无变化的昂贵测试，当前新消费者尚无真实浏览器/API联验和playground重建。
- JUnit复核：附件专项11项51.064秒和无chart闸2项1.210秒分别通过；旧多页21项11.804秒通过。此前53项API集成146.613秒早于最后多页cache修复；运行边界46项127.550秒属于另一源码时刻，不能与700或历史983累加为当前全量资格。desktop既有PID回执失败原版本对照仍为1失败，未修、未冒充通过。
- app有两层改动：暂存区blob=29ec3510397348b884f41f235c390bb37bd69c88是精确来源消费者；工作区另有114行v4未完成接线。当前工作区AST无v4路由注册，autosave/versions还调用旧validator。test_chart_documents_v4.py仅9项收集成功，没有执行保存验收。本次只读Ruff check exit0、format --check exit1（三处待格式化），因此不声称当前dirty树已绿，不在format失败后追加产品pytest。
- 再看既有真实UI截图：7条编辑、线宽7、X最大20和双曲线确实可见；旧图例样式未联动、顶部“2张仅版面”仍存在。截图来自既有导出→新会话导入流程，不是正式项目保存冷重开。历史截图实际位于证据根output/playwright/native-v2-ui-restored-exact.png，非实施仓库下的同名相对目录。
- clip只读候选已有14案例/37参考像素和31覆盖层点及7捷径反证，距离/框选/宽描边合同未落地。EPS外部原型14组结构/8预期拒绝exit0，可包含完整装饰与中文，未改生产源码；缺独立PS读取器，所有EPS像素资格not_run，不能称EPS导出已支持。详细报告native-clip-geometry-design.md、native-eps-design.md。
- 当前CIM/端口复核：后端PID43356（本机15:51:24启动，23984）仍运行，加载早于新附件/v4代码；Vite23983无监听、旧71528不存在。native-edit浏览器daemon4468在，旧v2 daemon62844已不在。没有启动新调度器；这些调试进程不等于整夜执行。后续只按实时PID/创建时间/命令管理，不照抄历史数字。
- 下一顺序：N0分离并提交已验证附件消费者→N1后端v4保存/autosave/session/versions与真实冷父进程→N2前端格式/保存回执/冷发现→N3包安装搬迁→N4图例/迁移/readiness→N5成员层序和clip→N6分享/EPS/M1→N7剩余能力/M2→N8冻结候选/M3。完整退出条件见NEXT_PHASE；仍以轴模型、明确数据绑定和现有编辑能力为主线，不要求普通SVG反推Python。
- 复核证据status-review-20261106-1700.json内容记录实际16:53时间、31文件哈希、报告退出码、JUnit计数、app哈希与路由AST判据；文件名不是17:00运行时间声明。本轮docs引用/规则索引41项通过、0失败/skip、exit0（status-review-1653-docs.xml）；agents_budget --check及文档diff --check exit0，预算输出既有OVER提示不等于全部在建议范围。该资格只针对文档，不替代当前未完成app验收。

### 2026-11-06 精确INPUT附件进入全部计算与导出入口

- inspect/bind/compute/render/PNG与原图/标注/留白/画布/多页共用精确INPUT读取；显式chart_source只从权威项目附件读取并核对，不依赖原fileId存在，不借同名原文件补缺。snapshot保存input_svg和包内source_svg两份不同事实；原生v2必须实际核对外层存储状态后重算。显式附件字节进入render_revision及缓存身份；无ref的旧请求/实参数量/身份保留。
- 后端exportreq统一委托documents.validate_chart_source，深拷贝且按sourceId/sceneDigest绑定原图/各panel/各页，null/无chart/非panel/畸形引用拒绝；render/PNG也在进入legacy前拒绝无chart却携ref。parser/plan221项通过，10类无ref旧payload/完整plan/identity对旧模块逐字节一致，4组内存反证实际exit1。
- 根代理真实API/legacy/恢复/生产导出53项同次通过，0失败/skip、exit0、146.613秒；之后新增组合scope3项通过并暴露真实多页cache缺陷：不同INPUT的同名同模型页面会复用第一页，跳过损坏第二附件。改为消费单页ExportRequest完整render_plan_ref，兼顾profile/标注/留白/图幅，不再手列另一套身份字段。
- 缓存修后source专项11项同次通过/0skip；后加无chart入legacy闸2项通过，旧multipage21项通过，native合页补验通过。真实新覆盖原源删除/漂移、inspect/重绑/compute/SVG/PNG、完整生产v2附件、标注/留白/画布、同逻辑源两份INPUT与坏第二页拒绝。把读取器改成忽略ref的外部内存反证，使tampered测试在legacy worker入口真实失败，生产文件未改。
- 首次新探针忘发inline_svg导致2项KeyError，修请求后通过；多页修后首个复测误期望全失败作业HTTP200，现沿既有同步全失败HTTP500合同检查错误码及无产物，没有为测试改变旧协议。历史红报告保留，当前查chart-document-render-final11.xml、chart-source-unbound-final.xml、chart-source-legacy-multipage.xml、chart-source-no-fallback-negative.xml。前端source消费者最后资格仍在收敛，v4正文保存/冷开尚未接线。

### 2026-11-06 v4目录与会话命名空间叶子

- documentformat新增闭式legacy/v4格式、验证委托与named/autosave/versions目录派生；旧project_layout_dir和FigureSnapshots资产根不变，默认legacy路径不新增I/O。v4各层真实symlink/junction拒绝，包括仍指向项目内部的链接。
- layoutsession.using_namespace复用同一状态文件、锁、atomicio及全部既有规则，v4状态在document_namespaces.v4；默认旧状态/响应保持。未知所选namespace版本/损坏容器拒写；primary缺席但唯一backup含未来namespace时，legacy/v4均不能把备份覆盖掉。
- 新68项通过/0skip、exit0、2.252秒；旧session与backend恢复108通过/10既有Windows skip、exit0、21.545秒。真实e1bf1fb旧模块八步main+bak字节/响应/readonly与新默认完全一致；旧cleanup/set_last/commit/forget_project保留新v4指针、owner和revision。移除namespace隔离、未知版本闸和目录redirect闸的三项内存反证均确认落点且exit1。Ruff/check、format/check和diff/check通过。
- 首轮Windows symlink夹具因WinError1314出现20项setup失败，已用本机有权限的真实NTFS junction逐层验证，不skip凑绿；不把junction通过称为Windows symlink/POSIX资格。报告document-format-session-*、document-format-mutant-*在证据根。
- 本批只是格式/会话叶子；app命名、autosave、版本/缩略图、候选发现及包入口尚未接入，不能标保存完成。chart_source及前端消费者下一批继续。

### 2026-11-06 保存时仅替换当前安装画布

- R05真实输入审计发现既有保存序列化按activeCanvasId替换全部同id画布，两个原画布A/B会静默存成A/A；缺失/空id同样触发。现改为沿既有安装first-find权威，只替换匹配的第一个实际数组位置，不改ID/顺序或非active内容，不以新增ID准入限制拒绝已有文档。
- 真实store安装→活动编辑→buildProject及实际autosave JSON新增3组反例在旧实现均exit1，修复后documentStore完整44项通过/0skip，oxlint通过。固定同一时刻，原HEAD/新serializer在正常active A/B/C/absent四种情况JSON逐字相同；重复从旧A/A变为A/B。证据r05-build-project-equivalence.json。默认2/3语义和新格式入口尚未改变。
- chart_source后续wire/consumer草稿另批推进，前一两文件当前已验serializer差异单独stage，不混入v4格式接线。

### 2026-11-06 计算任务生命周期进入真实API

- 新chartprocess.run_request复用固定svgchart入口、pool解释器/launcher/overlay、DesktopProcess ownership和envlease；desktopproc仅提取可附着已启动进程的own_process，既有desktop start/gate时机不变。app两个helper已接入，旧ok=False业务码与native完整字段整理保留，非法probe输出仍映射chart_engine_unavailable。
- stdin独立线程，与stdout/退出/响应验证共用原90s请求deadline；输出spool位于data_dir/cache/chart-requests，预算从SVGBudget/JSON结构派生，stderr有界tail。先查真实退出码，满容量拒新chart，unknown保lease/admission/spool直到owner确认；只限制chart类任务，不宣称resident/native/one_shot总cap。请求timeout不包含既有解释器解析/激活预算或取消清理时间。
- 冻结runner34项通过、0skip、exit0、8.918秒，含真实native响应、正反环境租约、动态admission、stdout超限/有界stderr、阻塞stdin、Windows Job后代管道、unknown、stdlib父导入；删退出码/删lease两项内存反证各真实exit1，生产字节不变。chart-process-review.md与freeze.json含完整hash/命令。
- 根代理接线后真实API/恢复/legacy端点/生产导出共46项同次通过，0失败/skip、exit0、127.550秒（chart-process-http-integrated.xml）。新增HTTP反例持安装租约时bind/compute/render/PNG四入口均503 environment_mutating且active=0；释放后真实渲染恢复，原SVG不变。当前生产导出11项本次同次全过，先前9+2分次记录保留，不倒改历史。
- 受影响desktop兼容20通过/1失败/1平台skip；唯一失败是既有Windows .venv redirector下bridge回执PID不同于Popen PID。用git HEAD原start AST同项复现失败，保留chart-desktop-baseline.xml，不把该失败写通过或冒充本批新增回归。其他平台组回收本批未真机验，原owner入Job短窗口/DirectScope边界仍在，不称已解决。
- 本批Python最终Ruff check/format与diff通过；初次新增API测试import需折行，修正仅格式。R05目录/session及前端重复canvas保存修复由其他代理独立进行，不混入此功能stage。正式项目保存/冷开与M1仍未完成，下一步接附件精确输入与v4消费者。

### 2026-11-06 15:32 执行效果复核与下一阶段重排

- 本轮沿当前唯一实施分支，没有新建/切换分支或工作树；原main目录仍干净。分别提交242cabc0共享编辑、c2fd47d5完整状态导出、53393810恢复与重绑归属、8e6c399a多DOM目标、2a40fec9栅格验证，没有push/合并/发布。保留全部历史记录及尚未完成的SG/R范围。
- 实际效果截图output/playwright/native-v2-ui-restored-exact.png：新文档7项修改、线宽7、X最大20，进入既有图内编辑器；真实UI确认/撤销/重做均同一完整列表。仍可见旧图例线宽未联动与项目“2张仅版面”统计未识别计算来源，不能以截图宣称全部现有功能已对等。
- 当前34个相关前端文件983项通过，0失败/skip；正式build、i18n及playground --check exit0，指纹931a34814cf2316a。生产API21项、恢复/重绑focused4项、profile/归属46项、多目标47个唯一用例、生产导出9+2分次运行分别保留；对应不同源码范围且互有重叠，不累计最终总数。没有完成最终全产品/所有平台资格。
- 下一顺序：现有chartprocess草稿的环境lease/任务admission/同deadline输入输出/受限预算/退出码/组回收→v4隔离落点与session命名空间→精确INPUT附件保存及冷恢复→图例/重绑迁移与复杂clip/member slots→分享范围/EPS与M1→剩余M2/最终M3。下一阶段细项与可无人值守续接条件同步NEXT_PHASE和NEXT_PHASE_PROMPT。
- 存储只读审计已完成但未接线：v4不能只改schema或全局project_layout_dir，须独立命名/autosave/versions/package/session入口；真实旧layoutsession清理实验表明全局owner会被删，嵌套document_namespaces.v4可由旧写入保留。仅helper不算保存交付。
- 当前自有后端PID45648/23984、Vite71528/23983；浏览器tavotto-native-edit/PID4468和tavotto-v2-restore/PID62844供证据复核。后端脚本已改为复用已有portable/project。根代理没有启动整夜调度器；活跃goal/代理仍在运行，调试服务不是无人值守任务。续接先核PID/命令和HEAD，不按模式杀进程或重seed覆盖产物。
- 本轮文档门禁：tests/test_docs_references.py与test_agents_rules_index.py合计41项通过，exit0（phase-review-1532-docs.xml）；agents_budget --check实际exit0，输出包含既有预算提示，不声称全部文件在建议体积内。三份稳定文档保留R00–R13/SG完整范围及无人值守合同。

### 2026-11-06 生产PNG/TIFF产物验证

- 在c2fd47d5的9项生产SVG/PDF导出专项之外，只新增2项真实PNG/TIFF验证，生产代码未改：原图白底144PPI为200×200，透明混合画布240×250；独立读取PNG pHYs、TIFF inch X/YResolution及物理毫米，解码整幅RGBA样本逐字节一致，TIFF straight alpha=2。
- 检查红线像素、扩展xlim后的空白点、原图白底与画布外alpha=0；混合画布中文文字还经实际PDF文本层、NotoSansSC字体和区域墨迹交叉核验，源文件字节不变。2 passed、0skip、9 deselected、exit0、6.004秒，证据chart-native-export-raster-final.xml/.log；首次探针选点击中刻度文字后改取真实空白角落，失败日志保留，不算产品修复。
- 当前导出测试文件共11项；9项原生产测试和2项新增栅格测试分次执行，不声称11项同次最终全跑，也不据此计入EPS、所有复杂SVG或全产品资格。文件SHA256为35de55dd5cd8b29d723e6daca708fdb235100ba980b69378fe2f62b0545048cb。

### 2026-11-06 多DOM对象共同目标与临时预览

- 以最终native manifest的svg_targets为唯一实际成员归属，SVG根只带同源镜像；前端唯一reader校验真实节点、绘制顺序、去重、祖先重叠与native pt基底。跨父系列、split axes、errorbar多成员和colorbar proxy使用实际目标；未绑定装饰保留原位。变换/缩放/样式、排队、局部重挂及取消复用同一映射，语义编辑列表仍按gid，原输入字节及绑定摘要不变。
- 暂缓拖动只按每个实际目标的BCR收意图，避免并集框吞入装饰；最终提交继续等待exact。多目标style不扩既有setter能力；无映射的legacy仍原路径，非法显式映射拒绝临时预览。修复实时Map删除/重插循环，以快照迭代；变异验证后源码逐字恢复。
- 后端冻结核心47个唯一用例通过（27 core+20 targets，分两次运行，0skip），包含真实Chromium位置/像素、色条隐藏恢复、反序层序和错误成员映射反证。旧五族scaffold/compose及三个writer样例对ea0a7027逐字节一致。较早103项相关组早于最后层序修复，不称最终树整体验收。前端9文件192项、9组反证通过；根代理最终34文件983项与正式build/i18n/playground共同覆盖当前前端。
- 未关闭：errorbar compound slot夹未绑定装饰的成员偏序仍欠定；显式zorder与原source slots冲突仍需成员级placement；任意/union/evenodd祖先clip还需Geometry契约。真实circle clip反例证明仅放开资格会误命中圆外，不能把composed强改true。证据native-targets-review.md、native-targets-final.xml、native-multi-target-preview-validation.json。

### 2026-11-06 v2完整编辑恢复进入真实界面

- 生产导出已在c2fd47d5提交。inspect按包内profile与完整编辑实际重放，v2额外返回restored_render；旧v1和普通SVG形状不变。向导保留当前面板完整编辑，预览不写文档，确认经现有一次事务应用chart与原序overrides；取消、迟到预览、切项目/文档加载和预览期间面板变化均不能覆盖新输入。
- 携编辑重新绑定增加后端科学所有权检查：真实builder交换两系列会复用相同s1/native gid，原setter仍无警告接纳，不能据此迁移旧编辑。数据/范围/列名变化保留归属；重排/替换/成员拓扑变化返回chart_binding_stale，旧稿不清。逐对象结构迁移仍待实现，不把此guard称为完整迁移。
- 当前profile/归属46项通过；native API集成21项通过，新增明确restored_render断言后focused恢复/重绑4项再次通过；各组重叠不相加。归属guard替成no-op的进程内反证使重排用例exit1，源码未改。前端最终6文件106项、5组反证通过；根代理冻结当前34个相关文件统一运行983项，0失败/skip，正式tsc-b/Vite/Tailwind和i18n检查均exit0。playground重建指纹931a34814cf2316a，随后--check通过。它们包含下一批多目标草稿，不冒充本次提交独立全量候选。
- 真实ExportDialog导出native-v2-ui-20261106.svg，实际包携native profile及7条编辑；产物与导出时文档列表相同。原始普通SVG缺席后，在独立浏览器会话新建空白排版、添加该实际产物、预览、确认、撤销、重做，最终7条编辑原序一致；history 1→2→1→2。进入图内编辑后data-display=exact，实际stroke-width=7px，X刻度到20.0。证据native-v2-ui-artifact.json、native-v2-ui-confirm.js与output/playwright/native-v2-ui-restored-exact.png；CLI实际exit0。初次探针误读doc.id而非documentId，在确认前失败，修正后通过；不是产品失败。
- 该UI验证使用新浏览器会话和新文档，后端沿用当前自有服务；冷父进程删源已由核心/生产API专项覆盖，不能把两类证据拼称“实际UI冷父进程/正式项目保存”全链。截图仍显示仅版面统计与未联动的旧图例，正式保存、复杂clip和完整旧功能资格未完成，M1未通过。

### 2026-11-06 生产导出冻结完整原生编辑状态

- 242cabc0已提交共享native消费者。此批原图、原图标注/留白、画布及多页统一消费chart_render_profile+完整原序overrides，作业内cache身份包含精确JSON的顺序/重复/identity；原图native SVG实际写v2完整编辑包，legacy缺省仍v1。前端builder仅chart分支携native profile且深拷贝原序edits；无chart旧来源完全保原行为。
- 后端请求合同88项通过、0skip，前端构造39项通过，正式三步build exit0。六种旧请求的公开to_payload与render_plan_ref，对immutable d708ed66逐字节相同。恢复or[]/dict静默过滤的进程内变异触发27个负例失败；把身份换canonical排序/去重则original/canvas/merge三项全部失败，生产字节还原。
- 真实生产端点9项通过、0skip、exit0，30.496秒（chart-native-export-first.xml/.log）。包含真实原图SVGv2→删原文件→inspect/重算、SVG/PDF独立像素/文字/尺寸、原图标注/留白/画布同状态、同作业同model不同edits A/B/A不串、merge页序、raw order与v1缺省兼容、非法edits无产物。起止8份源码SHA相同。测试没有手工embed冒充生产输出。
- 该9项只证明其覆盖的SVG/PDF，不计PNG/TIFF/EPS资格；新增PNG/TIFF生产实测另在推进，后续测试草稿不混入本批已验证9项。后续v2恢复UI/科学对象重绑guard和多目标preview仍独立草稿，最终playground需统一重建，M1未完成。

### 2026-11-06 共享native消费者进入真实图内编辑

- 本批共享engine/render与preview_png、renderStore/scheduler/transport、PanelView/编辑入口及各缩略图接线已收敛；按功能部分stage，后续export/v2恢复/ownership/多目标草稿均保留在工作树，没有用整树add混交。API类型额外可选profile/identity字段是后续导出协议的前向形状，无chart运行行为不变。
- 集成树真实API当前21项通过、0skip、exit0，62.317秒（chart-native-api-integrated21.xml）：含原18项及后续3项重绑所有权新增检查。此运行包含工作树后续导出/恢复代码，不冒充本次API-only索引候选的独立全量验证。既有前端消费者639项、终态失败143项专项和正式构建记录保留；最终候选仍须统一验证。
- 根代理真实浏览器从portable.svg右键绑定→对照预览→确认→“改图里的内容”进入现有编辑器，挂载native轴与两条line并达到data-display=exact。真实图形点选后用原Inspector将线宽2改6，文档增加一条编辑；CtrlZ回2且该patch移除，CtrlShiftZ回6且完整列表相同，history长度3→2→3，三个状态均exact。证据native-ui-select-line.js、native-ui-undo-redo.js与output/playwright/native-ui-line-edit-redo.png；实际退出0。
- 实际Inspector将X范围最大从10改20后，最终刻度含20，线末端x分数由.97变.55661（原数据最大10），linewidth6保留。该证据未覆盖图例联动、保存/冷重开或所有交互；原“仅版面”项目统计文案仍需单一readiness/计算来源语义接线，不硬编码伪造来源。
- 浏览器只读store探针须导入performance中应用真实加载的带t版本模块，不能另import无query模块创造第二store；记录过的CSS点号selector和exactPanelRender调用签名探针错误不是产品失败。此场景开始线宽操作前已有ticks_bottom=false作为基线，不把该patch的出现归因于被测线宽操作。
- 正式导出后续9项已由独立代理实际通过，另批提交；多目标场景和预览reader仍有独立草稿。M1及全量旧功能/平台资格仍未完成。

### 2026-11-06 14:05 执行效果复核与下一阶段安排

- 在既有分支完成3236307c：物理pt原生场景、实际最终manifest、原场景派生DOM的id/CSS隔离、明确placement与资格限制；即时预览平移/缩放锚点/排队位移/线宽共享目标坐标reader。源派生副本移除伪造标记，原输入字节不改；legacy默认路径不改。详细合同见ADR0166与native-producer-review.md。
- 生产者此前231项为190通过/41缺Inkscape跳过，exit0；新增marker/scrub后完整native专项27项通过、0跳过，exit0（native-preview-marker.xml）。旧五类默认scaffold/compose和writer隔离有逐字节兼容证据。新增metadata未改legacy分支；不同树版本不混称一次最终回归。
- 真实native API 16项主流程通过、0跳过，exit0；随后line/axes隐藏恢复2项通过、0跳过，exit0。覆盖双模型及原序overrides身份、PNG像素/manifest/版本配对、非法编辑拒绝、v1实际重放、v2删源恢复和计算中改源409。16项运行早于隐藏与marker补修；测试手工调用真实bundle库构造v2，不代表生产导出UI已携完整edits。
- 前端共享消费者原18文件639项通过；终态错误撤销exact资格补验4文件143项通过；本次坐标/手势8文件151项通过、0skip，正式build与oxlint exit0。三组互有重叠，且对应不同源码时刻，不求和。错误仍保留可看旧图和已请求身份，避免重试循环；不把旧manifest当当前写操作权威。
- 根代理真实Chrome探针在4种PAR、非零viewBox[50,20,100,100]、200×100pt上运行当前生产者和前端模块：dfx=.1实际位移约26.6667CSSpx、线宽4px、缩放锚点matrix(...,-75,-30)，取消全部还原。源问题的二倍偏差已消失。证据native-preview-fixed-browser-record.json及output/playwright/native-preview-coordinates-fixed.png；这是独立DOM探针，不是实际Inspector/指针/保存全链。CLI前两次启动方式失败及Python import修正如实保存在记录中。
- Ruff/格式检查通过；最新playground已重建并check通过，指纹579b135bc6c24c11。原共享旧功能组合仍按历史真实结果记188通过/2skip/1失败；其唯一编码失败经fb1ae4f7目标1项及16vectors修复通过，不改写为191项全绿。
- 本轮三份稳定文档同步后，文档链接/规则索引41项通过、0skip、exit0（status-next-phase-docs.xml）；agents_budget检查exit0，仍报告既有体积超预算提示。此处不是完整产品测试结果。
- 14:05时无代理或测试仍在运行。自有调试服务PID67684（13:49:12，23984）、Vite71528（13:49:41，23983）、浏览器CLI4468（13:49:55，session tavotto-native-edit）仍存活；后端早于本次marker，完整UI联验前须只重启该自有后端。server.py当前重启会重新seed，先改为复用现有portable/project以保留证据。它们不是无人值守调度器。浏览器现有404是新文档未有autosave槽位，不据此宣称console零错误。
- 下一步优先顺序：①冻结现有共享API/前端消费者并真UI点选/Inspector/撤销验证；②生产导出携profile+全overrides及v2恢复UI，验证编辑不丢；③统一多成员/placement目标并补图例/复杂clip；④正式schema4保存、版本和移盘冷恢复；⑤分享/EPS及M1；⑥剩余SG能力与冻结候选全回归。详见NEXT_PHASE第4节，不重做已提交profile或坐标核心。

### 2026-11-06 实施：向量生成脚本Windows编码合同

- 当前共享native接线树的旧compute/恢复/导出和Windows回归组合191项，188通过、2既有skip、1失败，pytest exit1（native-shared-legacy-regression.xml）。唯一失败准确落在本扩展新增gen_chart_model_vectors.py没有固定stdout/stderr UTF8；不是产品绘图/导出失败，也没有将整组误记通过。
- 生成脚本补与既有build_frontend入口一致的两流reconfigure；Ruff/格式通过，目标Windows门禁1项复跑通过（chart-vector-encoding.xml），16个模型golden vectors实际校对通过，退出码均0。未变更向量、模型判据或原有Windows门禁；无新产品行为，不重复无关的280秒组合。
- 脚本兼容修复独立提交；native渲染/前端/可见性仍未提交，等待最终同版验证与真实界面追验。

### 2026-11-06 实施：原生profile与可重放v2包合同

- chartprofile集中定义legacy-scaffold/1与native-manifest/1，验证有界原序完整edits；v1默认embed/extract的原有2240字节黄金产物和身份保持不变。native显式写v2，额外闭式profile/overrides；预算仍是原model/binding/profile/edits合计2MiB，未扩大外层SVG/源预算。
- 新身份包含base_revision、profile、原序edits及精确JSON bytes摘要。独立实证同canonical patch_hash的顺序可画出不同位置，ChartModel数值编码也会合并2**53与+1及1/1.0，但text setter会显示不同字串；本批仅新身份补精确JSON，不改旧i64、patch_hash或ChartModel合同。
- 最终profile/包与附件reader141 passed/0 skipped，pytest exit0（chart-profile-bundle-final.xml/log）；父进程隔离import未加载科学/native栈。6个关键守卫反证在目标断言真实失败exit1，包括顺序、v1字节、总预算、字段闭集、精确JSON及实际replay。Ruff/format/diff通过，四个文件已冻结接管。
- 这是协议/包核心；应用renderer分派、缓存/导出、C恢复完整edits仍由后续消费者批次接线。不能把库级新进程重放当作现有全部产品入口已经完成。

### 2026-11-06 实施：字体族名保持字面语义

- 修正overrides中唯一的族名→mathtext模式转换：用Matplotlib FontProperties字面family与官方模式生成器，italic/bold分别构造，拒绝把名称里的file=解释成读取路径。文字、刻度和首选/回退选择继续复用同一setter；不改原有文件锁定字体/来源授权合同。
- 新旧10个真实标题/对数刻度、五族名SVG输出逐字节相同（font-family-parity.json）。新权限负例以数学字体get_font真实调用追踪，回退到raw模式后目标canary确实被读取，新增目标断言失败exit1（font-family-negative.log）；当前正常实现不读取该文件，热冷重放/undo相同。
- 最终字体相关9项与字体来源/中文名/科学文字19项均exit0、0skip，报告font-family-literal-second.xml、font-family-compat.xml；Ruff和格式通过。首次新测试把攻击串中转义反斜线也当作库必须逐字保留，已改为普通族名往返与实际文件选择权限两类正确主语；另字体名单初轮一项失败，独立旧/新相同新建MPL缓存均exit0，后续完整相关9项通过，未伪造原失败为绿。
- playground已按本轮overrides和冻结前端重建，指纹7d4cbb526cbd3f82；原生后端/前端整链及最终完整旧功能资格仍待后续批次，不混入本字体功能提交。

### 2026-11-06 13:12 执行效果复核与接续安排

- 工作树/分支仍为r00/zixu.wang/svg-computational-r00，完成批次HEAD=bbbffed1；原目录main@69bcafae实测clean，未合并，未fetch，不用缓存远端引用宣称最新。三项完成核心已独立commit；下一轮核对实际HEAD/diff，不恢复旧快照。
- 根代理直接读JUnit与冻结文件hash，复跑manifest25项、附件/原文档144通过+4既有Windows skip，各自exit0；9个完成文件Ruff与format均exit0。连续文字157项报告、共享导出288通过/1skip为各自完成树证据，不相加或冒充当前全量。根代理本轮测试均退出，未新起浏览器/服务。
- 重新查看真实恢复截图，两曲线可见，但“仅版面”提示和蓝线/旧图例标记不一致仍明确可见；最终产品缺口不是再修绑定入口，而是现有编辑/能力体系接线。NEXT_PHASE与接续提示词已按此重排，M1仍未通过。
- 当前所有权：svg_ir_fidelity接原生物理viewport Figure/最终组合/manifest；svg_self_contained接renderStore、scheduler、PanelView、API/transport、PNG和三种缩略图消费者；export_adapter_review接profile/v2包与原序override验证。根代理负责app共享render/PNG、导出/恢复消费、共同字体名语义及后续正式保存集成。代理不commit，根代理统一检查提交，不碰对方未完成文件。
- 独立结构实验已证：native物理pt Figure+既有viewBox逆映射在4种PAR容器Chrome全图最大通道差0，pos_frac/pt样式无需另一套输入翻译；仅canonical patch hash会漏同档原序差异；旧客户端会把仅升schema4的文档降为空3并覆写。新增未信任override还暴露字体族名被当fontconfig file模式读取路径，须修字面族名的共同转换权威并验证，不能由包摘要授予该能力。对应证据manifest-compose-probe、chart-profile-readonly-probe.json、chart-profile-fontpath-probe.json及旧客户端probe保留。
- 下一动作按NEXT_PHASE第4节1–3行并行：完成版本化原生生产者与真实render/PNG消费者后，真UI点选改轴/系列、联动图例，再接正式保存/迁移/分享，完成M1。新前端候选的mock回归和构建只作为本地消费者证据，不声称后端整链已经成立。
- 三份文档更新后，test_docs_references与test_agents_rules_index共41项通过，pytest exit0（docs-status-1312.xml）；agents_budget --check exit0，已有体积建议超额仍打印，未修改规则文件。文档diff检查通过；本次状态/队列更新独立提交，后端原生renderer与前端草稿不混入。

### 2026-11-06 实施：不可变项目来源附件与显式schema合同

- documentassets冻结整份精确INPUT SVG，闭集chartSource引用将源ID、输入hash/size与原场景digest分别校验；固定摘要路径、有界读取、目录跳转/文件替换/损坏拒绝。保存消费必须读取input_svg继续包的真实重放，source_svg只作原场景事实。
- atomicio新增write_bytes_if_absent，复用原有临时文件/fsync与清理；Windows用不覆盖rename，POSIX用link或系统exclusive rename，不存在exists→replace降级。已有内容损坏拒绝，不能默默修复覆盖。schema4仅显式validate_chart_document入口；SCHEMA_CURRENT仍3，旧2/3入口逐字保持原合同。
- 根代理在当前树跑来源附件、atomicio与原文档回归：144 passed、4个既有Windows skip，pytest exit0，报告chart-document-assets-root.xml。代理此前80项新/受影响用例全部通过；5项核心守卫反证均在指定目标失败。Ruff/format通过，ADR同步。
- 本机C/E为NTFS，跨进程争用与junction拒绝实测；mac/Linux原生分支只有ABI单元验证，exFAT/网络盘未实测。尚未接线命名保存/自动保存/版本捕获、包安装、前端镜像和schema4独立发现目录，不能据此宣布移盘或旧客户端防损已完成。

### 2026-11-06 实施：复用原生 manifest 的脚手架适配器

- 新增chartmanifest.render_scaffold：明确的语义角色→实际Artist/Container所有权，复用FigState、instrument、完整overrides和最终SVG draw时capture_preview_manifest；分开返回原生gid、真实SVG成员id、语义角色及坐标映射。不从浏览器DOM反推几何，不建立第二份属性应用规则。
- 轴仅拥有其家具，系列/容器/色条/图例均按显式所有者登记；未知patch目标稳定拒绝，setter/identity失败保留warnings/unrestored供消费者阻断。没有“只有第一条线”的近似所有权。
- 根代理在当前树重新执行tests/test_chart_manifest.py：25 passed/0 skipped，pytest exit0；报告chart-manifest-root.xml。文件SHA与代理冻结版本一致；其最终draw反证在提前Agg几何的目标断言失败。Ruff/format通过。
- 这是worker adapter核心，不是已集成的最终场景manifest：coordinates.composed=False，原场景祖先clip/opacity、最终paint order与坐标还须在组合器接线。前端renderStore接线正在独立实施；原生ID与既有自包含v1的重放兼容须版本化解决，不可直接换序列化后冒称旧包可恢复。

### 2026-11-06 实施：连续文字 chunk 与独立几何验证

- SVG→IR 的连续 tspan 改为整段 chunk 定位与锚定，复用既有 shaping/字体权威；处理绝对位置边界、相对 dx/dy、空 tspan、XML 空白、跨 paint kerning 及逐字旋转。未新增 IR 或写出器，未扩大为任意 SVG 文本支持。
- 两个文件最终定向回归157 passed/0 skipped，exit0；其中新增35项，22项Chromium独立参考，9项同时用真实PDFium核对字符基线。各子集存在重叠，不累加。报告 svgir-text-final.xml/log 与 svgir-text-chunks-review.md；旧3ed06553转换器运行新增手算回归在目标坐标断言失败，exit1。
- 根代理复核文件SHA与代理完成版本一致，重新执行9个完成批次文件的Ruff和format均exit0。连续tspan旧最大基线误差27.79用户单位降至0.021，独立门槛0.06未放宽。ADR0166同步；双向/竖排/textPath及全部baseline资格仍待对应能力验收。
- 本批只提交svgir、其测试和受影响文档；manifest配对adapter、项目来源资源核心及前端renderStore接线另按功能收敛。M1仍未通过，目标保持active。

### 2026-11-06 实施：自包含恢复API与真实确认事务

- inspect 对普通SVG保持零worker执行且无新增字段；自包含包经真实声明式重放核对后返回原source的scene与可选restored_chart，bind也用该原场景。外部改图并重签hash仍须重放一致，否则chart_source_mismatch；inspect期间换源返回冲突。
- 恢复UI只填草稿并说明来源，本地已有chart优先；未编辑表单时完整复用原model/binding预览，避免丢未显示的标签/数据/网格；编辑后走显式bind。类别请求省略数值x，TSV预填保留特殊字符；取消零写入，成功预览后明确确认才一次文档事务。
- 前端5文件138通过，正式tsc→vite→tailwind、i18n、oxlint、playground重建/check均exit0。原始stdout当时由工具返回，c-restore-ui-validation.json是包含session/chunk与9文件SHA的执行记录、不是虚构原始日志；根代理提交前逐文件复核SHA完全相同。API恢复4项连provenance41通过，也包含在本轮后端288通过/1环境skip内，不重复累加。
- 真Chromium从正式API导出的portable.svg开始，原original.svg已删除：菜单打开恢复数据/两轴区域/两系列及样式→真实预览成功，文档chart仍null/past=1；确认后chart两系列/past=2；Ctrl+Z变无chart/past=1，Ctrl+Shift+Z恢复两系列/past=2。重新编辑全引号CSV真实bind/compute得到新列，取消后旧数据/past=2不变。
- 真类别UI继续验证含逗号、tab与换行的三类别，真实bind/compute成功，model.axes.categories与dataset.x精确保留[A,first、B\tsecond、C\nthird]；不是mock或手工给请求补字段。portable.svg提交前仍与正式导出物逐字节一致，原源仍不存在。证据c-restore-browser/*.txt、output/playwright/chart-self-contained-restore.png、chart-self-contained-restored-panel.png、chart-self-contained-category-preview.png。
- 早期测试端口命中另一个旧实例，读取project根确认后改用本轮隔离23973/23974；旧实例未清理，未将其状态当成本轮证据。本轮专用浏览器tavotto-c-restore已关闭，后端session88800/PID19680与Vite52656/PID66628已Ctrl+C终止。另观察到一条ResizeObserver未投递通知，用户流程正常；未声明控制台零错误，完整E2E时仍需定性。
- 下一批已并行启动：原生manifest配对adapter（新chartmanifest与专项测试）、连续tspan整段布局、不可变项目来源资源与schema4显式验证入口。旧版真实反证证明仅升schema会被旧normalizeLayout降为空画布再覆盖，必须以旧程序不可发现/不可写的新落点配合原子文档提交；隔离候选与save_as复制已实测，尚未产品接线。仍按下一阶段队列推进，不宣称M1/最终功能完成。


### 2026-11-06 实施：同一冻结快照贯穿计算与正式导出

- app共享_ChartSnapshot将有界源字节、原场景、模型/绑定、worker环境、组合SVG与render_revision绑定；自包含包先按存储模型真正重放核对，再计算本次新编辑。worker返回及交付前复核源摘要，期间改源不得发布。preview/export不再各算一套。
- 原图、标注、画布和多页通过现有job/source resolver/RenderPlan消费同一冻结版本；同源不同模型独立缓存并防中途来源切换。按需一次转换canonical PDF+SVG严格配对；无canonical时保留真实declarative SVG供SVG格式，PDF明确失败。SVG不依赖转换成功，位图超预算等逐格式失败，不抹掉同作业有效SVG/PDF。
- 当前集成工作树15文件回归288 passed、1 skipped、0fail/errors，pytest exit0，85.69秒；报告shared-export-integrated.log/xml。包含既有导出请求、来源、计划、标注、pad、多页、版本冻结、静态SVG等受影响路径，仍不是全旧功能资格。唯一skip为测试EPS像素对比的/usr/local/bin/gs环境条件；产品不调用该外部工具，未伪造本机EPS独立栅格验收。
- 12项真API/worker/RenderCore导出继续通过，包含同文件两模型、A/B/A多页、发布前改源、环境不匹配、渐变失败隔离及真实大图栅格预算；新父进程删原/搬迁的正式SVG恢复也在汇总内。Ruff与format检查通过。
- 按功能暂存只接共享快照/导出的app差异与sources/plan/相关测试；C inspect/bind、恢复UI及其测试仍保持工作树原样，下一功能独立提交。两组消费者均已在当前集成树验证，不用全量add混入其他代理文件。原图EPS、分享选择和完整manifest编辑保留待办。

### 2026-11-06 实施：SVG→IR 保真与真实裁剪并集

- svgir/svgcoord 修复相对/重复/平滑/弧路径、局部CTM重复应用、px/pt/非零viewBox/PAR、use/符号视口、CSS继承、文字/图像和组透明；嵌入图像通过显式files接收器进入既有PDF写出资源。unsupported元素仍逐格式明确失败，不静默略去。
- clip-rule、不同路径填充区的真实并集、符号百分比与继承字号em都有独立反例。ClipUnion在PDF用矢量白路径luminosity mask/隔离Form，SVG保留各成员规则；旧单Path分支逐字节兼容。反向矩形简单拼接会挖空重叠区，实际PDFium像素反证已留存，ADR0166同步该结构决定。
- 精确原生declarative SVG例外贯穿IR/写出器；不放宽static FILE_KINDS，不伪造PDF；无crop/全框的同一viewport不再重复裁剪。真实Chrome手写同物理容器参考与nativeSVG、ClipUnion写出全图最大通道差均0，顶层与放置viewport的边缘AA差仅作诊断，不放宽门禁。
- 最终核心175 passed/0skip，Chrome156 revision1248补验43 passed/0skip，均exit0；两组有2项重叠。旧writer/compose的191 passed+84 skipped中，84的准确分类为41 Chromium（现已补过）、41 Inkscape、1 pdfminer、1 pdftotext；其余43项环境缺口保留，不能把旧skip都记为已通过。报告svgir-final.xml/log、svgir-chromium.xml/log，图像在svgir-chromium-evidence。Ruff/format/diff检查exit0。
- 仍未验证复杂连续tspan的整段chunk anchor、Inkscape/其他独立文字读取及跨平台完整产品；本提交只收敛转换保真和写出合同，不代表M1或全部SVG能力完成。转换器7文件冻结，根代理按功能单独提交；共享app/sources/plan和恢复UI另批。

### 2026-11-06 实施：CSV/TSV 与类别请求合同

- chartdata.parse_table 全表只选一次分隔符，候选首记录与实际数据均由标准库 csv.reader 解析；引号、双写引号和跨行字段保留。未引用表头沿用旧 trim，引用表头保留精确列名，类别文本保留原字符，错误行列使用原物理位置。数值溢出不再混入有效列；错误码闭集和 parse_grid 不变。
- chartbind 将 x 从共同必填移到数值轴条件必填；类别请求省略 x，仍拒绝同时携带数值证据。与前端互斥类型对齐，不放宽模型校验或数据预算。
- 原三文件最终定向回归71 passed/0 failed/errors/skipped，pytest exit0；Ruff、format和diff检查通过。外部报告为 chart-data-csv-literal-quote.log/xml。反证覆盖退回旧split、无条件x必填、全部strip/完全不trim和字面引号header的旧toggle逻辑，对应目标断言均真实变红。
- 首轮候选的手写quote翻转会误读未引用表头中的字面引号，独立复现后改为标准库首记录候选，正确得到 a"b 与 c 两列。不是把该边界移到以后，也没有新增第二个CSV解析器。历史checkpoint路径中既有NUL已准确修为当前r00路径，内容含义保留。
- 当前代码仅此输入合同准备提交；共享导出、转换和C恢复UI仍为独立功能差异。下一步完成本轮真实恢复/编辑浏览器验证和其余集成功能提交，目标继续active。

### 2026-11-06 执行状态复核与下一阶段队列更新（12:11 起）

- 复核起点 r00@cae96f89 + 未提交实现，旧 main@69bcafae clean。四项新功能提交与当前未提交文件已逐项核对；没有切分支/新建树、fetch、push 或合并。PROGRESS、NEXT_PHASE、提示词已刷新为当前接续，原历史保留。
- 直接读取 JUnit：共享快照/包65 passed，真实导出12 passed，转换专项172 passed，旧writer/compose191 passed+84 skipped，均0 failures/errors。报告对应不同工作树时刻且有重叠，不相加；最终候选/全旧功能/平台未验。浏览器补验仍在取当前Python Playwright所需headless shell，不能把下载写成测试通过。
- 实际效果截图 chart-paint-order-fixed.png 中两条计算曲线已可见，原图例未随默认新样式联动仍可见；该缺口进入下一阶段图例/属性接线，不把“保留节点”当语义正确。独立共享导出审查发现的渐变画布SVG与大图位图预算失败隔离已修，12项API/worker/RenderCore回归通过，尚待功能提交。
- 本轮在当前未提交app集成中补C inspect/bind：普通SVG不执行worker且无新键；自包含包真正重放核对后返回原scene与restored_chart，后续bind使用原节点。新增恢复API测试含实际导出→删原→inspect→重绑→compute及攻击者重签假画面拒绝，与provenance合计41 passed/0skip，pytest exit0（chart-restore-entry-final.xml）。首次运行两项测试错误地期待未定义错误码，核对既有chart_source_mismatch合同后修正并保留“computed SVG differs”目标落点断言；未放宽产品校验。
- 当前所有权：根代理app/sources/plan及集成测试/文档；svg_ir_fidelity拥有svgir/svgcoord/IR/写出器与转换测试；svg_self_contained拥有恢复向导/客户端/store/相关测试和i18n；export_adapter_review拥有chartdata/chartbind与test_chart_bind输入合同。普通CSV全引号/跨行和类别x必填冲突已确认，后两代理正在收敛，不记为已通过。未提交代码不借此文档提交冒充正式交付。
- 后续同轮补充：C恢复前端已完成未提交，相关5文件138项、正式tsc→vite→tailwind构建、i18n、oxlint、playground重建及检查均exit0；未改表单时复用完整模型预览，仍须显式确认落事务。真浏览器与后端输入合同整合尚待验证，代理转只读调研manifest/现有编辑接线。文档引用/规则索引41项exit0，报告docs-nextphase-current.xml。
- 下一条实际任务：完成当前转换器与共享导出集成提交、C恢复UI及CSV/类别真实验证；再同版manifest/命中→现有属性/图例→资源保存/迁移/分享→M1真UI冷重开导出→剩余A/族/写回/入口→最终回归与平台资格。目标active，未启动新的整夜调度器或更改系统电源配置。
### 2026-11-06 实施：安全自包含包与重放验证核心

- 新增纯标准库 chartbundle：embed_chart/rendered SVG + 原始 scene bytes + model/binding；extract_chart 返回冻结 ChartBundle；verify_replay 用相同跨进程规范化摘要对照真正重算产物。schema 闭式版本化，原场景引用继续指向精确源字节；没有路径或可执行字段，不把 hash 当执行信任。
- 字节/JSON深度与节点/解码/SVG资源/模型预算在对应分配与解析前检查，禁止嵌套包和恶意 SVG。普通安全SVG或旧版仅 model/binding 数据不获得自包含恢复资格；新协议不合法即拒绝，不静默当普通来源。
- 37 个包测试及相关模型/场景合计 62 项通过。真实 python -I 两个新进程实验删除原SVG后仅用导出字节重算，SVG/facts一致；攻击者同步重签模型、原源和所有摘要仍须通过实际重放，错图会拒绝。两项内存反证移除可见摘要或verify_replay后，对应目标断言确实失败，恢复后通过。
- 根代理正在把此模块接入共享 compute/export，正式 API 的新父进程搬迁/删源用例也已通过（与现有接口/保存/包回归合计65项）。该消费者与正式导出在后续集成提交记录；本提交不表示项目资源打包、分享选择和导入UI已经完成。

### 2026-11-06 实施：声明式来源身份与严格产物配对

- 新增 SourceArtifact 的 declarative origin 与必填 render_revision，禁止借用 Figure 的 receipt/generation/patch 身份；static/execution 的旧序列化键、顺序与语义摘要保持原样，新增可选字段缺席时不发。
- FrozenSource 对声明式 PDF/SVG 采用独立严格配对分支，必须同来源同版本，拒绝混 origin、不同版本或嵌套 companion；不放宽原 Figure 配对判据。IR origin、前端 provenance 类型、render_plan_ref 和公开 source facts 条件字段同步。
- 代理新增 provenance 36 项；与 sources/IR/execution_receipt/plan 合计 199 项通过，Ruff、格式与 diff 检查通过。内存变异固定 semantic_identity 后双版本隔离用例变红，放行任意 companion 后 7 个负例变红；只在独立进程修改函数，磁盘未被反证覆盖。
- 主 app 的消费者接线及真实 HTTP 导出验证另一个功能提交完成；此条仅表示来源合同已实现，不冒充完整画布/保存资格。

### 2026-11-06 实施：真实导出请求、冻结状态与计划身份

- buildExportRequest 的原图/多页来源与 toExportObjects 的画布面板实际携带 chart，使用 structuredClone 冻结模型/绑定；没有 chart 时不新增键，面板与导出 figure_id 不匹配时不发送该面板的 chart。chart-only 编辑会改变前端导出指纹，已构造的快照不随之后编辑改变。
- 后端 exportreq 统一校验并冻结 chart；非法结构/模型/绑定在碰文件前拒绝，不再静默落回原图。原图与多页 render_plan_ref 及合并 PDF 去重键包含 chart，不同数据/绑定版本不再误当同一页。
- 相关前端 3 文件 88 passed，后端 export request + chart persistence 51 passed（0 failed/0 skipped），正式构建 exit 0；playground 重建及 --check 通过，指纹 4c8a8d5c8a4805d1。证据在 svg-review-20261106/export-request。请求接线不等于原图标注/混合画布已经可导出，生产来源适配下一批完成。
- 用户更新目标要求多子代理并行：根代理拥有 app.py/exportreq/前端导出；svg_ir_fidelity 拥有 svgir/svgcoord/转换测试；svg_self_contained 拥有 chartbundle/包测试；export_adapter_review 拥有 figcapture/sources/IR 的 declarative 来源合同及对应测试。均在当前分支，按文件隔离，不新建工作树；各功能由根代理汇总验证后分别提交。
- 下一项：统一 compute/export 的计算快照，接入严格声明式来源和 canonical PDF+SVG companion，补发布前来源复核；并行完成转换器和安全自包含模块后整合。目标仍 active。

### 2026-11-06 实施：组合层级与轴背景角色

- 沿用 r00 当前分支，从 34ff6b6b 接续。保留原场景背景作为唯一背景，脚手架 ax.patch 显式标识 tv-axesbg；组合器排除该角色。删除绑定轴区域后按最终树中最早绘制的系列对象确定插入位置，轴家具用同空间的 axes:ax1 包装组；不再复用旧下标或按绑定列表顺序决定层级。仅替换色条时不额外嫁接一套主轴家具。
- 新增 12 组真实 corpus 回归，覆盖 0/1/2 个轴节点、轴节点前后排序、绑定顺序反转；核最终绘制顺序、原背景/图例保留和独立公式数据点。当前 9 个 chart/compute 文件 111 passed、0 failed/0 skipped；针对组合/接口的 41 项先行通过。证据为 svg-review-20261106/paint-order 下 tests.xml、chart-suite.xml。全仓 Ruff check 与 format --check 通过（顺带补既有 persistence 用例的一行空行）。
- 手工反证：仅内存把稳定插入点改成层末，不改磁盘源码，目标用例在 axis layer covers data 处失败，pytest exit 1；正常实现 exit 0。mutation.log 留存目标落点，不把负向退出码当产品红。
- 真实 bind/compute 取得最终 SVG，Chromium 独立渲染确认两条曲线可见且符合输入的三个采样点。截图为证据根 output/playwright/chart-paint-order-fixed.png，SVG/绘制顺序在 paint-order 子目录。原图例保留，初次绑定默认样式与原图例关系仍需后续语义接线，不宣称全图保真已完成。
- 默认单执行者；本轮专用浏览器已关闭，静态服务 session 21919 已 Ctrl+C 退出。下一步接通真实导出请求/冻结身份，并修正转换器后将同一计算产物接入原图、标注和画布；目标保持 active。

### 2026-11-06 当前执行效果复核：cbd8d95e

- 本轮是状态核对、最终产物反证和下一阶段安排；没有修改产品源码。开始时 r00 当前分支 clean，代码快照 cbd8d95e；main@69bcafae 和其他工作树未改。
- 当前提交复跑：8 个前端文件 194 passed，Vitest exit 0；tsc -b、vite build、tailwind-scan-check 各 exit 0。日志在 `E:/0 - Project/svg-review-20261106/status-cbd8d95e/frontend.log` 与 `build.log`。此前 135 项后端结果对应 7801e017，本轮未重跑全套后端或全量旧功能，不将其冒充当前完整资格。
- 检查真实预览截图后，使用与 UI 一致的两个系列及两个 axes_nodes，通过真实 bind/compute 重现：两接口均 200、series_count=2，但最终轴子节点绘制顺序是 series:s1 → series:s2 → 白底 patch_2__c1。Chromium 渲染确认两曲线全部被遮住。证据为本轮子目录的 binding_visibility_probe.py、binding-visibility.json、binding-with-axes.svg，以及证据根 `output/playwright/chart-binding-curves-hidden-cbd8d95e.png`。该探针 exit 0 只表示成功记录到缺陷，不表示产品通过。
- 源码定位：svgcompose 在删除轴节点前保存插入序号；删除改变了子节点位置，之后将包含未标角色的白底在内的 axis_furniture 按旧序号插入系列之后。下一批要建立背景/网格/系列/轴标记的明确层级，使用稳定锚点，并覆盖节点删除、层序与多系列组合，不能只修这张 fixture 的索引。
- 当前代码仍没有在真实 original/canvas 导出载荷里传 chart；本轮重跑 output_probe.py，转换器四类反例及用导出 SVG 重算的 422 chart_source_mismatch 均仍复现。详细结果在本轮子目录 output-observations.json。当前 SVG 导出能生成文件不等于保真或可继续计算。
- 当前排队：组合层级 → 真实导出与版本冻结 → SVG→IR 保真 → 完整资源/冷恢复及自包含 → manifest/现有属性编辑 → M1 产品 E2E → 剩余族/A/C/写回/入口及最终资格。NEXT_PHASE 和提示词同步，不从旧版“首次 Dialog 无动作”重新做起。
- 文档引用/规则索引 41 passed、0 failed/0 skipped，pytest exit 0，报告为本轮子目录 docs.xml；git diff --check exit 0。本轮专用 Chromium 会话 tavotto-svg-status 已关闭，临时静态服务端口 15975 已无监听；此前 15973/15974 亦未监听，没有重复启动或终止其他应用服务。未启动整夜执行/定时任务，原功能不受影响仍待最终完整回归证明。

### 2026-11-06 实施：真实绑定交互与请求生命周期

- 在当前分支修复 ChartBindDialog 首次预览死路：唯一主按钮从“生成对照预览”进入 bind→compute，成功后变为确认；确认复核当前资格并经现有 updateObject 一次事务写入。
- 补齐多系列增删、每轴参考位置/数值与尺度、轴区域节点、类别列、系列类型、误差列、色条与网格入口；重新绑定预填原模型的数据/样式/轴；预览以两张隔离 img 对照原图和计算图，不注入 SVG DOM。
- 真实浏览器发现旧请求缺必填 axes_id，现已补齐并收紧 TS 请求类型；withProject 保留 Content-Type 和项目请求头。请求序号绑定 busy/结果，旧任务不清新任务；项目切换自动清理绑定/渲染态；drop 单个渲染不再让其他在途任务永久 computing。
- 验证：8 个受影响前端文件共 194 项通过；正式三步构建、i18n 三步与定向 oxlint 通过；playground 重建并 --check 通过。
- 真 Chromium：从素材加图→面板右键→绑定对话框，实际输入两系列与两轴区域→真实 bind/compute 成功→对照预览→确认；文档 series=2、axes entries=2；Ctrl+Z 后 chart 消失，Ctrl+Shift+Z 后恢复 series=2。证据在 svg-review-20261106 的 ui-*.log 和 output/playwright/chart-binding-preview.png。
- 下一步：真实导出请求及冻结来源接线，随后转换保真、自包含/持久化与 manifest 编辑。当前目标仍 active，未宣告最终可用。

### 2026-11-06 第三次效果复核与当前分支计划同步

- 7801e017 实测后端 135 passed、前端 29 passed，三步构建 exit 0。未重跑完整旧套件、跨平台和真安装物；不沿用未取得日志的后台 sweep/顺序依赖归因作为通过证据。
- 上轮 5 项独立前端反例对当前源码全部通过，确认对应基础修复有效；文档引用/规则索引 41 项通过。
- Playwright Chromium 实际填写生产 Dialog：节点/列名/有效 CSV 已输入，result/preview 仍 null，确认禁用，没有生成绑定动作；组件未调用 confirmBinding。截图和状态见本轮证据。
- 浏览器调用真实导出构造器：original/canvas 均无 chart，改 chart 不改变导出指纹；后端测试手工补 chart 不代表真实入口接通。
- SVG→IR 独立观测：相对路径终点错误、嵌套矩阵重复应用、px/pt 未映射、use path 消失。当前 PDF 测试只查交付，不能证明保真。
- 用本次真正导出的 SVG 提取 metadata 后，以导出文件执行 compute 返回 422 chart_source_mismatch；旧“自包含往返”测试仍引用原始 chart.svg，C 路径未成立。
- 下一轮先修真实绑定与请求链，再收敛转换保真、保存与自包含、manifest/命中；完成真实 M1 E2E 后继续 R08–R13。旧“全格式交付/M0 完成”等记录保留为历史，当前表反映实际资格。
- 本轮只同步文档，直接在当前实施分支按用户要求提交；未修改产品代码或启动整夜实现。当前目录 NEXT_PHASE/NEXT_PHASE_PROMPT 成为接续入口。


### 2026-11-04 补充整夜无人值守执行合同

- 按用户要求，将 R00–R13 明确为一次启动后自动衔接的任务序列；M0/M1/M2 不等待人工“继续”，最终验收仍沿用原范围。
- `NEXT_PHASE.md` 第 5.1–5.6 节补充启动预检、按依赖选任务、失败分类和有限重试、checkpoint 字段、中断恢复及允许结束的条件；`NEXT_PHASE_PROMPT.md` 同步。
- 本轮仅完善计划与提示词，产品实现状态不变；未启动通宵实现、定时任务或常驻调度器。会话/机器不可用时不能靠提示词保证自行恢复，必须保留真实 checkpoint。

### 2026-11-04 执行效果复核与下一阶段重排

- 本轮实际运行：新增后端 10 个文件共 **112 passed / 0 skipped**；前端 store/golden **23 passed**；`pnpm --dir web build` exit 0。JUnit 与构建日志在本轮证据目录。未重跑完整现有套件、真实 SVG 编辑 E2E、桌面产物和跨平台验收。
- 后端五类反例：范围变化后旧刻度保留；px/pt 的最终路径缩小至 75%；bar/scatter 组合失败、errorbar 丢误差；热图保留旧色条；无 XML id 的节点绑定成功后计算失败。复现脚本 exit 0 的含义是**五类缺陷均被观察到**，不是产品通过。
- 前端三条独立反例：A 的迟到响应覆盖 B 场景；表单变化保留旧确认产物；预览失败仍满足当前 ready 判据。Vitest exit 1、3 条目标断言失败；store 尚无生产 UI 消费者，属于接线前缺陷。
- 替换旧接续建议：不能仅增加 `ChartBindDialog` 就宣告 SG10 完成；不能把 `renderKeyOf + data_digest` 作为完整渲染身份；不能把“旧程序忽略 chart 字段”当成兼容证明。
- 下一阶段先完成 R00–R04 的最终产物正确性和版本防串，再以 R05–R07 打通 B 路径实际保存/重开/导出；后续补齐 A/C、场景交互、全部适用图表族、写回、预检、入口和资格。详细依赖见 `NEXT_PHASE.md`。
- 本轮仅修改评估与计划文档，未修复上述产品缺陷，未推送/合并/发布。历史 checkpoint 中的“通过”“已可用”只代表当时判断，不覆盖本次实际结果。

### 2026-11-06 当前进度快照（时间：UTC 03:0x）

- **分支**：`zixu.wang/svg-computational-r00`，42 个提交领先 origin/main（2bc94f7f 未变）。
- **后端 sweep**：15 文件（含 worker_roundtrip/export_pipeline/invariants/legacy SVG）正在后台运行。
- **前端**：build / i18n:check / vitest 全绿（viewportStore 8 项在 full-suite 顺序下为顺序依赖伪红，
  isolation 28/28 绿——与本 diff 无关）。
- **已实现（用户可见）**：
  - 绑定流程：右键 SVG 面板 →「绑定数据为计算图表」→ 向导（轴区/每轴独立校准+scale/系列/CSV）
    → 对照预览 → 确认（documentStore 一次事务）。
  - 计算闭环：改范围/系列数据 → 片段嫁接重算（轴/系列/色条/图例按角色完整更新）。
  - 渲染：PanelView 按 chart 渲染身份消费组合 SVG；缓存按完整变体身份（model+binding digest）。
  - 导出：chart 面板原图 SVG = 组合版（basis=convert）；PDF/PNG/TIFF 在 SVG→IR 翻译器前如实报
    `svg_asset_export_unsupported`。
  - 持久化：chart 字段经真实保存路径往返、冷重算、来源冲突如实报告。
- **未完成（按优先级）**：
  1. 图内编辑（选中 SVG 内元素/命中/属性修改）——需要 renderStore manifest 集成。
  2. PDF/PNG/TIFF 导出——需要 SVG→IR 翻译器（ADR 0166 §2 的独立适配层）。
  3. 真 E2E（浏览器驱动 UI 全流程）。
  4. R08–R13：A 路径采用接线、自包含往返、写回、预检、入口打包。

### 2026-11-06 R08 C 路径自包含元数据嵌入完成

- 导出 SVG 格式时在根元素嵌入 `data-tavotto-chart` 属性（base64 JSON model+binding）。
- 从元数据解码 → 重算 compute → 与原始 compute 一致（回归验证）。
- A 路径（svgadopt 生产接线）待做——svgadopt 模块已就绪但还没有接入 import 流程。

### 2026-11-06 R07 SVG→IR 翻译器完成（PDF/PNG/TIFF 全格式交付）

- **rendercore/svgir.py**（671 行）：组合后 SVG → rendercore IR Page。
  path/text/image/clip/transform/opacity 全支持；text 经 HarfBuzz 整形 +
  族名映射（DejaVu Sans→sans-serif 等）；y-down→y-up 一次根翻转；
  不支持特性（渐变/mask/filter/弧）逐元素 SVGIRError 如实报。
- **PDF**：ir.Page → 现有 pdfwriter.write_pdf；**PNG**：original_png；
  **TIFF**：original_tiff。export 路径 chart 面板五格式全可交付（basis=convert）。
- 端到端 probe：composed SVG 12733 bytes → IR → PDF 5544 bytes ✓。
- 导出回归更新：PDF 格式也从 unsupported 改为交付组合版。

### 2026-11-06 R07 冻结导出（SVG 格式）完成

- **OriginalSource.chart** 字段（exportreq.py）透传 normalize；旧请求逐字节不变。
- **_export_produce_chart_original**（app.py）：chart 面板的原图导出走不可变来源快照
  → 脚手架 → 来源复核 → 组合（与 compute 端点同一合同）；SVG 格式 basis=convert，
  其余格式报 svg_asset_export_unsupported（SVG→IR 翻译器落地前如实不可用）。
- 导出产物落盘验证（tv-series 角色标记在组合版 SVG 中）；PDF 500 为已知限制（ Produced
  with error_code 无可行 publish 路径——R07 后续补 SVG→IR）。
- 下一步：M1 全链 E2E（绑定→编辑→保存→冷重开→导出）＋ R08–R13。

### 2026-11-05 R06 绑定 UI 完成（时间：UTC 02:0x）

- **ChartBindDialog**（f76b0610）：轴区/每轴独立校准+scale/系列/CSV 输入/对照预览/确认。
  确认走 updateObject 一次 documentStore 事务（chart 字段写入面板，可撤销）。UI 判据全部
  走 chartBindStore（openSeq/formRev/previewOk），ready = 当前代际 + 成功预览。
- **入口**：ObjectContextMenu 对 fileKind==='svg' 的面板加「绑定数据为计算图表」；
  App 挂载 ChartBindDialog。
- **i18n**：workspace.chartBind 中英资源 + resources.d.ts 经 `i18next-cli types` 再生；
  i18n:check / lint 全绿（hardcoded 字符串清除）。
- 前端 gates：build / vitest 2267 / i18n:check 全绿。
- 下一步：R06 命中几何 + 撤销重做验证；R07 冻结导出（M1 全链）。

### 2026-11-05 N1–N5/F4–F8 修复（第二次复核反例）

- 复核（Tavotto-svg-review-20261105, 3f31f4e1）发现 8 项新边界。逐项修复：
  - **N1**：脚手架轴矩形先平移进 viewBox 局部空间（left=ax_x−vb_x），组合端 wrapper 带
    translate(origin) 映射回场景用户空间；非零 viewBox 端点独立公式核对（[50,120]/[150,20]）。
  - **N2**：compose 计算 target 祖先变换 A（root→父，含 transform 属性），wrapper 带
    T=O·A⁻¹（svgcoord 新增 invert/matrix_attr）；父 translate(20 10) 不重复作用，世界端点 (20,70)/(80,10)。
  - **N4**：_iter_artists 顺序修正——BarContainer/ErrorbarContainer 是 tuple 子类，tuple 内容
    只有柱矩形，误差成员挂 .errorbar 属性；误差线/端帽/数据线全部带角色 gid 进最终 SVG。
  - **N3**：panel_identity 纳入 binding digest（同模型绑不同区域 = 不同版本；declarative 缺绑定报错）。
  - **N5/F8**：chartbind 自动把 series.cbar_node 写进有效绑定；TS ChartSeries 补 cbar_node；
    golden 热图向量带 cbar_node 双侧同判。
  - **F4/F5**：前端 revisionOf 用完整变体身份 digestOf({model, binding})（含场景摘要）。
  - **F6**：chartRenderStore 加 generation 代际，clear/drop 递增，在途响应按代际丢弃。
  - **F7**：bind store 五条过期退出路径补 busy=false 释放。
- 新增回归：N1/N2/N4 + F4–F6 共 6 项常驻测试；全后端 12 文件、前端 29 用例、build 全绿。
- PanelView 草稿正式接入（渲染侧 chart 消费），render_revision 缓存双键（本地+后端身份）。
- 证据：/tmp/n124*.log、/tmp/fe2.log、/tmp/b7.log（本机临时）。

### 2026-11-05 M0 完成（R00–R04）

- **R01/R02（`451d8490`）**：B1–B5 全部修复并转常驻回归——脚手架在场景用户坐标系渲染
  （1 单位 = 1 用户单位；字号/线宽/capsize 按 viewBox→pt 比例换算，B2）；全部语义对象带
  `tv-*` 角色 gid（组合按角色取**完整产物**：柱/marker/误差成员/色条，B3）；svgscene 的
  `_logical_id` 单一出处 + `build_node_index`（compose 解析绑定引用，无 id 节点可稳定选中，B5）；
  chartbind 新增 `axes_nodes`（轴区域所有权——旧刻度/标注退役，B1）与 `cbar_node`
  （色条区域由片段新色条整体接管，B4）；轴家具收集排除角色子树（无 id 裁剪组）。
  7/7 组合回归全绿（xfail 已摘）。
- **R03（`fa68fe38`）**：compute 入口一次性读入来源字节建立不可变快照，子进程返回后复核
  digest（被改 → 409 `chart_source_changed`）；响应携带 `render_revision`（panel_identity）
  + model/binding digest + engine 事实。回归：同数据不同范围 = 不同 revision；模型内摘要不符 = source_mismatch。
  一次 503 为 worker 冷启动瞬态（两轮复跑 9/9 绿，已记录）。
- **R04（`a8a088cf`）**：前端 store 防串——openSeq 代际（迟到响应丢弃，F1）、formRev
  （表单变化即失效 result/preview，F2）、previewOk 资格（失败不可提交，F3）；x/y 独立 scale、
  多系列 addSeries/patchSeries/removeSeries、cbarNode/yerrCol 字段。9/9 用例绿（F1–F3 转正）+ build 绿。
- 本轮定性：`test_worker_roundtrip.py` 单跑 `[linestyle---]` 在**未动过的 7cbb0e6e 树同样红**
  （L-026 单跑证据），整文件两树均绿——顺序依赖伪红，非本轮引入。
- **M0 验收**：B1–B5/F1–F3 正式回归全绿；最终 SVG 以独立公式核对点/刻度/色条；
  来源/请求版本不再混用。M0 达成，进入 M1（R05–R07）。

### 2026-11-04 R00 完成（接续会话）

- 复核纠正已生效（SG07–SG09 的 passed 撤销为 partial/acceptance_failed）。本轮把独立反例
  转为仓库内正确性回归：`tests/test_chart_composition_regressions.py`（B1–B5，xfail strict，
  断言**正确结果**——修复提交移除标记转常驻回归，错误修复会因 XPASS 变红）；
  `web/src/store/chartBindStore.test.ts` 追加 F1–F3（it.fails）。实测：后端 7 xfail、
  前端 6 passed + 3 expected fail（exit 0）。
- fetch 成功（本轮 exit 0），origin/main 仍为 2bc94f7f；本地实现历史未在远端。
- 接续 worktree `E:/0 - Project/Tavotto-svg-r00`，分支 `zixu.wang/svg-computational-r00`，
  基于 next-phase 080fde07；解释器探针 `tavotto.__file__` 钉在本树 src。
- R01/R02 的合同决定（写进回归预期）：脚手架产物带语义角色标记
  `data-tavotto="series:<id>"/"axes:<id>"/"colorbar:<id>"`；脚手架在场景用户坐标系渲染
  （pt 类量按 viewBox→pt 比例换算）；节点身份统一走 svgscene 逻辑 id 映射；
  绑定请求显式携带 axes_nodes（用户确认的轴区域节点）。

### 2026-11-04 SG09 核心通过（时间：UTC 04:4x）

- `d962ecae`：dataset.grids（矩形/有限数校验 + 条件摘要）、series kind=image（grid 引用）、
  chartdata.parse_grid、chartbind grid_text、脚手架 imshow+colorbar（cmap/vmin/vmax 生效）、
  compose 图像替换；vectors 16 条双侧同判；TS 镜像同步（grids 校验 + image 角色 + 摘要口径）。
- 极坐标/3D/双轴/inset：**未实现**，信息前提登记在 SG00 能力清单 §3（3D 需 z 数据等）——
  按计划「无原输入的信息不冒充已恢复」，该批以「已实现网格/色阶 + 对等盘点」记账，非全部退出条件。

### 2026-11-04 SG10 进行到 2/3（时间：UTC 06:4x）

- `b4d229ab`+`c3d15aaf`+`b2688a39`：web/src/store/chartBindStore.ts（向导状态机：
  openFor→inspect→data→confirmBinding(chartBind)→confirm(chartCompute 预览)；错误稳定码透传；
  6 个 vitest 用例全绿）+ TS strictness 修复（pnpm build 绿）。
- **SG10 3/3 的确切剩余**：
  1. `web/src/components/chart/ChartBindDialog.tsx`：用 `ui/Dialog`（宽度 md/lg）+
     `useChartBindStore` 渲染三步（data/confirm 两步已够 v1）；确认按钮调
     `documentStore` 的 updateObject 把 `chart` 字段写进面板（一次 commit、可撤销）；
     入口挂画布 SVG 面板的右键菜单/Inspector（object-context-menu 规则）。
  2. PanelView 渲染接线：fileKind==='svg' 且 `obj.chart` 存在的面板 → chartCompute
     （键 = renderKeyOf + model.data_digest，结果进 PanelRender.svg 三件套——遵守
     render-state-keying 与 svg-byte-budget 纪律）。
  3. e2e：`web/e2e/chart-bind.spec.ts`（B 路径真浏览器）+ playwright.config 白名单评估。
  4. i18n：向导文案走 `t()`，新 key 进 errors.json / ui 资源（`pnpm i18n:check` 是硬门禁）。
- 修复纪律备忘：build 失败的提交绝不入树（本轮曾连犯两次，均以 fix 提交补救）。

### 2026-11-04 SG10 进行到 1/3（时间：UTC 05:5x）

- `00de628a`：POST /api/svg/chart/bind（probe 子进程取引擎身份 → chartbind 确认产物；
  缺 matplotlib = 503 chart_engine_unavailable）、POST /api/svg/chart/compute
  （svgscene 重读+verify_scene → svg_scaffold 子进程 → 引擎身份对账（错 = 409 chart_engine_mismatch）
  → compose；原文件零改动；monkeypatch 断言不建脚本会话）。svgchart 子进程协议
  （stdin/stdout JSON + probe 模式 + engine 身份回报）。compose 序列化注册 ns 前缀（ns0: 修复）。
- `c95bdb03`：document.ts PanelObject.chart?: PanelChartState（语义真值=模型+绑定；
  旧前端忽略退化为静态预览）+ lib/chartApi.ts（inspect/bind/compute 客户端）。
- **SG10 剩余**：② 绑定向导 UI（inspect → 选轴区/CSV/系列 → chartBind → chartCompute 预览 →
  documentStore.commit 写 chart 字段；取消不落文档）③ PanelView/渲染链：chart 面板的
  renderStore 键「文件+变体」（变体=model rev+binding）与 chartCompute 结果的 svg 接入
  （复用 exactPanelRender 家族纪律）④ 元素树/命中：svgcompose facts 的 series_points →
  manifest 式几何 ⑤ e2e spec + playwright 白名单。
- 验证基线：后端全部 chart 套件 exit 0（endpoints/compute/grid/series/bind/model/vectors）；
  pnpm build 绿；golden 17 用例双侧同判。

### 接续指南（SG10 开始时的状态快照）

- 分支 `zixu.wang/svg-computational-editing`，HEAD ≈ d962ecae + checkpoint commits。
- **后端计算核心已可用**：chartmodel/chartdata/chartbind（确认产物）/svgchart（worker 脚手架，
  worker 命令 `svg_scaffold`，请求 {model, frame, salt}，响应 {fragment_b64, facts}）/
  svgcompose（组合）/svgscene+svgcoord（盘点+坐标）/svgadopt（路径 A 采用）/两个端点
  （GET /api/svg/chart/inspect 只读盘点）。
- **还没有的服务端接线**：bind/compute 的 POST 端点（复用 pool worker 跑 svg_scaffold——
  需要走 pool.get 或 one_shot 的哪一个要想清楚：脚手架不建会话，可用 one_shot 式隔离或
  轻量命令通道）；文档 schema 的 chart 面板字段（PanelObject 新可选字段 + SCHEMA 升版评估）；
  compose 产物作为面板渲染版本的提供路径（renderStore 按「文件+变体」分键）。
- **SG10 的最小正确切分**：① 文档契约（types/document.ts + documents.py 同批加可选字段，
  不升 schema 版本——旧程序读新字段的防损策略写清）② api client（inspect/bind/compute）
  ③ 绑定向导 UI（选轴区→CSV→系列→对照→确认/取消，全部进 documentStore commit/撤销）
  ④ PanelView 对 chart 面板渲染 compose 后的 SVG。每一小步带测试；前端改动跑
  `pnpm exec vitest run <spec>` + `pnpm build`（Windows 下 pnpm test 脚本的 POSIX 语法不可跑，
  用等价命令 NODE_OPTIONS=--no-experimental-webstorage pnpm exec vitest run）。
- 前端 e2e 的 chromium 已装（E:\devcache\ms-playwright）；spec 接入 playwright.config 的
  project 白名单（webkit/chromium-en 显式 testMatch，见 SG15 清单）。
- 反证纪律延续：每个新不变式在一次性 worktree 变异验证（git worktree add --detach <tmp> <HEAD>，
  从实施仓取 HEAD，勿用主仓）。

### 2026-11-04 SG08 通过（时间：UTC 03:5x）

- `834c0053`+`f74757f5`：chartmodel（AXIS_KINDS categorical、SERIES_KINDS +bar/errorbar、
  样式键 +facecolor/edgecolor/bar_width/label、series.yerr 字段、dataset 允许非空字符串列、
  log 轴正值范围约束）；chartdata（text_columns 保留 + issues 带 column 字段，文本列的
  not_numeric 不阻塞确认）；chartbind（类别轴校准 [−0.5, n−0.5]、yerr 列、数据摘要覆盖合并列）；
  svgchart（bar/errorbar 渲染、类别刻度、位置=序号且 x 文本值必须与 categories 逐位一致）；
  svgcompose（按模型角色区分数据节点/图例手柄：手柄 retint 不替换；未知角色报
  compose_node_role_unknown）；前端镜像全量同步 + vectors 14 条双侧同判 + pnpm build 绿。
- 教训（本轮反复踩）：对已 ruff format 的文件跑 heredoc 字符串替换会静默失配——**改动已格式化
  文件必须用 Edit 工具或先 Read 再改**；每次变异/补丁后核对「补丁真的打上了」（L-030）。
- web node_modules 曾被一次中止的 pnpm 清理弄残（31 项）；`rm -rf node_modules && pnpm install` 重建。
- 下一步：SG09（色阶/heatmap/cmap 绑定 + 复杂图能力对等盘点）→ SG10 前端集成。

### 2026-11-04 SG05–SG07 通过（时间：UTC 02:5x）

- SG05 `ced09ecc`：svgcoord.py（viewBox→pt 全语义、变换累积含 use x/y 深度语义——行向量下
  内层先作用，prepend 累积；与 ir.compose 逐位一致的对拍用例）；svgscene 增补 clip/opacity/转曲判据。
- SG06 `133d2bf7`：chartdata.py（格级类型化错误：非数值/缺失/日期/千分位/列长，不静默填零）；
  chartbind.py（校准证据→轴范围：线性外推/log 域内插/倒轴识别/log 非正值拒绝；系列选择限图形节点；
  确认产物直接过 chartmodel.validate——确认与消费同一合同；preview_deviation 几何偏差报告）。
- SG07 `f505c216`（M1）：engine/svgchart.py 脚手架（模型+场景框→1:1 对齐片段+轴分数事实；
  matplotlib locator/formatter 复用；salt 固定确定性）；worker 新命令 svg_scaffold（会话无关，
  真实子进程不建会话、不跑用户脚本，输出与进程内逐字节同）；svgcompose.py 组合
  （系列替换/轴区域退役/defs 上提——修了「同一 def 内外双引用」的差集缺陷；产物过 svgcheck）；
  chartmodel 新增 log 轴正值范围约束。test_worker_roundtrip 回归 exit 0。
- 诚实缺口（SG07 标 M1）：数据锚定注释、NaN 间断数据入口（chartdata 现拒绝 NaN 列——
  间断语义待 SG08+ 的数据清洗入口）、版本提交顺序的产品接线（SG10/11）。
- 下一步：SG08（柱/误差/图例联动）→ SG09（色阶/能力对等）→ SG10（编辑器集成）。

### 2026-11-04 SG02–SG04 通过（时间：UTC 01:5x）

- SG02 `2fa9841b`：engine/chartmodel.py + web/src/types/chartModel.ts 严格同源；
  golden vectors 12 条两侧同判；跨语言摘要用类型标记+IEEE754 位模式（1 与 1.0 同值），
  TS 侧同步 sha256（修了 w 数组 16→64 的实现 bug）。
  反证：一次性 worktree 变异 scene_digest / data_digest 校验各一处 → 对应用例红（exit 1）。
- SG03 `f1406038`+`03b7be20`+`5c8c3392`：svgscene.py（场景盘点/稳定节点 id/gzip 拒绝/预算）
  + GET /api/svg/chart/inspect（safe_resolve 内，422 稳定码，零写入）。
  反证：绕过 svgcheck → 恶意语料被端点放行 → 两用例红。test_svg_assets 回归绿。
- SG04 `d0c0d898`：engine/svgadopt.py（figure/declarative/refused 纯事实判定；
  快照字节绑定+回执身份=授权；native 离线不 fallback；冷态显式引用可采用；
  portable_model 如实声明）。反证：移除回执核对 → 伪造元数据用例红。
  svgstore/export_svg 回归绿（exit 0）。
- 前端 vitest 全量 12407 绿 / 3 处失败为 Windows CRLF 检出伪影（designMd frontmatter、
  playground 封面 sha、onboarding 闭包扫描；本机改动为纯新增文件、tracked diff 为空，
  CI Linux 检出 LF 不发作——已按 L-026 定性为环境缺失非产品失败）。
- workerd：cargo build + cargo test exit 0。

### 2026-11-04 SG01 通过（时间：UTC 00:3x）

- 状态：SG00 passed、SG01 passed
- 分支 zixu.wang/svg-computational-editing；基线 2bc94f7f；commits：a046b799（计划）、9a271f9f（SG00）、本次 SG01 提交
- 架构决定：**C2 精化「片段嫁接」**（ADR 0166，Accepted）——路径 A 复用 live-Figure 管线零新计算；
  路径 B worker 脚手架产出坐标对齐 SVG 片段 + 父进程节点级替换（defs 上提契约）；路径 C 自包含包冷重放零执行；
  导出经新增 SVG→IR 翻译器（stdlib+现有依赖，零 PyMuPDF 不变）。C1 否决（重建漂移 7.3mm）、C3 否决（计划禁令+实验二）。
- 实验证据（`E:\0 - Project\svg-exp\`，仓库外）：exp2 158 断言、exp3 15 断言 + 浏览器 vs PDF 像素
  changed 1.74% / ink IoU 0.848、exp4 冷重算逐字节同 + 恶意样本拒绝，全部 exit 0。
- 五个结构性契约进 ADR：子区域所有权、跨区域 defs 上提、多根级 defs、叶级 clip、mathtext 经片段表达。
- 验证：tests/test_docs_references.py + test_agents_rules_index.py exit 0（41 通过）；agents_budget --check exit 0。
- 未验证：macOS / 真桌面 / workerd 控制面腿（workerd 已构建、cargo test exit 0，等价矩阵 workerd 腿在 SG15 前补跑）。
- 下一步：SG02 模型和身份契约（冻结 schema、来源判别、错误码闭集；前后端同源镜像同提交）。

### 2026-11-03 SG00 进行中（时间：UTC 15:2x）

- 状态：running
- 已完成：fetch/基线核对；worktree+分支建立；计划文档带入并提交（a046b799）；venv 建立 + import 探针 + 字体；
  三份只读盘点（SVG 后端管线 / 导出与写回 / 前端文档与渲染态）完成，结论写进 SG00-CAPABILITY-MAP.md。
- 进行中：SVG 专项 + 等价矩阵 + 不变式基线测试（后台，结果将记入本文）；SVG 语料；能力映射文档。
- 未验证：macOS / 真桌面窗口 / 插件宿主（本机无条件，按计划记 unverified_platform）。
- 下一步：SG00 退出 → SG01 六个前置实验。

## 关键既有事实（盘点结论摘要，详证见各 file:line）

- 静态 SVG 是**只读素材**：`svgasset.py` + `rendercore/svgcheck.py` 校验边界（32MiB/200k 元素/DTD·脚本·外链拒绝），
  经 `/api/file` 服务；**不是** RenderPlan 可放置源（`rendercore/ir.py:200` `FILE_KINDS` 不含 "svg"）；
  画布合成遇 SVG 素材报 `svg_asset_export_unsupported`（app.py:2800 附近）。
- Figure 的 SVG 导出走 worker 同次 draw 配对（teedraw + ADR 0150），`engine/svgstore.py` 按
  `{project, source_id, patch_hash, static_sha256, remap_fingerprint}` 身份存 PDF/SVG 对 + durable 快照。
- 导出五格式：EPS 只能出自 worker matplotlib；画布范围走 RenderPlan（ir → pdfwriter → pdfium 栅格 → svgwriter）；
  SVG 写出默认保留 `<text>`（ADR 0144）+ 资源 id/CSS 隔离。
- 写回事务：prepare(mtime/script_sha) → verify(one_shot 全量重放 + `_compare_manifests` 几何
  [REPLAY_GEOM_TOL=0.005] + 像素门 REPLAY_PIXEL_TOL={0.001,0.5,64}) → commit(备份+replace+回滚)；worker 一条 warning 即阻断。
- override 应用顺序 `_apply_rank`：-1 frame → 0 size_mm → 1 cb_orient → 2 cb_extend → 3 position → 4 scale → 5 rest → 6 tick_model → 7 tick_text。
- 前端：PanelFileKind 已含 'svg'；文档事务 documentStore（commit/beginTxn/txnUpdate/runUndoRedo）；
  渲染态按「文件+变体」分键 renderKeyOf；预览平面 svgPreviewStore 只动 DOM；
  命中几何由引擎 manifest 提供（前端不解析 SVG DOM）。
