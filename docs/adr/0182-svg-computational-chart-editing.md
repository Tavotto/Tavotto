# ADR 0182（草案）：SVG 计算图表编辑、保存与恢复

日期：2026-11-12 · 状态：**Proposed，待上游维护者确认**。

来源：[PR #1873 维护者意见](https://github.com/Tavotto/Tavotto/pull/1873#issuecomment-6818238300)。
编号由上游维护者于 2026-11-13 [分配为 ADR 0182](https://github.com/Tavotto/Tavotto/pull/1873#issuecomment-6846699749)。
维护者在 [#1877](https://github.com/Tavotto/Tavotto/issues/1877#issuecomment-6847781708) 已确认方向、默认开启及登记 1.0 豁免的安排，
并要求复用 ADR 0178、先确认新格式合同、复用现有 worker。维护者已确认 ADR 0178 接入方案与 native-manifest/2 合同，并要求修改其余存储、包和运行参数合同；这些后续接入尚未在本轮实现，整体保持 Proposed，不表示已取得合入或发布资格。
现役约束见 ADR 0017、0072、0096、0108、0120、0150、0178 及相关规则。

## 问题与目标

普通 SVG 只有绘制结果，缺少轴、数据、系列和图例归属。用户需要修改轴范围、重算数据、
联动图例，同时保留未绑定的注释、装饰与原有科学几何。保存后应能重开，跨目录恢复时
不依靠同名文件猜测来源，也不把归档输入或过去的执行回执当作本机脚本执行授权。

建议区分三种来源：

- 可信 Figure 来源：核验 SVG/Figure 配对和执行身份，复用既有编辑和写回流程。
- 普通 SVG：由用户明确校准轴、选择系列和提供数据，确定性脚手架只重算已绑定区域。
- 自包含计算 SVG：携带原场景与闭式编辑状态，导入时校验并重放，不执行原用户脚本。

不从外观猜原始数据；不增加通用 PDF→SVG 转换入口；不放松认证、沙盒、隐私或写回验证。

## 提议的合同与维护者决策点

### 1. 模型、坐标与计算

`ChartModel`、`Binding`、`SourceDescriptor` 是闭式结构，拒绝可执行字段、未知键和非有限数。
场景、数据和模型以字节摘要及 revision 绑定，Python/TypeScript 镜像共用 golden 向量。
场景解析沿用 `svgcheck` 的安全边界；坐标先由 SVG 用户单位转换为物理 pt，再进入现有面板放置链。

计算仍由 Matplotlib 的 locator、formatter、scale、norm 和既有 override/manifest 权威完成。
组合器替换明确归属的区域，并保持 defs/use、clip、透明度和层序；未绑定内容不被整体重画。

与 ADR 0178 的关系：两者都需要确定性计算和数据事实，但本方案服务于“已有 SVG 的显式绑定”，
不增加模型写代码或任意执行入口。配方、统计规则、数据出图工具集继续以 ADR 0178 为唯一权威；
复用其数据读取、指纹和合法性判据是 #1877 第 1 条的要求；具体模块接入继续独立跟踪，本次 worker 接入不代替该项工作。

### 2. `native-manifest/2` 与完整显示框

原 SVG 来源坐标 S 与容纳新刻度、标题的显示框 F 分开。显式 `/2` profile 携带 `render_frame`；
请求、缓存、exact、PNG、历史和导出共同消费 profile 身份。新的几何写操作只通过统一 F→S 适配。
显示框原点、原生尺寸和面板几何在同一文档事务中采用；legacy 与 `/1` 的缺省语义保持。

**维护者已确认（[#1919](https://github.com/Tavotto/Tavotto/issues/1919)，2026-11-15），按三点落实：**

1. 1pt 外扩是唯一合同常量 `FRAME_OUTER_PT`，只在 `engine/chartframe.py` 定义一处；
   `tests/golden/computed_frame_vectors.json` 的 `outer_pt` 与 `from_ink` 向量固定它。
2. `render_frame` 键集合保持闭集；以后加字段一律升 `native-manifest/3`，不在 `/2` 同版本加可选键。
3. 只保留 `manifest.render_frame` 一份，删除产物顶层那份。没有消费方只能读顶层：
   前端先读 manifest，内部快照与导出链读进程内快照，不读产物顶层键。

**回退表现**：旧版（不认识 `native-manifest/2` 的构建）在 profile 准入处把 `/2` 当集合外值拒绝，
返回 `chart_model_unsupported`（HTTP 422）；界面落在该面板的「渲染失败」角标，保留上一版画面，
不进入计算图表编辑；文档本身仍可打开、保存，不自动降级或改写。更早、连 profile 字段都不认识的
构建会把产物当静态 SVG 打开，同样不进入计算编辑，也不改写文档；若文档 schema 本身已高于旧版支持，
则先在文档准入处报 `schema_too_new`，界面显示「这份排版来自更新的 Tavotto」，文件零改动。

实现落点：`chartframe.py` / `chartmanifest.py` 在 3/9（[#1880](https://github.com/Tavotto/Tavotto/pull/1880)）；
HTTP 响应与 TS 镜像在 4e（[#1927](https://github.com/Tavotto/Tavotto/pull/1927)）；
`renderStore` 读取在 6b（[#1928](https://github.com/Tavotto/Tavotto/pull/1928)）。

### 3. schema 4、附件与保存

schema 4 显式表达计算状态及不可变 SVG 来源附件。**维护者已确认（[#1920](https://github.com/Tavotto/Tavotto/issues/1920)）单一存储合同**：
schema 4 与 schema 3 落在**同一套目录、同一个文档列表、同一条版本历史**里，只由正文里的 schema 号区分；
不设第二套命名空间、不复制目录、不另开列表或版本链。
`documents.validate_document` 按 schema 号分派（4 走 `validate_chart_document`）；保存、自动保存、
槽位探测、恢复候选、版本节点与教程校验共用这一个入口，不按格式加端点或会话域；
项目包按同一 `SUPPORTED_SCHEMAS` 集合准入。
附件先按精确字节和摘要保存，正文仍通过原 writer 单次提交。缺失或篡改的附件明确报错，
不回退到同名路径。项目文件身份、保存回执、恢复选择和预览归属沿同一文档代际与事务链传递。

**首次加入计算图表是原地升级**：同一 documentId、同一文件 / 自动保存槽位与版本时间线，正文在提交时写成
schema 4，既有版本历史保留。**升级前必须先**把整份项目文档存成时间线节点作为可退回的备份
（`takeProjectCheckpoint` 原语已就位），备份没落定不得继续写图表。版本节点按正文 schema 分派：2 是单画布检查点，
3 / 4 是整份项目，恢复整份项目节点换的是同一 documentId 的全部画布。
**接线状态（勿读成已实现）**：把图表第一次写进正文的入口属于 7/9；在它接线之前，
`takeProjectCheckpoint` 在生产代码里只有「恢复前」节点一个调用方，本层没有「先备份再升级」的
执行路径。`tests/test_chart_document_upgrade.py` 只覆盖后端能强制的边界（同一文件 / 时间线、
来源冻结失败时正文零改动），**不覆盖**前端那条备份接线。
本草案不重定义现役保存 writer；ADR 整体仍为 Proposed，不因这一合同成立而宣称已取得合入或发布资格。

### 4. 项目包封存、检视与安装

便携项目包封存多画布、普通资源、Figure 配对及已声明的实际输入字节。检视只核验资源关系，
向用户明确选择的新目录安装时先验证预算和成员身份，再排他发布，不覆盖既有目标。
安装不执行包内脚本，不继承其他机器的授权；后续运行仍经现有批准、解释器和资源租约链。

共用项目包入口并按内容分流（[#1921](https://github.com/Tavotto/Tavotto/issues/1921)）。
schema 4 的新包带 `document_format: "v4"`，该标记仅属于项目包清单。`/api/package/inspect` 只核验，
`/api/package/install` 在独占发布前校验所有成员和运行参数。配置导入以本机为准，冲突和超容量分别计数。
用户选择打开已安装的项目后，正文经现有写入路径保存。旧的无标记包继续走原导入路径。
归档图可冷恢复。随包采用执行配置或恢复原生 R 图内编辑仍各有条件。 [运行参数合同](https://github.com/Tavotto/Tavotto/issues/1922)

## `chartprocess` 复用现有 worker

维护者在 [#1877 第 3 条](https://github.com/Tavotto/Tavotto/issues/1877#issuecomment-6847781708)
确定：请求信封、容量计数和沙盒复用现有 worker，不在池外启动 chart 进程。

`chartprocess.run_request()` 保持调用与业务结果合同，转交 `pool.acquire_chart()`。
任务的池键显式区分项目与固定 chart 身份，不冒充项目脚本；启动规格使用既有 safe/module
形态，目标固定为 `tavotto.engine.svgchart`，由 `execspec.worker_argv()` 生成 `--task chart`。
该入口仅接受 v1 `chart` 和管理命令，不 build、不读取用户脚本、不产生用户脚本 execution fact。
probe、脚手架及 EPS 封装共用一个计算入口；每次恢复 rc 状态并释放本次 Figure。

chart 与脚本会话共用 `pool.max_alive()`、LRU/租约、环境代次及退役围栏；保留既有软上限政策，
不将 native 或 one-shot 改造成常驻池会话。取消、超时、协议错乱和未确认关闭都走现有生命周期。
工作目录固定为数据目录中的会话沙盒，文件操作守卫由 worker 与脚本 build 共用同一实现。

JSON/SVG/EPS 的既有预算保留在 `chartwire`；共享传输层有界读写，Python 产生的帧预算交给
workerd 消费。workerd 通过 `bounded_chart_tasks` 能力握手；旧二进制回退到现有 Python 池，
没有池外 chart 回退路径。格式合同与 ADR 0178 接入按各自议题的维护者决定继续独立落实，本节不替代那些工作。

## 保存、导出与安全不变量

- 同一计算 revision 冻结模型、绑定、数据、来源、profile 和原序 overrides；各导出 scope 消费同一事实。
- PDF 经 `pdfbackend`/RenderCore；零 PyMuPDF 不变。不能表达的格式能力按既有逐格式结果披露。
- 原件写回仍是 prepare → 全量重放/几何/像素 verify → commit；失败保持原件，外部新版本不得被覆盖。
- 历史来源意图与当前执行权限分开；重跑、取消、失败、旧响应和跨项目切换不提升历史来源权限。
- 诊断、遥测和运行目录继续沿现有边界，不新增用户文字、数据或路径的遥测入口。

## 审查与交付

原 #1873 保留历史参考；拆分候选与再拆层在 #1877 汇总。fork 依赖及固定 SHA 比较提供逐层增量，
上游 PR 仍以 main 为 base，等待维护者准备真实父分支；不能将 fork 叠栈视为已完成上游叠栈。
本地验证与上游 CI/CLA/维护者批准分开记录，未满足合入资格前不进入合并或发布。
实施进度、会话提示词和临时验收报告不入库；规则与待发片段随所属修改维护。
