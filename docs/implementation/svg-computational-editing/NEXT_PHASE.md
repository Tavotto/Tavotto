# SVG 计算图表编辑：当前效果与下一阶段安排

> 复核时刻：2026-11-07 09:55（Asia/Shanghai）。唯一实施树为 `E:/0 - Project/Tavotto-svg-r00`，沿用 `zixu.wang/svg-computational-r00`，代码 HEAD `4cfccfcf`。原 `E:/0 - Project/Tavotto` 仍为 clean `main@69bcafae`，没有合入成果。本轮不 fetch，不把本地引用称为远端最新。
> 本文维护当前安排；[PROGRESS.md](PROGRESS.md)保留日期记录，[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)保留完整范围，[NEXT_PHASE_PROMPT.md](NEXT_PHASE_PROMPT.md)提供无人值守接续提示词。恢复时实际 HEAD/diff 和最新 checkpoint 优先。

## 1. 本次复核结论

计算 SVG 的基础编辑、保存和部分冷恢复已实际跑通。项目包界面也已完成第二轮真实安装、单次正文保存、线宽修改/撤销/重做，以及跨项目恢复最新工作副本。上轮重复保存和目录选择竞态均已修复、分别提交。N3e 功能文件尚未提交；混合迁移、刻度裁切、图例联动和最终 M1/M2/M3 仍未完成，不能称最终可用或全部旧功能已验证无回归。

方向仍是轴模型、明确数据绑定及原计算编辑器。普通 SVG 不保证携带原始数值或 Python 来源；可拖动的文字/图元与可重算的科学模型分别验收，不从外观反推来源。

| 范围 | 当前实际状态 | 尚缺什么 |
| --- | --- | --- |
| 计算编辑 | 显式绑定、范围/样式、撤销、保存及精确 INPUT 冷恢复已有真 UI 证据 | 图例联动、复杂几何、科学族与全部入口逐格资格 |
| 项目包后端 | `0d86b357` 已提交：受检复制、原 renderer 验证、排他发布、分阶段回执 | 后端不打开项目、不写正文；资源发布不能替代界面采用 |
| 原保存/目录 | `4cfccfcf` 消费本次捕获覆盖的 debounce、合并慢 hydration 的同一 flush；`0f85d4b2` 绑定最新完成导航；`48bdbda9` 单独修旧测试捕获等待 | 只因新改动/新失败追加检查，不重做已通过的修复 |
| N3e 安装界面 | API、协调器、Dialog、两个入口、翻译和专项已实现但未功能提交；第二轮真实初次采用恰 1 PUT，旧稿及命名稿保留、零 Run | 修复切项目时图表来源与 pj 配对竞态、最终源核对和功能 commit；未知响应/迟到回执等目前为专项资格，不能冒称所有分支已做真实浏览器故障注入 |
| 继续编辑 | 真 UI 保存线宽后撤销/重做；切回旧项目后从回执继续编辑，浏览器 GET 全文与离开前最新稿一致，Inspector 显示 12 pt | 此次为同一服务跨项目恢复，不能替代新进程/空缓存冷开 |
| N3f 混合迁移 | 正式包和独立安装核验器已准备，尚未实际安装 | 中文新根采用、删自建旧源/cache、新父进程/浏览器恢复、继续编辑和历史恢复 |
| 效果 | 新截图中红线、标题、PNG 和恢复的 12 pt 可见 | 左/下刻度明显裁切；优先修 N5，再验 N4 图例联动 |
| 总体交付 | 同一实施分支按功能提交，原 main 保持 | M1/M2/M3 未通过；未 push、合并或发布 |

本轮效果图：`E:/0 - Project/svg-review-20261106/n3e-ui-second/output/playwright/n3e-second-resumed-linewidth.png`。它证明本次界面状态，同时直接暴露裁切问题，不作最终视觉合格证明。

## 2. 已核对的验证与证据边界

证据根为 `E:/0 - Project/svg-review-20261106`。不同源码窗口的通过数不相加为全产品总数。

