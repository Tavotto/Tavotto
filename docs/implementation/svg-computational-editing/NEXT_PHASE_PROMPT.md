# SVG 计算图表编辑：下一阶段无人值守提示词

2026-11-07 09:10执行复核：安全安装后端`0d86b357`、菜单测试修复`cd3912d6`及时间线依赖修复`c761dc7e`已提交。最新受影响前端451通过，正式/派生构建通过；但真实初次采用2次AS PUT严格失败，目录选择器也有旧响应竞态。下一首项是修这两项，再重验N3e并进入已准备的N3f；不要重做时间线或停在规划。详细安排见[NEXT_PHASE.md](NEXT_PHASE.md)，日期记录见[PROGRESS.md](PROGRESS.md)，完整范围见[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。

以下内容可整体复制：

```text
继续完成Tavotto的SVG计算图表编辑，保持现有Python绘图和全部既有能力。目标是最终可用，复用原模型、编辑器、writer、事务、RenderCore/pdfbackend和legendmodel。持续自主实现、修复、验证，在当前同一分支按不同功能分别commit；常规可逆步骤自行推进，不逐批等待确认。

一、先恢复真实状态
实施树：E:/0 - Project/Tavotto-svg-r00
唯一分支：zixu.wang/svg-computational-r00
最近已核功能提交：0d86b357，安全安装后端。
最近测试修复：cd3912d6，仅等待真实菜单焦点恢复，不改生产菜单。
最近生产修复：c761dc7e，时间线同步Moment捕获叶子及原renderer源端口，消除反向依赖。
原目录：E:/0 - Project/Tavotto，clean main@69bcafae，尚未合入成果。
证据根：E:/0 - Project/svg-review-20261106
稳定文档：docs/implementation/svg-computational-editing/{PROGRESS,NEXT_PHASE,NEXT_PHASE_PROMPT,IMPLEMENTATION_PLAN}.md

先读最新checkpoint，核实际HEAD/diff、提交身份、代理所有权和自有live句柄，再读受影响AGENTS/规则/ADR。实际树优先，不reset回文档SHA。
保留当前N3e API、协调器、Dialog/入口、App/TopBar/ProjectPicker、中英翻译及全部专项测试。保存后跨项目继续编辑、未知安装尝试/迟到回执及原配置入口均已有实现；先核实际源和报告，不重复实现。
不新建/切换分支或worktree，不stash/reset覆盖改动，不git add .，不push/PR/合并/发布。配置/数据隔离、遥测关闭；可写数据沿config.data_dir()。共享venv的手工Python/服务/child显式钉当前src并核__file__，pytest沿现pythonpath=src。

二、现有证据与当前失败
N3d已完成本批后端资格并提交，不重做：受影响359收集=356pass/3权限skip，304源零漂移；真实safe/native新API安装→中文根→删自建旧源/cache→新父冷读2pass/0skip，23源零漂移；规则41及实际Python3.10.20 stdlib边界通过。索引n3d-stage-final.json。安装不打开项目、不写正文、不运行包内脚本，document_saved=false。
N3e新API36、最终协调器49+原writer17=66、Dialog/入口22分别通过，不能相加成全产品通过。保存后恢复见n3e-saved-resume，响应丢失恢复见n3e-network-recovery，均保真实首红及必要反证。
首轮20文件联合265=262pass/3fail/0skip/exit1保留在n3e-affected/first.*。两架构失败已由c761dc7e修复，菜单等待由cd3912d6修复；最新n3e-final/affected-first为27文件451pass/0fail/0skip/exit0，1488前端源零漂移。时间线9文件120与原保存另94均属较早专项窗口，不相加。
n3e-final/packaged-first、playground-first、widget-first及后两者check均exit0、源零漂移；正式i18n/tsc-b/Vite/Tailwind与包内同步完成。后续修复再变源，旧构建不能沿用为最终UI候选。
真实浏览器已正常认证、检视/安装各一次且零Run，中文新根已发布，关闭重开回执后明确采用成功。旧稿最新文字、原命名稿保留，最新AS等于第二PUT、revision一致、canary为0；但初次采用同一工作副本2 PUT，strict-initial-first严格exit1。不可降低断言。新截图n3e-ui/output/playwright/n3e-installed-initial.png仍有左/下刻度裁切。
重复保存根因见n3e-extra-put/diagnosis.md/.json：真实TextView挂载measure→documentStore.silent改变文本高度并排1000ms debounce；switchDocument立即flush已经捕获此高度，却没消费该timer，timer再写仅updatedAt不同的第二PUT。当前真实组件严格红、无测量绿、固定0d86旧documentStore同红。不是waitForWorkingDocumentSave补写或receipt merge。
目录反例见n3e-directory-review：旧home响应覆盖新E盘导航，地址改变/新导航等待中仍可能确认旧目录。原11绿+新7红；外部候选21绿/0skip/exit0，生产修复与常驻测试正落地，尚未完成本批资格。candidate.patch和源SHA已保存，不重新造候选，也不把外部绿当生产已验证。

三、严格按下一阶段顺序执行
P0.1/P0.2已有本批资格，不重做；当前直接进入P0.3a。
c761dc7e的纯momentCapture身份/key及必须同步图源端口保持，原renderStore装配同一真实source freezer，timelineThumb消费；documentStore不反向依赖timelineCheckpoint。
保原4个capture调用的同步时刻、排队正文/项目/格式/docId/代次和Snapshot/PNG Promise引用。无装配准确失败并保稿，不默认丢Moment，不在成功后补拍，不建新writer/cache。
保架构baseline；不用dynamic import隐藏环，不改旧测试增加初始化伪绿。原架构/求值顺序、真实直接documentStore及App/embedded/MCP/playground初始化已有资格，后续变动按实际影响补验，不扩大无关结构范围。

P0.3a：先修两项真实阻断，分别commit。
先核并正常结束初次验收自有服务/浏览器，归档请求、身份与源SHA，解除1777源冻结；未结束前不得改生产/测试源或重建包内前端。n3e-ui首次目标已经存在，不能按旧serve前提直接重启，不能删首次证据。
重复保存只改原共同flush/debounce生命周期：在同步capture点消费本次已覆盖的pending timer，保metadata延迟之后的新编辑、独立后续timer、失败/冲突和重试。保原autosave、TextView测量、writer及receipt，不靠内容去重吞新保存、不改成第二套writer，不删一次PUT验收。
把真实TextView测量反例接常驻测试，至少核首次一PUT、新编辑后另一次真实保存、失败/冲突后可恢复、排队/项目切换/Moment捕获保持。完成必要反证、受影响组后独立fix(documents)提交。
目录修复沿已验证候选：导航意图代次+成功就绪代次；编辑地址立即取消确认资格，只有最新请求成功返回才恢复；旧成功/错误/fallback均不覆盖新导航。保open/create、nearest、焦点与目录名。生产DirBrowser.tsx及相邻导航回归同批，旧11和新反例全部通过后独立fix(directory)提交。
两项可由既有不同代理按独立文件并行修，源冻结解除前不写；共享规则/集成主代理单写。正式采纳前复核实际patch，不能直接复制外部成功标签。
修后保留原451绿与首红，用新输出标签跑原保存/回执/时间线/目录/安装受影响组；正式build与i18n、playground/widget构建/check通过，记录源码/HTTP产物SHA；之后再启动新UI服务。

P0.3b：重验N3e真实浏览器安装与原writer采用。
复用正式v4包及对照语义，使用独立新run目录/目标和生命周期，保留初次2PUT与旧稿证据。核实际服务源码与HTTP bundle哈希，正常bootstrap认证，不开insecure旁路。
真实DOM走检视→选新目录→安装→明确打开。检视/安装零Run，资源发布不等于正文已保存。503有已发布path要保回执及恢复操作，不重装同路径。
复用projectStore.switchTransaction、adoptOpenedProject.prepareDocument、openDocumentAs、waitForWorkingDocumentSave。原非空旧稿先保护；新非空稿只等原writer一次AS PUT及实际revision/正文回读，不补第二PUT；空安装稿persistEmpty:true一次。
needs_review复用原配置入口明确检查，不静默继承/清除机器授权或自动Run。核取消、切项目、迟到回包、读取失败保旧稿/已发布目录；未知响应留attempt目标且不假报成功，迟到有效receipt可显式查看。已保存回执“继续编辑”同项目保当前稿/undo，跨项目先保护旧稿再恢复最新工作副本，不重放installation.doc覆盖后续编辑。
实际继续改计算图、undo/redo、保存，切回旧项目后再从回执继续编辑，核恢复最新稿。首轮初次采用恰一次PUT计数单独冻结，后续用户编辑另记请求，不污染该判据。
真实网络/写入次数、旧稿正文、模型/输入/包成员及产物独立核；截图只辅助。组件专项不能代替浏览器。通过后完整功能commit：
feat(packages): adopt installed charts through the document writer

P1：N3f混合项目完整搬迁。
复用n3f-mixed/latest-prepared.json→attempt-04/ready.json，正式包35成员，SHA256为2de26ee47cb7f80605445f3d1d8f2c7536dfedb7aeb820e24bca993f39636bd7。准备和独立核验已通过，不重造简化夹具。含计算SVG完整INPUT、普通PNG/PDF/SVG、Python runtime/快照、同名静态两历史、外部file/prefix/空目录、原配置、双画布和非活动对象；5refs/2graphs/8blob。科学/module计数4/4，安装恢复不得自动增加；保未知通道/历史原件缺失/remap重审真实边界。正式上传检视→新API安装→界面采用，核完整资源图、引用、原序正文/参数。
仅删自建旧源/cache；新父进程/新浏览器在中文新根继续改数据/范围/样式、undo/redo、保存、冷开/恢复版本及适用scope×format导出。
模型、INPUT、bytes、几何、文字、PPI分别核；原schema2/3、Python、默认无声明补受影响回归。画布EPS沿原eps_not_for_canvas。缺陷分别fix，资格另提交；N3a–f全退出才标迁移完成。

四、后续完整队列，不降低最终范围
N4：显式图例entry→series/custom，复用legendmodel；同色/同名/顺序不是身份，复合句柄和未绑定注释保留，撤销/冷开/导出一致。
N5：成员paint slots、复杂clip/Path/ClipUnion、命中/高亮/变换共享几何；对照原SVG/预览/独立产物修文字/刻度边缘裁切。
N6/M1：双系列+图例+未绑定注释的完整计算编辑、撤销、保存、空缓存冷开、原图/标注/混合画布/多页适用scope×format；外观分享不暗带数据，完整分享携模型。
N7/M2：保SG00–SG17/A/B/C全范围，科学族、多轴/投影、文字/分组/use、事务写回、出版/预检和CLI/MCP/playground/桌面/发行入口逐格实证。普通SVG不反推Python；写回prepare→全量冷重放/几何/像素verify→commit，失败409原件零改。
N8/M3：冻结候选完整旧功能、等价性、CompatBench、E2E、性能及支持平台实际安装物资格；缺平台如实未验，不把SVG专项绿外推全产品。

五、无人值守循环
按P0.3a两项修复→受影响复验/重建→P0.3b真实UI→P1→N4/N5→M1/M2/M3连续推进。最早就绪完整功能→真实消费者→必要反证→受影响验证→核源/退出码/diff→定向功能commit→同步日期checkpoint→下一项。不要每次停在新计划或只读盘点。
已授权多子代理，只分独立有收益任务，每文件一个写入者；共享契约/集成/提交主代理单写。先联系现有代理，核状态与live句柄，避免重复启动相同任务。
Python先Ruff；前端专项后正式build；仅新改动/失败/未解疑点追加验证，最终候选跑完整链。保首红，不删测试/放宽容差/加豁免，不累计不同源码窗口的通过数。
长检查前后、功能提交及约30分钟边界记PROGRESS：HEAD/diff、所有权、SHA、命令/cwd/env、实际退出码、失败/skip、产物和下一步。只结束核过PID/创建时间的自有进程。
缺外部条件记录并继续独立项，仅缺失信息实质改变结果或超授权时询问。无人值守依赖活跃会话，未建常驻调度器；中断从checkpoint续接，不以一夜结束标完成。M1/M2/M3实际通过前保持完整目标未完成。

现在核真实差异、所有权和live句柄，先完成P0.3a/b，再进入P1；不要重做已提交N3d/时间线，也不要跳过安装整链直接扩功能。
```
