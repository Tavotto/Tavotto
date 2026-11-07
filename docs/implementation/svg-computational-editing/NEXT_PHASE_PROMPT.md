# SVG 计算图表编辑：下一阶段无人值守提示词

2026-11-07 09:55 复核：目录/保存修复已分别提交，最新受影响前端 596 通过，正式与派生构建通过。真实安装初次恰一次正文 PUT；线宽修改/撤销/重做、跨项目恢复最新稿（Inspector 12 pt）已通过。N3e 功能文件尚未提交，切项目预览 pj 与图源不配对的 422 待修复。下一步收尾 N3e，再完整混合迁移 N3f，优先修裁切 N5，之后图例 N4 和 M1/M2/M3。详见 [NEXT_PHASE.md](NEXT_PHASE.md)、[PROGRESS.md](PROGRESS.md)、[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。

以下内容可整体复制：

```text
继续完成 Tavotto 的 SVG 计算图表编辑，保持现有 Python 绘图和全部既有能力。目标是最终可用。复用原模型、编辑器、writer、事务、RenderCore/pdfbackend 和 legendmodel；持续自主实现、修复、验证，在当前同一分支按功能分别 commit，常规可逆步骤不逐批等待确认。

一、先恢复真实状态
实施树：E:/0 - Project/Tavotto-svg-r00
唯一分支：zixu.wang/svg-computational-r00
本次复核代码 HEAD：4cfccfcf；恢复以实际 HEAD/diff 为准，不回退。
已提交：0d86b357 安全安装后端；c761dc7e 时间线捕获依赖修复；0f85d4b2 最新完成目录导航；4cfccfcf 原保存 timer/慢 hydration；48bdbda9 旧测试等待确切 capture。不要重做。
原目录：E:/0 - Project/Tavotto，clean main@69bcafae，尚未合入。
证据根：E:/0 - Project/svg-review-20261106
先读 PROGRESS 最新 checkpoint、NEXT_PHASE、相关 AGENTS/规则/ADR，核提交身份、HEAD/diff、代理文件所有权与实际 live 句柄。
不新建/切换分支或 worktree，不 reset/stash，不 git add .，不 push/PR/合并/发布。保全部现有改动和证据；配置/数据隔离、遥测关闭，运行时写入沿 config.data_dir()。手工 Python/服务/child 显式钉当前 src 并核 __file__。

二、已验证事实及边界
n3e-after-fixes/first.xml：42 文件 596 pass/0 fail/0 skip/exit0；1490 前端源零漂移。n3e-final/*after-fixes*：正式 packaged、playground、widget、两个派生 check 全部 exit0。原目录、保存、时间线与安装组已覆盖；不是全仓/全平台回归，不相加不同窗口通过数。
N3d 受影响后端356 pass/3 Windows 权限 skip；safe/native 新 API 安装→中文根→删自建旧源/cache→新父冷读2 pass。规则41和实际3.10 stdlib窄边界通过；不是完整最低版本科学栈或界面资格。
第二轮 n3e-ui-second：正常认证、inspect/install各1、零Run；初次采用仅1 PUT，全文/revision、资源、模型/绑定/原序patch、旧稿和命名稿独立核对通过。见 browser-effects-initial-measured.json、measured-initial-third-exit.json。
真实 edit/undo/redo 保存 native width [5,7.7,5,7.7]；Inspector物理约7.8→12→7.8→12pt。切回旧项目再从保存回执继续编辑，浏览器GET138全文等于离开前最新稿，恢复后真实Inspector仍12pt。见 edit-undo-redo-proof.json、status-review-qualified-ui.json、review-05-resumed-properties.log。
此次跨项目恢复仍同一进程，不能当新进程冷开。单次初次采用窗口已冻结，后续用户编辑PUT另记。
原TextView高度及普通PNG缺席元数据由独立DOM/冻结PNG/原Registry精确推导，其余全文仍严格。不能从实际AS生成期望、忽略正文差异或降低一次PUT断言。保首轮2PUT、目录反例和核验器主语错误等首红。
新AS首次404为未创建基线；旧项目preview_png 422已确认：request104携新项目chart.svg/来源/线宽7.7却发旧pj。原样正常认证重放仍chart_source_mismatch；正确pj同body有7次200，后端附件读取即拒绝、无worker/旧目录改写。见review-preview-422/actual-replay-and-freeze.json。源码/AST对照表明机制早于本轮目录/保存修复、普通切换也可达；未跑旧版完整UI时序，不称旧版真UI复现。这是待修项目/来源配对问题，不假称正常。
效果图 n3e-ui-second/output/playwright/n3e-second-resumed-linewidth.png 仍有左/下刻度裁切。图例、混合冷迁移、M1/M2/M3未完成，普通SVG不反推Python或未知数据。

三、按顺序完成当前批次
P0 / N3e收尾：
保现有未提交API、projectPackageInstallStore、Dialog、两个入口、App、翻译/类型、专项、ADR及规则N3e段，不重写已具备实现。
先核最新归因，修preview_png的pj/图源配对竞态：先停止自有浏览器/服务解除冻结，实际链CanvasThumb→useVariantPng→previewPngResponse，项目先更新全局pj而旧panel仍挂载，单补captureProjectEpoch不够。按panel所属文档owner+pj+代次形成一份快照贯穿key/request/cache/response，guard迟到结果；转场无对应owner不发旧panel新pj请求，新稿就绪正常预览；普通切换、安装continue、快速A→B→A、in-flight返回及恢复失败回归。不得放宽chart_source后端验证、跨项目找附件或吞422。最小修复、必要反证、受影响验证后独立fix提交。未知响应/迟到回执等专项资格如实区别于真UI，不虚称已覆盖所有故障注入。
本轮浏览器/服务已正常终局exit0，两个自有PID已消失、24059无监听，1779源/11bundle零漂移，冻结解除；见stop-confirmed.json。目标已发布，不能按旧serve前提直接重启，后续UI用独立生命周期/新目标。新live/冻结状态仍以最新PROGRESS为准；不得边验边改源码/测试或重建，不按端口kill/盲重启，不删已发布目标。保存证据后只按已核身份正常close/shutdown/wait。
复核原writer单次采用、旧稿保护和跨项目最新稿。资源/配置/项目打开/正文分别表达，needs_review沿原设置；attempt未知保路径不假报成功、不自动重装/Run；跨项目不重放installation.doc覆盖最新稿。
通过后定向提交功能文件及受影响规则/ADR，状态文档另交：
feat(packages): adopt installed charts through the document writer

P1 / N3f混合中文根搬迁：
复用 n3f-mixed/latest-prepared.json→attempt-04/ready.json，不重造简化夹具。正式35成员包65662字节，SHA256 2de26ee47cb7f80605445f3d1d8f2c7536dfedb7aeb820e24bca993f39636bd7。双画布11对象、5refs、2graphs、8INPUT blob；含计算SVG、PNG/PDF/SVG、runtime/捕获、同名静态两历史、外部file/prefix/空文件/目录及原rc/argv。原科学/module计数4/4，安装恢复不得自动增加；保未知通道/历史原件缺失/remap重审的准确边界。
正常认证的新服务/浏览器走真实上传检视→原安装API→明确采用→原writer落盘/回读，保护非空旧稿。用已备 verify_installation.py 的实际 installed/initial 模式独立核全部引用/资源/参数，不用准备/离线绿代安装资格。文本测量和有限原元数据派生须有独立事实，其他全文严格。
仅在完整安装已核后，按绝对归属清单移除自建旧源及派生cache，保用户目录/新根/原证据；停旧服务后新父进程+新浏览器冷开。恢复活动/非活动画布和两历史，继续改数据/范围/样式、undo/redo、保存、再冷开/版本恢复。
逐项核模型、INPUT/资源bytes、历史身份、几何、文字、PPI，适用scope×format必须真实产物或原稳定拒绝码；画布EPS仍eps_not_for_canvas。补原Python/schema2/3/无声明受影响回归。发现缺陷按功能fix，不跳过；N3a–f全退出才标完整迁移通过。

P2 / N5裁切先于图例：
读 n5-tick-clipping-review/implementation-review.md：源100pt框保完整文字，但Y刻度到x=-19.265625pt、X到y=107pt，被物理框裁切。仅overflow无效，仅扩viewBox但维持100pt宽会缩数据几何，已有反证。
实验区分source frame/anchor与render ink frame，复用已有frameSwitchPatch及共享变换，明确新版本/旧格式兼容。预览、SVG/PNG/PDF、命中/高亮和页面布局使用同一合同；外扩导致页面负坐标必须沿原布局事务处理。不要缩字体/轴/数据、CSS-only或无条件tight来掩盖。实际产物、几何、真UI、旧格式反例合格后分功能提交。

P3 / N4图例：
显式entry→series/custom并复用原legendmodel；同名/同色/顺序不是身份。两系列、复合句柄、手工文字和未绑定注释保留，样式/模型联动→撤销→保存→冷开→导出一致；合同和真实消费者同批。

四、最终范围不缩减
M1：双系列+图例+未绑定注释的完整数据/范围/样式、撤销保存、空缓存冷开及原图/标注/混合画布/多页适用scope×format；外观分享不暗带数据，完整分享携模型。
M2：按SG00–SG17/A/B/C能力映射，补科学族、多轴/投影、文字/分组/use、事务写回、出版/预检及CLI/MCP/playground/桌面/发行入口。缺实现不得改成不适用。写回prepare→全量重放/几何/像素verify→commit，失败409原件零改。
M3：冻结最终候选的完整旧功能/等价性/CompatBench/E2E/性能/支持平台实际安装物资格；缺条件保未验，不把本机专项绿外推全产品。

五、无人值守循环
顺序N3e收尾→N3f→N5→N4→M1→M2→M3。核状态→可用实现/真实消费者→必要反证→受影响验证→核源/实际退出码/diff→定向功能commit→日期checkpoint→下一项。不要反复计划或重复绿测代替推进。
已授权多代理，仅分独立有收益任务；先核现有代理/句柄，每文件一个写入者，契约/集成/提交根代理单写。可并行独立核验与有界N5实验，不在共享源冻结期间写代码。
Python先Ruff；前端改动完成正式build及受影响派生检查。仅新改动/新失败/未解疑点追加验证，最终候选跑完整链；不删门禁/放宽容差/累计重叠通过数。
长检查前后、每次功能commit和约30分钟记PROGRESS：HEAD/diff、所有权/进程身份、命令/cwd/env、退出码、失败/skip、SHA/产物、下一步。只结束核过PID/创建时间的自有进程。
外部条件缺失先准确记录，继续独立就绪项；不编造科学数据或用户确认。无人值守依赖活跃会话，未创建常驻调度器，中断按checkpoint接续，不以一夜结束当完成。M1/M2/M3实际通过前保持完整目标未完成。

现在先核最新状态，完成N3e收尾和功能commit，然后进入N3f，不重做已提交修复。
```
