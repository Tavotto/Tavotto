# SVG 计算图表编辑：当前效果与下一阶段安排

> 2026-11-07 07:30后（Asia/Shanghai）复核。实施树`E:/0 - Project/Tavotto-svg-r00`，沿用`zixu.wang/svg-computational-r00`，功能HEAD `fa691a3b`。3d引用迁移已提交；N3d安装核心/API已有未提交实现及专项正证，尚未完成该批资格。原目录`E:/0 - Project/Tavotto`为clean `main@69bcafae`，未合入成果。
> 进度台账：[PROGRESS.md](PROGRESS.md)。完整范围：[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md)。可复制提示词：[NEXT_PHASE_PROMPT.md](NEXT_PHASE_PROMPT.md)。M1/M2/M3均未通过，不按测试数或工作时长估算完成比例。

## 1. 当前实际效果

| 能力 | 已实现和已有证据 | 当前边界 |
| --- | --- | --- |
| 普通SVG计算编辑 | 显式轴/系列/数据绑定；范围、线宽复用原编辑器；undo/redo、保存、精确INPUT冷恢复有历史真UI | 图例联动、复杂几何、全部科学族及入口未逐格验收 |
| 原图导出 | 单系列真实UI五格式及独立产物核对，计算原图EPS接线保留 | 双系列、图例、混合画布/多页的完整适用scope×format尚待M1 |
| 输入声明 | `8a13ed95`：原准备卡/脚本菜单；保存零执行、明确Run恰一次、取消/切项目/迟到保稿已验 | 声明不代表任意Python IO闭包，不为旧Figure补签新证据 |
| 输入归档和资源包 | 会话封存、正式冷存、v2包完整图/原字节，safe/native删源冷读已有实证 | 检视通过不等于安装完成，不继承发送者机器授权/默认配置 |
| 引用迁移和历史版本 | `fa691a3b`：完整oldRef→newRef、单一新remap指纹、惰性历史输入view；同名静态A/B历史独立PDF/SVG、保存/打包/画布并存已验 | 历史渲染配对不证明未携带的历史磁盘原件已恢复；EPS保原活Figure/同回执边界 |
| 安全安装后端 | 未提交：排他新根发布、闭合集合回读、原配置建议导入、真实API和原renderer；核心17/HTTP35专项通过 | 不打开项目、不写正文，`document_saved=false`；当前旧组联合验证及功能提交待完成 |
| 界面效果 | 本次复看既有截图，曲线可见 | 刻度/文字仍有边缘裁切，图例联动未完成；本次未新增浏览器验收 |
| 总体交付 | 已完成能力和原保存/恢复路径的已有证据保留 | 安装界面、完整迁移用户流程、最终旧功能/平台资格仍缺；尚未合入原main或发布 |

方向保持轴模型与明确数据绑定驱动的计算图表编辑。普通SVG不保证携带原始数据和Python来源；图元拖动不能替代计算语义。

## 2. 验证与提交状态

证据根`E:/0 - Project/svg-review-20261106`。不同源码窗口的结果不能相加，也不能将3d末验外推成当前N3d全套资格。

| 范围 / 原始记录 | 结果 | 可以支持的结论 |
| --- | --- | --- |
| 3d：`n3c3d-loader-review/closeout-core.xml` | 415通过、0skip、exit0 | 迁移/store/历史源消费者/结构门禁核心末验 |
| 3d：`n3c3d-closeout/affected-final.xml` | 300收集=296通过、4skip，exit0；18文件前后零漂移 | 上次两项失败已修复，受影响旧组末验通过；不是全产品回归 |
| 3d规则/实际3.10 | 规则41通过；Python3.10.20 stdlib迁移probe通过 | 该源码窗口规则与最低版本小范围路径，不是完整科学栈 |
| N3d：`n3d-install/qualification.md/.json` | 核心17通过、0skip、exit0；16直接源/fixture零漂移 | 真包/两历史输入、复制篡改、竞争、配置冲突和发布后状态；不是HTTP/UI |
| N3d：`n3d-http/final.xml`、`validation.md/.json` | HTTP35通过、0skip、exit0；6文件零漂移 | 原认证/项目归属/上传预算、实际renderer有/无caller、上传.venv不作engine根 |
| 发布器：`n3d-directory-validation.md/.json` | 新19通过/3skip；原文件发布17通过/10筛除；实际3.10/junction通过 | 本机排他发布和durability；POSIX仅选择器单测，不冒充真平台文件系统 |
| 定向末验：`n3d-install-review/FINAL_REVIEW.md` | 两个原反例修后各exit0，首红保留 | 旧本机配置准确needs_review，上传清理失败仍保路径/阶段回执 |
| 历史迁移：`n3c3d-real/final-integrated.xml` | safe/native同命令2通过/0skip | 正式包→中文新根→删旧源/cache→新父进程冷读；受检手工发布，不是新安装API/UI |
| 历史声明界面：`n3c3c-stage-final.json`、`n3c-input-ui/qualification.json` | 后端561、前端621各自通过，7段真UI | 原源码窗口资格，不是当前后端或本轮新浏览器验收 |

