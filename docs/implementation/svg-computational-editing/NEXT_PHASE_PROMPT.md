# SVG 相关改动：开源稳定性接续提示词

2026-11-09 本轮修复完成，按用户最新要求暂停。以下只供用户明确恢复时使用，不触发自动续行。结果与真实纠正见 [PROGRESS.md](PROGRESS.md)，详细现状见 [NEXT_PHASE.md](NEXT_PHASE.md)；[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)保留完整SVG范围，未启用候选不算现有产品完成状态。

~~~text
继续 Tavotto 当前工作的稳定性收敛，保既有功能和逻辑，最大化复用原功能，使用独立有收益的多子代理并行，在同一分支按功能commit，最后完成必要测试。唯一实施树E:/0 - Project/Tavotto-svg-r00，分支zixu.wang/svg-computational-r00；先核最新PROGRESS、HEAD/diff与受影响规则。不要新建/切换分支/worktree，不reset/stash/git add .，不推送/合并/发布；原main和用户项目/data/config/INPUT/命名稿保留。常规可逆步骤自主推进。

最新产品提交71ee8c78已修写回验证/备份期间外部原件被覆盖：两入口、PDF/同stem PNG、verify/备份后8项反例已入库，关联67pass/5skip，另15项来源/归属兼容通过。8129b8d6已修内部PDF隐藏图层误显露，77项定向通过。不要重做这两项；完整重放/几何/像素两门、备份和回滚保持。最终check→replace对外部writer并非原子CAS，列独立并发结构问题，不冒已解决。证据E:/0 - Project/svg-review-20261109-resumed，源码与退出码见各DELIVERY/REPORT及XML。

重要纠正：生产native描述符调用descriptors(project_root=None)，没有original_artifact/artifacts，合法native来源是runtime；普通磁盘FileAsset解析固定safe。因此前轮推断的native磁盘origin写回回归未被真实producer成立，不为它扩私有敏感事实/新执行协议。六文件未联验WIP保E:/0 - Project/svg-review-20261109-resumed/native-verifier及native-facts，生产已恢复HEAD；不把该草稿套回。runtime native既有编辑/预览/导出仍需守真实eid/epoch，原无原件写回禁止保持。

前端41路径候选仍在E:/0 - Project/svg-review-20261109/p2-app-frontend/pause-closeout，未启用。真实缺口是snapshot-only普通safe冷开被无条件派生成历史origin，已固化origin在明确Run后仍留旧receipt；另script=null入口和当前同名素材baked/profile错误决定历史资格。先区分明确历史意图与普通输出ref、首次P捕获的实际渲染/采用依据，复用原明确运行/来源采用文档事务，保普通冷开旧流程，不借同名当前脚本冒历史INPUT。确认完整消费者/响应/回包身份后原子应用候选并补正常认证UI。

同次render正证仅有只读提案，见E:/0 - Project/svg-review-20261109-resumed/p2-live-render-proof-design/REVIEW.md，未实施/未行为验证。若采用，先以真实反例冻结可选合同：消费本次child实际eid，核同版receipt/owner/实例及本次原序P；缺正证只省略额外字段，不改变原normal render的冷build/Workerd恢复/默认shape或把成功变409。当前receipt不能给旧响应补签，svgexecution撤销事实不能授正证；不得另建runner/执行账本或为native磁盘原件扩敏感协议。

已交付的8129b8d6只修新PDF内部叶子忽略XObject /OC导致隐藏图层显露的静默错图：structured unsupported，77项定向通过/AST反证有效，不称实现OCG。内部PDF叶子未接facade/HTTP；未批准字体和多Tj精度有残差，不能直接曝光。是否继续入口能力按当前实际目标与correctness范围决定，不以旧未验候选充交付。

每批实际状态/规则→真实反例→最小结构修复→适当验证→实退出码与源码SHA/实物→定向功能commit。Python先Ruff；前端走正式build及所需派生产物/i18n，不以tsc --noEmit代替。新核心判据须反证，不放容差、不删断言。保护认证/沙盒/隐私/零PyMuPDF和config.data_dir。每60秒内给简短实质更新，仅关闭核明身份的自有进程。保所有首红和草稿，错误推断追加纠正，不抹掉历史；总目标未被完整证据证明前不标complete，不自行暂停。用户再次要求暂停时收尾当前安全批次、记录准确未完成项并停止。
~~~
