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
- 不跨进程传任何 Figure；不落盘任何序列化的 Figure。**唯一例外**是 §九（2026-10-22 修订）：safe worker 在**本进程内存**里为
  「存盘后又被脚本改过」的图留一份保存瞬间的 pickle 字节，本进程自己还原、自己用，随会话消亡。

### 八、保存瞬间状态

来源驱动策略下，可编辑基础是**脚本跑完后的最终 Figure**。保存之后又改了 Figure 的脚本，最终态与原件会不同——
这正是对照确认要让用户看见的东西；系统不声称拥有保存瞬间的快照。（2026-10-22：safe worker 现在能拿到快照了，见 §九；
以下 2026-10-21 的告知机制仍是判据，快照只在它判为真时才被用上。）

**补充（2026-10-21）：至少要告知。** 可编辑基础仍是最终 Figure、仍不拍快照；但「存完又被脚本改过」这件事要说出来，
否则用户在 Tavotto 里看到的与磁盘文件不同却无从知道。判据是 matplotlib 自带的 stale 传播：给 Figure 的 `stale_callback` 套一层只记录的透明包装（`figcapture.mark_saved`，只套一次，
原回调原样转调），每次保存记完账把私有标记清成 False，被以真值调用就置真；脚本跑完（native：第一次交出去）时标记为真即改过
（`figcapture.note_modified_after_save`，每张图只判一次，之后 Tavotto 自己的编辑不算；同 stem 惰性验证的那次渲染还原标记）。
另连一个 `draw_event` 回调（只连一次、weakref）在每次重绘画完时清标记——draw 过程中 matplotlib 自己会置 stale，不该算脚本改图；
语义与旧的「清 stale」一致：从最后一次保存或重绘之后被改过才算，存后改了又重绘一次的盲区不再报告。
两个钩子只在脚本运行期存在：`note_modified_after_save` 判定时一并卸掉（`stale_callback` 还原、`draw_event` 断开；已判过的图不再装），
之后 Figure 与原生无异，采纳检查 `_require_artifact_figure` 看不到它们（Codex #1250 P1）。
**不写 `fig.stale`**：用户脚本在存盘后读到的 stale 必须与原生 matplotlib 一致（Codex #1250：清零会让读 stale 的脚本走不同分支）。
不额外渲染、不复制 Figure。manifest 为真时带
`modified_after_save: true`，前端在元素树搜索行放一枚胶囊、悬停给原因。只改 rcParams 不改已有 artist，不算改图。
看护 `tests/test_modified_after_save.py`、`tests/bridge/test_bridge_same_path_last_writer.py`。

## 分阶段

| PR | 内容 | 风险档 | 回滚 |
| --- | --- | --- | --- |
| PR1 | 失败恢复体验：409 reason 结构化分类（单一出处）、一句话 + 一个主按钮、原图始终保留、失败记忆（不再每次点击重跑脚本）、有脚本时「重建为可编辑图」（复用 ADR 0013 运行期素材：另起一个面板，原 PNG 面板不动） | normal | 纯前端，revert 即回 |
| PR2 | `script-backed-figure-v1`：编辑档安全条件、`artist_profile` reason、对照确认、`sourceBackedFigure` guard、服务端写回闸、零编辑原像素 / 编辑整图重渲染 | high（full-ci、mutation 清单、合前维护者点头） | 功能开关默认关；关掉即回到 PR1 行为 |
| PR3 | AI 交接来源捕获：交接时记录脚本、解释器与 savefig 参数（按字节摘要绑定），保存瞬间证据，同次执行多格式绑定 | high | 记录是附加数据，忽略即回 |

PR3 现状（第一片；「保存瞬间证据」已由 §九 单独成文并授权）：只做「交接时记录事实 + 响应里只读展示」——插件 `handoff.py` 在真的跑了脚本时，由脚本进程退出时
自报版本 / 后端 / `rcParams["figure.dpi"]`（不记 savefig 实参，探针看不到），按原件字节 sha256 写 `data_dir()/origin/`
（`engine/originrecord.py`，不存解释器路径），准入响应的 `source_backed.origin` 展示它与本次重画的版本。
**不参与 exact / needs_confirmation 判定，不换解释器。** 同次执行多格式绑定、按记录选解释器
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


## 修订（2026-10-22）：九、保存瞬间快照——「存后改过」从告知升级为可编辑基础

