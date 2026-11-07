# SVG 计算图表编辑：下一阶段无人值守提示词

2026-11-07执行复核：安全安装后端已提交`0d86b357`，菜单测试等待修复已独立提交`cd3912d6`；N3e界面实现未功能提交。下一首项是收口时间线依赖环，重跑受影响联合并完成真实浏览器安装/采用，再进入N3f。详细安排见[NEXT_PHASE.md](NEXT_PHASE.md)，日期记录见[PROGRESS.md](PROGRESS.md)，完整范围见[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。

以下内容可整体复制：

```text
继续完成Tavotto的SVG计算图表编辑，保持现有Python绘图和全部既有能力。目标是最终可用，复用原模型、编辑器、writer、事务、RenderCore/pdfbackend和legendmodel。持续自主实现、修复、验证，在当前同一分支按不同功能分别commit；常规可逆步骤自行推进，不逐批等待确认。

一、先恢复真实状态
实施树：E:/0 - Project/Tavotto-svg-r00
唯一分支：zixu.wang/svg-computational-r00
最近已核功能提交：0d86b357，安全安装后端。
最近测试修复：cd3912d6，仅等待真实菜单焦点恢复，不改生产菜单。
原目录：E:/0 - Project/Tavotto，clean main@69bcafae，尚未合入成果。
证据根：E:/0 - Project/svg-review-20261106
稳定文档：docs/implementation/svg-computational-editing/{PROGRESS,NEXT_PHASE,NEXT_PHASE_PROMPT,IMPLEMENTATION_PLAN}.md

先读最新checkpoint，核实际HEAD/diff、提交身份、代理所有权和自有live句柄，再读受影响AGENTS/规则/ADR。实际树优先，不reset回文档SHA。
保留当前N3e API、协调器、Dialog/入口、App/TopBar/ProjectPicker、中英翻译及全部专项测试；时间线修复可能正在同树进行，先核实际文件，不重复实现。
不新建/切换分支或worktree，不stash/reset覆盖改动，不git add .，不push/PR/合并/发布。配置/数据隔离、遥测关闭；可写数据沿config.data_dir()。共享venv的手工Python/服务/child显式钉当前src并核__file__，pytest沿现pythonpath=src。

二、现有证据与当前失败
N3d已完成本批后端资格并提交，不重做：受影响359收集=356pass/3权限skip，304源零漂移；真实safe/native新API安装→中文根→删自建旧源/cache→新父冷读2pass/0skip，23源零漂移；规则41及实际Python3.10.20 stdlib边界通过。索引n3d-stage-final.json。安装不打开项目、不写正文、不运行包内脚本，document_saved=false。
N3e新API36、协调器25+原writer17、Dialog/入口16分别通过，不能相加成全产品通过。
首轮20文件联合265=262pass/3fail/0skip，exit1，1486源零漂移，原始n3e-affected/first.*保留。2个架构失败是固定0d86同样存在的时间线扩环；1个菜单测试时序已由cd3912d6修复，原文件18pass、受控当前/旧TopBar对拍均通过。完整联合尚未因此变绿。
时间线窄修随后已落6生产源/2测试，9文件120pass及原documentStore/projectSave另94pass，均0skip/exit0；lint0，两项内存AST反证均真实红，磁盘源零变。frozen-sources.json已冻结8个改动文件及未改baseline，代理释放、无live句柄，尚未功能提交。先核末次报告/源，统一联合和正式build后独立提交；不重写现实现，不把120/94当完整20文件复验或相加。
n3e-build/packaged-second曾完成真实i18n/tsc-b/Vite/Tailwind及包内同步，但后续时间线源改变后必须重建。n3e-ui夹具已准备，尚无真实浏览器安装资格。历史曲线截图仍有文字/刻度裁切，不能声称视觉全部完成。

三、严格按下一阶段顺序执行
P0.1：收口时间线依赖环。
沿已授权的窄结构修复：纯momentCapture身份/key及必须同步图源端口，原renderStore装配同一真实source freezer，timelineThumb消费；documentStore不反向依赖timelineCheckpoint。
保原4个capture调用的同步时刻、排队正文/项目/格式/docId/代次和Snapshot/PNG Promise引用。无装配准确失败并保稿，不默认丢Moment，不在成功后补拍，不建新writer/cache。
保架构baseline；不用dynamic import隐藏环，不改旧测试增加初始化伪绿。原架构/求值顺序、真实直接documentStore及App/embedded/MCP/playground初始化、Moment/工作副本回执/时间线组必须通过，必要反证保原红。修复按独立fix(timeline)提交。
若原调用范围已完成，直接核证据，不重做。遇到还需扩大结构范围，先说明用户不变量与候选，记录实际失败，避免无限扩展无关模块。

P0.2：冻结N3e并完成受影响前端检查。
保留原20文件首红；修复后用新输出标签重跑，按时间线实际变动追加必要原组。正式build与i18n通过；按web/src影响重建/检查playground和本地widget，记录真实源码/产物哈希。源再变只补相应delta，不沿用旧build冒称最终。
同步受影响规则，不能只改状态文档关闭失败。新旧相关检查通过后再启动真实UI，避免服务使用旧包内前端。

P0.3：完成N3e真实浏览器安装与原writer采用。
复用n3e-ui已准备的正式v4包、非空旧稿、中文新目录和独立canary；核实际服务源码与HTTP bundle哈希，正常bootstrap认证，不开insecure旁路。
真实DOM走检视→选新目录→安装→明确打开。检视/安装零Run，资源发布不等于正文已保存。503有已发布path要保回执及恢复操作，不重装同路径。
复用projectStore.switchTransaction、adoptOpenedProject.prepareDocument、openDocumentAs、waitForWorkingDocumentSave。原非空旧稿先保护；新非空稿只等原writer一次AS PUT及实际revision/正文回读，不补第二PUT；空安装稿persistEmpty:true一次。
needs_review复用原配置入口明确检查，不静默继承/清除机器授权或自动Run。核取消、切项目、迟到回包、读取失败保旧稿/已发布目录；核历史回执恢复和“继续编辑”实际落点。
真实网络/写入次数、旧稿正文、模型/输入/包成员及产物独立核；截图只辅助。组件专项不能代替浏览器。通过后完整功能commit：
feat(packages): adopt installed charts through the document writer

P1：N3f混合项目完整搬迁。
夹具含计算SVG完整INPUT、普通素材、Python runtime/快照、同名静态两历史、外部file/prefix/空目录、原配置、多画布和非活动对象。正式导出→上传检视→新API安装→界面采用，核完整资源图、引用、原序正文/参数。
仅删自建旧源/cache；新父进程/新浏览器在中文新根继续改数据/范围/样式、undo/redo、保存、冷开/恢复版本及适用scope×format导出。
模型、INPUT、bytes、几何、文字、PPI分别核；原schema2/3、Python、默认无声明补受影响回归。画布EPS沿原eps_not_for_canvas。缺陷分别fix，资格另提交；N3a–f全退出才标迁移完成。

四、后续完整队列，不降低最终范围
N4：显式图例entry→series/custom，复用legendmodel；同色/同名/顺序不是身份，复合句柄和未绑定注释保留，撤销/冷开/导出一致。
N5：成员paint slots、复杂clip/Path/ClipUnion、命中/高亮/变换共享几何；对照原SVG/预览/独立产物修文字/刻度边缘裁切。
N6/M1：双系列+图例+未绑定注释的完整计算编辑、撤销、保存、空缓存冷开、原图/标注/混合画布/多页适用scope×format；外观分享不暗带数据，完整分享携模型。
N7/M2：保SG00–SG17/A/B/C全范围，科学族、多轴/投影、文字/分组/use、事务写回、出版/预检和CLI/MCP/playground/桌面/发行入口逐格实证。普通SVG不反推Python；写回prepare→全量冷重放/几何/像素verify→commit，失败409原件零改。
N8/M3：冻结候选完整旧功能、等价性、CompatBench、E2E、性能及支持平台实际安装物资格；缺平台如实未验，不把SVG专项绿外推全产品。

五、无人值守循环
按P0.1→P0.2→P0.3→P1→N4/N5→M1/M2/M3连续推进。最早就绪完整功能→真实消费者→必要反证→受影响验证→核源/退出码/diff→定向功能commit→同步日期checkpoint→下一项。不要每次停在新计划或只读盘点。
已授权多子代理，只分独立有收益任务，每文件一个写入者；共享契约/集成/提交主代理单写。先联系现有代理，核状态与live句柄，避免重复启动相同任务。
Python先Ruff；前端专项后正式build；仅新改动/失败/未解疑点追加验证，最终候选跑完整链。保首红，不删测试/放宽容差/加豁免，不累计不同源码窗口的通过数。
长检查前后、功能提交及约30分钟边界记PROGRESS：HEAD/diff、所有权、SHA、命令/cwd/env、实际退出码、失败/skip、产物和下一步。只结束核过PID/创建时间的自有进程。
缺外部条件记录并继续独立项，仅缺失信息实质改变结果或超授权时询问。无人值守依赖活跃会话，未建常驻调度器；中断从checkpoint续接，不以一夜结束标完成。M1/M2/M3实际通过前保持完整目标未完成。

现在核真实差异与所有权，先完成P0.1–3，再进入P1；不要重做已提交N3d，也不要跳过安装整链直接扩功能。
```
