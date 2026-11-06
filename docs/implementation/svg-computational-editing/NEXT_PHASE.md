# SVG 计算图表编辑：执行效果复核与下一阶段任务

> 2026-11-07（Asia/Shanghai）复核，最新功能HEAD=11c67ab1。b5f9f557包成员单一权威与11c67ab1 safe/workerd执行捕获已提交；N3b严格检视及旧保存/导出资格保留。已有实际worker/Rust控制面/3.10及缺省身份兼容验证；native、准备/启动过期核验、完整声明资源入包及UI仍缺，M1/M2/M3均未通过。
> 下一动作：N3c-3a剩余native捕获与过期核验→3b完整图/外部及prefix资源→3c声明UI→3d精确引用迁移→N3d独占安装→N3e原writer采用→N3f冷搬迁整链。safe捕获、N3a/b、配置/已知资源、FRAME不重做。随后N4图例→N5几何→N6/M1→N7/M2→N8/M3，不缩减SG00–SG17。
> 进度唯一台账：[PROGRESS.md](PROGRESS.md)；最终范围：[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)；接续提示词：[NEXT_PHASE_PROMPT.md](NEXT_PHASE_PROMPT.md)。沿用当前实施分支，按功能commit；无人值守合同见第5节。

## 1. 当前执行情况与实际效果

实施树`E:/0 - Project/Tavotto-svg-r00`，唯一分支`zixu.wang/svg-computational-r00`。原目录`E:/0 - Project/Tavotto`仍是clean main@69bcafae，未包含修改；不能从原目录运行旧版本判断新功能。未新建/切换分支，未推送/合并/发布；未fetch，不声称远端最新。

共享venv裸Python默认import另一旧树；现pytest.ini的`pythonpath=src`已核正确。续跑手工Python/服务必须显式指定本实施树src并核实际模块路径，见`status-20261107-pytest-import.json`。本次最终socket父进程实际导入当前树；此前长驻演示服务未重启，不能把它当作已加载新API。已有验证按各自源码时刻记，不合计成全分支资格。

| 能力 | 已实现/已验证 | 尚缺 |
| --- | --- | --- |
| SVG绑定与计算编辑 | 显式轴/系列/数据绑定，范围、线宽等经原编辑器；undo/redo、v2完整编辑再导入已有真UI证据 | 所有科学族、多轴、文字/分组等逐格资格 |
| 删原源继续编辑 | 精确INPUT附件参与预览/渲染/导出，正式保存后空缓存新浏览器+新后端恢复并继续编辑已验 | 全入口/平台资格 |
| 五格式原图导出 | a06cc3d3已提交；真UI PDF/PNG/SVG/EPS/TIFF全部done，独立文字/尺寸/PPI/像素/GS核对 | 完整双系列/图例/混合画布/多页M1 |
| 命名文件关联 | d7a73ce1前端+a7008840/d7b6eef9后端；改标题/空缓存新进程/CtrlS写原文件/外改409已验 | 保留回归；包迁移后的本机关联不继承发送者 |
| 明确冲突恢复 | 真覆盖、真reload等待期间新增编辑与下一普通保存、双独立窗口200/409均验；404旧链备份失败P1已修并提交 | 最终旧功能/平台候选全量资格待补 |
| 预检误报 | 0b611411已提交，真实输入两范围误报消失，旧128份spec对拍及正式构建通过 | 图例与广泛readiness仍未完成 |
| 项目包 | 真实v4导出、保原配置ID/已知登记及输入已验；新增5e000c6f实际严格检视API，完整资源/快照/声明缺件及认证均有真实HTTP证据 | 完整输入声明、外部/prefix资源、安全安装API/UI、搬迁整链未完成；导出/检视不等于安装 |
| 执行时输入证据 | 11c67ab1：认证声明管理、safe/workerd起止文件/目录清单、冻结执行胶囊、receipt可选revision；真实worker/Rust/3.10已验 | native、准备/启动过期核验、完整声明图及资源入包、声明UI；当前公开revision不证明完整可复现包 |
| 图例 | 曲线样式已生效；真实native Legend控制实验可复用现legendmodel跟随 | 当前旧SVG图例仍不跟随，须显式关系/所有权合同 |
| 几何与总体交付 | 多DOM基础与共享nested viewport已提交 | 成员paint slots、复杂clip命中/框选/高亮，截图刻度边缘裁切需对照；最终旧功能/平台资格 |

### 1.1 最近验证证据

证据根`E:/0 - Project/svg-review-20261106`。不同范围、源码时刻及重叠用例不相加成最终总数。

| 记录 | 真实结果与范围 |
| --- | --- |
| eps-staged-qualification.json / eps-ui-five-formats-qualification.json | EPS15HTTP、429项前端/build，真五格式和独立产物；已提交a06cc3d3 |
| project-file-recovery-staged.json | 后端74HTTP通过/0skip；明确恢复已提交；真UI证据另见n21-recovery-* |
| n21-p1-frontend-validation.json / n21-frontend-staged-final.json | 最终N2.1 431通过/0skip，build/i18n/lint0；404P1原探针先红后绿，两反证，已提交d7a73ce1 |
| n21-ui-external-named-qualification.json | 真实F2编辑/CtrlS返回409，外改A与另一B字节不变，原绑定revision保留；普通冻结bundle范围 |
| unified-current-build-validation.json | 冻结22文件统一正式build/playground/build后check均exit0，源无漂移，指纹551ca2013f6b078c；非完整回归 |
| n21-recovery-overwrite-qualification.json / n21-recovery-reload-qualification.json / n21-two-window-qualification.json | 真按钮/真实GET等待期编辑/下一普通保存与双独立窗口200/409保稿已验 |
| n3-package-http-staged.json / n3-packagebudget-freeze.md | 旧包18新HTTP/8旧、204基础及真实故障反证，已提交；非v4完整安装资格 |
| n3-working-receipt-staged.json / n3-chart-budget-factory-staged.json | 单writer回执448前端与计算包82末次专项及正式检查/反证，均已提交；产品消费者仍待接 |
| n21-ui-binding-qualification.json | 普通冻结bundle上：A改标题B→零缓存新浏览器/新后端仍关联A且dirty→CtrlS只写A，正文等于实际请求，另一B不变、原源缺席 |
| package-container-staged-current.json | 历史容器+原子发布121通过/0skip；随后旧app已由336783bf消费，不再将“app未接”当当前状态 |
| chart-package-staged.json | 计算包末次专项54通过/0skip，3次新worker删源精确重放，非混合资源安装资格 |
| n4-preflight-freeze.json / n4-preflight-staged.json | 78前端/33Python、49向量、真实输入与旧128spec对拍，统一build和41规则通过；已提交0b611411 |
| n3-package-export-frontend-validation.json | 历史6文件候选的78项/模拟API资格；随后缺件状态扩成85项并由8ed50616实际联验提交，不再作为未提交或无后端状态 |
| n3a-export-staged-final.json / n3a-final-integrated.xml | 8ed50616已提交；同一最终联合命令438通过/0skip/exit0，另85前端专项、3真实HTTP与4门禁反证、全仓Ruff及最终build/i18n/playground通过，不是全分支回归 |
| n3a-real-ui-package-qualification.json / n3a-ui-final-frozen.json | 真菜单完整/缺件包及最终新后端/bundle导出，整份正文/原序编辑/完整INPUT与实际UI请求一致；原chart.svg始终缺席 |
| n3b-targets-staged.json / n3c-portable-resource-config-review.md | 目标计划277专项通过、已提交；历史worker探针发现依赖/配置缺口。配置与已知资源已由下列提交实现，完整声明/外部资源仍缺 |
| n3c-runconfig-portable-freeze.md / n3c-runconfig-portable-final.xml | 75b09b57保配置ID/参数及新版writer共同锁；145通过/0skip，双进程、冷进程和反证已验 |
| n3c-execution-export-staged.json / n3c-final-integrated-command.json | be84ba33真实执行资源导出；718联合/0skip/exit0，另8新HTTP、56前端及构建各自通过，不累加、不当作全分支资格 |
| n3c-real-ui-correct-kind-qualification.json | 真菜单与严格ZIP回读：整份正文/已知资源/原rc_ID/argv一致，脚本计数1；coverage明确incomplete，不是安装证明 |
| n3c-frame-lifecycle-final.xml / n3c-compat-frontend-build.json / n3c-frame-ui-qualification.json | 13720bc4/1c9da4ca：100专项/0skip及正式构建通过，指纹9c11afde28799951；新鲜浏览器旧文档立即菜单导出200，过期ref正确替换 |
| n3b-package-streaming-validation.md | 1795f244：326通过/0 skip，20MiB真实资源有界读取、4反证、旧包完整字节相同 |
| n3b-svgstore-full-freeze.md | d1389db6：316通过/31 Win1314权限跳过，全部档案/marker/blob及真实junction、6反证；不授予当前执行权 |
| n3b-inspect-http-missing-candidates-validation.md / n3b-inspect-http-68-final.xml | 5e000c6f最终consumer430ba6…：68通过/0 skip/exit0；声明缺件P2已修、定向复核收口，全仓Ruff/格式通过 |
| n3b-final-integrated-command.json | 较早consumer6a6fbb…的511联合通过/0 skip；其后缺件处理delta由最终68项覆盖，不冒称511已在最终源码重跑 |
| n3b-network-inspection-result.json | 最终consumer真实网络401/200/409/422，整份正文/清单相同、14资源/1快照，计数1；非安装/搬迁资格 |
| n3c-input-portability-design.md / n3c-input-portability-result.json | 历史真实worker与冷child机制探针揭示remap及捕获缺口；捕获API随后由11c67ab1部分实现，外部资源完整安装仍缺 |
| n3c-package-members-freeze.md / n3c-members-final.xml | b5f9f557：637通过/0skip，原成员权威迁入stdlib叶子，旧API/整份ZIP字节保持，实际循环反证失败 |
| n3c-capture-staged-final.json | 11c67ab1：冻结23文件SHA；核心24、HTTP21、receipt43、settings30、错误/i18n366各自通过。早期173联合的末修delta由核心24/HTTP21覆盖，不报最终源码重跑173 |
| n3c3a-worker-capture-validation.md / n3c-capture-python310-final.xml | Python worker137、真实workerd23、Cargo62、Python3.10实际worker23各自通过/0skip或ignore；3.10非完整最低版本套件 |
| n3c-capture-shipping-final.xml / n3c-capture-review-*.log | 发行147通过/9 POSIX/Linux条件skip；本次Ruff/format/diff、playground和插件画布build/check均exit0，未验实际安装物 |
| n3c-native-capture-lifecycle-design.md | 已核runner起止/屏障/续跑lineage及真实测试入口；只读设计，尚无native捕获实现资格 |

