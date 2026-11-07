# SVG 计算图表编辑：下一阶段无人值守提示词

2026-11-07 16:30更新。**当前任务已按用户要求暂停。以下仅供用户明确恢复后复制，不触发自动继续。** 详情见[NEXT_PHASE.md](NEXT_PHASE.md)，证据与历史见[PROGRESS.md](PROGRESS.md)。

~~~text
恢复 Tavotto 的 SVG 计算图表编辑工作，在当前分支自主实现、修复和验证，按功能分别commit，以完整计算编辑和既有Python/旧项目不回归为目标。沿IMPLEMENTATION_PLAN.md的SG00–17/A/B/C与M1/M2/M3，复用原轴/数据模型、文档事务、Preparation/runner、RenderCore/pdfbackend和legendmodel。

先核现场：唯一实施树E:/0 - Project/Tavotto-svg-r00，分支zixu.wang/svg-computational-r00；上轮产品HEAD2c4d5aaa，文档提交可继续前进，以实际HEAD/diff为准，不reset。原E:/0 - Project/Tavotto保留main@69bcafae、未合入；不新分支/worktree、不切分支、不stash/add全仓、不push/PR/合并/发布。读最新PROGRESS/NEXT_PHASE及受影响规则/ADR，保已有改动、原A/B/C/D、data/config/AS/命名稿/INPUT/包和首红。证据根E:/0 - Project/svg-review-20261106；上轮未fetch，不称远端最新。

已提交不要重做：a4ffd5f3平台/离线夹具；1def88dc静态SVG合成/overflow/原图单scene；1b7c5006批量continuation/终态归属和runtime合法默认名；2c4d5aaa既有panel-stem精确原图事实、assetStore代际缓存、PDF位图实际mm。N3e35adee6d、P0c9e2980d和既有归档/编辑功能保留，OUT候选不是待自动全套应用。

资格：正式76文件1726前端全过；当前后端定向56全过；较早受影响194pass/1skip；平台两完整文件253pass/25skip；Ruff1492与新正式/playground/widget构建及check均0。各窗口不累计，完整109首红3539pass/16fail/128skip仍保留；16条已在相应文件分别修复/复验，未重跑全109，更非最终全仓资格。缺Inkscape/Office/macOS/发行矩阵不能标通过。最终badSVG与host守卫的有限反证已保留，不重复无变化绿组。

真实UI：p3-batch-export-ui/26.json、review-actual-jobs.json、review-artifacts-final.json及review-original-artifacts-final.json。整版250a2e1ce97748a4 PDF/PNG/TIFF成功，SVG因ordinary.pdf缺可信配对partial；独立180×130mm、4252×3071@600、文字/12色点、PNG/TIFF像素及7对象6panel/2fullref/有序patch保持。六图顺序无跳过，5次冲突经另存续行；chart/ordinarySVG/History/runtime各四格式done，PNG/PDF三格式+SVG明确失败；27成功含5报告，2失败，不混前整版。PNG130B/64×48原字节/dpi=null；PDF100pt≈35.278mm、833²@600及回执正确；普通SVG四格式≈42.333×21.167mm、文字/颜色正确。观察器原PDF容器byte相等误判已纠正为既有copy_foreign下的独立像素/来源SHA，首红留存。

停机：browser30close0、exec40459和child61324终局0；launcher79648/child不存在、24129无监听，1792源/11HTTPbundle及持久稿末核通过，counter/module/argv各4。无旧live句柄，不续等；旧一次性serve不要重启冒fresh，继续已有安装data/config，不重新install/adopt/reseed/清缓存。恢复后如需UI，建独立证据生命周期和正常认证窗口，不注入store或伪造成功请求。

验证局限：收尾审计正文/绑定/计数及12份非config文件SHA相同，隔离验证环境config.json SHA变化，旧正文未捕获，不能推断具体字段原因或称配置字节未变；首红留存，未覆盖配置。下一轮启动前保存可脱敏比较的配置基线。文档41项与预算检查exit0，预算既有OVER提示仍在。

第一批先修P3余项：问题面板/样式报告对隐藏PNG与归档runtime仍误报missing-asset。复用现有精确来源与fullref正证，保真实缺失反例、代际/刷新/host边界；正证不授执行。核旧文档native40/48与实际源页面、显示框/放置合同，不能强改源/文档伪对齐。PNG/PDF→SVG与整版无配对PDF能力仍未完成，按第三路径审结构做矢量实验；不伪配同名、不删对象或栅格化关闭缺口。补标注/留白/透明/复杂组真实UI矩阵，缺实现留M2未完成。按完整功能commit，不重做已提交修复。

第二批P2a：读p2-integration-map，冻结完整origin/response合同，来源至少完整snapshot ref+基准有序patch，current overrides独立；只有patch_hash不得反推空基准。接原commit/undo/redo/保存/版本/多画布/复制/包闭包，首改清figureSnapshot仍保来源。同步request/renderKey/队列/latest/recent/exact/mounted/PNG/导出/写回，复用原store/writer。safe/native守卫候选未应用；delta-after-safe与combined二选一，接真实认证/worker/session/receipt/eid/epoch，不透明respawn/fallback。验同次热改/undo零重跑，冷/错pj/ref/基准/receipt/eid/epoch及迟到不隐式执行不入exact，普通Python保持。

第三批P2b→P2c：复用load_input_archive和原Preparation/execspec/runconfig/safe/native runner/租约，冻结INPUT角色/cwd/精确argv/rc与当前授权；native补原受控启动来源角色，不写用户脚本或建第二runner。明确运行后先重放基准P取得真实pair/manifest、独立核几何/像素/文字再接Q；新执行新receipt，正式采用新ref或有界私有接纳，不伪造旧回执。取消/迟到保正文并正常reap。再清点仅自建旧源/cache并隔离，保新根/包/INPUT/AS/named/config/AIhistory/layout-sessions(.bak)；新父/浏览器验真正删源双画布五ref冷迁移及12成员真runtime家族包，不能用混合包kind=pdf对象替代。

后续N5→N4→M1/M2/M3：N5接S源坐标/F显示框、同次render_frame/native2及旧1兼容、schema/请求/cache/exact/frame事务/F→S几何/全scope导出，实际glyph ink反例解决刻度/标题裁切，不缩数据/字号或仅扩viewBox。N4复用entry→series/custom绑定，验同名同色/复合句柄/手写注释、undo/保存/冷开/导出。M1普通绑定及可信/声明式双系列完整流程；M2复杂SVG/图族/投影/文字/use/写回/预检/所有CLI/MCP/playground/desktop/发行入口，替代engineTransport归档消费者尚缺；M3最终冻结后完整旧项目/Python回归、CompatBench/等价性/E2E/性能及支持平台安装物。缺实现不标不适用，未验不标绿，三门全退出前不complete。

无人值守规则：每批真实状态/规则→实现原消费者→必要反证→受影响测试→真实exit/源SHA/产物→定向功能commit→日期checkpoint。仅独立有收益任务按已有授权委派，每文件一个写入者，root统一集成/浏览器/提交。Python先Ruff再pytest，前端正式build类型检查及必要派生产物；源未变不重复绿组，失败先分产品/夹具/观察器，不删断言/放容差。用户工作更新≤60秒，长检查/提交记进度；只关闭身份核明自有PID，末核HTTP/源码/持久稿/监听。保认证/沙盒/隐私/零PyMuPDF/config.data_dir和prepare→完整重放/几何/像素verify→commit写回事务。常规可逆步骤继续，只有实质缺信息/超授权才询问；用户再次要求暂停时完成当前可安全收口批次并停止，不自行创建常驻调度。
~~~
