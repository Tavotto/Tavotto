# SVG 计算图表编辑：下一阶段无人值守提示词

2026-11-07 07:30后复核：3d已提交`fa691a3b`，两项旧失败已修复。N3d安装核心/API已有未提交实现，专项核心17/HTTP35通过，待受影响旧组联合验证和功能提交。详细安排：[NEXT_PHASE.md](NEXT_PHASE.md)；台账：[PROGRESS.md](PROGRESS.md)；完整范围：[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。可复制以下全文：

```text
继续完成Tavotto的SVG计算图表编辑，保持现有Python绘图和全部既有能力，复用原模型/编辑器/writer/事务、RenderCore/pdfbackend和legendmodel。持续自主实现、验证、修复，同一分支按功能commit，常规可逆步骤不逐批等确认。已授权多子代理，只分独立有收益任务；共享app/schema/身份/规则/集成/提交主代理单写，每文件同时一个写入者。

一、恢复真实状态
实施树：E:/0 - Project/Tavotto-svg-r00
分支：zixu.wang/svg-computational-r00
复核功能HEAD：fa691a3b，其前81fd701f/cc3175e8分别修复import环和冷包fixture。
原目录：E:/0 - Project/Tavotto，clean main@69bcafae，未合入成果。
证据根：E:/0 - Project/svg-review-20261106
稳定文档：docs/implementation/svg-computational-editing/{PROGRESS,NEXT_PHASE,NEXT_PHASE_PROMPT,IMPLEMENTATION_PLAN}.md

先核实际HEAD/diff、提交身份、自有进程和最新checkpoint，读相关AGENTS/规则/ADR。实际树优先，不reset到文档SHA。3d已提交，不重做；未提交N3d含app.py、atomicio.py、packagerelocation.py、新packageinstall.py及3个新增测试，保留现实现。状态文档可能已另提交。不要新建/切换分支/worktree，不stash/reset清改动，不git add .，不push/PR/合并/发布。config/data隔离、遥测关闭，可写数据沿config.data_dir；手工Python/服务/child显式钉当前src并核__file__，pytest沿现pythonpath=src。

二、第一项：收口N3d安装后端
读n3d-install/qualification.md/.json、n3d-http/validation.md/.json、n3d-directory-validation.md/.json、n3d-install-review/FINAL_REVIEW.md及status-review-20261107-installation.json。核心17/HTTP35各自0skip，目录19pass/3权限skip，原文件发布17pass/10筛除均exit0；分窗口结果不累计或当全产品资格。目录parent-fsync、安装闭合集合、renderer调用内存反证已做，两项原窄probe修后exit0，保首红。3d末验415/0skip、扩大296/4skip属于已提交fa691a3b，不能外推N3d。

现实现：活跃Prepared/原迁移计划→受检流复制到同父私有stage→原registry元信息→原renderer验证→两次闭合集合回读→排他发布→原runconfig/inputremap导入建议及实际指纹核对。API为POST /api/documents/v4/package/install，复用原有界上传/认证，不打开项目、不写正文，document_saved=false。携带INPUT用可信caller或自有空engine根验证，上传.venv不是解释器来源，包内Python零执行。

本批待完成：
1. 核7文件SHA和专项冻结记录；按app上传公共helper、内部source_storage和迁移copy的影响，补原inspect HTTP、chart保存/渲染/原图导出、迁移、原atomicio/import架构必要联合组。先Ruff再pytest，无新疑点不反复跑昂贵已验组。
2. 真实safe/native包走新安装API，核完整ref、实际remap指纹、历史输入/rc_ID/精确argv建议；仅删自建旧源/cache，新父进程在中文新根冷读。旧手工受检发布不能代新API资格。
3. 同名/并发零覆盖、源变/断写/坏包拒绝，只清未发布且身份未变的自有stage；发布后不递归回滚。精确保path、publication_unconfirmed/installation_unconfirmed及配置pending/needs_review/ready。保旧默认/cwd授权，不导入发送者授权或自动Run。已知缺件不报完整安装，unknown coverage不表示任意IO齐备。
4. 保两项已修边界：旧目标路径元信息→needs_review；发布后上传cleanup失败503仍有精确path及upload_cleanup=pending。不把503一律当未发布。源再变才补相应delta/反证。
5. 同步受影响规则/ADR及派生索引，规则/结构/Ruff合格后定向stage完整功能commit：feat(packages): install verified v4 projects without clobbering。独立缺陷分别fix，状态文档另提交。

三、N3e原界面采用
沿原项目界面检视、选目录、安装、采用；资源发布/配置差异/打开项目/正文落盘分阶段表达。503含已发布path时保回执和恢复操作，不无提示重试同目录。needs_review复用原配置操作明确确认，不静默清授权、不自动Run。
复用projectStore.adoptOpenedProject(status,{prepareDocument})、switchTransaction、openDocumentAs、waitForWorkingDocumentSave。最终正文先冻结，前后核pj/format/documentId/loadSeq/打开意图。非空只等原writer一次写及实际revision/正文回读，不补第二PUT；空稿显式persistEmpty一次。取消/切项目/迟到/超时/后阶段失败保旧稿，不继承发送者机器绑定。
专项Vitest/API、正式build、i18n/受影响派生产物后，真实DOM验中文流程及网络/写入计数。commit：feat(packages): adopt installed charts through the document writer。

四、N3f中文新根完整搬迁
冻结混合夹具：计算SVG完整INPUT、普通素材、Python runtime/快照、同名静态两历史、外部file/prefix/空目录、原配置、多画布/非活动对象。正式导出→上传inspect→新API安装→界面采用；核全部成员、完整ref、原序正文/参数。仅删自建旧源/cache，新父进程/新浏览器开中文新根，继续改数据/范围/样式、undo/redo、保存、冷开/版本恢复及适用scope×format。
模型/INPUT/bytes/参数/几何/文字/PPI独立核，不以截图/菜单成功代替。原schema2/3、Python及默认无声明补受影响回归。缺陷分别fix，资格另commit；N3a–f全退出才标迁移完成。

五、完整剩余队列
N4图例：显式条目→series/custom，复用legendmodel；同色/同名/顺序不能当身份。验复合句柄、数据/范围/样式联动、重绑、撤销/冷开/导出，注释保留。
N5几何：成员paint slots、Path/ClipUnion、祖先AND/子形状union/evenodd/objectBBox及运动；命中/框选/高亮/描边共用几何。对照原SVG/预览/产物修文字/刻度边缘裁切，不降低保真。
N6/M1：双系列+图例+未绑定注释完整绑定/数据/范围/样式→undo/redo→保存→空缓存新进程→原图/标注/混合画布/多页适用scope×format；外观分享不暗带数据、完整分享携完整模型，画布EPS沿原eps_not_for_canvas。
N7/M2：按IMPLEMENTATION_PLAN保全部SG00–SG17及A/B/C，补科学族/多轴/投影、文字/图元/分组/use、事务写回、出版/预检和CLI/MCP/playground/桌面/发行入口逐格资格。普通SVG不反推Python；写回prepare→verify全量冷重放+几何+像素→commit，失败409原件零改。
N8/M3：冻结候选完整旧功能/等价性/CompatBench/E2E/性能/支持平台真实安装物，Windows权限/PID回执和缺平台如实处置，不以SVG专项绿替代原路径资格。

六、无人值守循环
最早就绪完整功能→真实消费者→必要反证→受影响验证→核源/退出码/diff→定向功能commit→同步PROGRESS日期checkpoint→下一项。Python先Ruff，前端专项后正式build，按影响补派生/UI，最终候选才完整链。保首红，不删测试/放宽容差/加豁免，重叠组不累计。
长检查前后、功能提交及约30分钟边界记HEAD/diff、所有权、SHA、命令/cwd/env/退出码、失败/skip/产物、自有进程/下一步；只结束核过PID/创建时间的自有进程。缺外部条件先记录并推进独立就绪项，仅缺信息实质改变结果或超授权时问。
无人值守指活跃会话持续迭代，未建常驻调度器；中断按checkpoint恢复。不以一夜结束或测试数标整体完成，M1/M2/M3实际通过前goal保持active。

现在核安装差异与证据，完成N3d受影响集成/功能提交，再接N3e。
```
