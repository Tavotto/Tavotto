# SVG 计算图表编辑：下一阶段无人值守提示词

可直接复制下面的提示词续接实施。详细依赖和退出条件见 [NEXT_PHASE.md](NEXT_PHASE.md)，实际进度唯一台账为 [PROGRESS.md](PROGRESS.md)；最终范围保持 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) 的SG00–SG17。状态截至2026-11-06 20:02（Asia/Shanghai），续接以实际树为准。

```text
继续完成 Tavotto 的 SVG 计算图表编辑，在保留现有功能和逻辑的基础上达到最终用户可用。按无人值守方式持续实现、验证、修复并按功能commit；已授权多子代理处理独立模块，一个文件同时一个写入者，根代理统一契约、共享文件、集成及提交。常规可逆步骤自行推进，不逐批等我确认。

一、恢复当前任务，不另起分支

唯一实施树：E:/0 - Project/Tavotto-svg-r00
唯一分支：zixu.wang/svg-computational-r00
原目录：E:/0 - Project/Tavotto，clean main@69bcafae，尚未合入新功能；不要在那里覆盖/自动合并后判断效果。
稳定文档：docs/implementation/svg-computational-editing/{PROGRESS,NEXT_PHASE,NEXT_PHASE_PROMPT}.md
证据根：E:/0 - Project/svg-review-20261106

先读当前AGENTS和改动层最近规则/ADR，再核HEAD、index、diff、Git身份、Python实际import源、代理和自有进程。沿用当前分支，不新建/切换分支或worktree，不reset/stash覆盖草稿，不git add .混批，不push/PR/合并/发布。当前功能HEAD=ec2cb144，后续文档或功能提交可能继续前进，不能回滚到此hash。

特别保护：app.py当前已stage的是独立EPS候选，index blob=e1a9c89c2bd2d759a04153cdc16ccc33cdfbb3f2，物理SHA256=bf98579d445fcb68d755944dd0b701db619219b9c4b04f8b4b6c4c2396ecfd1f。N2.1尚无app HTTP路由。先核实际索引；保留EPS边界，后续binding改动不能被重新stage混进EPS提交。其他前端/关联/package草稿未冻结部分不能一并提交。

二、已完成和已验证的东西保留，不重做

- 2452d8fc：精确INPUT附件进入render/PNG/缩略图/timeline/重建/导出；有ref必须校验附件，坏ref不借同名原源。
- 5abb9a7d：v4命名/autosave/session/versions/thumbs隔离及严格来源冻结；20项真实HTTP/冷父进程、旧312通过/20skip及反证有报告。
- 95684f89：跨格式共享打开意图与显式GET/repair；明确打开分总序，自动保存不抢新文档，不用mtime/max-at猜。
- 264ce59c：N2前端43文件+ADR/规则/生成速查表已提交；单一documentStore、schema3/4边界、首次计算fork、位置回执、格式化地址/队列/冷恢复/versions等保持。
- c9fa29a4：完整矢量场景准备EPS；8fa84f5d：共享nested SVGIR viewport/overflow/clip修复。已独立提交，不重新实现。
- ec2cb144：保存结构化错误已提交；191项/正式build/真实来源422通过，显示实际后端原因且本机稿保留，不再误报磁盘空间。
- 更早242cabc0/c2fd47d5/53393810/8e6c399a提供共享native编辑器、原序生产导出、v2编辑恢复和最终多DOM目标；5b2b1403保留受控runner/lease/预算/deadline/owned process。完整记录见PROGRESS。

真实用户效果：正式命名保存N2_正式保存_20261106，删原chart.svg，空localStorage新浏览器+新后端自动恢复同v4工作ID d_kyyf7jn1，线宽3.02→4.03→undo→redo，sourceRef不变且exact。PDF/PNG/SVG独立产物已验；冷开projectFile=null仍是未完问题。

新EPS候选：公共契约/owned action102项、app HTTP15项通过；旧EPS合组84通过/2skip；offer修复429项前端/正式build通过。真实ExportDialog保留EPS，PDF/PNG/EPS/TIFF/SVG五格式作业27b3fd4aa3fb4750均done。eps-ui-five-formats-qualification.json独立核对完整模型/原序edits/374B INPUT、PDF100pt及文字、PNG/TIFF833²与TIFF600dpi、EPS经Ghostscript实际成图位置/4.03pt线宽；原源仍缺席。首次勾选反例和后来terminal wait超时保留：后者是SSE完成后停止轮询，实际最终job另读成功，未重导出来覆盖。接线/route/UI仍未commit；单曲线原图不代替完整M1。

所有测试范围/源码时刻不同且有重叠，不累计成最终总数。旧43文件SHA不能覆盖N2.1新稿。最新playground曾因并行源变化指纹不一致；冻结后重建/check，不称当前中间稿全绿。Windows旧desktop PID回执失败有原版对照，尚未解决最终资格；M1/M2/M3均未通过。

三、下一阶段执行队列

1. EPS先收口完整功能提交。
   根核native-eps-wiring.json、eps-route-freeze.json中的后端与最新eps-offer-freeze.json的8前端SHA；重叠前端以最新offer冻结为准。保留严格含formats的routeKey，EPS offer仅复用同scope/project/完整来源事实的后端how/reason最小投影，UI与request消费同一份，不复制授权规则/不沿用旧完整计划。
   同步ADR0166中过时eps_needs_script和相关export/RenderCore规则；核根app已stage候选、15项HTTP、正式build和真UI证据，独立commit，排除binding/package中间稿。画布EPS仍eps_not_for_canvas，partial alpha/gradient/mask边界按真实表达处理，不伪PDF或静默整图栅格化。真实五格式仍显式ack了missing-asset/unapplied-override误报，预检尚未修，保留N4/N7任务。

2. N2.1保存关联为当前最高产品优先级。
   复用现layout-sessions/data_dir、原锁/atomicio，(project,format,workingId)寻址closed record；不建第二store/DB，不把机器路径塞入可携正文，不按标题或portable project.id猜目标。layoutsession.py关联叶子已冻结37项，旧合组230通过/10既有skip，5实际反证；测试/报告在project-file-binding-*，HTTP契约见project-file-binding-api.md。
   根先补cancel_project_file_intent(key,doc_id,sequence)：原锁内只清匹配当前pending，过期零写superseded，保留target/axis/计数；不让app读私有DB、不信客户端整ticket、不嵌套非重入锁。
   根独占app接GET /api/layout-session/project-file/<workingId>及/reserve、/confirm、/intent/<sequence>，v4经原别名注册复用认证/namespace。named POST仅显式working_doc_id/binding_intent增加关联；AS仅显式project_file_edit闭式JSON增加轴，旧缺省wire/实参数量保持。reserve验证实际已刷AS revision/axis，目标name/file由后端规范；confirm在named锁内验证实际revision。AS同document锁内写前prepare→原子正文→同证据commit_slot，不得先写body再拒绝。禁止嵌套两个可能命中同stripe的非重入document锁。
   HTTP口径：业务superseded/unconfirmed为200+state；named实际变更409external_change；AS轴拒绝409stale_write且旧body零改；坏字段/null/非法seq400；future修改503，GET unavailable200；正文成功但元信息失败仍200并单列unavailable，不得诱导覆盖重试。
   前端svg_self_contained独占关联helper/API/writer/SaveContext/store/restore/open/对应测试+i18n，不碰EPS。每editor实例纯内存editor_id，derived保持继承轴，第一真实编辑新actor+seq；不可跨tab取max。hydrate完重捕获最新body进原DiskWriter，不能替换已排队ancestry。reserve须用已刷真实快照，不能旧票据配新body；binding sequence参与ctx防ABA。只有明确404回旧wire，503/未知保持binding/dirty。
   前次正文成功/meta失败时先仅confirm；确认后如仍有新编辑，在project/format/workingId/loadSeq/保存意图仍匹配时重捕获新ctx交同队列，不放宽旧ctx sequence判断、不自动重写旧body。superseded停止旧票据，冷开无可靠票据不能猜关联。
   根app接好后才collect+跑tests/test_layout_project_file_http.py；该文件仅Ruff/格式已过，未跑pytest，不把预计数量算通过。前端初轮5文件86项只是阶段检查，新有状态链/build/i18n/playground/反证及真UI均待补。
   验收：保存A后改标题B且另有B→清缓存/新父进程仍关联A；仅AS新编辑冷开dirty；外改A后CtrlS409且文件零改；A/B/ABA迟到回执、多tab、双format/不同项目同ID不串；正文/meta各故障准确报告。外部AS漂移unknown还须显式安全恢复，不把永久不能保存或静默洗轴当完成。按可独立检出的功能分别commit并同步规则/ADR。

3. N3包原子输出与显式安装搬迁。
   documentpackage.py/atomicio.py/专项已冻结121项及4反证，尚未接app。先替换旧api_package直接写最终ZIP为staged_package：临时asset在helper退出前立即流入，add失败即锁定整包失败，CRC/结构验证后原子发布；不无依据给旧合法大项目加低cap。内部合法symlink资源需在原containment校验后捕获canonical目标，保持旧manifest相对ID/可用性，防外部目标与竞态。
   再明确caller预算/manifest契约，复用现容器、来源附件和写回事务做v4安装；不是只读package/open。路径/摘要/资源/版本/预算验证→安装附件→实际重放→单次正文提交，坏包/断写/冲突不覆盖用户脚本资产/旧正文，不任意extract。新目录/中文路径/移盘、删原源、版本恢复、旧包和旧客户端逐项验；独立叶子可恢复svg_ir_fidelity代理，只授一个文件范围，根独占app/安装整合。

4. N4图例/标题/数据/readiness及逐对象迁移。
   显式科学身份驱动系列与图例同步，未绑定装饰保留，不按颜色猜；范围/数据变更保合法编辑，重排/删除对象不套错，不匹配项显式且可撤销。预检/项目状态消费同一计算来源/附件事实，不能有效附件仍报缺失/未应用或硬编码可编辑。实际UI和导出独立核对后分别commit。

5. N5成员placement与复杂clip几何。
   保留多DOM成果，解决source/compound/native成员paint slots真实偏序，不搬装饰造容器。复用SVGIR Path/ClipUnion，统一祖先AND/子形状union/evenodd/objectBoundingBox/成员运动；命中距离、框选、高亮、宽描边墨迹一起验。圆外/孔洞真实反例不得命中，不能只判断查询点inside/强设composed。外部native-clip-geometry-design.md原型证据不算生产已实现。

6. N6分享选择与完整M1。
   纯外观不暗带模型/数据，完整模式显式含模型/数据/全部编辑。真UI双系列+未绑定注释→轴/数据绑定→范围/样式/图例→精确选取/undo/redo→正式保存→清缓存/新父进程→原图/标注/混合画布/多页scope×format，独立读取文字/矢量/尺寸/PPI/像素。EPS画布保持原拒绝合同。全部退出才M1，通过立即继续M2。

7. N7/M2能力对等，再N8/M3最终资格。
   保持SG00–SG17完整范围：A可信来源采用、C自包含、文字/分组/use/图元、全部适用系列/统计/色阶/多轴/投影、事务写回、出版/预检/样式、CLI/MCP/playground/桌面/发行入口逐格验。普通SVG不反推原始Python，不猜相邻同名脚本。写回prepare→verify全量冷重放+几何+像素→commit，失败409原件零改。
   冻结最终候选，跑完整旧功能/等价性/不变式/CompatBench/E2E/性能和支持平台真实安装物；新SVG专项不能代替旧功能回归，缺平台写未验证。处理已有PID失败对资格的影响。每功能分别commit，不因M1、一夜结束、轮次/上下文耗尽缩减目标。

四、验证和持续执行纪律

- 判据先说主语：谁的哪个进程/时刻/维度；最终图/落盘/冷开/产物分别量，metadata/path数量/文件存在不能代替科学视觉正确。
- Python先Ruff再针对性pytest；前端受影响Vitest→正式tsc-b/Vite/Tailwind，再按改动i18n/playground/真实UI。核真实退出码，skip/未跑不写通过；已通过且源码未受影响不反复跑，最终冻结候选完成项目要求全量检查。
- 新核心不变量做真正落点反证，先红后恢复生产字节再绿；不删测试、不放宽容差迎合错图。同类两轮仍出新边界查结构/单一权威，换反例，不无限补丁。
- 最大化复用原模型、事务、能力/属性/几何、RenderCore/pdfbackend、锁/资源/runner；零PyMuPDF、会话认证、worker沙盒、隐私和运行数据data_dir不变。
- 根审精确stage范围再按功能commit，保留所有用户改动，不加Co-Authored-By。失败日志/反证/源码身份落仓库外证据根；不安装重复依赖或破坏共享venv/node_modules。
- PROGRESS是唯一进度台账，NEXT_PHASE/PROMPT维护当前队列；功能提交/长检查前后/约30分钟安全边界checkpoint，记HEAD/diff/index、源码与命令/cwd/env/退出码、代理所有权、真实自有PID+创建时间+命令、缺口和下一动作。不能仅凭旧PID杀进程，也不能重seed覆盖已有证据。
- 一项退出后选下一就绪项，不等待我确认。用户询问状态时简短回答后继续原目标；不要停在反复更新计划。真实平台/权限阻塞只阻塞依赖项，继续其余就绪工作。
- 仅全部完成、用户明确停止、运行条件强制中断，或所有有意义工作确实需要外部变化时结束。goal仍active；不把本轮状态审计当整项完成。

五、当前服务和恢复入口

20:02核验：后端66888（19:32:55，source-ref-browser/server.py，23984/session39115）；Vite10168（18:51:24，native-edit-browser/vite.mjs，23983/session80916，含N2.1中间稿）；冻结EPS预览76372（19:48:20，eps-preview.mjs，23985/session82628）；Playwright tavotto-eps-ui daemon24696（19:48:46）、tavotto-v4-cold daemon71840（18:57:08）。旧后端10460已结束。先核实时归属，不照数字杀；后端载入EPS候选但无新binding HTTP，接线后需按原隔离fixture重启，不重seed。

冻结dist在证据根eps-ui-dist-1945，独立于正在变化的web源码；截图output/playwright/eps-ui-five-formats.png及n2-v4-cold-edited.png。服务不是无人值守调度器；未创建新定时任务，提示词不会唤醒休眠机器或恢复已结束会话。活跃执行会话持续做可执行工作，中断后从真实checkpoint接续，不重新生成整套计划。

现在核实际树/index/代理：收口EPS功能提交，同时完成N2.1集成→N3包→N4/N5→N6/M1→N7/M2→N8/M3。不要用旧冻结文件覆盖正在写的新稿，不把专项绿灯称最终可用。
```