| 检查 | 实际结果 | 范围与原始证据 |
| --- | --- | --- |
| N3d 后端 | 15 文件 359 收集：356 pass、3 Windows 权限 skip、exit0；304 源零漂移 | `n3d-stage-final.json`；受影响组，非全仓或全平台 |
| N3d 新 API 安装冷读 | safe/native 2 pass、0 skip、exit0；23 直接源零漂移 | 正式包→中文根→删自建旧源/cache→新父冷读；不是界面采用 |
| N3d 版本边界 | 规则 41 通过；实际 Python 3.10.20 stdlib 安装/碰撞通过 | 非完整最低版本科学栈资格 |
| 本轮受影响前端 | 42 文件 596 pass、0 fail、0 skip、exit0，1490 前端源零漂移 | `n3e-after-fixes/first.{xml,log}`；涵盖原保存、回执、时间线、目录及安装相关组，非全部前端/旧功能回归 |
| 正式/派生构建 | packaged、playground、widget 及两个派生 check 均 exit0、1490 源零漂移 | `n3e-final/*after-fixes*`；含 i18n、tsc-b、Vite/Tailwind、包内同步。HEAD 元数据早于随后提交，源字节相同，非 release artifact |
| 目录修复 | 原 11 + 新 10 = 21 pass；固定旧源新 10 红；撤两处 owner 闸实际红 | `n3e-directory-implementation/`；真实 UI 也观察到旧 home 响应晚到后确认仍禁用，最新目录成功后恢复 |
| 保存修复 | 新 9 项真实 TextView/Moment/writer 回归；最终 20 文件 299 pass；必要反证真红 | `n3e-save-debounce-implementation/`；596 已含相关组，不叠加。保失败/409/后续编辑/no-owner 路径 |
| 第二轮初次采用 | inspect/install 各 1，零 Run；初次新工作副本恰 1 次 PUT 200；PUT、浏览器回读、磁盘全文/revision 一致，旧稿/命名稿/资源保留 | `n3e-ui-second/browser-effects-initial-measured.json`、`measured-initial-third-exit.json`；冻结初次窗口，不把后续用户编辑计入 |
| 实际编辑及恢复 | 保存 native 线宽序列 `[5,7.7,5,7.7]`；物理 Inspector 原约 7.8 pt→12→7.8→12；跨项目恢复后仍 12 pt | `edit-undo-redo-proof.json`、`status-review-qualified-ui.json`；浏览器 GET138 全文等于离开前稿，其他正文保持 |
| 当前源码/服务 | 1779 源与实际 11 HTTP bundle 零漂移，浏览器/服务已正常退出 | `n3e-ui-second/stop-confirmed.json`：exec/child exit0，两个 PID 不存在、24059 无监听；冻结解除 |
| N3f 准备 | 正式 35 成员包、独立核验及核验器离线正/反例通过 | `n3f-mixed/qualification.md`、`installation-verifier-readiness.json`；不是安装/冷恢复通过 |

保留历史失败：首轮联合 262/3、首轮真实 2 PUT、目录反例、旧 slowSnapshotSave 时序首红，以及第二轮外部核验器的主语错误均不改写。后者仅将期望改为冻结包 + 真实 DOM 测量 + 原 registry/PNG 可独立推导的明确派生值；其余完整正文仍严格比较。不得从实际保存正文直接生成期望。

控制台有新 AS 尚不存在的 404，以及旧项目 `preview_png` 422。404 对应首次创建前的正常基线；422 已确认是 request104 在切项目期间携带新项目 chart.svg/来源/线宽7.7，却发向旧 pj。正常认证原样重放仍返回 `chart_source_mismatch`，原日志相同请求发正确 pj 有 7 次200对照；后端在附件读取处拒绝，未进入 worker，旧项目21文件字节未变。此为必须修的项目/图源配对问题；只读源码/AST对照表明机制早于本轮目录/保存修复，普通切换也可达，但未执行旧版完整UI时序，不称旧版真UI复现；不假定正常，也不否认已核过的正文保存事实。证据 `n3e-ui-second/review-preview-422/actual-replay-and-freeze.json`。

## 3. 下一阶段分批执行

最近交付目标：**正式混合项目包安装到中文新根，旧稿无损；原来源与派生缓存移除后，新进程仍能恢复、继续计算编辑、保存及导出。** 随后关闭可见裁切和图例缺口，才进入最终资格。