截图`output/playwright/n21-ui-cold-ctrlS-A.png`可见标题B与“已保存到项目”，实际tooltip/网络/落盘共同证明目标A；不凭标题猜关联。图例根因和控制实验见n4报告。更早资格/失败/跳过与原始产物保持在PROGRESS日期记录中。

## 2. 下一阶段优先级判断

保存冲突的主要用户路径与受影响构建已完成，保留其回归。现在先把项目包串成一条真实可用的搬迁流程，避免继续堆叠未接通的底层能力。图例与几何是科学图表正确性缺口，必须在完整M1之前解决。最终旧功能资格仍单列，SVG专项全绿不能证明Python原路径完全不受影响；当前不报完成百分比。

轴模型、明确数据绑定仍是计算图表方向；普通SVG不要求反推原始Python。现有一个documentStore、writer、override/事务、RenderCore/pdfbackend及legendmodel最大化复用。会话认证、worker沙盒、data_dir、隐私和prepare→verify→commit不变。

## 3. 下一阶段里程碑与完成口径

### M0：恢复核心正确性（R00–R04）

原B1–B5/F1–F8、N1–N5和已提交层序/转换/共享快照修复保留，不重复实现。现在完成原生最终场景manifest与真实消费者；模块通过不能代替M1用户流程。

### M1：第一条实际可用的纵向闭环（R05–R07，依赖 M0）

真实界面完成：普通 SVG 含两条曲线及未绑定注释 → 选择轴区/两个系列 → 导入明确数据 → 每轴独立校准 → 对照确认 → 修改范围/样式/图例 → 精确选择与撤销重做 → 保存 → 清空缓存后新进程重开 → 原图和混合画布导出。第一套 fixture 按既有 scope×format 合同逐项验收成功产物或明确拒绝：原图核验五格式的可兑现路径，画布 EPS 保持既有 `eps_not_for_canvas`。各格式用独立读取器检查，SVG 再导入只作外观对照；自包含语义恢复在 R08 验收。

两条曲线是集成验收的最小场景，不是最终范围缩减；柱、散点、误差和色条的已声明核心输出在 M0 就应修好。M1 通过也不能关闭原计划 SG05–SG09 的其他族和交互。

### M2：能力对等与三路径齐全（R08–R12）

补齐 A/C、图元与文字/分组编辑、全部适用图表族、写回、出版/预检及各入口。以 SG00 能力矩阵逐格验收；产品缺实现记 pending，不能改写成输入限制。

### M3：最终交付资格（R13）

在冻结候选树完成原功能回归、独立审查、性能与支持平台真产物资格。区分本机通过、平台未验证、待发布；没有发布授权不推送/合并/发布。

## 4. 分批任务清单

### 2026-11-07 捕获事实收口后的效果判断与最近交付目标

目前计算编辑、保存恢复和原图导出已有用户路径证据，真实v4只读检视与safe/workerd捕获已接通，完整项目搬迁仍未闭环。新增捕获解决“旧Figure事后配上新声明”的一部分根因，尚未提供新的声明界面或完整可携资源包。接下来以“在新目录恢复并继续编辑”作为最近交付目标，每一项底层改动都须接实际消费者；专项通过只授予其实际覆盖范围。截图可见的刻度边缘裁切及已证实的图例不联动继续保留为缺陷，不将其隐去或计入已完成。

本次直接查看既有旧图幅截图，曲线/运行图已可见、打包提示准确说明运行依赖尚未确认完整；坐标数字和上缘文字仍有可见裁切。截图没有覆盖新检视API，不替代原SVG/预览/独立导出产物的几何根因核对。当前没有依据估算完成百分比或整夜必完时间。

| 顺序 | 当前状态与改动 | 本批退出条件 | 提交边界 |
| --- | --- | --- | --- |
| N3c-1 | 已提交75b09b57：保留原rc_ID导入与全部新版writer共同锁 | 最终145专项/0skip、双进程、冷进程、旧字节及反证已验；保留其实际范围 | 已完成，不重复实现 |
| N3c-2 | 已提交be84ba33：闭式execution与已知资源真实导出/准确UI | 718联合/8新HTTP、56前端/正式构建、真UI与ZIP/计数已验；未宣称完整依赖集 | 已完成事实收集，不当作完整N3c退出 |
| N3b | 已完成后端严格检视：9a9843d2目标计划、1795f244流式、d1389db6全store、5e000c6f实际API | 最终68HTTP与真实网络通过；完整正文/所有资源/缺件准确，零项目写/零脚本执行。不是安装UI资格 | 已分功能commit，保留验证，不重做 |
| N3c-3a 已完成部分 | 11c67ab1：声明API/CAS、safe/workerd真实捕获与receipt；b5f9f557共享成员权威 | 真实worker/Rust/3.10、默认字节/身份及实际反证已有证据；无声明旧路径保持，不重做 | 两项已分别commit |
| N3c-3a 当前首项 | native真实start/barrier/lineage，再接preparation/launch声明过期核验 | unknown不猜rc_ID；旧图不补证；continue/迟到/EOF/项目代次不串事实；现有native编辑行为保留 | native捕获与准备/启动核验分别commit |
| N3c-3b/c/d | 完整声明图/资源与prefix→声明UI→精确ref迁移 | 实际v2包/检视/新根loader；未知通道准确；真UI声明重跑/取消/切项目保稿 | 可携资源、声明UI、引用迁移分别commit |
| N3d/e | 待N3c闭合：独占创建新项目、完整资源发布、界面采用及原writer回执 | 既有目标零覆盖；正文只经原writer，非空一次写并回读；取消/切项目/迟到/断写保旧稿；部分成功状态准确 | 后端安全安装、前端接线分别commit |
| N3f | 待整链：中文新目录、删原源、空缓存、新父进程/浏览器的混合多画布场景 | 实际打开→编辑→undo/redo→保存→冷重开→各适用导出→版本恢复；模型、INPUT、资源、几何及文字独立对照 | 实际缺陷分别fix；资格记录单独提交 |