用户明确要求（2026-10-22）：脚本 `savefig` 之后又改了 Figure 时，可编辑基础用**保存那一刻**的状态，不止告知。§八 把这件事留给
「单独 ADR 与用户授权」；授权已给，设计如下。**结论先说：方案站得住，且全部落在已有不变式之内，没有需要用户另行裁决的取舍；
两处刻意收窄（只做 safe worker、不动精确策略）见 3 与 9。**

### 1. 机制

在 safe worker 的 `LiveFigureSession.note_savefig`（每次被拦截的 savefig 记完账、`mark_saved` 之后）给这张图拍快照：
`pickle.dumps(fig)`——matplotlib 文档化的 Figure 持久化，不是自造序列化。脚本跑完、`note_modified_after_save` 判为真、
pyplot 兜底收完之后（`LiveFigureSession.adopt_save_moments`，在 `prepare_artifact` / `instrument_all` 之前），
对判为真的图**还原复制品**，让它顶替捕获表里那张最终态 Figure；判为假的图一个字节不动，快照直接丢弃。
还原出的复制品挂回 `FigureCanvasAgg`（unpickle 出来的是裸 canvas），其余走**同一条**编辑代码路径（`FigState` /
manifest / overrides / 撤销重做不分叉）。

### 2. 进程、时刻、存放、预算

- **进程与时刻**：safe worker 进程内，每次保存记完账之后（此时 `savefig` 内部对 Figure 的临时改动，如 `bbox_inches="tight"`、
  dpi 切换，已经还原；与 `mark_saved` 清脏标记是同一时刻）。快照不是脚本改图：拍完把脏标记重置为「刚存完」。
- **与 ADR 0014 §6 / ADR 0020 §8 的关系**：那两处防的是 Figure 跨进程。快照不越界，所以是登记的例外：门禁按类 / 函数登记（`figcapture.py` 的
  `_RestrictedUnpickler` / `_SnapshotPickler` / `_snapshot_dumps` / `SaveMomentLedger._take_into`），并用 AST 断言字节流向。
- **存放**：只在**本进程内存**（`SaveMomentLedger`，每张图只留最近一次保存的字节串，会话结束或判定完即释放）。
  **不写 `data_dir()`、不写包目录、不写项目、不发给别的进程。**
- **账的生命周期绑「一次脚本执行」，不绑会话**（Codex #1314 P1-a）：`Worker.build` 在跑脚本之前调 `begin_save_moments()`
  换一份新账（旧账先释放）；`adopt_save_moments` 采用或放弃之后调 `release_save_moments()` 清空字节，账对象留着、不置 None。
  pool 会在同一个 worker 里重跑脚本（`instrument_all` / 预览发布临时失败后重试），重试因此从新账开始——否则存后改图的脚本会
  被悄悄按最终态编辑，而一次性重放建新账、用保存态，热态所见 ≠ 重放所得，违反写回不变式。
- **预算**：单张 ≤ 32 MiB，本次执行累计 ≤ 128 MiB，超出即放弃并记原因（`too_large` / `budget_exceeded`）。**只用字节数当预算，
  不用墙钟**——热会话与写回重放必须在同一处同样地放弃，计时抖动会破坏「热态 == 重放」。实测拍一张快照约 2–5 ms
  （1,000,000 点散点 18 MB，仍是毫秒级）。
- **哪一次保存**：该图**最近一次**保存（与 §八 的脏判据「最后一次保存或重绘之后」同一时刻）。同一路径多次 savefig：取最后一次，
  与「最后写者胜」一致；精确/来源驱动采用要求选定那次保存（`occurrence`）恰是快照那次，否则 `not_selected_save`、退回最终态。
  同一张图以多个 stem 被捕获（`a.png` / `b.png`）：说不清快照属于哪个 stem，`multi_stem`、退回最终态。
  `bbox_extra_artists` 非空：那批 artist 属于原件，复制品对不上，`bbox_extra_artists`、不拍。

### 3. 适用范围（刻意收窄）

- **只做 safe worker。** native bridge（用户进程里的活 Figure、脚本在屏障后继续跑）与浏览器 playground 不拍快照，
  维持 §八 的告知（`modified_after_save` 无 `save_moment`）；它们的写回路径不是这条一次性重放，扩展需另议。
