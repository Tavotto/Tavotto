# 外部脚本复测：项目上下文与环境修复闭环

Current follow-up: [shared recovery and the user-authorized builtin default](SELF-HEAL.md). Routing statements below describe the earlier frozen trial, before this switch.

本文件是第一阶段的历史快照；最新流程优先的验收与复测见 [FUNCTIONAL-FOLLOWUP.md](FUNCTIONAL-FOLLOWUP.md)。
历史保真缺口仍见 [FIDELITY-FOLLOWUP.md](FIDELITY-FOLLOWUP.md) 和 LATEST-RESULTS.json（存档，见 [EVIDENCE.md](EVIDENCE.md)），默认路由不变。

日期：2026-10-25。基线 `d22b61a92e25d69c0e446e6276c13f5860e04b32`，最终产品代码
`4420cdce9cfea854866e4b0925d989e2fc3a263e`，macOS arm64，真实模型
`claude-sonnet-5-5`，Anthropic 协议。以下结论仅对应这一阶段。

**这次修复了项目资源不可见、诊断副本成功却被当成应用修好、转换采用后输出归属冲突，以及多页捕获不完整却能采用的问题。环境修复的五个受控案例均通过应用执行复查；跨语言转写仍有已证实的保真缺口。因此保持默认路由不变，不据此批准所有 API Key 用户默认切换。**

## 修复与判据

| 对象与时刻 | 原问题 | 修复后的证据要求 |
|---|---|---|
| 转换会话启动时，模型能看到的项目输入 | 只有入口脚本和参考图，已有字体、图片、MATLAB helper 不可见 | 有界静态依赖快照、路径映射、原始及脱敏副本摘要；遗漏明确标记，不能据此声称资源不存在 |
| 校验、恢复和采用时，原资源及只读副本 | 资源已变化仍可能采用旧候选 | 重新核对源与快照的内容摘要；变化即拒绝并清理不可采用候选 |
| 修复工具完成时，当前应用准备会话 | `try_run` 的独立副本成功，真实 worker 的 cwd、环境或缓存仍旧 | 经单次批准消费同一准备会话的 run 动作；记录解释器、配置版本、计划及尝试身份；最后一次变更后才允许 `fixed` |
| 修复后应用重新运行时 | 安装器结束后仍读取旧依赖事实；相对字体/输出目录在另一 cwd 下失败 | 变更退休依赖事实，失败的部分安装也失效缓存；工作目录选择复用既有设置和批准/回滚机制 |
| 转换检查到采用后重开 | 新脚本复用原图 stem，采用成功后 `multiple_stem_conflict` | 仅新候选的实际 Matplotlib savefig 获得独立输出名；先规范化再真校验，所检查、留存、采用的字节一致 |
| 多页候选校验时 | 一页能捕获就通过，漏掉其它失败页 | 应用适配器保留 partial/failed/dropped；任意不完整捕获阻止采用 |
| 临时探针存在时，项目扫描器的归属判断 | 临时脚本被静态注册成用户项目 owner | 使用已有基础设施文件前缀；临时文件不能进入项目脚本归属 |

没有放宽 worker 写入根、会话鉴权、删除守卫或遥测规则。共享脱敏/报告函数下沉用于打断导入环；
11 个搬移函数的 AST 对拍一致，40 个真实/合成文本输入的脱敏结果逐字节一致。
`builtin_default` 的实现与缺省值未改；29 次转换会话均观测为 false，测试只在各自进程选择内置路径。

## 样本与实验边界

固定 **23 个案例、22 个不同上游文件、10 个 GitHub 仓库**：Pillow 11、R 6、MATLAB 6。
两个 boundedline 案例来自同一 README 的不同逐字段落，不算两个独立源文件。
来源 commit、blob SHA、提取行号和许可见 [sources.json](sources.json)。
这是定向外部语料，教程和同仓库样本相关，不是随机用户样本或独立 holdout。

- 原生参照由 Pillow 12.3（旧 API 案例使用隔离 Pillow 9.5）、R 4.5.3、真正的 MATLAB R2025a 运行得到。
- 17 个 Pillow/MATLAB 案例沿用原图参考，R 只提供源码。数值答案、评估判据、旧候选和错误反馈不进入模型项目。
- 实际工具预检确认 51 个源/资源文件可见且摘要正确；35 个文本可读取，16 个二进制显示路径与摘要。字体不是伪装成可读文本。
- 转换使用预装科学栈，关闭转换自动装包；环境修复单独使用各自虚拟环境和真实安装器，不设置强制 worker Python。
- 入口是 Flask 测试客户端，执行实际 agent/worker/准备会话。批准卡由测试程序按已审阅的案例目录边界逐项回答，没有改写模型参数；不等于真人 UI 验收。
- 原始语料、模型记录和 native 图保存在本地评估目录；仓库只提交来源、摘要和结果，不重新分发许可不明确的教程源码。