N3完成后按依赖继续N4显式图例联动、N5复杂几何与裁切对照、N6完整M1、N7能力矩阵、N8旧功能/平台最终资格。无人值守执行按第5节记录安全checkpoint，不逐批等待确认；不把一夜结束或一个commit当完成条件。

### 后续完整执行队列

| 顺序 | 工作与依赖 | 退出条件 | commit边界 |
| --- | --- | --- | --- |
| 保留 | N0/N1/N2/N2.2、EPS完整接线及后端关联/恢复均已提交 | 不重做，不拿旧冻结SHA覆盖新稿 | 保留全部有效提交 |
| 已完成 / N2.1 | 保存关联/明确覆盖与重载/404保护均提交d7a73ce1 | 431项、真UI恢复/读取期间编辑/下一保存及双窗口已验 | 不重做，保持现共用writer/回执合同 |
| 已完成 / N4窄修复 | 预检0b611411 | 精确附件正证/完整render identity、旧spec及build通过 | 图例不在本批资格内 |
| 已完成 / N3基础 | e468d516预算/名称，336783bf旧HTTP原子输出；71e637c8回执；206a1c34预算工厂 | 204基础、18新HTTP/8旧、448前端、82末次计算包，分范围不累计 | 保留独立提交，不把基础叫完整安装 |
| 已完成 / N3a | 8ed50616实际导出/菜单与准确缺件状态 | 438联合、85前端、最终build/playground、真实完整/缺件UI及INPUT核对 | 已提交，不重做 |
| 已完成后端 / N3b | 目标投影、完整Figure语义和实际v4检视已提交5e000c6f及前项 | 最终68HTTP/真实网络；流式及store各自资格保留，尚无生产安装成功资格 | 后续只因受影响变化补验证 |
| 下一项 / N3c-3→N3d/e/f | 补native/过期核验，完整声明资源，再安全安装及UI | 保留11c67ab1及前项；公开捕获revision/partial观察不当完整；保科学身份，沿原writer一次正文与回读 | 按下表独立验收/功能提交 |
| 4 / N4 | 图例/标题/数据/readiness与逐对象重绑迁移 | 显式身份驱动，图例真UI/导出跟随；custom保留；重排/删除不套错，未匹配项显式且可撤销 | 图例合同/producer/消费者；重绑迁移；状态适配 |
| 5 / N5 | 成员paint slots及复杂clip几何 | 装饰层序不变；circle/holes/union/祖先AND/evenodd/objectBBox；距离/框选/高亮/宽描边/运动一致 | placement；共享clip及消费者 |
| 6 / N6 / M1 | 分享纯外观/完整数据选择及完整用户闭环 | 双系列+图例+未绑定注释→绑定/数据/范围/样式→undo/redo→保存→空缓存新父进程→全适用scope×format独立核对 | 分享选择；M1缺陷分别fix；验收记录 |
| 7 / N7 / M2 | A可信来源/C自包含、文字/分组/use、剩余科学族/多轴/投影、事务写回与全入口 | 按SG00–17逐格验；写回全量冷重放+几何+像素，失败409原件零改 | 每个可验收功能分别commit |
| 8 / N8 / M3 | 冻结候选完整回归/等价性/CompatBench/E2E/性能/平台安装物 | 无未解释新增丢编辑/错图/回归；既有Windows PID失败实际处置；缺平台列未验证 | 缺陷分别fix，最终资格/交付文档 |

#### 已完成N2.1：保留已提交功能与验收

N2.1完整20文件及3份规则/ADR已提交d7a73ce1；明确恢复后端d7b6eef9保持。原先“待接HTTP/前端未冻结/旧EPS app已stage”的指令均过时，不再按旧SHA覆盖当前源码。

真实用户证据包括：A改标题B仍写A；空缓存/新后端恢复；外改named A→CtrlS409且外部字节/原revision保留；明确覆盖/重载（实际GET暂停期间真实F2编辑进入恢复槽）及下一普通编辑；两个独立浏览器新actor/旧base_axis触发409，双方本机/磁盘稿受保护。现代UI基于冻结完整恢复bundle；最后仅404链保护和共用回执reader另以最终测试/build证明，不扩大其浏览器资格。

404兼容reload备份失败P1原探针确实丢等待期新编辑，修后停止安装，最新431项/正式build/i18n/lint与两反证通过。后续71e637c8抽共享回执，最新448项/正式build及10反证通过，playground指纹7f69cc026d69e1f4；不把不同阶段组相加。保存规则仍一个store/writer、只有404降旧链、named revision不洗白、固定recovery proof、完整作用域守卫。

#### N3：完整产品接线是当前主线

按下表实施，不将整批N3拖成一个巨型commit。修改前先读对应层规则；共享app/模型/manifest/同源合同由主代理唯一写入，独立资源闭包和测试可并行。N3a已提交8ed50616，N3b目标叶子已提交9a9843d2；旧“前端候选未接真实后端”状态作废。

| 子批次 | 改动落点与依赖 | 必须完成的验证 | 建议功能commit |
| --- | --- | --- | --- |
| **N3a：真实v4导出，已完成** | 8ed50616接`POST /api/documents/v4/package`与同一菜单；完整INPUT/全画布、普通file/登记script/runtime描述符/已核配对；旧三参及schema2/3保持 | 实际完整/缺件UI、严格ZIP/新父进程重放、坏源/磁盘/同名/认证/重复ref/越界已验；最终438联合及构建通过。完整执行依赖仍由N3c负责 | 已提交8ed50616，保留资格，只有受影响变化才复测 |
| **N3b：严格检视与目标计划，后端已完成** | 5e000c6f显式v4检视消费原目标计划/流式reader/全store语义；旧open保持只读2/3，历史owner不变当前授权 | 最终68HTTP及真实401/200/409/422，缺件回归和故障反证已验；全store31权限skip准确记录；UI及安装留N3e/d | 已提交9a9843d2/1795f244/d1389db6/5e000c6f |
| **N3c：可携运行与资源闭包，部分完成** | 75b09b57/be84ba33配置/已知资源保留；11c67ab1已接safe/workerd捕获。余项native/过期核验、完整资源、UI、ref迁移分批 | 真实新根消费动态数据/helper/remap/原rc_ID与快照；明确声明完整项目可恢复，未声明继续incomplete。环境/授权/成功信任不复制，导入不自动执行陌生脚本 | 已完成批次不重做；余项见下一节 |
| **N3d：安全安装到独立项目** | 依赖N3b/c。默认独占创建新项目根，保留全部相对科学身份；抽取既有排他目录reserve与原子发布能力。`create:true`和可跳文件的copy_project不能充当严格安装器 | 原目标绝不覆盖；资源逐份读回/摘要/语义/真实重放；同名、并发、源变化、断写失败可恢复；只有完整资源闭包合格才允许正文安装 | `feat(packages): install verified v4 projects without clobbering` |
| **N3e：现界面安装与落盘回执** | 依赖稳定的N3d合同。复用projectStore的prepareDocument/switchTransaction、openDocumentAs及waitForWorkingDocumentSave；不接旧relink清除edits，不增第二writer | 非空正文一次原writer写入并实际回读；空稿显式persistEmpty；取消、切项目、迟到、超时、资源成功但正文失败的状态准确，旧稿保留；中文/i18n和真实浏览器 | `feat(packages): adopt installed charts through the document writer` |
| **N3f：搬迁整链验收与收口** | N3a–e均接通后冻结候选；混合普通素材/Python runtime/快照/计算SVG、多个画布，中文新目录或移盘 | 删除原目录、清派生缓存、全新父进程/浏览器后继续编辑、undo/redo、版本恢复及全部适用scope×format；独立比较模型/INPUT/几何/文字/PPI。按影响补playground和旧包兼容 | 实际缺陷分别`fix`；资格记录单独commit，全部退出才标N3完成 |