| 顺序 | 工作包与范围 | 退出条件 | 提交边界 |
| --- | --- | --- | --- |
| P0 / N3e 收尾 | 修复切项目时预览 pj 与图表来源配对竞态；核源码/HTTP bundle、保存与继续编辑证据；正常结束自有 UI/service | 原样422反例转回归；请求冻结同一项目/文档/图源代次，迟到结果不写新项目；原项目切换及安装继续编辑均通过。保原单次 PUT、旧稿、真实最新稿资格 | 实际必要 fix 独立提交；随后 `feat(packages): adopt installed charts through the document writer`。计划/记录单独 commit |
| P1 / N3f 当前主线 | 复用现有正式混合包和原界面安装/采用；新中文根完整核验；删自建旧源/cache 后新进程恢复 | 两画布、非活动对象、两历史、全部 INPUT/资源/参数可独立核；继续改数据/范围/样式、undo/redo、保存、版本恢复及适用导出均通过，安装/恢复零隐式 Run | 修实际缺陷按功能 commit；迁移资格记录单独提交；N3a–f 全退出才称迁移完成 |
| P2 / N5 可见正确性 | 修源图幅与派生可见外框混同；同步预览/冻结产物/命中几何/页面布局 | 刻度/标签完整；数据几何、单位、锚点不缩放漂移；旧格式/旧项目不回归；独立 SVG/PDF/PNG 对照与真 UI 通过 | 先实验反证再落结构及消费者，同源合同与兼容测试同批 |
| P3 / N4 图例联动 | 显式 entry→series/custom 关系并复用原 legendmodel | 两系列同名/同色不串，复合句柄、手工文字、未绑定注释保留；编辑→撤销→冷开→导出一致 | 合同与实际编辑消费者同批，不只加字段或 helper |
| P4 / N6 / M1 | 双系列+图例+未绑定注释完整用户闭环 | 数据/范围/样式→undo/redo→保存→空缓存冷开；原图/标注/混合画布/多页适用 scope×format 有独立有效产物或原稳定拒绝码 | 实际矩阵通过才标 M1；原画布 EPS 保 `eps_not_for_canvas` |
| P5 / N7 / M2 | 按 SG00–17、A/B/C 能力映射补齐未实现项 | 科学族、多轴/投影、文字/分组/use、事务写回、出版/预检、CLI/MCP/playground/桌面/发行入口逐格实证 | 按独立功能提交；缺实现不得改名为不适用 |
| P6 / N8 / M3 | 冻结最终候选跑完整兼容与交付链 | 原 Python/旧文档、完整回归、等价性、CompatBench、E2E、性能及支持平台实际安装物资格；未有平台条件如实列未验 | 资格文档；未经授权不 push/PR/合并/发布 |

### 3.1 P0：保留现有实现，完成收尾

1. 第一项是项目/预览身份配对修复：已定位 `CanvasThumb → useVariantPng → previewPngResponse`：项目先切全局 pj、旧 panel 仍挂载；单补 captureProjectEpoch 仍可能捕获错误 owner。按面板所属文档 owner + pj + 代次形成一份快照，贯穿 key/request/cache/response，校验迟到结果；转场无对应 owner 时不发旧 panel 新 pj 请求，新稿到位后正常预览；覆盖普通项目切换、安装后继续编辑、快速 A→B→A、in-flight 返回及恢复失败。不得放松后端 chart_source 校验、改成从别的项目找附件或吞422。实施前正常停止验收进程，修复独立 commit，再按实际影响补验。
2. 不重做已提交目录、保存、时间线修复。N3e 未提交文件包括 API/协调器/Dialog/两个入口/App、翻译/类型、专项、ADR0166 与规则内 N3e 段；与状态文档分开定向 staging。
3. 资源发布、配置 `pending/needs_review/ready`、项目打开、正文落盘仍分别表达。配置沿原设置入口；未知响应保 attempt 目标和状态，不假报成功，不自动重装或 Run。保存后跨项目走原最新工作副本恢复，不用 `installation.doc` 覆盖后续编辑。
4. 本次真实 UI 已通过的单次采用与跨项目恢复不重跑凑数；只有修复影响、新失败或未解疑点才补验。故障注入专项与真实用户路径分别记证据层次。
5. 本轮自有浏览器/服务已正常退出、1779源/11bundle零漂移，冻结解除；见 stop-confirmed.json。恢复仍先核最新句柄。第二轮中文目标已经存在，不按旧 serve 前提直接重启；后续 UI 使用独立生命周期/新目标，保全部首红/已安装目录。不得按端口 kill 或盲目重启。

### 3.2 P1：混合迁移的具体步骤