## 真模型转换结果

| 组别 | 会话 | 当时可采用 | 其余结果 |
|---|---:|---:|---|
| 固定首轮 | 23 | 20 | Pillow paste、rotate、R intervals 为 needs_input |
| 预先列出的盲复跑：Arial、intervals、sinc | 3 | 3 | 与首轮分开计算，不取最好一次覆盖首轮 |
| 最终代码定向复跑：polygon、anchors、plotmath | 3 | 2 | plotmath 单次输出达到上限而截断；无候选采用 |

前 26 次主要行为版本为 `55401e9`；其中部分运行与共享函数的 AST 等价下沉重叠，逐次 dirty diff
摘要保留，不能称全部在 clean commit 上完成。最后三次模型运行及以下完整应用复核使用 clean
`4420cdce9`。逐次状态、候选 SHA、代码版本和 provider 原始用量见 results.json（存档，见 [EVIDENCE.md](EVIDENCE.md)）。

**最终产品复核覆盖全部 25 份曾被采用的候选：24 份通过检查、再次采用并在新应用会话完整运行；
R plotmath 的旧候选因两页捕获失败被正确拒绝。** 新会话用 OS sandbox 禁止读取对应转换缓存，
不调用模型。所有复核案例在采用前原文件摘要保持不变；原源文件/资源保留，临时 probe owner 为 0。
首轮 23 个案例中最终为 19 个能完成该运行闭环、3 个 needs_input、1 个不完整捕获被拒绝。
这是执行与采用证据，**不是 19/23 或 24/25 的保真通过率**。

最后两次 Pillow 定向模型输出各产生一个完整图，polygon 最大通道 MAE 为 0.309/255；
anchors 的 9 个文本实际使用所给 Noto 字体，锚点偏移数值与原生度量一致，图中栅格线仍有像素差异。
plotmath 的新模型会话在工具写入之前出现 `output_truncated`，没有为了通过而提高预算或补喂答案。

## 五类环境故障复测

| 受控故障 | 同一应用与新应用执行 | 捕获 Matplotlib | 独立原生输出比较 |
|---|---|---|---|
| 缺 seaborn | 均执行完成 | 是；finish=fixed | 1/1 像素一致 |
| Pillow 旧 API | 选择 Pillow 9.5 后均执行完成 | 否；finish=convert | 2/2 像素一致 |
| 图片移位 | 关联输入并选择项目 cwd 后均执行完成 | 否；finish=convert | 8/8 像素一致 |
| 缺输出目录 | 创建目录并选择项目 cwd 后均执行完成 | 否；finish=convert | 8/8 像素一致 |
| 缺 Gidole 字体 | 放入用户选定字体并选择项目 cwd 后均执行完成 | 否；finish=convert | 1/1 像素一致 |

共 **5 次模型会话、4 份不同源脚本**；五例原脚本均未改。独立原生输出为 **20/20 像素一致**，
还在隔离字体缓存下复查过。应用捕获事实与原生像素比较分开计量；四个 Pillow 原脚本没有生成
Matplotlib 对象，正确结局是转入转换，不是把 `no_figures_captured` 当作环境仍坏或宣称已经可编辑。
fresh 环境复核使用 `7bb3610`；之后 `4420cdce9` 的改动限于转换采用/捕获检查。

## 仍未通过的保真项目

- **字体与文字几何**：给定 Gidole/Noto 文件已被真正使用，Gidole 两行的 baseline/alpha 正确。
  Arial 案例两次都回退到 DejaVu Sans Bold；文字 top 从旧错误的 0 px 恢复到 9 px，却从原生
  `[3,9,316,54]` 扩大到 `[4,9,368,55]`。第二次还把椭圆宽度由 201 改成 251。
  不能因为一个 anchor 指标变绿就说字体保真通过。
- **Pillow alpha**：putalpha 的四张图 alpha 逐像素一致；有效预乘 RGB 最大差小于 1/255。
  全透明像素的隐藏 RGB 差异会让普通 RGB MAE 很大，不能误判成白底铺平。composite 五个最终输出保留；
  `rocket_resize.jpg` 是中间输入，不虚算成丢失的第六张最终图。pieslice 用 200 点多边形近似椭圆，不算精确保真。