最近功能提交：

- `81fd701f`：规划器沿活跃Prepared消费，消除`chartpackage ↔ packageinspection`逆向运行时依赖，保writer/reader同源检查。
- `cc3175e8`：仅修正式冷包专属fixture，使用合法remap和真实可读配对，不改共享哨兵、不弱化生产校验。
- `fa691a3b`：16文件引用迁移、历史源消费及受影响规则/测试完整功能。

旧294通过/2失败/4skip首轮及基线归因保留，两项不再是待修复任务。3d的4skip为Ghostscript缺失、2个卷大小写条件、Windows symlink权限；目录3skip为symlink权限，实际junction实验是补充证据，不将skip改记通过。

当前未提交产品批次：`app.py`、`atomicio.py`、`packagerelocation.py`、新增`packageinstall.py`及3个新增测试。其余本次差异为状态文档。所有写入者已冻结释放；续接先核实际HEAD/diff，保留现实现。

## 3. 下一阶段批次

最近交付目标：**包安装到中文新目录并采用到原编辑器；旧目录和派生缓存不存在时，新父进程/浏览器仍可继续计算编辑、保存并导出。**

| 优先级 / 批次 | 工作 | 退出条件 | 功能提交 |
| --- | --- | --- | --- |
| P0 / N3d收口 | 补受影响旧组、实际API冷读、规则/结构检查 | 上传原合同不退化；safe/native包走新API后删源冷读；无未解释新增失败，源身份和阶段回执齐全 | `feat(packages): install verified v4 projects without clobbering` |
| P1 / N3e | 原界面选目录、检视/安装、采用正文及配置差异提示 | 非空正文原writer恰一次并回读；空稿显式persistEmpty；取消/切项目/迟到/失败保旧稿；中文真UI通过 | `feat(packages): adopt installed charts through the document writer` |
| P1 / N3f | 混合项目正式导出到安装/采用/删源冷开 | 新父进程/浏览器在中文新根继续编辑、undo/redo、保存、版本恢复及导出；独立核输入/正文/产物 | 缺陷分别fix，资格记录另commit |
| P2 / N4 | 显式图例条目→series/custom关系，复用legendmodel | 两系列样式和图例联动，同名/同色/复合句柄不串；注释保留，撤销/冷开/导出一致 | 关系合同和真实消费者一起提交 |
| P2 / N5 | 成员paint slots、复杂clip、命中及边缘裁切 | 对照原SVG/预览/产物定位根因；选择/高亮/变换/导出共用几何，文字/刻度不错误裁切 | 按独立功能/缺陷提交 |
| P3 / N6、M1 | 双系列+图例+未绑定注释完整闭环；外观/完整分享 | 绑定/数据/范围/样式→撤销→保存→空缓存新进程→原图/标注/混合画布/多页适用scope×format | 缺陷分别fix，资格另提交 |
| P3 / N7、M2 | SG00–SG17/A/B/C逐格补科学族、多轴/投影、文字/图元、写回、出版/预检和入口 | 每格有实现/实证；写回prepare→全量冷重放/几何/像素verify→commit，失败409原件零改 | 每项可验收功能独立提交 |
| P4 / N8、M3 | 冻结候选完整旧功能、等价性、CompatBench、E2E、性能及平台真实安装物 | 无未解释新增丢编辑/错图/回归，Windows权限/PID问题实际处置，缺平台明确未验 | 最终资格/交付文档，不擅自push/合并/发布 |

### 3.1 N3d收口

