# SVG 计算图表编辑：下一阶段无人值守提示词

2026-11-07 15:18 复核后更新。已有真实混合导出实物，也发现新的批量流程失败；原91338/66615已终局，不再续等或重启旧一次性工具。详细批次与出口见[NEXT_PHASE.md](NEXT_PHASE.md)，历史证据见[PROGRESS.md](PROGRESS.md)。

以下可整体复制：

~~~text
继续完成 Tavotto 的 SVG 计算图表编辑，以最终可用及现有 Python/旧项目功能不回归为目标。复用原轴/数据模型、编辑器、document writer、Preparation/runner、RenderCore/pdfbackend 和 legendmodel。当前同一分支自主实现、修复、验证，按完整功能分别 commit，常规可逆步骤不逐批等待确认；完整范围沿 IMPLEMENTATION_PLAN.md 的 SG00–17/A/B/C 与 M1/M2/M3。

一、先恢复实际状态
唯一实施树 E:/0 - Project/Tavotto-svg-r00，分支 zixu.wang/svg-computational-r00。复核基点 HEAD c85c96c7，最近产品提交35adee6d；文档提交可能随后前进，以实际HEAD/diff为准。原 E:/0 - Project/Tavotto 为 clean main@69bcafae、未合入。证据根 E:/0 - Project/svg-review-20261106。
读 PROGRESS 最新checkpoint、NEXT_PHASE 和受影响目录AGENTS/规则/ADR，核Git身份、index/diff、代理和真实进程。保所有未提交P3代码/测试/规范、A/B/C/D现有安装、AS/命名稿、旧包及首红。不新建/切分支/worktree、不reset/stash/git add .，不push/PR/合并/发布。本次未fetch，不称远端最新。
归属8f4985ee、归档后端135aba94、归档前端e2c026fa、Moment归属c9e2980d、N3e35adee6d及此前功能已提交，不重做。P3静态SVG合成、overflow修复及原图统一scene均已应用；不要再套旧16文件/one-scene候选。

二、当前资格如实继承
n3f-svg-production/backend-first-exit.json：109文件3683项，3539pass/16fail/128skip，真实exit1、源零漂移，原PTY91338终局。4条旧SVG判据已修，contract-repairs-production-first的正式三文件76pass/0skip；统一scene新9pass/0skip。其余12条已在原依赖修复闭包复现，候选仅OUT未应用；不能称全109已绿。
frontend-first为70文件1514pass/0skip，正式packaged-first及playground/widget构建/check均0、1502源零漂移。独立Chrome两页29手算点通过并正常关闭；Inkscape/Office未验。源未变不重复已绿组，不与旧1320或候选组累计。
128skip=84平台文件系统条件+41缺Inkscape+1旧硬编码GS路径+1缺pdfminer.six+1缺Poppler；skip不算通过。新独立GS已真实运行，不等于旧reader全跑。

三、最新真实UI结果与边界
n3f-svg-export-ui/output/playwright/35.json、review-actual-jobs.json、review-artifacts-final.json为实际证据。
A整版job6a452aab0ee94e38为partial：PDF/PNG/TIFF成功，独立180×130mm、4252×3071/600dpi、文字/12颜色点、PNG与TIFF逐像素相同；7对象6panel及2完整ref/patch/顺序/坐标与保存稿相同。SVG被ordinary.pdf无可信同次SVG配对阻断，svg_source_unsupported；不能删PDF来冒完整画布通过。
逐图批次chart.svg jobc81359eea7e343b6四格式done，History.pdf job9a4513d9f5e54978四格式及报告done；ordinary.pdf job33479f13689246ed为partial；ordinary.svg job039acdee18a04645因同名输出conflict、尚无产物资格。另普通PNG被UI报来源缺失、runtime:plot被非法冒号文件名拒绝。UI“17成功4失败”含前次结果，不能当新批独立计数或全成功。
普通PNG/SVG/PDF源文件实际仍在。普通PDF原图请求40mm而产物约35.278mm/833px，下一轮独立核原生图幅/放置/元数据口径，未定性为本轮新增回归。
current-A-fitted.png仍可见刻度/标题裁切，N5未进产品。原History render500、完整冷迁移、图例/复杂SVG/其他入口和平台格仍未完成。
该UI正常close36=0；服务原exec66615与child均0，launcher24768/runtime50148消失、24109无监听，1791源/11实际HTTPbundle末核及持久稿/计数4保持。无本任务自有live服务/浏览器/测试，三既有子代理均已交付。不要重用旧serve、重装现有目标、重新采用包正文或清缓存掩盖问题。

四、第一批：先完成P3，再交功能commit
复核 p3-platform-fixture-review/REVIEW.md、release.json。portable-fixtures.patch SHA256=2d631dbed464b6c720b3331406aa05acb1a732f352f8bc3bb474c6f2d935dbf3，仅两个原测试文件，尚未应用；原assert AST保留。候选分轮10pass/1fail/1skip，最后唯一失败修复单项1pass，不伪并为一次全绿。先Ruff，再正式两文件pytest；精确WinError1314 skip如实留未验，不更改生产来源/授权。夹具修复单独commit。
沿原SourceResolver/尺寸事实复现普通PNG原图准备失败；沿原文件名同源权威/作业协调器修同stem多扩展素材、runtime合法名及同批/并发/已有磁盘冲突。来源ID与显示名分开；保原重命名/替换/取消、partial和失败恢复，不静默覆盖、丢conflict或另造命名规则。
在新正常认证生命周期、当前源/bundle冻结后，真实UI验证原A全对象整版及逐图批次，含ordinary.svg原图四格式、留白/标注；独立PDFium/PIL核尺寸/文字/颜色/透明、原稿/fullref/counter，不直接POST绕产品流程。
静态PDF缺SVG配对的整版SVG格按能力矩阵和第三路径审结构、做原矢量契约有界实验。要求支持而尚缺实现的格留M2；不伪配同名SVG、不删对象/栅格化/只改提示关闭根缺口。
正常终局后按完整依赖分别commit：测试夹具、独立批量命名/协调修复、P3导出功能；同步现有规范/ADR/待发说明。不能把共享前置拆成不能运行的提交，也不能因专项绿宣告P3/总目标完成。