- **精确策略 `selected-figsize-v1` 一字不动**（§二）：其「存后改过 → 像素不等 → 拒绝」是自己的闸门，
  `adopt_save_moments` 在该策略下直接放弃。来源驱动策略与非 artifact 的普通路径使用快照。
- 开关：环境变量 `TAVOTTO_SAVE_MOMENT_SNAPSHOT=0` 整体关掉（回到 #1250 行为）；worker 都由同一父进程拉起，热会话与一次性重放读同一个值。

### 4. 还原保真度：证据，不是断言

- 复制品**不称**「原件精确一致」。「精确」仍只由 `artifactcontext.validate_candidate` 的逐像素零差判定；快照只是让那道门更常过。
- 结构自检：拍快照时记结构签名（图幅、每个 Axes 的 line/collection/patch/text/image/container 计数），还原后复核，对不上
  `structure_mismatch`、退回最终态。
- 语料实测（`tests/test_save_moment.py` 与手工脚本 `tests/support/save_moment_corpus.py`）：CompatBench 96 个 case 共 136 张图，
  matplotlib 3.8.4 / 3.10.7 / 3.11.2 三个版本结果**逐一相同**：131 张还原后与原件 PNG 逐字节相同（0 张像素差），5 张因 lambda
  格式化器 / 用户自定义类退回（`unpicklable` / `restore_failed`）。
- 运行期不额外渲染（每次保存多渲两次不可接受）；保真度由上面的语料 + 结构签名 + 来源驱动路径上原有的逐像素对照承担。

### 5. 写回事务不变式（热态所见 == 写进文件 == 重开后重放）

写回的 verify 段本来就是**一次性 worker 从零重放整个脚本**（`pool.one_shot`），与热会话跑同一份 safe worker 代码。快照在重放里
同样被拍、同样被采用，且判据只依赖脚本与 matplotlib（字节预算、结构签名都是确定的，不含墙钟或随机），所以热会话与重放得到同一个
复制品；`test_hot_session_and_fresh_replay_see_the_same_snapshot_figure` 钉住。任何一侧放弃而另一侧没放弃，会被既有的
`replay_divergence` / 像素门拦成 409，不会静默落盘。**不是**「重开时重放的是快照而非脚本」——重放永远是跑脚本，快照是跑脚本过程中的产物。

### 6. 安全

- pickle 字节**只在产生它的进程内存里存在**，从不落盘、从不来自项目文件（项目文件可由他人提供，读 pickle 就是反序列化漏洞）。
  Tavotto 全仓没有任何「读项目里的 pickle」的路径，本修订不新增。
- 还原用受限 Unpickler：只解析 matplotlib / mpl_toolkits / numpy 本体（对象自己的 `__module__` 也须在这些包里；带点名字只许是
  类的嵌套类）和少数精确名字（`object` / `getattr` / `functools.partial` / `collections.OrderedDict` / `itertools.count` 等）。
  用户自己的类 / 函数 / 模块一律拒绝（`restore_failed`）：还原不执行用户代码、不 import 用户模块。这是纵深防御——真正的边界是上一条。
