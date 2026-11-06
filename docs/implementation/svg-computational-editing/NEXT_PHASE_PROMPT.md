# SVG 计算图表编辑：下一阶段无人值守提示词

2026-11-07 复核：3c 已提交 `8a13ed95`；3d 已实现但未提交，专项已有正证，扩大组 294 通过/2 失败/4 skip，须先收尾。详细安排：[NEXT_PHASE.md](NEXT_PHASE.md)；真实进度：[PROGRESS.md](PROGRESS.md)；完整范围：[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。可复制以下全文：

```text
继续完成 Tavotto 的 SVG 计算图表编辑，保持现有 Python 绘图和全部既有能力，最大化复用现模型/编辑器/writer/事务、RenderCore/pdfbackend 和 legendmodel。持续自主实现、验证、修复，在同一分支按功能分别 commit，常规可逆步骤不逐批等确认。已授权多子代理，只分配独立有收益的任务；共享 app/schema/身份/规则/集成/提交由主代理单写。

一、恢复真实状态
实施树：E:/0 - Project/Tavotto-svg-r00
分支：zixu.wang/svg-computational-r00
原目录：E:/0 - Project/Tavotto，clean main@69bcafae，未合入成果。
证据根：E:/0 - Project/svg-review-20261106
稳定文档：docs/implementation/svg-computational-editing/{PROGRESS,NEXT_PHASE,NEXT_PHASE_PROMPT,IMPLEMENTATION_PLAN}.md

先核实际 HEAD/diff、提交身份和自有进程，再读相关 AGENTS/规则/ADR。复核基线5884ede2，最新已提交功能8a13ed95；3d生产代码/新测试/规则仍是未提交差异，不能覆盖或重做。实际工作树与最新checkpoint优先，不reset到文档SHA。不新建/切换分支或worktree，不stash/reset清已有改动，不git add .，不push/PR/合并/发布。测试config/data隔离、遥测关闭，可写数据沿config.data_dir。共享venv的裸Python可能导入另一旧树；手工脚本/服务/child明确钉当前src并核__file__，pytest沿现pythonpath=src。

二、当前第一项：收口现有3d，勿再从设计开始
已实现原svgstore.save(archive_only=True)、load_document_snapshot、inputremap.fingerprint_rules和packagerelocation.prepare_relocation；保完整oldRef→newRef、原序overrides、receipt/PDF/SVG/输入图与原字节。新根一份真实remap全表只有一个指纹；历史同from异bytes按ref惰性分视图，同时至多一份，不作执行许可。N→1映射不丢行，capture集合从新store重算；闭合publication_files不带锁/view，每次复制仍核实际流。原load_snapshot逐维精确、无ref静态当前hash、已知新执行撤旧及artifact policy不放松。历史配对和当前普通文件分别核验，不声称恢复了未携带的历史磁盘原件。

读取n3c3d-root-final.xml（226/0skip，早于最后app EPS delta）、n3c3d-real/final-integrated.xml（safe/native同命令2/0skip，非安装UI）、n3c3d-loader-review/document-sources-accepted.xml（最终app23/0skip）；各组不相加。必要内存反证、Python3.10 stdlib probe、两项迁移修复及EPS窄复核见NEXT_PHASE第2节，不重复未受影响大组。

扩大组n3c3d-loader-review/status-review-affected.xml实际300收集=294pass/2fail/4skip/exit1，源码零漂移，先处理：
1. 旧test_svg_input_cold_staging的新父进程冷打包422：固定HEAD5884ede2已实测同红；外部依次修合法remap ID和真实PDF/SVG后原断言全过。只修正式冷包专属fixture，不改共享存储哨兵或弱化生产图验证；归因报告n3c3d-loader-review/cold-staging-baseline-attribution.md/.json。
2. import架构门chartpackage↔packageinspection逻辑环已在HEAD5884ede2和当前同例实际复现，来自前批06806c2c，新迁移模块不在环中。按共同依赖方向修结构，保writer/reader同源语义验证；不得登记豁免、换延迟import或放松门禁。报告n3c3d-loader-review/import-cycle-baseline/REVIEW.md。
复现报告在原证据根保留。基线同红也不能说扩大组全绿；修复按影响补delta/集成，保原失败与skip。所有必要受影响检查合格后，定向stage完整功能，commit：feat(packages): plan verified snapshot references for relocated inputs。独立修复按功能commit，稳定状态文档另提交。未通过就保留差异和准确checkpoint，不硬标完成。

三、直接接N3d→N3e→N3f，不停在新盘点
N3d安全安装：消费活跃Prepared和同一迁移计划，在用户明确选择的新根独占reserve、受检流发布、逐份回读/摘要/语义验证；现projects/open create=true会复用目录、copy_project可skipped>0，不能当严格安装器。保相对sourceId/script/runtime、原rc_ID/非敏感精确argv建议、全部画布和原序patch；不为冲突改panel.fileId/用户Python。新remap设置沿原权威并核实际指纹，历史映射不是本机读权。既有目标零覆盖，只清自有attempt；不复制机器授权、默认配置或成功信任；检视/安装零脚本执行。准确区分资源发布/项目打开/正文写/元信息确认。真实认证、错误项目、同名/并发/断写/源变/坏件/取消保旧目标与正文。独立commit feat(packages): install verified v4 projects without clobbering。

N3e界面采用：复用projectStore.adoptOpenedProject(status,{prepareDocument})、switchTransaction、openDocumentAs、waitForWorkingDocumentSave。最终正文在beforeInstall前固定，前后核pj/format/documentId/loadSeq/打开意图；非空只等原writer一次写及真实回读，不补第二PUT；空稿显式persistEmpty一次。取消/切项目/迟到/超时、资源成功但正文失败、元信息未确认准确分列，旧稿保留。不继承发送者本机文件绑定。专项Vitest/API、正式build/i18n/受影响派生产物、真实DOM验收后commit feat(packages): adopt installed charts through the document writer。

N3f整链：冻结计算SVG/普通素材/Python runtime/快照/同名静态两历史/外部prefix空目录/原配置/多画布混合夹具，正式导出→上传inspect→安装→界面采用；仅删除自建原目录/cache，全新父进程/浏览器开中文新根，继续编辑/undo/redo/保存/冷开/版本恢复及全部适用scope×format。独立核模型/完整INPUT/成员字节/参数/几何/文字/PPI，补原schema2/3和Python受影响回归。实际缺陷分别fix，验收记录另commit；N3a–f全退出才标迁移完成。

四、完整剩余队列不减范围
N4：显式SVG图例条目→series/custom关系，复用legendmodel跟随/脱开/handle_*；不按同色/同名/顺序猜。验复合句柄、数据/范围变化、重绑迁移、撤销/冷开/导出；未绑定注释保留。
N5：成员paint slots、共享Path/ClipUnion和祖先AND/子形状union/evenodd/objectBBox/运动；命中距离/框选/高亮/宽描边共用几何。对照原SVG/预览/导出定位刻度和文字边缘裁切再修，不降低保真。
N6/M1：两系列+图例+未绑定注释真实绑定/数据/范围/样式→undo/redo→保存→空缓存新进程→原图/标注/混合画布/多页适用scope×format。纯外观分享不暗带数据，完整分享携完整编辑模型；画布EPS仍沿原eps_not_for_canvas。
N7/M2：按IMPLEMENTATION_PLAN保全部SG00–SG17和A/B/C，补文字/图元/分组/use、科学族/多轴/投影、事务写回、出版/预检与CLI/MCP/playground/桌面/发行入口逐格资格。普通SVG不反推原Python；写回prepare→verify全量冷重放+几何+像素→commit，失败409原件零改。
N8/M3：冻结候选完整旧功能/等价性/CompatBench/E2E/性能/支持平台真安装物；Windows权限/PID回执及缺平台如实列出，不用SVG专项全绿替代旧路径资格。

五、无人值守循环
选最早就绪完整功能→实现接真实消费者→必要反证→受影响验证→核源码/退出码/diff→定向功能commit→同步PROGRESS当前表和日期checkpoint→继续下一项。Python先Ruff再pytest；前端专项后正式build，按影响补派生/UI；最终候选才全套。无新疑点不重复昂贵组，不删测试/放宽容差/改豁免。单项缺外部条件先记录并推进独立就绪项；仅缺信息实质改变结果或超授权时问。
长检查前后、功能提交和约30分钟安全边界记录实际HEAD/diff、所有权、sourceSHA、命令/cwd/env/退出码、失败/skip/产物、自有进程和下一动作。原首红保留，重叠组不累计。只结束核过精确PID/创建时间的自有进程。
未创建常驻调度器；无人值守指活跃会话持续迭代，休眠/会话中断后按checkpoint恢复。不得因一夜结束、helper数量或测试数标整体完成；M1/M2/M3实际通过前goal保持active。

现在核当前差异和两项原失败归因，收口3d，再直接实现N3d。
```
