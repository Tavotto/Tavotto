# SVG 计算图表编辑：下一阶段无人值守提示词

2026-11-08更新。**当前任务按用户要求暂停。以下仅供用户明确恢复后复制，不触发自动继续。** 详细分批任务和退出条件见[NEXT_PHASE.md](NEXT_PHASE.md)，日期证据见[PROGRESS.md](PROGRESS.md)，完整范围见[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。

~~~text
恢复 Tavotto 的 SVG 计算图表编辑工作。在当前分支自主实现、修复和验证，按功能分别 commit，以完整计算编辑和既有 Python/旧项目不回归为目标。沿 IMPLEMENTATION_PLAN.md 的 SG00–17/A/B/C 与 M1/M2/M3，复用原轴/数据模型、文档事务、Preparation/runner、RenderCore/pdfbackend 和 legendmodel。

先核现场：唯一实施树 E:/0 - Project/Tavotto-svg-r00，分支 zixu.wang/svg-computational-r00；最近产品 HEAD 96cbbb33，后续状态文档提交以实际 HEAD/diff 为准，不 reset。原 E:/0 - Project/Tavotto 保留 clean main@69bcafae、未合入。不新建/切换分支或 worktree，不 stash/add 全仓，不 push/PR/合并/发布。读最新 PROGRESS/NEXT_PHASE 及受影响规则/ADR，保已有改动、原 A/B/C/D、data/config/AS/命名稿/INPUT/包与首红。本轮证据根 E:/0 - Project/svg-review-20261108；更早历史在 E:/0 - Project/svg-review-20261106。本轮未 fetch，不能称远端最新。

已提交不要重做：4985938c 支持普通 PNG/JPEG/TIFF 原图 SVG，复用 Image IR 与冻结源；保像素、alpha、物理尺寸，vector=False，不制造 Figure 回执或科学数据。96cbbb33 统一主问题面板/报告的精确普通来源、完整归档 ref+有序 patches 只读正证，报告等待当前事实；真实缺失/坏 ref、刷新/ABA、迟到及改文档反例已覆盖。此前 a4ffd5f3/1def88dc/1b7c5006/2c4d5aaa、N3e35adee6d、P0c9e2980d 与既有归档/计算编辑均保留。OUT 候选不是待自动全套应用的产品补丁。

资格：正式前端84文件1969pass/0skip，后端冻结112pass/0skip，预检同源42pass/0skip，实际exit0且对应源SHA零漂移。代理13文件475项与这些窗口重叠，不相加。旧导出7文件171pass/1skip、实际exit0，但启动前完整SHA未捕获；skip为旧EPS用例固定/usr/local/bin/gs。Ruff check/format1493文件、Oxlint、正式前端/playground/widget构建及check、i18n均exit0。4个前端与3个后端AST反证准确变红。旧109组首红3539pass/16fail/128skip保留；相关文件虽分别修复复验，完整109/全仓/跨平台最终资格仍未取得。缺Inkscape/Office/macOS/安装物证据不能标通过；无变化绿组不重复刷数。

真实 UI：round-ui/actual-jobs.json、verified-outputs.json 与 output/playwright/13.log。正常认证、真实六图顺序及6次另存冲突续行，28成功产物含5报告、1失败，仅ordinary.pdf的SVG。额外整版7b4f551976024465为PDF/PNG/TIFF+报告成功、SVG同一缺口，不混入批次数。PNG的SVG897B、一个Image、64×48 RGBA等于原像素、32×24mm；PNG原130B逐字节复制，TIFF像素相同，PDFium量PDF尺寸一致，四格式源SHA相同。Chrome真实GET200且显示正确。主问题面板与6份实际报告无missing-asset；runtime-edited的unapplied-override仍是待修历史编辑事实。截图刻度/标题裁切仍在，不能称视觉全绿。

持久化与停机：A/B/C/D图表内容、完整refs/原序patches、命名稿与counter/module/argv各4行保持。A自动保存updatedAt、派生revision/SHA及binding.working_revision变化，已有精确diff与原PUT；config变化仅最近打开时间及版本查询缓存，已有私有旧正文与字段差异，不称全部文件字节不变，未恢复覆盖。元数据字节相等误判及UI选择器/导出范围观察器首红保留。browser17 close0，原exec17200/child终局0，launcher10400/runtime39352消失、24139无监听；1794源/11HTTPbundle末核，三代理均已停止，无live句柄或调度器。不得续等终局句柄或重启旧一次性serve冒fresh。后续需要UI时建新的证据生命周期，沿已有安装data/config正常认证，不重新install/adopt/reseed/清缓存；更新检查关闭应使用TAVOTTO_DISABLE_UPDATE_CHECK=1。

第一批P3余项：已修来源误报和位图SVG不重做。核旧文档native40/48、实际源页面与显示框/放置合同，不能强改文档/源伪对齐；补标注/留白、透明/复杂SVG组与scope×format真实UI/独立实物矩阵。普通PDF无配对的SVG及含它的整版SVG仍未实现。本轮p3-pdf-svg/REPORT.md仅OUT实验：PDFium getter丢W/W*会使受控例10.2273%像素错，CID/ToUnicode反例能将01画成00，轮廓不等于live text。下一最小实验先取得可信原PDF绘制事实、实际CID/GID和裁剪状态，再接原IR/SVGwriter，保源SHA/页框/透明/资源。不能伪配同名SVG、整页栅格化或只改提示关闭缺口；该独立结构支线不阻止就绪的P2任务，缺格继续记M2未完成。

第二批P2a：读p2-integration-map，冻结完整origin/response合同。来源至少完整snapshot ref+基准有序patches，current overrides独立；只有patch_hash不得反推空基准。接原commit/undo/redo/保存/版本/多画布/复制/包闭包，首改清figureSnapshot仍保来源。同步request/renderKey/队列/latest/recent/exact/mounted/PNG/导出/写回，复用原store/writer。safe/native守卫仍是OUT候选未应用；delta-after-safe与combined二选一，接真实认证/worker/session/receipt/eid/epoch，不隐式respawn/fallback。验同次热改/undo零重跑，冷/错pj/ref/基准/receipt/eid/epoch及迟到不隐式执行、不入exact，普通Python保持。来源引用/只读正证不授执行许可。

第三批P2b→P2c：复用load_input_archive与原Preparation/execspec/runconfig/safe/native runner/租约，冻结INPUT角色/cwd/精确argv/rc和当前授权；native接原受控启动来源角色，不写用户脚本或建第二runner。明确运行后先重放基准P取得真实pair/manifest，独立核几何/像素/文字再接Q；新执行有新receipt，正式采用新ref或有界私有接纳，不伪造旧回执。取消/迟到保正文并正常reap。之后列绝对路径与归属，仅隔离自建旧源/cache，保新根/包/INPUT/AS/named/config/AIhistory/layout-sessions(.bak)。新父/新浏览器验真正删源的双画布五ref冷迁移及12成员真runtime家族包，不能用混合包kind=pdf对象替代。

后续N5→N4→M1/M2/M3：N5接源坐标S/显示框F、同次render_frame/native2及旧1兼容、schema/请求/cache/exact/frame事务/F→S几何/全scope导出。用实际glyph ink反例解决刻度/标题裁切，不缩数据/字号或仅扩viewBox。N4复用entry→series/custom绑定，验同名同色/复合句柄/手写注释、undo/保存/冷开/导出。M1普通绑定及可信/声明式双系列完整流程；M2复杂SVG/图族/投影/文字/use/写回/预检/全部CLI/MCP/playground/desktop/发行入口，替代engineTransport归档消费者尚缺；M3最终冻结后完整旧项目/Python回归、CompatBench/等价性/E2E/性能及支持平台安装物。缺实现不标不适用、未验不标绿，三门全退出前不complete。

执行规则：每批真实状态/规则→原消费者实现→必要反证→受影响检查→实际exit/源SHA/产物→定向功能commit→日期checkpoint。仅独立有收益任务按会话已有授权委派，每文件一个写入者，root统一集成/浏览器/提交。Python先Ruff再pytest，前端用正式build类型检查及必要派生产物；源未变不重复绿组，失败先分产品/夹具/观察器，不删断言/放容差。工作更新≤60秒，长检查与提交记录进度。只关闭身份核明自有PID，末核HTTP/源码/持久稿/监听。保认证/沙盒/隐私/零PyMuPDF/config.data_dir和prepare→完整重放/几何/像素verify→commit。常规可逆步骤自主推进，实质缺信息或超授权才询问。用户再次要求暂停时完成当前可安全收口批次并停止，不自行创建常驻调度。
~~~