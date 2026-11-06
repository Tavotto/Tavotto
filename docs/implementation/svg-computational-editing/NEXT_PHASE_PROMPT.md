# SVG 计算图表编辑：下一阶段无人值守提示词

可直接复制以下提示词。2026-11-07 00:07（Asia/Shanghai）复核，实际工作树优先。详细任务：[NEXT_PHASE.md](NEXT_PHASE.md)；进度唯一台账：[PROGRESS.md](PROGRESS.md)；最终范围：[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。

```text
继续完成 Tavotto 的 SVG 计算图表编辑，保留现有 Python 绘图、静态素材和其他已有功能。最大化复用现模型、编辑器、事务、writer、RenderCore/pdfbackend 和 legendmodel；持续自主实现、验证、修复，在同一分支按功能分别 commit。常规可逆步骤自行推进，不逐批等待确认。已授权多子代理，只分配独立且有收益的任务；共享合同、app、Schema、规则、文档、集成和提交由主代理唯一写入，同一文件同时只有一个写入者。

一、恢复真实状态，沿用当前分支

实施树：E:/0 - Project/Tavotto-svg-r00
唯一实施分支：zixu.wang/svg-computational-r00
原目录：E:/0 - Project/Tavotto，仍为 clean main@69bcafae，未合入成果。
稳定文档：docs/implementation/svg-computational-editing/{PROGRESS,NEXT_PHASE,NEXT_PHASE_PROMPT}.md
证据根：E:/0 - Project/svg-review-20261106

复核时功能 HEAD=13720bc4；随后可能有文档或实现提交，以实际 HEAD/index/diff 为准，禁止 reset 到此 hash。两份未提交代码是 src/tavotto/engine/svgstore.py 和 tests/test_svgstore_portable_inspect.py，由主代理接管，先核 SHA/diff，不覆盖。此前子代理均已完成，没有在途写入任务；三份稳定文档同步本次复核。

先核分支、提交身份、Python 实际 import 路径、自有进程，再读 AGENTS 和涉及层的规则/ADR。沿用当前树/分支，不新建或切换分支/worktree，不 reset/stash 覆盖已有改动，不 git add . 混批，不 push/PR/合并/发布。复用 venv/node_modules；测试 config/data 隔离、遥测关闭，运行可写数据沿 config.data_dir()。

注意共享 venv 的裸 Python 默认 import 另一旧树。pytest.ini 的 pythonpath=src 已经独立 collection 核实正确；手工 Python/服务/子进程须显式令 PYTHONPATH 指向本实施树 src（隔离模式用既有显式源码入口），在实际进程核模块 __file__。不得仅凭 venv 路径判定测试/服务源码。

二、保留已完成，不重复实现

- 既有显式 SVG 绑定、计算编辑、精确 INPUT 附件、v4 正文/冷恢复、EPS 完整接线、命名关联/冲突恢复均保留。原图五格式已有单曲线真 UI 和独立产物证据，完整双系列 M1 尚未通过。0b611411 预检、d7a73ce1 保存、e468d516/336783bf 旧包、71e637c8 原 writer 回执、206a1c34 预算工厂不重做。
- 9a9843d2 统一目标计划、8ed50616 实际 POST /api/documents/v4/package 和项目菜单已提交。旧只读 schema2/3 open/relink 保持；不能让 v4 走会清 edits 的旧 relink。
- 75b09b57：runconfig.import_portable 保原 rc_ID/精确 argv，全部新版配置 writer 共用跨进程 guard；145 通过、0 skip，真实双进程/冷进程已验。不复制默认/成功事实/授权，不是跨版本 CAS；atomicio 替换后目录 fsync 失败也不保证字节回滚。
- be84ba33：execution v1、已知登记/脚本/CSV/helper/参数/内部精确 file remap 已接正式导出和准确 coverage UI。最终联合 718、另 8 新 HTTP、56 前端各范围通过，不相加或称全分支回归。真菜单/ZIP/实际整份请求/输入字节/rc_ID/argv 已核，计数仍 1；未声明完整输入时有脚本包准确 incomplete。
- 13720bc4：旧图幅迁移只有实际追加 FRAME 才撤旧快照引用，其余路径保留。1c9da4ca 只将旧测试改等真实持久化回执，未改生产时序。100 项、lint/build/playground/check 均通过，指纹 9c11afde28799951。新鲜浏览器旧文档立即菜单导出 200，ZIP 仅补正确 FRAME 引用，原编辑/参数保持，计数仍 1。

读 n3c-execution-export-staged.json、n3c-real-ui-correct-kind-qualification.json、n3c-frame-ui-qualification.json、n3c-compat-frontend-build.json。原失败、夹具错误、超时和权限 skip 保留，不改写历史。

三、当前第一批：N3b 产品严格检视，并行准备 N3c-3

最近交付目标是“包带到中文新目录，原目录不存在、缓存清空、新父进程/浏览器下，仍可恢复并继续计算编辑”。依赖顺序：N3b 严格检视 → N3c-3 完整输入/外部资源 → N3d 安全安装 → N3e 原 writer UI → N3f 整链资格。N3c-3 独立资源工作可与 N3b 并行；共享 execution/manifest/app 由主代理统一接线，合同稳定前不并行接安装 UI。

N3b：实际 v4 只读检视 API 与全部资源语义。
1. 复用 inspect_chart_package、活跃 Prepared、plan_project_resources 和唯一 portable 比较键；核 CRC/路径/摘要/预算、跨 namespace 目标碰撞、全部资源及引用。不任意 extract，不执行陌生脚本；检视失败/取消零项目写，必要暂存只在隔离 data_dir 并回收。
2. 未提交 inspect_portable_snapshot 仅证明档案内部身份/owner/PDF+SVG 配对 blob，已有 243 通过、31 Windows symlink 权限跳过、4 反证，尚无生产消费者。先读 n3b-svgstore-portable-freeze.md/SHA，沿原 svgstore 核全部归档引用/闭包，再核 execution/资源/remap 关系。摘要不是签名，历史 receipt 不是当前执行授权，不能漏查未被某面板引用的存储成员。
3. 安装只消费已核冻结字节。真实 HTTP 正反例、认证、取消零项目写、检视后源变化、坏 owner/receipt/basis/缺 blob/碰撞均验；实际消费者通过才提交产品检视，不把叶子称完整安装。

N3c-3：完整输入声明、外部与 prefix 资源。
1. 保留 execution v1、配置导入及事实收集，沿 Registry/runconfig/launchctx/workdir/inputremap/databinding 唯一权威。静态分析和成功 receipt 不能证明任意 Python 的完整依赖集。
2. 实现可核完整输入声明、准确 coverage 和实际 UI。无声明仍 incomplete；明确声明且验证完整的项目必须能走完整迁移，不能永久只报待补。补动态输入、项目外显式选择资源、prefix 成员集合与来源变化；不静默丢资源/改用户 Python。
3. 外部读取来自既有明确选择/授权，不将 argv 路径猜成读取权限。敏感参数、本机环境/cwd 授权和成功执行信任不复制。真实数据/helper/参数/remap/快照在新根消费，保 rc_ID/科学身份/argv。完整声明与外部/prefix 功能分别 commit。

四、随后接通安装与整链

N3d：独占创建新项目根，完整保留相对 sourceId/script/runtime/snapshot 身份。projects/open create=true 会复用目录，copy_project 可 skipped>0 成功，都不能直接当严格安装器。复用排他 reserve/原子发布，逐份摘要/语义核对及必要受控重放；同名/并发/断写/源变化保旧目标和正文，不以改 panel.fileId 规避冲突，不继承发送者本机绑定/绝对路径。检视/采用不自动执行陌生 Python，需执行沿现授权/worker 入口；完整闭包和恢复资格分别表达。后端独立 commit。

N3e：复用 projectStore.adoptOpenedProject(status,{prepareDocument})、switchTransaction、openDocumentAs；最终正文在 beforeInstall 前固定，前后核 pj/format/documentId/loadSeq/意图。waitForWorkingDocumentSave 已存在：非空只等待原 switchDocument 排队的一次正文写及实际回读，不补第二 PUT；空稿显式 persistEmpty 一次，await switchDocument/一轮微任务不代表落盘。取消/切项目/迟到/超时/资源成功但正文失败/正文成功但元信息未确认准确分列，旧稿保留。中文/i18n/真 UI 验完独立 commit。

N3f：冻结自建混合 SVG/普通素材/Python runtime/快照、多画布、中文目录夹具。仅删除自建测试原目录，清缓存，新父进程/浏览器开新项目；实际编辑、undo/redo、保存、冷重开、版本恢复、全部适用 scope×format。独立核模型/完整 INPUT/资源/参数/几何/文字/PPI，补旧 schema2/3 和原 Python 受影响回归。实际缺陷分别 fix，资格记录独立 commit；N3a–f 全退出才标完整迁移，不以一个菜单成功或一夜结束作完成条件。

五、N3 后的完整队列

N4：显式 SVG 图例条目→系列/custom 关系和持久化合同，再复用 legendmodel 跟随/脱开/handle_* 与原 paint slot。曲线改色而旧图例不变是已证实缺陷，不按颜色/同名/顺序猜。验同色同名、多系列、复合句柄、范围/数据变更、撤销、冷开、导出；重绑迁移保科学身份，未匹配项显式可撤销。

N5：成员 paint slots、共享 SVGIR Path/ClipUnion 的祖先 AND/子形状 union/evenodd/objectBBox/运动。命中距离/框选/高亮/宽描边共用几何，圆外/孔洞实际拒绝。截图刻度/文字边缘仍可见裁切，先比原 SVG、预览和产物确定根因，再修对应层。

N6/M1：纯外观分享不暗带模型/数据，完整分享含全部编辑/模型/数据；真实双系列+图例+未绑定注释→绑定/数据/范围/样式→精确选择/undo/redo→正式保存→空缓存新父进程/浏览器→原图/标注/混合画布/多页全部适用 scope×format 的独立产物核对。画布 EPS 保持既有 eps_not_for_canvas。

N7/M2：SG00–SG17 不减，A 可信来源/C 自包含、文字/分组/use/图元、适用科学族/多轴/投影、事务写回、出版/预检/样式、CLI/MCP/playground/桌面/发行入口逐格验。普通 SVG 不反推原 Python；写回 prepare→verify 全量冷重放+几何+像素→commit，失败 409 原件零改。

N8/M3：冻结候选做完整旧功能/等价性/不变式/CompatBench/E2E/性能/支持平台真安装物。既有 Windows desktop PID 回执失败需处置，缺平台列未验证；SVG 专项全绿不证明全部现有功能未受影响。

六、无人值守执行合同

每批：读真实 checkpoint/选就绪任务 → 明确不变量和可观测退出条件 → 实现并接真实消费者 → 必要反证/受影响验证 → 核 diff/源码身份/真实退出码 → 定向 stage/功能 commit → 同步稳定台账 → 继续下一就绪任务。无新增疑点不重复昂贵检查，不以反复写计划代替实现。

Python 先 Ruff 再针对性 pytest；前端专项 Vitest 后正式 build，按影响补 i18n/playground/真 UI；最终候选才完整回归。skip/未执行/平台缺口照实记，重叠组不累计，不删测试或放宽容差。新增核心不变量做实际产品落点反证；同类两轮仍出边界，先查共同结构/单一权威。源码结构用 AST，热态/落盘/冷开/导出分别量。

每次功能提交、长检查前后、约 30 分钟安全边界更新 PROGRESS：HEAD/index/diff、所有权、源码 SHA/命令/cwd/env/退出码、失败/skip、下一动作。NEXT_PHASE/PROMPT 只维护当前队列，保留历史证据。阻塞单项先转独立就绪工作；仅缺失信息实质影响结果或超授权时询问。日志/临时 fixture 留外部证据根；Playwright CLI 沿 run-playwright-captured.py 原始 UTF8 捕获，走真实 DOM，不直接改 store 伪造用户操作。

服务线索须重新核 PID/创建时间/命令后使用：64860（2026-11-06T23:26:50.033107+08:00，n3c-browser/server.py，23989）服务当前 web/dist，禁止重新 seed 覆盖证据。fresh 浏览器 tavotto-execution-v4=39048（23:40:27.365023），tavotto-frame-legacy=75500（2026-11-07T00:01:17.572366），后者 working d_n3c_legacy_frame_fix 是 fixture durable last，原 d_n3c_browser 与 named 保留。旧 N3a 后端75464/23988（22:45:34.778615）和 tavotto-package-v4=66204（22:13:21.576551）是旧资格服务，不称当前 N3c 服务。更早 PID 仅历史线索，只结束精确身份已核的自有进程，不按模式广杀。

未创建定时任务；无人值守指活跃会话持续迭代，休眠/会话结束后按 checkpoint 接续，不宣称提示词能唤醒机器。M1/M2/M3 均未通过，goal 保持 active；状态询问简短答复后继续原目标。

现在核真实 HEAD/diff，读 N3b 冻结 API 与 N3c 已提交资格，接 N3b 实际严格检视消费者，并行推进独立 N3c-3 输入/资源功能，再 N3d–f。不要重做配置导入、已知执行事实导出或 FRAME 修复，不停在新的盘点报告。
```
