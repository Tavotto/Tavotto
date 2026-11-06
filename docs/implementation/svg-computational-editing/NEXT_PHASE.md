# SVG 计算图表编辑：当前效果与下一阶段安排

> 2026-11-07（Asia/Shanghai）复核。当前实施树 `E:/0 - Project/Tavotto-svg-r00`，沿用 `zixu.wang/svg-computational-r00`；复核基线 `5884ede2`，最新已提交功能 `8a13ed95`。3d 引用迁移已有实现和专项验证，尚未功能提交；扩大回归有 2 项失败，先收尾再安装。原目录 `E:/0 - Project/Tavotto` 为 clean `main@69bcafae`，未合入成果。
> 进度唯一台账：[PROGRESS.md](PROGRESS.md)。完整范围：[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。可复制提示词：[NEXT_PHASE_PROMPT.md](NEXT_PHASE_PROMPT.md)。M1/M2/M3 均未通过；不以测试数量或工作时长估算完成比例。

## 1. 当前实际效果

| 能力 | 已实现和已有证据 | 当前边界 |
| --- | --- | --- |
| 普通 SVG 的计算编辑 | 显式轴、系列、数据绑定；范围/线宽等复用原编辑器；undo/redo、保存、精确 INPUT 冷恢复有真实 UI 证据 | 图例联动、复杂几何、全部科学族及入口尚未逐格验收 |
| 原图导出 | 已有单系列真 UI 五格式及独立产物验证；计算原图 EPS 接线保留 | 完整双系列、图例、混合画布/多页的适用 scope×format 尚待 M1 |
| 输入声明界面 | `8a13ed95`：原准备卡及脚本只读菜单；保存声明零执行、明确 Run 恰一次、取消/切项目/真实迟到响应保稿已验 | 声明文件集合不代表任意 Python IO 闭包；不为旧 Figure 补签新证据 |
| 计算当时输入的保存与资源包 | `97e75d41/a5305ddb/f79771c3/06806c2c`：会话封存、正式冷存、应用消费和 v2 包完整图/原字节；safe/native 真实删源冷读已验 | 包已核不等于安装完成，不继承发送者机器授权/默认配置 |
| 精确引用迁移（3d，未提交） | 原 store archive-only；全量 oldRef→newRef、单一新 remap 指纹、按历史 ref 惰性输入视图；真实 safe/native 新中文根/删旧源冷读有证据 | 仅迁移准备/手工受检发布测试，无生产安装 API/UI；扩大回归仍有两项失败 |
| 同名静态图的历史版本（3d，未提交） | HTTP 23 项通过：A/B 两版分别取自己的 PDF/SVG，画布同时显示两版；删当前原件、保存/打包/检视、篡改和执行失效均覆盖 | 历史渲染配对不证明历史磁盘原件恢复；EPS 仍需同回执活 Figure，不自动执行 |
| 总体交付 | 原保存/冲突恢复、命名关联、预检修复及已完成能力保留 | 图例不同步、边缘裁切/复杂 clip、完整迁移与最终旧功能/平台资格仍缺 |

方向保持为轴模型与明确数据绑定驱动的计算图表编辑。普通 SVG 不保证携带原数据和 Python 来源，不把图元拖动或反推脚本当作本项目的完成条件。

## 2. 本次验证结论

证据根 `E:/0 - Project/svg-review-20261106`。以下是不同范围/源码窗口，不能相加成最终全套结果。

| 记录 | 结果 | 可支持的结论 |
| --- | --- | --- |
| `n3c3c-stage-final.json`、`n3c-input-ui/qualification.json` | 前批后端 561、前端 621 各自通过/0skip；7 段真实 UI、正式 ZIP/上传检视通过 | 声明界面已提交并真实验收；不是当前后端全套重跑 |
| `n3c3d-root-final.xml` | 226 通过/0skip/exit0 | 迁移/store/fingerprint/route 与原向量；app 最后 EPS delta 不在该命令窗口 |
| `n3c3d-real/final-integrated.xml` | safe/native 2 项同命令通过/0skip/exit0 | 真执行一次→正式包→中文新根→新父进程→删旧源/cache→冷读；不是安装 UI |
| `n3c3d-loader-review/document-sources-accepted.xml` | 最终 app 上 23 通过/0skip/exit0 | 历史源消费者和 EPS 零执行；有独立 PDFium 像素/尺寸核对 |
| `n3c3d-loader-review/status-review-affected.xml` | 300 收集 = 294 通过、2 失败、4 跳过，exit1，源码零漂移 | **扩大检查未全绿**，不能宣称现有功能完全不受影响 |
| `n3c3d-real/counterproof-validation.json` | 原目标 5 通过；两个内存反证分别 2/3 项按预期失败；实际 Python 3.10.20 stdlib 迁移 probe 通过 | 出口 verify 与历史 view 检查有效；不是最低版本完整科学/E2E 套件 |
| `n3c3d-integration-review/FINAL_REVIEW.md`、`EPS_DELTA_REVIEW.md` | N→1 引用收敛、额外文件/空目录修复定向复核；EPS 11 条窄探针通过 | 已发现问题的收口；不代替安装或全平台资格 |

本次已修三类实际边界：旧引用收敛时重复 capture、stage 接受额外成员、历史 EPS 错误退成 format_failed。原首红、夹具错误与修复记录保留。

扩大组的两项失败须先处理：

1. `test_svg_input_cold_staging.py::test_fresh_application_parent_packages_original_input_after_source_and_capture_cache_deleted`：新父进程打包 422。固定 HEAD `5884ede2` 同样失败；旧夹具同时使用 `remap:opaque-set` 和不可读哨兵 PDF。外部仅修合法 ID 后仍被 PDF 校验拒绝，再替可读真实 PDF/SVG 后，原测试全部断言通过。应只修正式冷包测试的专属夹具，不动共享存储哨兵、不弱化生产验证。证据：`n3c3d-loader-review/cold-staging-baseline-attribution.md/.json`。
2. `test_import_architecture.py::test_no_cycle_outside_the_baseline`：固定 HEAD 与当前均实际失败，`chartpackage ↔ packageinspection` 两边相同，源自前批 `06806c2c`；新迁移模块不在该环。下一批消除 reader 语义检查与上层检视的逆向依赖，保留 writer/reader 同源验证；不得用延迟 import、登记豁免或降低门禁修复。证据：`n3c3d-loader-review/import-cycle-baseline/REVIEW.md`。

两项均是本批前已有问题，已在外部固定 HEAD 副本真实复现，仓库测试/门禁未为归因修改；它们仍是当前收尾项。四项 skip 与历史 Win1314/平台缺口分别保留在原 XML/checkpoint；不把基线失败、skip 或缺平台写成通过。

## 3. 下一阶段工作顺序与提交边界

最近交付目标：**完整包安装到中文新目录，旧目录和派生缓存不存在时，新父进程/浏览器仍可编辑、保存并导出。** 先完成此用户流程，再进入完整图例/几何与最终资格；不继续添加未消费的基础 helper。

| 顺序 | 本批工作 | 完成条件 | 提交边界 |
| --- | --- | --- | --- |
| 0 / 收口 3d | 处理上面两条失败的归因和修复；复核当前未提交迁移、历史源消费和原默认合同 | 新增专项、受影响旧组与结构门禁无未解释新增失败；原 v1/缺省字节及完整身份保持；必要反证有效 | `feat(packages): plan verified snapshot references for relocated inputs`；独立缺陷按功能 fix，状态文档另提交 |
| 1 / N3d | 接通生产安全安装：独占新根、受检资源发布、实际 remap 设置与迁移身份核对、准确回执 | 同名/并发/断写/坏包保原目标；资源完整回读；检视/安装不运行陌生脚本，认证与原项目归属保持 | `feat(packages): install verified v4 projects without clobbering` |
| 2 / N3e | 原项目界面采用安装结果；复用一个 projectStore/documentStore/writer | 非空正文原 writer 恰一次并回读；空稿显式 persistEmpty；取消/切项目/迟到/超时保旧稿；中文和真 UI 通过 | `feat(packages): adopt installed charts through the document writer` |
| 3 / N3f | 冻结混合夹具，真实中文新根搬迁冷开闭环 | 删自有原目录/cache，新父进程/浏览器开新项目，继续计算编辑、undo/redo、保存、冷开、版本恢复与适用导出 | 实际缺陷分别 fix；验收记录单独 commit。N3a–f 全退出才标迁移完成 |
| 4 / N4 | 显式 SVG 图例条目→series/custom 关系，复用 legendmodel | 曲线样式与图例联动；同色/同名/复合句柄不串；custom、未绑定注释保留；撤销/冷开/导出一致 | 关系合同和真实消费者作为完整功能提交 |
| 5 / N5 | 成员 paint slots、复杂 clip、命中与边缘裁切 | 对照原 SVG/预览/产物定位；共享几何覆盖祖先 AND、子形状 union、孔洞/evenodd/objectBBox；选择/高亮/导出一致 | 按独立几何功能和实际缺陷分别提交 |
| 6 / N6、M1 | 双系列+图例+未绑定注释完整用户闭环；纯外观/完整分享选择 | 绑定/数据/范围/样式→undo/redo→保存→空缓存新进程→原图/标注/混合画布/多页适用 scope×format，独立核产物 | 缺陷分别 fix，资格记录另提交；画布 EPS 沿原 eps_not_for_canvas |
| 7 / N7、M2 | SG00–SG17 能力矩阵逐格：A/C、文字/图元/分组/use、科学族、多轴/投影、写回、出版/预检及全部入口 | 每格有实现与实际验证；写回 prepare→全量冷重放/几何/像素 verify→commit，失败 409 原件零改 | 每项可验收功能独立 commit，不把缺实现改为输入限制 |
| 8 / N8、M3 | 冻结候选后完整旧功能/等价性/CompatBench/E2E/性能/支持平台真安装物 | 无未解释新增丢编辑/错图/回归；Windows 权限及 PID 回执问题有实际处置；缺平台明确未验证 | 最终资格与交付文档；无授权不 push/合并/发布 |

### 3.1 N3d：后端安全安装的具体要求

- 消费活跃 Prepared 和 3d 的同一迁移结果/闭合 `publication_files`；每次复制仍用原受检流，Path 不是租约。不重写第二个 store、ZIP reader 或安装元数据库。
- `projects/open create=true` 会复用目录，`copy_project` 可 skipped>0 成功，均不能充当严格安装器。复用现排他 reserve/原子发布能力，只创建调用者明确选择的全新项目根；任何既有目标零覆盖。只清理能证明属于本 attempt 的产物。
- 保相对 sourceId/script/runtime、原 rc_ID/精确非敏感 argv 建议、原序 overrides 和全部画布位置；完整 oldRef→newRef 按完整引用替换，不为冲突改 panel.fileId 或用户 Python。合法 N→1 不丢原映射行。
- 新根实际规则仍由原 inputremap 权威写入/读取，再比较唯一指纹；历史映射字符串不是本机读权。历史输入 view 仅供核对，不是当前运行配置或执行许可。registry/runconfig/launchctx/workdir/databinding 各自沿原权威，不搬机器授权/默认/成功信任。
- 明确区分资源准备、发布、项目打开、正文提交、元信息确认；后续阶段失败不能报整体成功。缺件备份仍可检视，但不标完整安装；不从发送者 coverage 推断任意 Python IO 已齐备。
- 实测正常认证、错误项目归属、同名目标、两请求竞争、源变更、磁盘/发布失败、坏 graph/blob/ref、取消和清理；保旧目标哨兵/原正文，计数证明安装零执行。

### 3.2 N3e：界面采用与正文落盘

- 先冻结最终正文，再经 `projectStore.adoptOpenedProject(status,{prepareDocument})`、`switchTransaction`、`openDocumentAs` 进入原 ready 流程；不走会清 edits 的旧 relink。
- 前后核项目、format、documentId、loadSeq 与打开意图。非空正文只等原 `switchDocument` 排队的一次 writer，使用 `waitForWorkingDocumentSave` 的实际 revision/正文回读；不补第二 PUT。空稿只显式 `persistEmpty` 一次。
- 资源成功但正文失败、正文成功但元信息未确认、超时、取消、切项目及迟到分别表达；旧稿保留且可恢复。不继承发送者本机文件绑定或绝对路径。
- 专项 Vitest/API、正式 build、i18n 与受影响派生产物通过后，用真实 DOM 验收；不能直接改 store 伪造用户操作。

### 3.3 N3f：必须有的完整搬迁夹具

混合计算 SVG（完整 INPUT）、普通素材、Python runtime/快照、同名静态两历史版本、外部 file/prefix/空目录、原运行配置、多个画布及非活动对象。先走正式导出/上传检视/安装/界面采用，核全部成员与原序正文；仅删除自建测试旧目录并清自有派生缓存。用新父进程和新浏览器恢复新项目，继续编辑/撤销/重做、保存、冷重开及版本恢复。模型/INPUT/资源/参数/几何/文字/PPI 分别独立核对，不以截图或一次菜单成功代替。适用 scope×format 明确成功产物或原稳定拒绝码。原 schema2/3、Python 路径及默认无声明合同纳入受影响回归。

## 4. 无人值守执行合同

1. 读取实际 HEAD/diff 与最新 checkpoint；沿当前工作树/分支，不新建分支/worktree、不 reset/stash 覆盖已有改动，不 `git add .` 混批。原 main 保持；不 fetch 后宣称自动升级，不 push/PR/合并/发布。
2. 按上表选择最早就绪功能，完成实现→必要反证→受影响验证→定向 stage/功能 commit→同步台账→下一就绪项。3d 收尾后直接 N3d，不重复捕获/冷存/声明已完成项，也不停在新的盘点报告。
3. 已授权多子代理：独立测试/反证/只读定位可并行；共享 app/schema/身份、规则、集成和提交由主代理单写。同一文件同时只有一个写入者，不为凑流程委派。
4. Python 先 Ruff，再针对性 pytest；前端专项后正式 build，按影响补 i18n/playground/真 UI；最终候选才跑完整要求链。已通过且未受影响的昂贵组不反复重跑。失败不得删测试、放宽容差或添加豁免；原首红保留，修复只补相应 delta 和必要集成。
5. 测试配置/数据隔离、遥测关闭；所有运行可写数据走 `config.data_dir()`。共享 venv 裸 Python 默认可能导入另一旧树：手工脚本/服务/子进程显式钉当前 src，并核实际 `__file__`；pytest 沿现 `pythonpath=src`。
6. 长检查前后、功能提交与约 30 分钟安全边界更新 PROGRESS：源码身份/命令/cwd/env/退出码、失败/skip、产物、进程所有权及下一步。结论取实际退出码/JUnit，重叠组不累计；只结束精确 PID/创建时间核过的自有进程。
7. 单项缺外部条件时记录证据并推进独立就绪项；只在缺失信息实质改变结果或超授权时询问。不得以休眠、一夜结束、helper 数量或专项全绿标整体完成。

无人值守指活跃会话持续迭代，未创建常驻调度器。中断后按实际工作树与 PROGRESS 恢复；提示词不保证唤醒机器。最终范围和各 SG 完整验收沿 IMPLEMENTATION_PLAN，M1/M2/M3 均保 pending。