- **R 数值**：intervals 首轮安全要求真实数据；盲复跑取得真实 singer 数据后，8 组中位数/CI 最大误差
  `4.98e-14`，原 IQR 与 factor 顺序正确。旧首轮区间缩窄 14.2857% 的候选能被独立反证检出。
  复跑成功不能替代首轮结果或保证以后模型稳定。
- **R 多页与数学文字**：sinc 两轮均恢复两张独立图，50×50 网格最大误差 `5.33e-15`，但第一张仍缺
  原生 x/y/z 标签，光照近似。plotmath 原生五页，旧候选 Agg 捕获两页失败，现已阻止采用；求和下限
  从 `1` 变成 `i=1` 仍是语义错误。lattice labels 也多出 `i=`，配色不同。
- **MATLAB helper**：Airy 26×26 核最大误差 `2.78e-17`；boundedline/shadedErrorBar 的 helper 定义已可读取，
  透明度、NaN、样本标准差等比原测试改善。仍有 parula 近似、z/颜色范围变化、64 与 256 级 LUT 差异、
  空心圈变实心、MATLAB 绿色被映射成 Matplotlib 深绿，以及 Z=150 surface 被替为二维的问题。

R bar、percent stack 在声明的有限数值/结构判据下通过，不作逐像素主题等价声明。
任何近似披露都只记录为近似，不升级为保真通过。本批不能证明通用转写能力已经过关。

## 回归与反证

| 反证/验证 | 退出码与结果 |
|---|---|
| 换回旧上下文/采用实现 | 各 exit 1；当前对应测试 exit 0 |
| 放开资源读取竞态、跳过脱敏、保留变化资源的恢复候选 | 三种变异各 exit 1；当前各 exit 0 |
| 换回旧嵌套 worker 错误投影与依赖缓存处理 | exit 1，5 个失败；当前修复定向组 exit 0，258 passed / 1 skipped |
| 换回输出归属、partial 传播和探针隔离修复前的实现 | exit 1，7 failed / 2 passed；失败覆盖实际采用后重开、三种不完整捕获、precommit、适配器和扫描器 |
| 最终转换/上下文/导入架构/路由集成组 | exit 0，391 passed |
| 最终不完整捕获与错误词表组 | exit 0，296 passed；与前组有 8 个重叠，不累加 |
| 隐私、遥测、导入架构、修复工具和上下文组 | exit 0，556 passed / 2 skipped |
| 前端 | 11,352 tests / 600 files；i18n 检查、类型生成和 pnpm build 均 exit 0 |
| 本地应用冒烟 | 实际服务 cold/hot render、PDF 导出/覆盖、诊断及干净关闭通过 |
| 仓库检查 | Ruff check/format、retirement source/deps/block、36 项规则索引、AGENTS 预算、diff check 通过 |

没有宣称完整后端套件、发行包、Windows/Linux、浏览器批准 UI 或全部 Matplotlib 版本通过。
测试数量是对应批次的记录，存在重叠，不能相加成一个总数。

## 与最近 PR 的关系

- [#1416](https://github.com/Tavotto/Tavotto/pull/1416) 的正文是 6 份合成脚本上的 18 个改图场景，
  不能替代本次环境修复与跨语言转换证据；[#1398](https://github.com/Tavotto/Tavotto/pull/1398)
  已有缺 CSV 的真实修复正例，也不能说此前完全没有真模型环境测试。
- [#1418](https://github.com/Tavotto/Tavotto/pull/1418)（审查 head `a5bcf0a`）仍 open，处理包遮蔽、
  安装失败分类与 worker 日志。它与本改动的报告/工具模块共享代码域，未纳入本轮测试。
- [#1419](https://github.com/Tavotto/Tavotto/pull/1419)（审查 head `8715bd5`）仍 open，将改图 check
  接到 `run_validation`/`_convert_runner`。集成时必须区分“创建转换脚本的新输出归属”和“保留原图编辑的输出归属”，
  不能把转换的自动输出名改写及独立 owner 检查套到原图编辑上；它镜像的 `STAGE_PREFIX` 也必须同步。
  两侧改同一适配器返回区，合并时要保留 partial/failed/dropped/manifests，重跑双方用例；未宣称组合已验证。

默认路由不变。本报告提供修复与已验证边界，剩余模型保真问题继续留作失败案例。