资源缺失口径已在N3a冻结：已声明计算来源无法核验时拒绝发布；普通素材/脚本/执行配对缺失保留显式missing与准确UI状态，允许备份但不标为完整搬迁。N3c的“依赖集合未声明”不是假资产，不塞入missing凑数量；应有独立闭式coverage事实。不要为规避冲突重写panel.fileId或用户Python。

1. **基础不重做**：旧包api_package已以336783bf接staged_package，data_dir持快照temp到输出结束、canonical已核源/原ID、精确预算、CRC与不可覆盖发布。新18HTTP/原8均通过，故障/并发/真实junction/NFC已验。e468d516保持旧名显式writer与默认严格reader、ZIP64；普通旧容量不新加低cap。
2. **v4导出与入口已接通**：8ed50616真实显式v4端点进入正确format/认证namespace；复用完整来源冻结与chartpackage.write_chart_package(budget_factory=packagebudget.output_budget)，内部唯一manifest不在app复制。全画布完整INPUT、普通file/登记script/runtime描述符/已核Figure配对已携带；缺件准确状态、原三参wire和旧open保持。响应为name/url/assets/missing加document_format='v4'及去重INPUT数chart_sources。真实菜单/严格reader/删源新父进程render/export资格已有，不重复实现；registry、实际数据和执行上下文仍在N3c完成。
3. **检视与资源方案已接**：旧package/open保只读2/3语义。5e000c6f真实v4检视核闭式包、CRC/路径/摘要/预算、全部资源和语义引用；普通资源流式，全Figure树含未引用blob核安全SVG及单页PDF。统一目标计划裁决跨namespace冲突。声明只提供缺件候选，不变本机读权；原coverage保留但完整声明未核时明确缺口。新声明资源接入时扩同一消费者，不新建平行检视器。
4. **身份保真与目标目录**：作为新项目完整安装，保相对sourceId/script/runtime及原运行配置身份；remap变化时snapshot ID按已核oldRef→newRef映射更新，不假称内容身份相同。复用现projects/open与projectStore.adoptOpenedProject(status,{prepareDocument})/switchTransaction在ready前安装文档。projects/open create=true会复用既有目录，不能作排他占位；copy_project可skipped>0成功，不能当完整安装证明。现有目录冲突不可仅改panel.fileId，须真实引用迁移或明确新目标；所有旧文件/正文零覆盖。
5. **已知执行事实已交付，完整闭包仍缺**：75b09b57/be84ba33已落实execution v1、保外来ID导入和真实导出，注册关系/已知CSV/helper/argv/internal file remap由正式HTTP和冷进程证明。静态expected漏动态CSV、外部改指仍有观察盲点，receipt完整不等于依赖集合完整，故当前有脚本包准确incomplete。接续补可核完整声明/外部与prefix资源及快照remap语义，不能永久只支持简单小包；不复制本机环境/cwd授权/成功执行信任，不把新版共同咨询锁称跨版本CAS。
6. **实际单次正文提交**：资源准备/安装先验证及真实重放，再通过原openDocumentAs/项目prepareDocument安装，等waitForWorkingDocumentSave的真实revision/正文。非空不补第二PUT，空稿明确persistEmpty一次；超时、元信息失败、用户取消/切项目/迟到响应准确分列，不再复制资源或覆盖named。新包不继承发送者本机绑定/绝对路径；持久回执叶子不代表API/UI已接。
7. **完整退出**：真实HTTP+新父进程在中文新目录/移盘、原目录删除下冷开→编辑/undo/redo→全适用导出→版本恢复；混合普通素材/脚本/runtime/快照实际落齐、摘要/语义核对。坏包/缺件/断写/同名/资源变化保旧资产与正文；取消检视零项目写入。通过后再标N3完成。

#### N3c-3：从已提交safe捕获接续，补齐可携完整声明

判据主语：**某一次真实execution_id/native epoch产生的Figure，计算当时所用的明确声明及全部文件内容**。不是导出此刻的文件，也不是用户后来补的清单。机制研究见外部证据`n3c-input-portability-design.md`及主worker、prefix/view、binding-time三个probe；它们不是产品完成资格。

已证根因：当前`databinding`在导出时重算，`binding.revision`不进入receipt identity；准备流程可将当前binding配给旧热图。remap fingerprint包含机器绝对目标，换根使旧snapshot ref精确失配。partial observer还会漏外部读取。必须建立捕获时事实和明确的oldRef→newRef事务，不能降低原loader身份门或事后给旧归档补签。

| 子批 | 具体工作及生产消费者 | 独立退出条件 | commit建议 |
| --- | --- | --- | --- |
| N3c-3a 捕获事实，部分完成 | 11c67ab1已接认证声明API/私有CAS、safe/workerd真实起止与冻结胶囊、receipt可选public revision。下一项native真实epoch/lineage与preparation/launch过期核验；`binding`语义不改 | 缺省JSON/identity对拍与safe/workerd反例已验；native真实start/barrier/continue/EOF、未知参数、旧图不补证仍须验证。公开revision入快照不代表完整声明图已入包 | safe已提交；余项按下表两批commit |
| N3c-3b 可携资源 | execution版本化扩展，不向闭式v1偷偷添字段。资源SHA/字节仍用原manifest/ResourceFile；collector与实际inspect接同一声明。外部file/prefix来自现明确选择，prefix完整列成员、拒越界symlink/junction/别名。考虑原路径存在优先，sandbox相对输入视图支持真实open/exists/glob/native库读取；绝对原生路径先做可兑现适配实验 | 动态CSV/helper、原精确argv/rc_ID、外部及完整prefix真包往返；成员增加/缺失/篡改拒绝；仅检视包中旧绝对字符串零外部读取。声明文件闭包与全通道执行依赖准确分列 | `feat(packages): carry verified declared inputs and prefix resources` |
| N3c-3c 声明UI | 复用现准备/配置/选择器与i18n，显示明确输入和未知通道，用户确认后按现授权真实新执行；修改清单撤销旧资格，旧图可作对照但不显示新声明已捕获 | 实际浏览器完整声明→新执行→真实导出/inspect；取消/切项目/迟到/失败保原稿；未声明或旧执行仍准确incomplete，不默改用户Python/运行参数 | `feat(inputs): declare portable inputs through the preparation flow` |
| N3c-3d 精确搬迁计划 | 将经核capture声明/旧ref与新本地资源、实际新remap fingerprint绑定；复用public svgstore.save在自有stage生成oldRef→newRef，保source/rc/argv/patch/receipt/配对字节，snapshotId随内容身份正确改变。多历史版本保archive-only，不把历史marker装成当前成功事实；与N3d事务接线 | 新根新进程精确loader查询成功，篡改输入或prefix成员拒绝；同图多patch/多版本按完整ref映射，不只按sourceId；无捕获证据旧包不自动升级，原权限门完整保持 | `feat(packages): plan verified snapshot references for relocated inputs` |

##### 下一阶段首两批：native捕获与准备/启动过期核验

先读证据根`n3c-native-capture-lifecycle-design.md`。它定位了真实接口和现有测试，未实现该功能。pure stdlib `inputdeclaration`仍是唯一freezer，不另建native SHA/清单/资源预算权威。共享合同由主代理先冻结，然后才拆独立runner/session验证任务；同一文件只有一个写入者。