1. 核当前7文件和三份专项报告SHA。复用活跃Prepared、同一迁移计划、原受检流、目录发布器及registry/runconfig/inputremap权威；不重做3d，不另建store/ZIP reader/安装元数据库。
2. 上传公共helper影响原inspect，内部`source_storage`参数影响chart恢复。针对原inspect HTTP、chart保存/渲染/原图导出、迁移copy、原atomicio和import架构补必要受影响联合组；先Ruff再pytest，记录源码窗口/退出码，不反复跑未受影响昂贵组。
3. 真实safe/native包走`POST /api/documents/v4/package/install`，核完整ref、实际remap指纹、两历史输入及原rc_ID/精确argv建议。仅删自建旧源/cache，在中文新根用新父进程冷读；旧手工发布不能代新API资格。
4. 既有/竞争目标零覆盖，只清未发布且身份不变的自有stage。发布后不递归回滚；`publication_unconfirmed`、`installation_unconfirmed`与配置`pending/needs_review/ready`分别保真。旧默认/cwd授权保留并沿原权威投影，不导入发送者授权、不静默宣称执行就绪。
5. 计算验证走原renderer，上传.venv不能作engine根，包内脚本零执行。已知缺件阻止完整安装，unknown coverage不表示任意IO齐备；`document_saved=false`保留到N3e原writer实际成功。
6. 同步受影响规则/ADR及派生索引，规则/结构/Ruff检查通过后定向stage完整功能/测试/规则并commit；保已有内存反证，源再变补相应delta，状态文档另提交。

### 3.2 N3e界面采用

- 原项目界面检视、选新目录、安装、采用，资源发布/配置待确认/打开/正文落盘分别表达。503含已发布path时保回执和恢复操作，不能报目标不存在或无提示重试同路径。
- 复用`projectStore.adoptOpenedProject(status,{prepareDocument})`、`switchTransaction`、`openDocumentAs`；不另建store/writer，不走会清edits的旧relink。
- 最终正文先冻结，前后核pj/format/documentId/loadSeq/打开意图。非空只等原switchDocument排队的一次writer和`waitForWorkingDocumentSave`实际revision/正文回读，不补第二PUT；空稿显式persistEmpty一次。
- needs_review沿原配置操作明确确认，不静默清授权、不自动Run，不继承发送者机器绑定。取消/切项目/迟到/超时及后阶段失败保旧稿可恢复。
- 专项Vitest/API、正式build、i18n/受影响派生产物通过后，真实DOM验中文流程及网络/写入计数，再功能提交。

### 3.3 N3f完整搬迁

夹具含计算SVG完整INPUT、普通素材、Python runtime/快照、同名静态两历史、外部file/prefix/空目录、原配置、多画布/非活动对象。正式导出→上传检视→新API安装→原界面采用，核全部成员、原序正文及新根引用；只删除自建旧目录并清自有派生缓存。

新父进程/浏览器在中文新根恢复，继续改数据/范围/样式、undo/redo、保存、冷开/版本恢复。模型/INPUT/bytes/参数/几何/文字/PPI分别核，不以截图/菜单成功代替。原schema2/3、Python和默认无声明补受影响回归。适用scope×format分别有有效产物或原稳定拒绝码，画布EPS沿原`eps_not_for_canvas`；N3a–f全部退出前不标完整迁移完成。

## 4. 无人值守执行合同

1. 沿当前树/分支，不新建/切换分支/worktree，不reset/stash覆盖改动，不`git add .`混批；原main保持，未授权不push/PR/合并/发布。
2. 最早就绪完整功能→真实消费者→必要反证→受影响验证→核源身份/退出码→定向功能commit→同步台账→下一项。现在从N3d收口开始，不重做已提交3d和两条已修旧失败。
3. 已授权多子代理，仅独立测试/反证/定位并行；共享app/schema/身份/规则/集成/提交主代理单写，每文件同时一个写入者，不为凑流程委派。
4. Python先Ruff再pytest；前端专项后正式build，按影响补i18n/派生/UI，最终候选才完整链。保首红，不删测试/放宽容差/加豁免，不以专项绿代旧功能资格。
5. config/data隔离、遥测关闭，可写数据沿`config.data_dir()`。共享venv裸Python可能导入旧树，手工脚本/服务/child显式钉当前src并核`__file__`，pytest沿现`pythonpath=src`。
6. 长检查前后、功能提交及约30分钟边界记PROGRESS：HEAD/diff、所有权、SHA、命令/cwd/env/退出码、失败/skip/产物/下一步；重叠组不累计，只结束核过PID/创建时间的自有进程。
7. 缺外部条件时记录并推进独立就绪项，仅缺信息实质改变结果或超授权时询问。活跃会话持续迭代，中断按checkpoint恢复；未建常驻调度器，不以一夜结束标完成。