五、第二批：P2a完整历史来源与热编辑
先读 p2-integration-map/IMPLEMENTATION_MAP.md、source-map.json，冻结闭式origin/response合同。figureOrigin候选为完整snapshot ref+基准有序patches；current overrides独立。档案只有patch_hash，不能反推或默认空基准。
接原commit/事务、首改/undo/redo/AS/named/版本/多画布/复制/包闭包与ref迁移；清输出figureSnapshot不丢持久来源。同步全部request/renderKey/queue/want/last/latest/recent/exact/mounted/PNG及导出/写回消费者；不新store/writer，不借旧manifest写几何。
safe候选 n3f-edit-guard-candidate 与native n3f-edit-native-guard-candidate均未应用。native delta-after-safe与combined二选一。沿enginesession单一分派接app正常认证/fullref/receipt及parent/Workerd/child/native真实execution与epoch守卫；旧端忽略字段不能阻止lazy build，历史支路不得透明respawn/fallback。
正式safe/Workerd/native及真实HTTP/UI验同次热编辑/undo零重跑，冷/错来源/eid/epoch/迟到不隐式执行，不入exact；普通无origin Python原路径保持。持久引用不授执行许可。完整来源与真实接纳分别功能commit，OUT实验不冒产品通过。

六、第三批：P2b原INPUT准备链，再P2c完整冷迁移
沿load_input_archive、原PreparationPlan/Service/prepsession、execspec/runconfig、safe/native原runner/资源租约。冻结origin+INPUT revision、源/数据角色、cwd、精确argv/rc、当前授权与租约生命周期。比较视图不是执行目录，旧rc/包不授运行许可。
safe复用shadowscript；native补原受控启动的来源角色，不塞safe参数、不写用户原脚本、不建第二runner。明确运行后先基准P取得同次真实pair/manifest，独立几何/像素/文字对照通过再接当前Q；新执行新receipt，正式采用新ref或有界私有接纳，不伪造旧receipt。取消/过期/迟到零正文变化并回收/reap。
通过后对自建旧源/cache列准确清单再隔离，保新根/包/INPUT/AS/命名稿/config/runconfigs/绑定/意图/layout-sessions(.bak)，不全清data/cache。新父/新浏览器验双画布五ref、计算编辑/undo/保存/重开/版本/适用格式；另验真正runtime家族12成员包，混合35包kind=pdf对象不能替代。修具体首红，不循环重装/重开冒进度。

七、后续N5→N4→M1/M2/M3
N5复用已有OUT原型，保持S源坐标/F派生框、同次render_frame/native2与旧1兼容，贯穿schema/请求/cache/exact/frame采用事务/F→S几何/各scope导出。实际glyph ink反例验裁切，不只扩viewBox或缩数据/字体/偷改页面。
N4复用legendmodel，显式entry→series/custom，保同名同色/复合句柄/手工文字和未绑定注释；编辑/undo/保存/冷开/导出一致，各功能分别commit。
M1双系列+图例+注释，真实数据/轴/样式→undo/redo→保存/版本→空派生cache冷开→适用各格式实物，A/B/C分别验。
M2按SG00–17补复杂SVG、图族/多轴/投影/文字/组/use、写回/预检/出版、CLI/MCP/playground/桌面/发行。缺实现不标不适用，替代engineTransport归档消费者仍缺。
M3最终冻结后完整原Python/旧项目回归、等价性/CompatBench/E2E/性能及支持矩阵安装物。缺平台/外部reader精确留未验；M1/M2/M3全退出前总目标不complete。

八、无人值守执行
每批状态/规则→实现真实消费者→必要反证→受影响检查→实际exit/源SHA/实物→定向commit→日期checkpoint→下一批。Python先Ruff再pytest，前端类型走正式build及所需派生产物。源未变不重复绿组，最终完整链不以受影响集合代替。
保既有多代理授权，只对独立有收益任务并行，每文件一个写入者；root统一合同/浏览器/集成/提交，先核既有代理，不重复派工。长检查前后/提交/约30分钟更新PROGRESS，记录所有权/句柄、cwd/env、实际fail/skip、SHA/首红/下一项；用户更新不失联超过60秒。
UI冻结产品源/build、正常认证、关闭遥测、只读观察，禁止注入store/成功JSON；正常关闭核明身份的自有进程并末核HTTP/PID/listener/持久稿。失败分产品/夹具/观察器，保首红不删断言放容差；同类多轮扩散按第三路径审结构。
保认证/沙盒/隐私/零PyMuPDF、config.data_dir及写回prepare→完整重放/几何/像素verify→commit（失败409原件零改）。无人值守为活跃会话自主接续，本任务未建常驻调度器；推进实现而非反复计划，时长不代验收。
~~~