| 批次/顺序 | 修改落点与实现要求 | 必须看到的退出证据 | 功能提交 |
| --- | --- | --- | --- |
| 3a-2a：冻结native上下文 | `declaredinputs`/`inputdeclaration`与既有`execspec`、`preparation`、`desktopproc`。明确实际desktop cwd及known RunSelection如何匹配声明；`spec.cwd_mode`的native sandbox占位不能冒充真实cwd。terminal非空参数当前无经核rc_ID，保持unknown；明确无参数也须实际正证。沿原私有IPC传候选，不从argv推权限 | 默认无声明payload/argv/owner完全兼容；中文/脚本子目录/原配置、未知参数、未批准输入及候选失配反例；原生执行权限与声明资源授权分开 | 与3a-2b作为一项可用native功能提交，避免只交未消费helper |
| 3a-2b：runner/session事实链 | `bridge_runner.run_script`在实际open/read/compile之前begin，或绑定实际编译字节；真实user start关联execution_id，每次barrier在engine reads/rebase前finish。`nativesession`按execution_id+snapshot_epoch接事实，`receipt.from_native_session`只消费已冻结正证。continue延续初始lineage，不能给旧Figure新建证明链 | 真实两次show/continue、同长度数据变更、修改声明、旧无证图中途声明、编译失败、`-m`父包提前屏障、EOF/部分发送/迟到回复、owned零输出结束均覆盖；无声明native编辑/导出行为保持；新增核心判据实际反证 | `feat(native): capture declared inputs across execution barriers` |
| 3a-3：准备与启动消费 | `preparation`/`launchctx`/app只复用捕获revision及原执行权威。计划绑定原声明代次，实际启动核同一候选；捕获后改声明不能把新候选与旧热Figure拼接。项目切换/关闭代次、source/run/cwd/remap变更撤当前资格，历史不可变receipt不重写 | 真实plan→声明变化→launch拒旧/明确刷新；ensure_built旧Figure不能获新正证；迟到前项目结果不进入新项目；未声明旧合同保持。已有safe/workerd末次证据只对受影响delta补测 | `fix(preparation): reject stale declared input capture plans` |

随后3b必须让完整capture图与原manifest资源共同进入版本化execution并被正式export/inspect消费；目前receipt只携`{version,revision}`，不能据此将coverage置complete。3c接真实声明界面，新执行取得证据后才能显示已捕获。3d与安装器共享完整oldRef→newRef计划。绝对原生路径若出现兑现冲突，按第三路径审查先实验结构方案，不能悄悄只支持open hook或禁用正常native路径。

用户可见验收优先：上述证据链完成后立即串3b/c/d与N3d/e/f，交付中文新根真实恢复；不得继续扩大未消费的底层helper。N4图例和N5文字/刻度裁切的独立根因实验可在不争用共享文件时并行，最终M1之前必须收口。

主代理先冻结共享合同并唯一写入app/receipt/schema/集成；合同稳定后可委派独立资源枚举、UI、原默认字节对拍或真实控制面验证，文件所有权互斥。每批接实际消费者，不只交未调用helper。完成一批便同步台账继续下一就绪项；需要跨批生产接线时调整边界，使每个提交可审查/可验证，不能先提交一个永久未接叶子。

#### N4：预检窄修复与图例结构分别收口

- 预检已提交0b611411，78/33/49及真实response/旧128spec对拍和统一build/playground已验；保留该资格，不重做，保持exact完整身份正证，不因chart字段存在就豁免缺素材。
- 图例真实根因是Binding只表达axes/series，producer没创建Legend/ownership。先冻结可持久化显式旧SVG区域→图例条目→系列/custom关系，版本与严格镜像/向量同步；再由现legendmodel管理跟随/脱开/handle_*，按原paint slot组合。不能按颜色、标签、列表顺序猜，不能另写SVG重染。
- 图例未绑定时保留装饰；绑定确认一次可撤销事务。同色/同名、多系列、bar/errorbar复合句柄、范围/数据/结构更改、冷恢复及导出都需独立证据。
- readiness消费同一后端来源/能力事实；逐对象迁移保持科学身份，未匹配项显式、可撤销，不把现归属guard当完整迁移。

#### N5–N8：保持原完整目标

成员placement保留source slots、group opacity/clip与native成员顺序；复用SVGIR Path/ClipUnion统一复杂clip，圆外/孔洞负例必须实际拒绝命中。截图刻度边缘裁切先比原SVG/最终产物确认主语，不凭截图做错误补丁。

纯外观分享不暗带数据，完整模式包含模型/数据/全部编辑。M1沿完整双系列场景验用户闭环，立即继续M2适用能力矩阵与M3旧功能/支持平台资格；不因一夜结束、一个commit或专项全绿缩减范围。原R00–R13详细任务保留如下。

| 批次 | 目标 | 依赖 | 对应原批次 | 推荐 commit 边界 |
| --- | --- | --- | --- | --- |
| R00 | 核对最新实施基线、保留证据、恢复验收台账 | 无 | SG00/01/16 | `test(svg): preserve independent composition regressions` |
| R01 | 统一节点身份及坐标空间 | R00 | SG02/03/05 | `fix(svg): preserve scene identity and composition coordinates` |
| R02 | 完整语义对象与组合所有权 | R01 | SG06/07/08/09 | 按轴、系列、色条分 `fix(svg): ...` |
| R03 | 不可变来源、执行边界及完整渲染身份 | R01/02 | SG02/03/10 | `fix(svg): compute from immutable chart revisions` |
| R04 | 向导状态机防串与表单合同 | R00；最终对接 R03 | SG06/10 | `fix(svg): invalidate stale chart binding requests` |
| R05 | 保存、资源附件、迁移与冷恢复 | R03/04 | SG11 | `feat(svg): persist complete chart revisions safely` |
| R06 | 真绑定 UI、现有编辑器/命中/事务接线 | R02–R05 | SG06/10 | 按向导、渲染、操作分别 `feat(svg): ...` |
| R07 | 冻结导出与 M1 全链验收 | R05/06 | SG12，追验 SG07/10/11 | `feat(svg): export frozen computational chart artifacts` |
| R08 | 可信 Figure 采用与自包含导入 | R03/05/07 | SG04，A/C | 分来源采用、语义往返两个 commit |
| R09 | 文字、分组、实例与图元编辑完整接入 | R01/03/06 | SG05/10 | 按文字、分组、实例分 commit |
| R10 | 剩余图表族与科学语义补齐 | R02/06/07 | SG07–SG09 | 每个系列/坐标族一个可验收功能 commit |
| R11 | 原件事务写回与冲突恢复 | R05/07/08/10 | SG13 | `feat(svg): verify chart writeback before commit` |
| R12 | 预检、样式、入口、插件和发行依赖 | R07–R11 | SG14/15 | 按能力适配、transport、打包分别 commit |
| R13 | 完整回归、性能、平台资格与交付 | R00–R12 | SG16/17 | 缺陷分别 fix；最后提交验证记录 |

依赖安排：可提前做导出候选实验和独立语料；共享 ChartModel、Binding、app.py、document.ts、golden vectors 保持单一写入者。按用户最新授权使用多子代理并行处理独立模块；共享接口由主代理统一接线，独立验证使用不同输入/公式/读取器，不重复相同昂贵套件。

### R00：基线、反例和架构假设

- [ ] 查当前实施分支最新 HEAD、worktree和未提交改动；保留 `7cbb0e6e` 以来的所有功能提交。fetch 成功才记录最新远端 SHA，失败记录原始错误，不以缓存引用冒充最新。
- [ ] 按用户最新指示复用当前实施分支及工作树，不新建或切换分支。若 HEAD 已前进，在其最新有效状态继续并同步受影响文档；需要整合主干时保留实现历史和未提交改动，解决冲突后验证，不从 main 重做。
- [ ] 核验解释器实际导入当前 worktree；字体和依赖沿用项目入口。测试解释器不能硬编码 `.venv/Scripts/python.exe` 给所有平台；从测试运行环境/现有 fixture 取得。
- [ ] 将 B/F 反例转成维护性测试，保留独立几何/文本/颜色预期和故障时序；每个修复提交携带其对应回归。不把一整套已知失败测试提交成“通过”里程碑。
- [ ] 更新 SG00 能力状态，保留历史结果；基线全量历史红用同机独立干净基线作对照，不能凭文件路径/CRLF 猜归因。
- [ ] 针对“合成保真与计算正确兼得”复核 ADR 0166；按第三路径审查记录不变量、共同根因、候选、实际实验和未解问题。多实例实验必须运行，像素指标必须有事先解释的阈值。

退出：输入、SHA、真实失败点和正确预期可重跑；每个缺口有负责人批次；旧 passed 不再驱动错误跳步。