复用 `n3f-mixed/latest-prepared.json → attempt-04/ready.json`。正式包 65662 字节、35 成员，SHA256 `2de26ee47cb7f80605445f3d1d8f2c7536dfedb7aeb820e24bca993f39636bd7`；双画布 11 对象、5 refs、2 graphs、8 去重 INPUT blob。包含计算 SVG、普通 PNG/PDF/SVG、runtime/捕获、同名静态两历史、外部 file/prefix/空文件/空目录及原 rc/argv。

1. 核原 167 文件/包 SHA、目标不存在及服务真实 import；原科学/module 计数 4/4，安装和恢复不得自动增加。保未知输入通道、旧静态原件缺失和 remap 重审的准确边界。
2. 正常认证服务与新浏览器通过真实 UI 上传检视→新 API 安装→明确采用，保护非空旧稿、核原 writer 真实落盘/回读。原准备阶段不能冒称采用成功。
3. 用 `verify_installation.py` 的实际 installed/initial 模式核整包成员、引用、原序 patch、输入指纹、图幅、普通媒体/PPI、配置建议和原参数；参数不齐明确失败。初次文本高度/普通元数据须由独立 DOM 与冻结资源推导，只处理有原权威的有限派生，保持其余全文严格。
4. 验正式安装和落盘后，仅移除清单内自建旧源及派生 cache；核绝对路径归属，保原始证据/新根/用户目录。停止旧服务，记录进程退出及源窗口，再起新父进程和全新浏览器。
5. 在新根恢复活动/非活动画布、当前与两历史版本，继续改数据/范围/样式、undo/redo、保存、再冷开。核模型、INPUT bytes、历史身份、几何、文字和资源，不以“图能显示”替代。
6. 对适用 scope×format 独立检查真实产物；Python/schema2/3/默认无声明路径跑受影响回归。发现缺陷先最小反例→修复→受影响资格→功能 commit，再续剩余矩阵。

### 3.3 P2：N5 已定位的裁切根因

独立实测见 `n5-tick-clipping-review/implementation-review.md`。原 viewport 为 100×100 pt；Y 刻度最左 -19.265625 pt，X 刻度最下 107 pt。文字仍在 SVG/IR 内，外层物理页框裁掉它们。只改 overflow 无效；只扩 viewBox 而维持 100 pt 物理宽会把 80 pt 数据线缩成约 67.0772 pt，已实验否定。

候选应区分原 source frame/anchor 与派生 render ink frame，复用现有 frameSwitchPatch/共享变换，明确版本和旧格式兼容；不要伪装已有 ManifestFrame.source provenance。预览、SVG、PNG、PDF、命中/高亮及画布布局使用同一 frame。当前面板本就在页面近左边，扩外框后的负坐标还需原布局事务的明确处理，不能仅让图外字越过页面。禁止缩字体、缩数据几何、CSS-only、无条件 tight 裁边或隐藏问题作为完成。

## 4. 无人值守执行与检查点

- 继续现有树/分支，不新建或切换分支/worktree，不 reset/stash，不 `git add .`；原 main 保持。本地功能 commit 已授权，远程操作/发布未授权。
- 顺序为 **N3e 收尾 → N3f → N5 → N4 → M1 → M2 → M3**。已验证项只按实际影响补验；不能不断回到计划、反复跑相同绿测或先扩新图表族。
- 现有多代理授权保留：可并行做 N3f 独立核验、N5 有界实验/反证；每文件同时一个写入者，根代理统一契约、集成和提交。先核旧代理状态与句柄，避免重复任务。
- 每批循环：核状态/规则→形成可用功能与真实消费者→必要反证→受影响验证→核实际退出码/SHA/diff→定向 commit→同步日期 checkpoint→接下一项。Python 先 Ruff，前端改动完成正式 build 和受影响派生检查；最终候选再跑完整链。
- 配置/数据隔离、关闭遥测，运行时写入沿 `config.data_dir()`；手工 Python/服务/child 显式钉当前 `src` 并核 `__file__`，避免共享 venv 测到原主树。
- 长检查前后、功能提交和约 30 分钟记录 HEAD/diff、所有权/进程身份、命令/cwd/env、实际退出码、失败/skip、源码/产物 SHA、下一步。不能并行改正在验收服务的源；只结束已核 PID/创建时间的自有进程。
- 外部条件缺失则保准确 checkpoint，继续独立就绪项；不编造科学数据/用户确认、不降门禁。无人值守依赖活跃会话，未建立常驻调度器；中断从 checkpoint 续接，不以一夜结束视为完成。
