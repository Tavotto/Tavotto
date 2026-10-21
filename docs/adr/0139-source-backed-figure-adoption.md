# ADR 0139：来源驱动采用——把「原件是什么」和「拿什么编辑」拆开

日期：2026-10-20 · 状态：**Proposed**（PR1 落失败恢复体验；PR2、PR3 逐阶段取得资格）
相关：[0013 运行期 Figure 素材](0013-runtime-figure-assets.md)、[0014 safe / native](0014-safe-native-execution-profiles.md)、
[0020 Native Matplotlib 桥](0020-native-matplotlib-bridge.md)、[0034 图例条目绑定](0034-legend-entry-binding.md)、
[0049 写回像素门](0049-write-back-pixel-verification.md)、[0053 执行回执](0053-foundation-contracts-and-preparation.md)、
[0098 savefig 图幅就是这张图（含「完整 PNG 的受限采用」）](0098-savefig-frame-is-the-figure.md)、
[0127 执行策略](0127-execution-policy-and-unified-retry-guard.md)、[0133 兼容路由](0133-run-routing-compatibility-first.md)。
起因：[#1200](https://github.com/Tavotto/Tavotto/issues/1200)。

## 问题

ADR 0098 末节的「完整 PNG 的受限采用」（`selected-figsize-v1`，#789）把三件事绑成了一个条件：

1. **原件身份**——磁盘上这张 PNG 还是不是用户的那张（sha256 + 字节数 + 项目内路径）；
2. **可编辑对应**——重跑脚本得到的 Figure 能不能当作这张图的编辑基础；
3. **像素等价**——零编辑重画是否与原件逐像素相同（`artifactcontext.validate_candidate`，`max_abs_diff == 0`）。

现行实现只用第 3 条证明第 2 条，并为了让像素证据稳定，用 `figsession._ARTIFACT_ARTISTS` 把可采用的图限制在
线 / 柱 / 文字。审计（#1200 附件 A–D）实测：

- 白名单拒掉的 Legend、offsetbox、FancyBboxPatch、scatter、errorbar、fill_between、annotate、colorbar，
  在普通（非 artifact）路径上**都已可编辑**、`unregistered` 为空——拒它们只为像素证据，不为编辑安全；
- 原件由用户自己的 Python 画、Tavotto 用内置 runtime 重画时，跨 matplotlib 小版本（Win11：3.10.7 → 3.11.1）
  去掉图例仍差 2 万像素，逐像素必拒；macOS 终端直跑（macosx 后端 + Retina）还会让 `figure.dpi` 变成 200；
- safe worker 拦 savefig 但**不渲染、不快照**：`occurrence` 只证明「这次执行里哪一次调用写向被选路径」，
  不证明磁盘字节是它写的；「保存后脚本又改了 Figure」今天唯一的防线就是逐像素零差。

结果：大多数真实科学 PNG 进不了编辑，用户只能重写整张图。

## 决定

### 一、三件事分开验证，各有自己的结论

| 证明什么 | 由谁证明 | 失败意味着 |
| --- | --- | --- |
| 原件身份 | `artifactcontext.create_context / assert_current`，六处复核点（入口、解析 worker 前、准入锁内、`validate_candidate` 前中后、`_engine_attempt` 后、导出发布前），**两种策略原样共用** | `source_changed`，什么都不做 |
| 可编辑对应 | 选中上下文里的 safe 执行：保存调用唯一（`_artifact_matches == 1`）、捕获的 Figure 就是那次保存的 Figure、图幅一致、编辑安全条件（见三）、无 `unregistered` | 这张图不能经脚本编辑 |
| 像素等价 | `validate_candidate` 的逐像素零差，**判据一字不改** | 不能宣称「精确」，不等于不能编辑 |

### 二、两种策略并存，严格策略不动

- **`selected-figsize-v1`（精确）**：原样保留，包括白名单、零差、guard（`artifactValidation` v1）与全部拒绝面。
  零差成立的图走它，行为与今天逐字节相同。
- **`script-backed-figure-v1`（来源驱动，新）**：进 `SELECTED_ARTIFACT_POLICIES` 闭集，有自己的池键段，与严格策略、
  与不同字节**永不共享** worker、缓存与 witness。只在精确策略被拒、且原因属于「像素证据拿不到」时才提供
  （见四）；不能证明可编辑对应时一律不提供。

采用状态的稳定语义：

| 状态 | 含义 | 零编辑显示 | 可否编辑 |
| --- | --- | --- | --- |
| `exact` | 精确策略通过 | 原像素 | 可以（现行行为） |
| `needs_confirmation` | 来源驱动的可编辑对应已证明，但重画与原件有可见差异 | 原像素 | 需要用户看过「原图 / 重画」对照后确认 |
| `reconstructed` | 用户已确认以这份重画为编辑基础 | 原像素 | 可以；界面与导出都不称其为原件精确复现 |
| `unavailable` | 可编辑对应证明不了（保存不唯一、自定义绘制、来源不完整……） | 原像素 | 不可经脚本编辑；位图排版照常 |

**不设自动接受的像素容差。** 「非零差但看起来一样」不能被可反驳地证明（抗锯齿、字体回退、tight_layout 漂移与
实质内容变化在像素统计上互相重叠），所以来源驱动策略下任何非零差都进 `needs_confirmation`，由用户对照确认；
`reconstructed` 永不升级为 `exact`。差异度量（差异像素比例、差异包围框、按区域的热图）只用来**帮用户看**，不用来替用户判。

### 三、编辑安全条件与像素条件拆开

`_require_static_artifact_figure` 拆成两档：

- **像素档**（精确策略专用）：现有函数原样，含 `_ARTIFACT_ARTISTS` 白名单。
- **编辑档**（来源驱动策略）：去掉「类型必须在白名单」这一条，**保留其余所有安全条件**——
  自定义 `draw` / `get_children` / `get_tightbbox` / `get_transform` 覆写、`draw_event` 与 artist / axes 回调、
  `agg_filter`、`path_effects`、非标准 transform / locator / formatter / scale / canvas、`usetex`、
  自带字体文件、非 rectilinear 或带 locator 的 Axes、子图 / 寄生轴、活跃布局引擎、存活线程。
  对每个 artist 的判据从「类型在白名单」改为「类型来自 matplotlib 本体、没有实例或子类级的绘制覆写」，
  并照旧要求 `finish_artifact` 的 `unregistered` / `unsupported` 闸为空。

编辑本身**不另做一套**：仍是 `LiveFigureSession` / `FigState` / `manifest` / `overrides` / `legendmodel`，
撤销、重做、热会话与一次性冷重放沿用同一条代码路径。

### 四、哪些拒绝可以转来源驱动

精确策略拒绝时按 reason 分流（前端与后端共用一张表，登记为同源对）：

- **可转来源驱动**：`source_mismatch`（像素不等）、`unsupported_render_state` 中**仅由白名单触发**的那一种
  （后端要把它细分成独立 reason `artist_profile`，不再与真正的不安全状态混在一起）。
- **不可转**：`save_ambiguous`、`destination_unresolved`、`capture_incomplete`、`layout_history_required`、
  其余 `unsupported_render_state`（自定义绘制、回调……）、`validation_budget_exceeded`、`source_unreadable`、
  `source_changed`、`background_visibility_required`（后者是让用户选择，不是失败）。

### 五、原件与编辑结果分离（最重要的不变量）

1. **零编辑 = 原像素**：显示、原图导出都用磁盘字节，不启动 worker；`needs_confirmation` / `reconstructed`
   也一样。撤销回零编辑即回到原像素。
2. **有编辑 = 整图重渲染**：用可信 Figure 整张重画，不在原 PNG 上叠加局部，不出现旧曲线残影。
3. **永不写原件**：来源驱动策略下写回、历史写回、跨图同步、specfix、invalidate、历史 GET 全部拒绝。
   审计 D 指出今天这些拒绝全由**请求自带的 policy** 触发、服务端没有状态；来源驱动策略额外加一道
   **不依赖请求声明**的服务端闸：采用成功时在 `config.data_dir()` 记一条「该项目 × 该源文件 × 该字节身份
   已被来源驱动采用」，写回类入口命中这条记录一律拒绝。
4. **导出如实标注**：编辑后的 PDF 保留 matplotlib 能给的矢量结构；不把「位图转 PDF」说成拿到了矢量对象。

### 六、文档与 guard

- 新 guard 为面板上的**并列可选字段** `sourceBackedFigure`（`version: 1`），不复用 `artifactValidation` v1
  的形状或字段名：`{version, policy, sourceId, bytesSha256, sizeBytes, scriptSha1, reconstructionSha256, confirmedAt}`。
- `reconstructionSha256` 是用户确认时那份零编辑重画的字节摘要：确认绑定的是**那一份重画**。重开或重新准入时
  重画摘要不同（脚本、数据、依赖、解释器任何一项变了）→ 确认作废 → 回到 `needs_confirmation`，编辑保留、不自动换基。
- 未知 / 畸形 / 版本不对的 guard 一律拒绝，不当 legacy；旧前端读到会忽略该字段、把面板当未采用 PNG
  显示原像素（安全的降级）。老项目不会凭空获得这个字段。
- 采用与确认各是一次事务性提交：只写 `figure.frame="figsize"`、nativeW/H 与 guard，**不改页面 x/y/w/h/crop**；
  取消、worker 重启、切项目时不留半个 guard。

### 七、执行边界

- 只在 safe 档执行，沿用 `_admit_artifact` 对 native 的拒绝；不新增 native 路径，不因「想编辑 PNG」放宽 Safe。
- 只在用户动手（选中后开始编辑 / 点确认）时执行；打开项目、预览、扫描零执行（ADR 0013 §6/§7、`projscan` 门禁）。
- **不改 `pool._prioritized_candidates` 的全局顺序。** PR2 用全局链条选出的解释器重画；跨版本差异交给
  `needs_confirmation`。「用画原件的解释器重画」（审计 B §6.3：按字节摘要绑定的来源记录 + 用户一次确认的授权 +
  池键 pin 段）放到 PR3 之后，单独评估。
- 已知缺口（如实写进 PR）：safe 档不拦 `open('w')` / `PIL.Image.save` / `shutil.copy`，脚本在重画时覆写原件只能
  **事后检出**（`assert_current` → `source_changed`，整个采用作废），不能阻止或回滚。不在本 ADR 内扩大写拦截面。
- 不 pickle、不跨进程传任何 Figure。

### 八、保存瞬间状态

来源驱动策略下，可编辑基础是**脚本跑完后的最终 Figure**。保存之后又改了 Figure 的脚本，最终态与原件会不同——
这正是对照确认要让用户看见的东西；系统不声称拥有保存瞬间的快照。PR3 评估在 `_patched_savefig` 命中被选路径
那一次保存时就地留下渲染证据，届时才可能把「保存瞬间」从推断变成事实。

## 分阶段

| PR | 内容 | 风险档 | 回滚 |
| --- | --- | --- | --- |
| PR1 | 失败恢复体验：409 reason 结构化分类（单一出处）、一句话 + 一个主按钮、原图始终保留、失败记忆（不再每次点击重跑脚本）、有脚本时「重建为可编辑图」（复用 ADR 0013 运行期素材：另起一个面板，原 PNG 面板不动） | normal | 纯前端，revert 即回 |
| PR2 | `script-backed-figure-v1`：编辑档安全条件、`artist_profile` reason、对照确认、`sourceBackedFigure` guard、服务端写回闸、零编辑原像素 / 编辑整图重渲染 | high（full-ci、mutation 清单、合前维护者点头） | 功能开关默认关；关掉即回到 PR1 行为 |
| PR3 | AI 交接来源捕获：交接时记录脚本、解释器与 savefig 参数（按字节摘要绑定），保存瞬间证据，同次执行多格式绑定 | high | 记录是附加数据，忽略即回 |

PR3 现状（第一片）：只做「交接时记录事实 + 响应里只读展示」——插件 `handoff.py` 在真的跑了脚本时，由脚本进程退出时
自报版本 / 后端 / `rcParams["figure.dpi"]`（不记 savefig 实参，探针看不到），按原件字节 sha256 写 `data_dir()/origin/`
（`engine/originrecord.py`，不存解释器路径），准入响应的 `source_backed.origin` 展示它与本次重画的版本。
**不参与 exact / needs_confirmation 判定，不换解释器。** 保存瞬间证据、同次执行多格式绑定、按记录选解释器
（审计 B §6.3 的授权与池键 pin）仍是后续，需单独 ADR 与用户授权。

## 不支持

- 没有脚本的 PNG：只能位图排版，不伪造 Figure。
- 同 stem 但不是同一次保存的 PNG / PDF：不按 stem 绑定。
- 同一路径多次 savefig、保存目的地解析不了：`unavailable`。
- 自定义 artist / 回调 / 自定义绘制：`unavailable`。
- 「重画相似」永远不被称为「原件精确一致」。

## 修订（2026-10-21）：「跑完没出图」原因卡的文件证据与库覆盖

`engine/rasterhint`（脚本用位图库直接画图时的原因卡）原先只认「字面量输出路径解析出的文件运行后存在」，输出路径运行时才定
（argparse / 默认目录）的 Pillow 脚本永远不触发，用户只看到 PNG 被当图片放上画布。本次修订：

1. **第二种文件证据**：静态上有写出调用、本次运行也没有 Matplotlib 图时，项目素材盘点（`iter_assets`，沿用既有边界，3 s 预算）里
   mtime ≥ 运行开始 - 1 s 的新增 / 被改写文件即证据；hint 带 `path`（项目相对路径，能确定时），`in_project` 为真。
2. **库覆盖**：plotly（`write_image`，`write_html` 不算）与 ROOT（`TCanvas.SaveAs` / `.Print`）；二者另认 `.pdf` / `.svg` / `.eps` 输出。
   ROOT / plotly 在有动态 import 时仍可提示（真实 RooFit 脚本按拟合结果动态加载生成模块）；位图库保持看不全不说。
3. 不变：import 了 matplotlib 家族一律沉默；证据缺失（旧文件 / 没写）一律沉默；不改 outcome / facts。
4. hint 里的 `library` 与 `path` 是后续「生成可编辑版本」按钮（本修订不含）的输入。