### R01：稳定场景与完整变换

- [ ] inventory、绑定、合成、持久化共用稳定节点身份。可采用版本化解析场景和源位置映射，但先用无 id、重复 id、多实例重开实验选择；不能只在原 SVG 原地补 id。
- [ ] 将片段自身坐标 → 源 SVG 用户坐标 → 被替换节点父空间表示为明确可组合变换；保留已验证的单位换算，补全原点/父空间映射，不为修边界重做正确部分。不要既烘焙坐标又重复应用祖先 transform。
- [ ] 同一变换用于路径、text、marker、clip、defs/use、image；保持 paint order 和组透明语义。
- [ ] 覆盖 px/pt/mm、非零 viewBox、meet/slice/none、嵌套缩放/旋转/翻转、use 实例引用、重复拷贝和共享 defs。实例修改不污染共享定义或另一个实例。

退出：B2/B5 消失；最终 SVG 实际路径和独立点位对照通过，非目标节点和未绑定装饰保持原语义；不能只验证变换 helper。

### R02：语义所有权与最终产物

- [ ] 优先修复本轮两系列/双轴节点的白底遮挡：明确背景、网格、系列、轴家具的角色与绘制顺序，节点移除后插入锚点仍稳定；最终可见曲线和独立点位均须通过，不能只查 path 存在。
- [ ] 建立轴、系列、刻度、图例、色条、数据锚定注释与 SVG 区域的明确映射，绑定构造端和消费端执行同一合同。跨区域 clip/defs 引用可达且无冲突。
- [ ] 生产脚手架时给完整语义对象明确标识与所属轴；合成时据身份取得全部成员。禁止猜 `line2d_` 前缀、取第一个 axes、按遍历位置配系列。
- [ ] 轴范围/scale 改动同时更新主次刻度、offset、轴标签、数据几何及裁剪；保留独立装饰，不叠画两套坐标。
- [ ] line/scatter/bar/errorbar 保留完整 artist 组合，系列样式与图例联动；颜色条与 image/norm 共同更新，以实际布局后的轴区计算 manifest。
- [ ] 覆盖多系列不同长度、缺失值/NaN 间断、线宽等样式实际生效；明确不合法数据报错，不静默改成别的系列种类。

退出：B1/B3/B4 在最终产物和 HTTP 消费路径通过；两系列、带误差与端帽、热图 norm/colorbar 都有独立输出断言。引入人为错轴/删误差成员/保留旧色条，门禁必须在目标断言变红。

### R03：不可变计算版本与执行协议

- [ ] 一次读取并验证源字节，建立资源快照；整个计算、组合和 manifest 使用同一版本。源文件在计算期间改变时不能混合新旧字节，后续提交须验证来源冲突。
- [ ] 完整版本身份覆盖源及资源、模型、绑定、数据、语义 edits/视觉覆盖、字体、引擎/转换器与输出相关配置。只放影响对应输出的内容，复用仓库现有权威合同，不建平行缓存规则。
- [ ] 结果返回可追溯的 render revision、模型/绑定身份、SVG 和同版精确 manifest；后续 renderStore、保存和导出消费该身份。
- [ ] 复用既有执行隔离、用户环境/内置 runtime 选择和认证；复核当前直接 subprocess 路径是否满足这些合同。父进程不导入 matplotlib，输入/输出/内存/超时与取消有预算。
- [ ] 做来源计算中变更、环境不同、源同名、两实例同数据异样式、进程失败和取消的实际试验。稳定错误码和前后端镜像同提交。

退出：新旧来源不会被拼为一个成功版本；同数据异范围/样式不能共用错误缓存；失败不污染文档或原件；已有 Python safe/native/runtime 路径不回归。

### R04：表单表达与向导状态机

- [ ] 实现项目/面板/源/请求代际，取消、关闭、重开、切项目后旧响应均不可写状态。测试 inspect、bind、compute 的乱序和失败，而非只测正常顺序。
- [ ] 区分草稿、已绑定模型、已预览版本、最终可提交版本；表单/数据/来源变化使下游资格失效。允许展示旧图作对照时必须明确其身份，不能把它当当前成功预览。
- [ ] x/y 独立 scale、单位和校准；多系列节点与列映射、类别轴、误差/网格字段与后端一致，不先硬编码一个系列再绕过合同。
- [ ] 取消不写文档，确认只产生一次文档事务；失败、源冲突或过期预览不能进入最终确认。

退出：F1–F3 正式回归通过；多系列及半对数请求符合真实 API 合同；项目切换后的迟到响应不改变新项目。

### R05：持久化与冷恢复先于真实保存入口

- [ ] 确定 schema 版本和旧客户端防损策略；用旧版本程序实际读/保存实验，不能靠“未知字段仍在 JSON”推导安全。
- [ ] 保存原 SVG、必要字体/图片/数据附件、模型/绑定、版本与来源；使用现有项目包、data_dir 和资源生命周期机制。
- [ ] 多文件保存有唯一可恢复提交点；中断/磁盘失败后能保留旧版本或恢复新版本，不能只顺序 replace。
- [ ] 数据改变、附件缺失、来源移动/失联、引擎版本变化均有可恢复路径；项目迁移不依赖绝对本机路径。
- [ ] 撤销/重做、时间线、恢复使用相同语义版本。关闭应用、清空派生缓存并换新进程后可重建，不读取旧热态结果冒充恢复。

退出：模型/附件/实际显示的新旧版本一致，异常保存原件不变；旧 Python 项目和静态 SVG 素材仍正常；C 路径后续可复用本合同。

### R06：用户入口、渲染和编辑操作

- [ ] 复用既有 Dialog、菜单/Inspector、i18n、transport 和 documentStore。完成导入→轴区/系列/数据→对照→确认/取消的真实流程。
- [ ] 计算来源接入 renderStore 和 PanelView；统一 SVG、manifest、命中与图层版本，保持既有精确几何/预览边界；不在前端重新解析 DOM 充当几何权威。
- [ ] 范围/刻度、系列样式、图例位置、标题等操作进入现有能力与属性体系，单选/多选/平移缩放后的变换保持一致。
- [ ] 撤销重做和临时预览分离；确认后立即编辑、切项目、同源两个面板不同改动均不串状态。
- [ ] 真浏览器 E2E 用实际后端和独立 fixture；不能 mock bind/compute 证明产品可用。保存/重开直接使用 R05 正式接口。

退出：M1 用户操作完成到保存冷重开；对应 spec 实际被测试项目收集。截图只是辅助，数值、几何、文档内容也要断言。

### R07：冻结导出与第一条完整交付

- [ ] 将同一计算版本接入现有 export request、source resolver 和 RenderCore；原图、原图标注、画布范围都消费同一冻结身份。
- [ ] 落实 SVG→现有渲染合同的真实适配方案，先验证复杂度/字体/透明/clip/资源语义；复用成熟实现时核对版本、许可、平台和打包，不盲目手写标准子集。
- [ ] SVG/PDF/PNG/TIFF/EPS 按既有 scope×format 合同逐格验证成功/应失败结果；PDF/可表达 SVG 保留矢量与文字，栅格格式遵循 PPI。为普通计算 SVG 的原图 EPS 验证可兑现路径；画布 EPS 保持现有 `eps_not_for_canvas`，本计划不新增画布 PostScript 能力。EPS 的真实表达限制按元素说明并按第三路径审查，不能按“没有 Python 来源”对原图全局拒绝。
- [ ] 覆盖透明组、中文/旋转文字、跨 defs/use、图像/色条和 Python+SVG 混合画布；导出字体、位置、数据语义和预览一致。
- [ ] M1 的两系列图在全新进程导出，各格式用独立读取器核验；SVG 重新导入作外观比较，继续计算的自包含语义往返留给 R08，不新增 EPS 等格式导入。原文件保持不变。导出中途编辑不会混入新模型；取消/错误不遗留伪成功文件。

退出：M1 所有步骤有真实产品证据；第一套 fixture 的五格式与两种范围按合同取得预期成功或明确失败；其他图表族扩展仍留在 R10，不称 SG12 全部已完成。

### R08：来源采用与自包含往返