- **拍摄侧同样先把关，不执行用户的序列化代码**（Codex #1314 P1-b）：快照用 `pickle.Pickler` 子类，`reducer_override(obj)` 先于任何
  `__reduce__` / `__reduce_ex__` / `__getstate__` 被调用，按 `type(obj)`（类 / 函数按自己的 `__module__` + `__qualname__`）查
  **与还原侧共用的同一张白名单**（`figcapture.save_moment_module_allowed`，单一出处；还原侧在它之上再加解析后的 `__module__` 校验）。
  用户类、用户继承 matplotlib 的子类（模块是用户的）、函数 / lambda、实例上挂的 `__reduce__` 钩子一律立刻抛 `PicklingError`，
  记 `unpicklable`；绑定方法（C 实现的 `getattr(self, 名)`）放行、`self` 仍逐个过关。否则用户对象的 `__reduce__` 改了活 Figure
  再抛异常，`pickle.dumps` 只记 `unpicklable` 却不还原那次改动，随后 `mark_saved` 清脏标记，就把改动藏进了「只保存一次」的图里。
  还原前的结构签名也排在序列化之后算（先通过白名单，才读 `ax.lines` 之类，不会落到用户重写的属性上）；Figure / canvas 本身若是用户子类，
  连 `canvas.manager = None` 都不碰。失败时仍把脏标记重置为「刚存完」：因为拍摄期间没有任何用户代码执行，不存在需要保留的改动。
  `reducer_override` 对 None / bool / int / float / str / bytes 与精确的 list / tuple / dict / set / frozenset / bytearray 不调用
  （它们没有用户代码，容器元素仍逐个经过）；这些的**子类**会被调用，因此用户的 dict / list 子类被拒。matplotlib 自己的
  `Figure.__getstate__` / `CallbackRegistry.__getstate__` 只做纯数据整理，不回调用户代码；回调函数本身要被序列化时同样过白名单。
  **第三层：可信方法面指纹（Codex #1314 P1-d；用户 2026-10-22 裁决：检测到 matplotlib 被 patch 就放弃快照，不再逐个封堵）**：
  脚本能 patch 的不止拍摄时的 `__reduce__`，还有还原时走的 `Figure.__setstate__`，以及任何被拍摄 / 还原间接调用的方法。
  逐个堵是无底洞，所以直接问「matplotlib 此刻还是不是启动时那个样子」：
  - **范围**：`matplotlib` / `mpl_toolkits` 已导入模块里每个类 `__dict__` 中的可调用与描述符（函数、staticmethod、classmethod、property、
    内建）加模块级类 / 函数，外加还原路径上实测会解析到的几个入口模块（`copyreg` / `enum` / `collections` / `functools` / `numpy.ma.core` /
    `numpy` 的重建函数所在模块）。值记 `id` 并握住对象本身（防地址复用漏报）。
  - **时刻**：`figcapture.freeze_trusted_surface()` 在 worker 里**用户代码（含 paper_style）跑之前**、Tavotto 自己的 patch 装完之后拍一次，
    进程内只拍一次（同 worker 重试不重拍，否则上一轮脚本的 patch 会被当成基线）。**拍摄前（`take`）、还原前（`restore`）各比对一次**，
    每次都重算真实值，不缓存；不一致 → 原因 `matplotlib_patched`（进闭集 `SAVE_MOMENT_REASONS`；前端只透传该字段，无按原因分支的文案），
    退回只告知，`modified_after_save` 照常如实报。
  - **运行期才导入的模块**（`mpl_toolkits.mplot3d`、`matplotlib.pyplot` 常常在启动时还没导入）：没有启动基线，所以**从严**——首次见到时
    要求它的 Python 函数的定义文件都在 matplotlib / mpl_toolkits / 标准库 / numpy 目录里（被用户函数顶替当场判被改过），通过才补登记，
    此后靠 `id` 比对。已知边界：用户用「库来源」的可调用对象去顶替，不在检测范围（那不是用户代码）。
  - **指纹只做静态读**（Codex #1314 P1-e）：被检查的对象可能带自定义 `__getattr__` / `__getattribute__` / `__eq__` / `__hash__` / `__bool__` 或元类的
    `__instancecheck__`，比对本身不能把它们的代码跑起来。所以只用 `type(x)` + `is`、`id`，以及直接取自 `type` / `function` / `code` /
    `staticmethod` / `property` / `ModuleType` 的 C 描述符去读（模块只认精确的 `ModuleType`，子类一律当「被改过」）；不对被检查对象用
    `getattr` / `hasattr` / `isinstance` / `callable` / `==` / `hash` / `bool` / `str` / `repr`。拍摄侧的 `reducer_override` 与类级钩子检查同样静态读。
    `take` / `restore` 对比对整体兜底：任何异常都按 `matplotlib_patched`（拍摄侧意外记 `unpicklable`）放弃快照，绝不让 build 失败。
    指纹记录范围也据此收紧：只记函数 / 内建 / 描述符 / 嵌套类，以及类型不是库的对象；库类型的实例（如 `Path._unit_circle` 这类首次使用才写入的缓存、
    `__slotnames__`）与库类在模块间的惰性重新绑定（pyplot 的 `_backend_mod`）会合法变化，不记。
  - **变化条目的复核只接受 matplotlib 包内的函数重绑定**：与启动指纹不一致的条目，只有新对象是精确的 `FunctionType`（文件在 matplotlib /
    mpl_toolkits 的包目录下、`__module__` 顶层也是它们）或 C 层 builtin（`__module__` 属于它们）才接受并更新基线——这只为覆盖 matplotlib
    自己的惰性重绑定（pyplot 的 `switch_backend` 等）。`functools.partial(用户函数)`、绑定方法、描述符包装、库类型的实例、scienceplots /
    seaborn 等第三方包和标准库的函数一律判为被改（用户裁决：matplotlib 被任何东西改过就放弃快照）。指纹因此也记库类型里**可调用 / 描述符**
    的实例（partial、绑定方法、`cached_property`……）。「库来源」的扩大目录集合（site-packages、解释器前缀）只用于首次见到的模块的来源判定。
  - **合成文件名与迭代纪律**（Codex #1314 复核）：`<string>`（用户 `exec` 生成的函数就是它）一律不算库来源；`<frozen X>` 只在 X 精确等于函数静态读出的
    `__module__` 且是标准库模块（`sys.stdlib_module_names`）时才认，其它 `<…>` 合成文件名都不算——Windows 打包 runtime 里冻结的标准库函数照常通过。
    首次见到的模块里外来类所在顶层包的 `__path__` 只认精确的 `list` / `tuple`（元素是精确 `str`）；命名空间包的 `_NamespacePath` 一迭代就会重算、
    走 `sys.meta_path` / `path_hooks`（用户可注入的代码），所以其它类型一律当「非库来源」，绝不迭代。启动时取 matplotlib / mpl_toolkits 的包目录也不迭代
    `_NamespacePath`，改读它已算好的内部列表（再不行用 matplotlib 目录的兄弟目录）。字典查找（类字典 / 模块字典）逐项只比精确 `str` 键，不用 `in` / `[]`；
    `sys.modules` 不是精确 `dict` 就当不可信。
  - **实测耗时**：启动指纹约 10 ms（107 个模块、约 6800 个条目），每次比对约 3.8 ms（静态描述符读比 `getattr` 慢，换来不执行用户代码）。
  - **与纵深层的关系**：指纹覆盖 matplotlib 与上述入口模块；`_class_hooks_trusted` 另外覆盖没进指纹的类型（其它 numpy 类型等）的类级钩子，
    且只在 dump 内读一次、很便宜，所以两者保留，不删。结构层（拍摄期间置脏即作废）同样保留。
  - **代价**：脚本自己 patch 了 matplotlib（哪怕是无害的包装）就拿不到保存瞬间的快照，退回只告知。CompatBench 语料里没有这类脚本，可还原数不变（131）。
  语义代价：语料里 `al_ticks`（用户 lambda 的 FuncFormatter）与 `ca_collection_alpha`（用户 HandlerBase 子类）原本在还原阶段才
  `restore_failed`，现在拍摄阶段就是 `unpicklable`——都是退回最终态，数量不变。
