# SVG 计算图表编辑：下一阶段实施提示词

以下内容可直接复制到实施会话。它授权按既定目标继续本地实现、测试和功能提交，不授权远程推送、合并或发布。详细任务安排见 [NEXT_PHASE.md](NEXT_PHASE.md)，最终范围仍以 [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) 为准。

```text
请继续实现 Tavotto 的 SVG 计算图表编辑，以最终用户可用为目标，在现有授权范围内持续自主修改、验证、修复和提交。常规可逆步骤自行推进，不要停在方案、单个模块或一轮测试通过。先读下面的实际状态，不能照旧 checkpoint 直接补 Dialog 后宣布完成。

一、工作位置与必须保留的成果

原工作区：E:\0 - Project\Tavotto
已有实施 worktree：E:\0 - Project\Tavotto-svg-computational-editing
已有实施分支：zixu.wang/svg-computational-editing
本轮复核代码：7cbb0e6ed6746441fbf0d867b941a0fe79f7fa6f
复核/接续文档 worktree：E:\0 - Project\Tavotto-svg-next-phase
复核/接续文档分支：zixu.wang/svg-computational-next-phase
独立复现证据：E:\0 - Project\svg-review-20261104

先检查各树实际 HEAD、status、worktree 占用和 Git 身份；现有分支可能已经前进。不得覆盖用户/其他会话的改动，不得 reset --hard 或丢弃已有 24 个实现提交。

尝试 fetch 核对最新远端。上轮 fetch 因连接和 SSH 认证失败，缓存 origin/main=2bc94f7f 不能冒充今天最新。成功后评估并整合主干；失败记录错误并继续不依赖远端的本地工作，不修改用户凭据或关闭认证。

从最新有效实施状态创建独立 worktree 和新的 zixu.wang/ 前缀分支，带入本轮文档。若实施分支没变，文档分支已包含完整实现历史，可作为起点；若已前进，保留其新增提交，带入文档后重新核对当前缺陷。不要从 main 另起一套而漏掉现有实现。不要在并发使用的实施树切分支或改文件。

二、先读的文档

1. 根 AGENTS.md、待修改层的最近 AGENTS.md 及它指向的相关规则/ADR。
2. docs/rules/repo/lessons.md 的相关层；同源契约表、判据主语、第三路径审查和本地验证要求。
3. docs/implementation/svg-computational-editing/NEXT_PHASE.md。
4. 同目录 PROGRESS.md、IMPLEMENTATION_PLAN.md、SG00-CAPABILITY-MAP.md。
5. docs/adr/0166-svg-computational-chart-editing.md 与对应实验；把其未验证假设当作待验证，不当作可降低用户目标的授权。

NEXT_PHASE.md 的 R00–R13 决定下一阶段顺序；原 SG00–SG17 决定最终能力范围。历史 checkpoint 中“核心已可用”“SG10 剩 1/3”等结论已被本次复核纠正。没有充分新证据不能恢复 passed。

三、当前真实情况

已有模型/绑定、场景和坐标工具、数据输入、脚手架、组合器、HTTP API 及未接线前端 store。尚无完整 UI、现有画布渲染/命中接入、项目冷恢复和冻结导出闭环。

复核时新增后端 112 项、前端 23 项、前端 build 通过，但以下最终产物缺陷已独立复现：
- 改 x 范围为 [2,8]，facts 为 2/4/6/8，最终 SVG 仍显示旧 0/10。
- 100px、100viewBox 的路径最终缩为 75 单位，片段 pt 与源用户坐标混用。
- bar/scatter 组合失败；errorbar 成功返回但丢失误差线和端帽。
- 热图改 cmap/vmin/vmax，最终保留旧 colorbar。
- 无 XML id 的普通 path 可以绑定，却在 compose 时找不到节点。

前端 3 条反例也复现：A 迟到的 inspect 覆盖 B；改表单仍保留旧 result/preview；compute 失败仍满足当前 ready 判据。当前 store 尚无生产 UI 消费者，先修再接线。

backend-repro/reproduce.py 的 exit 0 表示成功观察到五类缺陷，不表示产品正确。frontend-repro/result.json 和 root-result.json 的 3 条失败落在目标断言。正式回归应断言正确结果，旧实现红、修复后绿，不能将“缺陷存在”测试当资格。

四、执行顺序

先完成 M0（R00–R04）：
1. 核对最新状态，保留/整理反例，修正能力台账，验证组合架构假设。
2. 统一节点身份、片段 pt 到源用户坐标再到目标父节点的变换，覆盖无 id、嵌套 transform、非零 viewBox、preserveAspectRatio、use/defs/clip。
3. 用明确的轴/系列/图例/色条所有权生成和替换完整对象，禁止按 line2d_ 前缀或遍历序号猜系列。修复最终刻度、误差成员、散点/柱、热图色条和布局。
4. 从同一不可变来源/资源快照完成验证、子进程计算、合成及 manifest；验证源文件计算中改变的时序；保持既有执行隔离和预算。
5. 统一完整渲染身份，包含影响输出的来源、模型、绑定、数据、视觉覆盖、字体和引擎/配置。不能只拼 renderKeyOf + data_digest。
6. 修复向导异步代际和提交资格；支持 x/y 独立 scale、多系列/列映射。改表单、切项目、关闭、失败均使旧结果失去当前提交资格。

再完成 M1（R05–R07）：
普通 SVG 两条曲线及未绑定注释 → 用户选择轴/系列/数据 → 独立校准 → 对照确认 → 改范围/样式/图例 → 精确命中 → 撤销重做 → 保存 → 清派生缓存、新进程重开 → 按既有 scope×format 合同逐项验证原图和混合画布导出。五格式使用独立读取器核验，画布 EPS 保持现有 eps_not_for_canvas；SVG 再导入只作外观对照，自包含语义恢复在 R08 验收，不新增 EPS 等格式导入。

持久化合同和旧版防损策略须先于真实保存入口落实；不能声称“旧程序忽略 chart 字段就不丢数据”。资源附件、模型和 SVG 版本有唯一可恢复提交点。

UI 复用 documentStore、renderStore、PanelView、现有能力/属性、i18n 和 transport；预览、manifest、保存、导出引用同一版本。E2E 使用真实后端，不 mock bind/compute 证明产品可用。

M1 的两曲线 fixture 是集成顺序，不是最终范围缩减。继续 M2（R08–R12）：
- A：可信 Figure 来源采用，保持 safe/native 和运行授权；不能按同名 Python 文件猜来源。
- C：真正自包含模型/资源的 SVG 和项目冷恢复，原 Python 不存在时仍能计算；外部改图保留 metadata 必须做可见内容对照。
- 图元、text/tspan、文字旋转、分组/取消组合、use 实例与现有编辑体验；解除计算绑定需明确且可撤销。
- SG00 矩阵中的剩余统计/系列族、色阶、多轴、双轴、inset、极坐标、3D 等适用能力。缺产品适配不能伪装成缺用户数据；不编造原始数据、误差定义或 z 值。
- 事务写回 prepare → verify（全量冷重放+几何+像素门）→ commit，失败原件零改动。
- 预检/出版规范/样式/问题定位、桌面/CLI/MCP/内嵌画布/playground 和真实发行依赖。

最后 M3（R13）：冻结候选，运行完整原功能回归、等价性/不变式/CompatBench、真实 E2E、性能和支持平台真安装物验收。完成原 SG00–SG17 的全部退出条件才称最终可用。

五、验证和提交纪律

- 先说清断言主语：最终图、当前面板当前版本、真实保存文件、冷启动结果或导出产物。不能用中间 facts、包含 path/image、字节可重复替代科学和视觉正确性。
- 最终 SVG 的点位/刻度/色条/误差用独立预期核对；人造错轴、漏系列成员、旧色条、旧响应和删 verify 应使对应检查在目标处失败。
- Python 改动先 Ruff，再针对性 pytest；前端跑相关 Vitest 和 pnpm build，按改动补 i18n、生成物和真实 E2E。完整候选执行项目规定全量；新改动或未解问题才追加无关昂贵检查。
- Windows 下使用实际解释器和绝对 PYTHONPATH 探针，测试不可硬编码只有 Windows 才有的 venv 布局。Node 的相关测试按仓库记录设置 NODE_OPTIONS。日志保留命令真实退出码，skip 不算通过。
- Chromium 英文/WebKit 的关键 spec 要先用 --list 核对实际收集。浏览器不冒充真桌面，Windows 通过不冒充 macOS 通过。
- 基线红、环境红、新增红分别用证据定性；不得删测试、放宽容差、静默降级/栅格化、关闭功能或降低写回验证来凑绿。
- 不全局拒绝无脚本来源的原图 EPS，验证可兑现路径；保留既有画布 EPS 范围限制。遇格式或 SVG 表达限制先按第三路径审查验证结构方案，记录真实不可兼得的边界。保持 RenderCore/pdfbackend 和零 PyMuPDF，不建立第二套权威管线。
- 不重写稳定 Python 核心，不绕过认证/项目边界/沙盒，不污染真实用户配置；运行缓存用 data_dir，测试用隔离目录，仅清理自己启动的 PID。
- 每个完整功能分别 commit，合同镜像、测试和对应文档同步；不 squash 成一个“支持 SVG”，不加 Co-Authored-By。未通过的中间构建不能称完整功能提交。
- PROGRESS 只更新当前状态表并按日期追加证据，保留历史。记录 SHA、命令/退出码/数量、实现与验证边界、遗留项和下一步。不要生成一整套重复进度文档。

六、无人值守和交付

在授权范围内持续推进，例行实现、测试、局部修复和本地提交不反复询问。能独立并行的后端修复、前端状态机和反例审查可委派；共享合同指定一个写入者。

不存在可靠输入时不替用户确认科学语义；自动测试用明确定义的 fixture。遇真正需要外部权限/平台/输入的条件，记录具体缺口，继续独立任务。不要把时长、轮数或上下文结束当作完成条件，也不要在缺少条件时假称通过。

当前授权不包含 push、远程 Issue/PR、合并或发布。最终交付本地分支与按功能 commit 列表、真实使用步骤、测试证据和样例产物；明确区分已实现、本机已验证、待平台验收、待发布。

现在开始核对当前树和复现证据，按 NEXT_PHASE.md 的 R00 顺序实际执行，持续推进到最终验收条件满足或确有无法自主解除的外部阻塞。
```