- [ ] 把 `svgadopt` 接入生产入口，用执行回执/归档验证来源；同名 `.py`、复制 metadata、过期来源不能自动采用。
- [ ] 保留 safe/native 与运行授权语义。导入和冷预览不执行陌生脚本；自定义 Python 行为继续走原来源。
- [ ] 纯数据元数据可声明自包含或仅引用，枚举携带的数据范围；外观导出不意外泄露隐藏点或完整样本。
- [ ] 验证模型重放与可见 SVG 一致；外部软件改图但保留 metadata 时进入明确对照，不静默覆盖新图。
- [ ] 在原 Python 不存在、缓存清空时跑通真正自包含 C；仅引用来源的文件准确要求所需输入。

退出：A/B/C 各有真实入口与往返证据；伪造身份不获得执行权限；不能以 C 文件只显示旧图认定计算恢复完成。

### R09：图元、文字与分组适配

- [ ] `<text>`/`tspan` 的文字、字体、旋转、基线与现有词汇/样式一致；转曲文字作为路径，不虚构字符。
- [ ] 拖动、缩放、多选、分组/取消组合保留累计变换和组透明；未绑定装饰可以编辑，绑定对象保持科学关系。
- [ ] “进入组/取消视觉分组”不等于删除数据绑定；解除计算绑定需显式、可撤销并保留模型历史。
- [ ] 复制对象重映射 ID/引用；只改一个 use 实例不误改所有实例。对准实际绘制几何做精确命中。

退出：文字、分组和实例操作经过热态、撤销、保存重开、导出对拍；SG05 的核心 harness 与产品追验都有证据。

### R10：图表族、复杂轴和科学关系

- [ ] 对照 SG00 清单补齐横向/分组/堆叠柱、面积、误差、统计族、图例关系及现有可编辑系列；逐族明确原始输入与统计定义。
- [ ] 补齐图像/网格/norm/colorbar，多个轴、共享轴、双轴、inset、极坐标和 3D 的适用路径；有输入但无模型适配的项继续记实现缺口。
- [ ] 注释区分 data/axes/figure 锚定；轴变换只移动应移动的对象。日期/类别/线性/log、倒轴与缺失值保持科学含义。
- [ ] 每一族同时验最终图、用户操作、冷恢复和导出，不只新增 scaffold 冒烟；前后端能力、golden、命中与错误闭集同步。

退出：能力矩阵无“缺实现却记缺数据”的格；无法从普通 SVG 唯一恢复的内容提供显式补充入口，自定义 Python 能力由可信来源承担。

### R11：原件事务写回

- [ ] 复用 prepare → verify → commit；对新来源扩展同一合同，包含模型/资源和完整版本身份。
- [ ] verify 必须全量冷重放、几何比对和像素门，任一失败返回冲突且原件零改动；不能以 metadata hash 相等代替图像一致。
- [ ] 写回期间原文件变更、解释器失效、资源缺失、中途失败和恢复均可解释；备份/回滚不损坏原件。
- [ ] 真实 fixture 验热态所见 == 写进文件的 == 重开后重放出来的，并分别验证原 Python 写回仍满足原合同。

退出：删除任一 verify 关键步骤会触发对应反证失败；失败不留下部分写回的新模型/旧 SVG 组合。

### R12：产品能力、入口和发行接线

- [ ] 预检、样式、期刊/出版规范、问题定位和自动修复使用当前 chart 能力与版本，不按“是否有脚本”猜能力。
- [ ] 中文/英文、错误码、诊断脱敏、状态与帮助同步；不发送用户脚本、路径、数据或图中文字。
- [ ] 桌面拖放/选择器、CLI/handoff、MCP/内嵌画布、playground 使用现有协议和同源入口，保持其他文件类型优先级与授权边界。
- [ ] 依赖进入 wheel/桌面包/浏览器对应产物；真实离线启动验证，不要求用户预装系统转换器。更新 feature registry 和实际 E2E 收集范围。

退出：各适用入口实际完成编辑保存导出；出版/预检与编辑能力一致；仍不扩大支持矩阵或授权发布。

### R13：最终资格与交付

- [ ] 冻结候选 SHA，按原计划第 7 节和仓库验证链运行完整套件、等价矩阵、不变式、CompatBench 与 E2E。必要的工作区合并态也要重新验证。
- [ ] 中/英文 Chromium、WebKit 用 `--list` 核对关键 spec 确实收集后运行；浏览器测试不代替桌面 WebView/IME 真机格。
- [ ] 性能测小图、10k/100k 节点、use 实例、大数据、长历史、并发和取消；先定基线和阈值，失败后不放宽到“通过”。
- [ ] 按当时支持矩阵检查 Windows/macOS 相应架构及 Linux 实际口径；无条件的格标 `unverified_platform`，继续可做工作，不冒充全平台完成。
- [ ] 独立审查静默错图、丢编辑、冷恢复、导出和平台差异；修复后定向复验。确认原有功能清单未删除、无新增失败。
- [ ] 交付分支/commits、使用步骤、测试 SHA 与结果、样例产物、遗留项和恢复方法；区分“代码完成”“本机通过”“资格通过”“待发布”。

退出：原计划 SG00–SG17、A/B/C 和能力矩阵全部满足对应条件，才能宣布最终可用。某个平台/外部条件未验，明确写缺口，不用本轮时长或绿灯数量替代。

## 5. 验证矩阵与无人值守纪律

| 被验主语 | 必须观察的结果 | 不能用来替代的证据 |
| --- | --- | --- |
| 最终组合 SVG | 真实路径/文字/色条/误差成员与独立预期一致 | scaffold facts、存在 path、文件变大 |
| 当前面板当前版本 | SVG、manifest、模型、绑定、资源身份一致 | 文件名相同、data_digest 相同 |
| 确认事务 | 正好一次有效模型写入，可撤销；过期/取消零写入 | ready 按钮可点、mock API 成功 |
| 冷恢复 | 新进程、清缓存、移动项目后从正式资源重建 | 同进程再次调用、读热缓存 |
| 五格式导出 | 按 scope×format 合同取得预期成功/拒绝，成功产物使用同一冻结版本 | 浏览器下载成功、只查文件头 |
| 写回 | 全量 verify 后原子提交；失败原件不变 | schema/hash 验证、只测 prepare |
| 既有功能 | 当前候选完整回归无新增失败 | 新增 SVG 专项绿、删除旧登记 |
| 平台资格 | 支持平台真安装物/真实入口证据 | 开发树 browser E2E 或单平台 build |

执行循环：选择一个最小完整功能 → 读相关规则与当前代码 → 先复现/规定正确输出 → 实现 → Ruff（Python）→ 针对性测试 → 受影响构建/集成检查 → 必要反证与独立复核 → 检查 diff → 按功能 commit → 更新 PROGRESS 当前状态及追加记录。整个候选完成后运行完整要求的检查；之后仅新改动、失败或未解问题触发额外复测。

每次 checkpoint 记录：基线/当前 SHA、负责批次、实现与用户效果、命令及真实退出码、passed/failed/skipped、产物位置、仍未实现项、下一条可执行任务、自己启动的进程和资源。不能从日志子串计数代替测试报告。

不重复请求常规可逆步骤授权；不做无关重构、不删除用户改动、不改认证/安全边界、不替真实用户确认科学数据。困难或一轮结束不是结束标准，继续推进可执行工作。需要真实外部输入/权限/平台时记录具体阻塞并继续独立任务；不要绕过真实阻塞伪造完成。任务可以阶段性交付，但 pending 继续保留。

Windows 使用 PowerShell 原生命令和任务专用变量；运行数据/配置放隔离 data_dir，进程只按本会话 PID 清理，不杀其他会话服务。分支不覆盖，失败环境不反复破坏依赖；有证据后修根因。没有远程操作授权不 push、建远程 Issue/PR、合并或发布。

### 5.1 整夜执行合同与启动预检

目标是**无人值守完成全部可执行工作**，不是每完成一批就请求“是否继续”。启动即按 R00–R13 和依赖持续推进；M0、M1、M2 仅是自动验收点。没有用户指定的截止时间，不自行设“只工作一小时”“做到 M1 就交接”；全部目标提前完成则直接交付，不为凑满一夜空跑。