- 拍快照临时摘掉 `canvas.manager`（matplotlib 据此写 `_restore_to_pylab`，否则还原会把复制品登记进 pyplot），`finally` 放回。

### 7. 失败即退回现状、可回滚

任何一步失败（不可 pickle、超预算、还原失败、结构不符、选定保存不是最后一次……）→ 可编辑基础仍是脚本跑完的最终态，
manifest 带 `modified_after_save: true` + `save_moment_unavailable: <闭集原因>`，行为与 #1250 完全相同。成功则
`modified_after_save: true` + `save_moment: "restored"`，前端胶囊改说「按存盘时的样子编辑」（不再说「与存盘文件不同」，那已不成立）。
闭集 `figcapture.SAVE_MOMENT_REASONS`。

### 8. matplotlib 版本

3.8.4 / 3.10.7 / 3.11.2 实测一致（见 4）。`Figure.__getstate__` 自 3.x 起稳定，同进程内存内的往返不涉及跨版本反序列化
（matplotlib 的版本告警只在跨版本读盘时出现）。

### 9. 不做

- 不对 native / 浏览器拍快照；不改精确策略；不在运行期渲染做对拍；不把复制品称作原件；不为快照新增任何落盘文件或 API 字段以外的协议改动
  （manifest 加两个可选字段，老前端原样忽略）。
- 存后改了又重绘一次的盲区（§八）依旧：判据没报，就不会用快照。
- 回归看护：`tests/test_save_moment.py`、`tests/test_modified_after_save.py`、`tests/test_script_backed_policy.py`。