启动时一次性核对并记录：

- 当前分支、干净/未提交状态、已有实现和文档提交、任务 worktree 的占用情况；一个文件同一时刻只有一个写入者；当前授权多子代理共用实施树时必须先登记文件所有权，由根代理统一接共享契约与提交。会话已拥有的工作树可恢复使用，不能每轮新建分支后遗失上一轮差异。
- 可用工具/权限、网络与远端读取、依赖和字体、磁盘空间、实际解释器导入位置、浏览器/测试入口；沿用已有项目入口，不重复安装正常依赖。
- 隔离数据目录、证据根目录、服务启动命令/端口，以及已有任务进程。运行服务要后台可控且输出落盘，避免打开等待人工点击的窗口。
- 将“当前可做”“真实缺外部条件”分别登记；fetch 认证失败不阻止本地正确性修复，但远端新鲜度仍待补。缺 macOS 不阻止 Windows、浏览器和无平台依赖的实现/检查。

本计划规定代理在有效执行会话中的行为，不是常驻调度器。机器休眠、会话终止、工具不可用或运行额度耗尽会中断执行；提示词本身不会自动唤醒机器、恢复已结束会话或增加额度。本轮没有设置定时任务，也没有修改系统电源/安全设置。执行所需会话和机器需保持可用；中断后按第 5.5 节续跑，不承诺一夜内必然完成所有跨平台资格。

### 5.2 自动选取下一项工作

每完成一次提交/验证或遇到阻塞，重新执行下面的选择规则，不等用户回复：

1. 优先修复本次改动引入的回归、错图、丢编辑及身份混用；本批退出条件全部有证据后才标通过。
2. 优先从第 4 节最新日期的执行队列选择就绪任务，再结合原 R 依赖图推进；已通过的子项不从头重做。大批次拆为最小可独立验证的功能，完成后立即选下一项。R04 的独立状态机修复不必等待后端全部结束。
3. 某子项因外部条件不可做时，只阻塞该子项及真正依赖它的消费者。选择其他就绪项；可提前做已允许的独立语料/实验，但不越过未验证合同接线或编造成功结果。
4. 全部实现完成后转入 R13 完整验收；验收有新失败，回到对应功能修复，再重验受影响部分和项目要求的检查。新修改使相关旧资格失效，不能沿用别的 SHA 的绿灯。
5. 全部目标满足才以 completed 交付；尚有外部资格缺口时，只有在**已无任何有意义的独立工作可做**后才以明确的未完成清单结束本次执行。

每批提交后做一次简短进展更新并继续调用工具，不使用终结回复宣告“本批完成，下一步可以……”。技术难度、一次测试失败、夜间无人回复都不是结束理由。不要用文档润色或重复跑已通过测试代替剩余实现。

### 5.3 失败分类、自主修复和有限重试

| 失败类型 | 无人值守动作 | 禁止的处理 |
| --- | --- | --- |
| 产品测试/构建失败 | 保存原始退出码和日志，缩成触发场景，检查根因，修复后跑对应门禁；稳定后继续批次 | 直接跳过、改预期迎合错图、把失败归为环境而无对照 |
| 同类修复两轮仍有新边界 | 改查共同结构/权威状态/版本边界，按第三路径审查做反例实验，选择不同候选 | 对同一输入反复打补丁或无限原样重跑 |
| 网络瞬断/下载失败 | 先检查已有缓存和本地可用条件；有瞬态依据时最多再原样尝试一次，随后切换本地就绪任务 | 无限 fetch/install；认证失败后反复试凭据或修改账号配置 |
| 本机依赖/路径/端口问题 | 核对当前任务环境，按项目入口修复隔离环境；选择空闲端口并同步测试配置 | 删除共享依赖、覆盖别的 worktree、杀其他会话服务 |
| 测试/子进程无响应 | 按既有 CI 超时或同机基线设置并记录合理上限；检查进程/日志，超时只终止本任务进程并保存故障证据 | 无期限阻塞；持续有进展的完整套件被任意短超时误杀 |
| 权限/真实输入/平台条件缺失 | 记录具体需要什么、已尝试什么、影响哪些任务；保持 pending，转入不依赖它的工作 | 绕过授权、伪造用户确认、用 mock 冒充平台资格 |

“最多再试一次”仅限制没有变化的瞬态重试，不限制基于新证据的实现修复。没有改动或新诊断依据时，不再重复相同失败命令；困难问题要继续分析或换实验，不自动等同于外部阻塞。

### 5.4 Checkpoint 必须支持实际恢复

复用 `PROGRESS.md` 的当前状态和日期追加记录，不增加另一份进度权威。每个功能提交、长耗时验证前后、发现外部阻塞或准备结束前写 checkpoint；长批次约每 30 分钟在安全步骤边界补一次。日志写仓库外证据根，必要临时产物仍遵循 data_dir 规则。

每条 checkpoint 至少包含以下字段；写事实，不写未来假定结果：

```text
时间 / 本次 worktree / 分支 / HEAD / 已核验的远端 SHA（或未验证原因）
当前 R 批次与子项 / 实现状态 / 验证状态 / 本轮功能 commit
本任务未提交文件与改动意图 / 是否仍有不能提交的中间状态
已执行命令、cwd、所需环境、真实退出码、报告和生成物位置
本会话启动的进程 PID、启动时间、命令、端口、日志及已知运行状态
外部缺口、影响的依赖、解除条件 / 其他仍可执行的任务
下一条具体命令或代码修改落点 / 预期正确结果 / 尚需的验证
```

验证红的工作不能标为通过提交；中断前保留当前任务差异和明确说明，不自动 stash/reset。已通过的完整功能按不同功能分别 commit，记录实际提交号。仅验证过的状态有资格成为恢复基线。

### 5.5 会话中断、上下文切换后的续跑

恢复入口固定为本目录 `NEXT_PHASE_PROMPT.md` 与 `PROGRESS.md`。若是同一会话上下文压缩，直接继续原任务；若执行会话确实结束，需要宿主已有续跑能力或重新启动一次，不能在日志中虚构后台仍在运行。

恢复时先核对记录中的 worktree、分支、HEAD 和实际 diff，确认进程 PID **及启动时间/命令**是否仍属于本任务；PID 可复用，不能仅凭旧数字杀进程。正在运行且有进展的测试继续收集，已结束的读取退出状态；没有退出码的旧日志不能补写 passed。不要再次启动相同服务或完整套件。

若文件/HEAD 被其他会话修改，先区分自己的差异和新增工作，保留所有有效提交；不能用旧 checkpoint 覆盖实际树。核对测试对象的源码和相关依赖身份：没有变化且证据完整的昂贵检查不重跑，有受影响变化的资格重新验证。然后按第 5.2 节选任务继续，不能重新从“生成一份计划”开始。

### 5.6 允许结束的情况与最终交付

仅在以下情况结束本次执行：全部验收完成；用户明确要求停止；会话/工具/额度等运行条件强制中断；或所有剩余有意义的工作都确实依赖无法自主取得的外部条件。正常完成一个 commit、M1 通过、一个问题难修、暂时没人回复都不在此列。

结束报告包含已完成的用户能力和使用入口、实际分支/commits、测试 SHA 与结果、产物路径、仍缺的实现/平台项和下一条恢复动作。未完成就是未完成；不以“整夜运行过”“专项全绿”作为最终可用的证据。暂时中断时保留可接续状态；已完成时无需持续轮询或空跑。

## 6. 本轮文档交付边界

三份稳定文档同步11c67ab1捕获收口状态，PROGRESS保留全部历史；包成员权威与safe/workerd捕获已分别commit。核真实SHA、原始JUnit、代理释放状态及既有截图；本次实际补跑Ruff/format/diff、playground和插件画布build/check，不把历史产品套件冒称新跑。下一项为native事实链和准备/启动过期核验，再完整声明资源/UI/ref及N3d–f；图例/裁切/复杂几何和最终资格仍缺。M1/M2/M3未通过、goal active；未设置定时调度或远程发布。沿当前分支继续，保留有效改动，不为状态询问建分支。
