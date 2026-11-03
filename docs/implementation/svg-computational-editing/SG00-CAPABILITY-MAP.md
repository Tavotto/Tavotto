# SG00：能力清单与基线（SVG 计算图表编辑）

> 状态：draft → passed 由本文末尾的「基线结果」与「退出核对」决定。
> 本文档回答计划 §6 SG00 的四件事：环境记录、registry 全量映射、SVG 语料、基线测量。
> 事实性盘点结论（file:line 级）摘要在 `PROGRESS.md`；本文不重复引用细节。

## 1. 环境与工具链记录

见 `PROGRESS.md` 环境节。要点：实施 worktree / 分支 / 基线 SHA `2bc94f7f`、
venv（Python 3.13.11，matplotlib 3.11.2，pytest 9.0.3）、字体已取、import 探针钉在本树。

## 2. registry.json 全量映射（73 个 active 功能）

主语约定：**「SVG 计算面板」= PanelObject.fileKind 为 'svg' 的面板**（现状已存在该 kind，用于静态素材）。
「原样保留」表示该功能不区分面板来源、靠共享通路自然覆盖，不需要专属实现；
「共享通路」批次指该功能的既有实现必须被验证在 SVG 面板上同样成立，实现落在所指批次。

### 2.1 项目与接入层（原样保留；来源选择扩展属 SG15）

| 功能 | 原路径回归证据 | SVG 计算面板语义 | 前提 | 批次 |
| --- | --- | --- | --- | --- |
| project.first-launch | e2e golden-paths | 不变 | — | 保留 |
| project.open-by-path | e2e golden-paths | 不变（含中文/空格路径） | — | 保留；SG16 追验 |
| project.readiness | e2e golden-paths | SVG 项目同样给出接入状态 | — | 保留 |
| assets.run-script-figure | e2e asset-library / first-run-qualification | Python 路径不变 | — | 保留 |
| assets.open-and-run | e2e open-and-run | Python 路径不变 | — | 保留 |
| assets.editability-filter | e2e asset-library | 素材过滤需含 SVG 卡（现状已含） | — | SG15（口径核对） |
| assets.dependency-one-click-repair | e2e dependency-one-click 等 | 不变（SVG 导入不引入新 Python 依赖） | — | 保留 |
| assets.png-adoption-recovery | e2e guarded-png-activation | 不变 | — | 保留 |
| assets.source-backed-adoption | e2e guarded-png-activation | 不变；SVG 绑定不与其混用 | — | 保留 |
| assets.missing-input-relink / rewrite | e2e missing-input*.spec | 不变 | — | 保留 |

### 2.2 画布层（共享通路，必须对 SVG 面板成立）

| 功能 | 原路径回归证据 | SVG 计算面板语义 | 前提 | 批次 |
| --- | --- | --- | --- | --- |
| canvas.place-and-render | e2e features | SVG 面板放上画布并渲染（现状静态 SVG 是 `<img>` 直读；计算 SVG 需走渲染版本） | SG03 导入、SG07 计算渲染 | SG10（接线）、SG03/07（能力） |
| canvas.select-and-drag | e2e features | 点选/拖动 SVG 面板（页面级变换） | 与 PDF/raster 面板同一套 x/y/w/h 变换 | SG10 |
| canvas.proportional-scale-snap | e2e proportional-scale-snap | 角柄等比缩放 + 子图边吸附 | manifest 几何 | SG10 |
| canvas.single-figure-edge-resize | e2e figure-edge-resize | 边拖留白 / 角拖图幅，文字 pt 不变 | 图幅语义 | SG10 |
| canvas.layout-panel-corner-edge | e2e layout-panel-edge-crop | 角缩放（含标注）/ 边裁剪 | 同上 | SG10 |
| canvas.single-context-bar | e2e features | 浮动栏可达 | — | SG10 |
| canvas.multi-context-bar-align | e2e features | 多选对齐含 SVG 面板 | — | SG10 |
| canvas.page-fit-and-placement | e2e features | 不变 | — | 保留/SG10 复验 |
| canvas.quick-menu | e2e quick-menu | 右键菜单（z 序/重建/对齐）对 SVG 面板成立 | 「重新构建」对 SVG = 重算绑定模型而非跑脚本 | SG10 |
| canvas.text-annotation | e2e golden-paths | 画布标注叠加在 SVG 面板上（标注是画布对象，不改 SVG 数据） | — | SG10；导出叠加 SG12 |
| canvas.text-rotate | e2e features | 同上（标注自由旋转） | — | SG10 |
| canvas.copy-paste / cross-canvases | e2e cross-tab-paste / features | 复制实例独立编辑（不共享绑定） | 模型/绑定随实例复制且重映射 id | SG10 |
| canvas.zoom-to-selection / empty-menu | e2e features | 不变 | — | 保留 |

### 2.3 图内编辑层（计算语义的核心，批次主体）

| 功能 | 原路径回归证据 | SVG 计算面板语义 | 前提 | 批次 |
| --- | --- | --- | --- | --- |
| figure.select-and-edit | e2e features / inspector-redesign | 点选 SVG 场景元素改属性（装饰）或计算对象（轴/系列） | SG05 场景身份、SG06 绑定 | SG05/06/07 |
| figure.element-drag | e2e fake-realtime | 拖动预览平面 + 一次定稿 | svgPreviewStore 语义复用 | SG05 |
| figure.patch-resize | e2e flowchart-resize | 独立框缩放（装饰类） | — | SG10 |
| figure.multi-select-drag | e2e multi-select-geometry | 多选整组拖动 | — | SG10 |
| figure.overlap-cycle | e2e twin-axes-pick | ⌥ 轮换（命中几何来自场景解析） | SG05 命中 | SG05 |
| figure.shared-colorbar-group | e2e shared-colorbar-group | 共享色条成组 | SG09 色阶 | SG09 |
| figure.patch-drag-carries | e2e patch-drag-carries | 拆组/拖框携带子元素 | SG05 组语义 | SG05 |
| figure.ticks | e2e ux-consistency | 四边刻度/方向/次刻度（计算对象） | SG07 tickmodel 复用 | SG07 |
| figure.large-figure-edit | e2e large-figure | 降档预览仍可编辑 | preview budget 复用 | SG10 |
| legend.properties | e2e features / inspector-redesign | 图例位置/样式（计算联动） | SG08 图例绑定 | SG08 |
| figure.mixed-script-fonts | e2e mixed-font-fallback | 中文字体选择与回退 | SG05 文字/字体契约 | SG05 |
| figure.cross-figure-align | e2e cross-figure-align | 跨图吸附 | manifest 几何 | SG10 |

### 2.4 样式 / 检查 / 历史 / 文件

| 功能 | 原路径回归证据 | SVG 计算面板语义 | 前提 | 批次 |
| --- | --- | --- | --- | --- |
| style.panel | e2e features | 样式面板读/改/恢复 | override/样式链 | SG14 |
| problems.panel / subplot-cards / canvas-pins | e2e inspector-redesign / features | 预检问题定位到 SVG 元素 | SG14 判据单一出处 | SG14 |
| edit.undo-redo | e2e features / fake-realtime STATE-08 | 同一撤销栈覆盖绑定与装饰 | documentStore 复用 | SG02 起各批自带，SG10 集成 |
| edit.timeline-scrubber | e2e timeline-scrubber | 版本缩略图含 SVG 面板 | SG11 持久化 | SG11 |
| file.autosave-restore | e2e fake-realtime / asset-library / layout-session | 自动保存/重开含模型与绑定 | SG11 | SG11 |
| keyboard.core-loop | e2e keyboard-golden-path | 键盘闭环对 SVG 面板成立 | — | SG10 |
| help.shortcuts / command.palette / tutorial.entry | e2e nav-audit / features / tutorial | 不变 | — | 保留（SG15 文案核对） |

### 2.5 导出

| 功能 | 原路径回归证据 | SVG 计算面板语义 | 前提 | 批次 |
| --- | --- | --- | --- | --- |
| export.pdf / png / tiff | e2e features / keyboard-golden-path | 计算面板按冻结版本导出（original+canvas） | SG12 冻结源扩展 | SG12 |
| export.svg | e2e features | 计算面板导出 SVG（文字保留/可选元数据） | SG12 | SG12 |
| export.preview-zoom / location-picker | e2e export-preview / export-location | 不变 | — | 保留 |
| （EPS 无独立条目，随 exportreq.FORMATS） | tests/test_export_svg.py | 计算面板 EPS 能力按 SG01 结论逐项披露 | SG01 | SG12 |

### 2.6 设置 / 诊断 / 交互辅助（原样保留）

settings.dialog / settings.search / settings.journal-picker（SG14 接预检）/ settings.appearance /
settings.coding-agents / i18n.language / diagnostics.bundle / embedded.codex-canvas（SG15 MCP）/
desktop.* 全组（SG15/SG17 平台格）/ contact.maker。

### 2.7 SVG 专属现状条目

| 功能 | 现状 | 本计划处置 |
| --- | --- | --- |
| assets.svg-preview | e2e svg-assets.spec.ts（`/api/file` 矢量预览 + 加入画布） | 保留并作为 SG03 的回归底线；计算入口在 SG06/10 上新 |

## 3. manifest 可编辑属性面（SVG 计算适配的目标闭集）

来自 `engine/manifest.py` `_fields_for`（盘点详见 PROGRESS.md 摘要）。
按批次归属：

- **figure**：size_mm / facecolor / transparent → SG07（图幅几何）、SG10（UI）。
- **axes 2D**：position / visible / xlim / ylim / xscale / yscale / invert_x/y / aspect / 四边 ticks / grid_* / spine_* / facecolor → SG07（axis 联动闭环）。
- **axes3d**：elev / azim / roll / pane / proj_type / axis_arrows → SG09（3D 对等，需三维模型）。
- **ticks（刻度组）**：fontsize…format / major_mode / major_step / major_values / minor_* → SG07（tickmodel 复用，禁另写刻度规则）。
- **text / ticklabel**：text / fontsize / color / weight / style / fontfamily / rotation / alpha / visible / ha / va / linespacing / zorder / bbox_* / stroke_* → SG05（文字几何）+ SG07（数据锚定注释）。
- **line / linecoll**：label / color / linewidth / linestyle / alpha / visible / marker / markersize / markerfacecolor / markeredgecolor / zorder → SG07（二维线/散点）。
- **collection**：facecolor / size / marker / edgecolor / linewidth / linestyle / alpha / hatch / cmap / vmin / vmax → SG07（散点）+ SG09（色阶）。
- **bar_series / bar**：label / facecolor / edgecolor / linewidth / hatch / bar_width / alpha → SG08（柱图族）。
- **errorbar**：color / linewidth / capsize / cap_thickness → SG08（误差定义）。
- **stem_series**：label / color / linewidth / linestyle / marker / markersize → SG08。
- **legend / legend_text**：loc / loc_anchor / fontsize / frameon / title / entry_order / ncol / …；条目 binding + handle_*（LEGEND_ENTRY_STYLE_PROPS 严格同源）→ SG08。
- **colorbar**：label / orientation / extend / cmap / vmin / vmax / tick_* / outline_* → SG09。
- **image**：cmap / vmin / vmax / interpolation / alpha / origin → SG09（网格/图像）。
- **arrowpatch / patch**：arrowstyle / color / mutation_scale / facecolor / fill / hatch / size_frac → SG05（装饰保留）。
- **generic**：visible / zorder → SG05。

**对等性判据**：上表每行要么有 SVG 计算面板的对应实现与证据，要么在 SG00/SG09 的「信息前提」栏写明缺什么输入（如 3D 需 z 数据、统计图需原始样本），不允许静默降级为置灰。

## 4. 导出合同与来源判据（现状事实，改造目标在 SG12）

- 两范围：original（无 x/y/w/h，图幅由源定）与 canvas（页面尺寸 + z 序 objects）；normalize 唯一入口 `exportreq.normalize()`。
- 五格式路径：EPS=worker-only；canvas 范围 PDF/PNG/TIFF=RenderPlan（Canonical PDF → PDFium 栅格）；SVG=svgwriter（text 策略 + 隔离）；静态 SVG 素材 original=字节复制、canvas=不支持（`svg_asset_export_unsupported`）。
- 来源判据：`wants_execution = runtime || overrides || rerender`；`FILE_KINDS` 不含 svg → **SG02/SG12 的核心扩展点：新增「声明式模型」来源类型**，不把 svg 塞进 FILE_KINDS 冒充可放置原件。
- 写回：仅 pdf/png 两格式、`not_parameterizable` 门（有脚本才可写回）；SVG 计算写回在 SG13 按同一 prepare→verify→commit 合同扩展，资格判据显式化（哪些来源有原件可写回）。

## 5. safe / native / runtime 与冷态行为（现状事实）

- 执行入口两条：safe worker（池 + one_shot）与 native bridge（`tavotto run`）；`enginesession.resolve()` 是唯一路由；native 会话绝不 fallback safe。
- runtime 素材（`runtime:` id）只读、写回硬拒、导出必须当次 live 渲染。
- SVG 身份五元组 `{project, source_id, patch_hash, static_sha256, remap_fingerprint}`；durable 快照内容寻址、immutable 引用只认 durable store、冷重放零执行零发现。
- **SVG 计算适配必须**：静态来源/声明式模型不得被误判为用户 Python 脚本（不进 safe worker 的脚本路由）；冷态（新进程重开）按引用重建，不执行陌生代码。

## 6. SVG 语料（tests/support/svg_corpus/）

自建夹具（无第三方版权问题，随测试提交；外部样本如引入另行记录许可）：

| 文件 | 内容 | 用途 |
| --- | --- | --- |
| `line_two_series.svg` | matplotlib 生成：两条曲线 + 图例 + 刻度，附独立原始 CSV | 路径 A/B 主夹具（数据已知可核对） |
| `bar_error.svg` | matplotlib：柱 + 误差棒 + 图例 | SG08 |
| `heatmap_colorbar.svg` | matplotlib：imshow + colorbar | SG09 |
| `plain_shapes.svg` | 纯图元（rect/circle/path） | 装饰编辑、绑定无关 |
| `text_outlined.svg` | 文字转曲（glyph path） | SG05 转曲判据 |
| `use_and_groups.svg` | `<use>`、嵌套 `<g>`、变换 | SG05 结构 |
| `clip_opacity.svg` | clipPath、透明组叠加 | SG01 实验二/三 反例 |
| `gradient_image.svg` | 渐变 + 内嵌 data-URI 图片 | 导入边界 |
| `external_danger.svg` | 带脚本/外链/DTD 的恶意样本 | SG03 安全（测试拒绝路径） |

生成脚本 `tests/support/svg_corpus/make_corpus.py`（用实施 venv 的 matplotlib 渲染可复现字节；
`line_two_series` 等附 `*.data.csv` 与 README 记录每个样本的「独立原始数据 + 图表含义」）。

## 7. A/B/C 三条验收配方（可复现数据）

- **A（可信来源）**：`make_corpus.py` 生成 `line_two_series.svg`（同时留 matplotlig 渲染工程：脚本 + 数据 CSV）。
  导入 Tavotto → 校验与该 Figure 的绑定 → 改 x 范围/图例/颜色 → 保存重开 → 导出 SVG/PDF →
  用独立公式核对数据点坐标、用渲染像素对照原版。
- **B（普通 SVG + 原始数据）**：`plain_shapes` 改造的纯 SVG 轴框 + 已知 CSV（x=0..10, y=x² 采样）
  → 用户绑定轴（线性）+ 两系列 → 对照确认 → 改 log/线性 → 独立坐标公式验证点位置与刻度。
- **C（保存重开）**：A/B 保存的项目包 → 关闭进程 → 重开 → 冷重放像素/几何一致；
  自包含导出 SVG 在无原始工程的环境导入可继续编辑；仅引用型如实提示缺来源。

## 8. 基线测量结果

（运行环境：实施 venv，Windows 10.0.26200，`TAVOTTO_NO_TELEMETRY=1`，被测 SHA `a046b799`（= 基线 `2bc94f7f` + 计划文档提交）。完整日志在任务证据目录 `svgplan-evidence/`（b1_svg.log / b2_equiv.log / b3_invariants.log / exits.txt）。）

| 套件 | 结果 | 说明 |
| --- | --- | --- |
| SVG 专项（test_svg_assets, test_svgfonts, test_rendercore_svgwriter, test_rendercore_svginspect, test_svgstore） | exit 0；331 passed，107 skipped，0 失败 | skip 定性：① `not_run: independent Inkscape renderer unavailable`（本机无 Inkscape，浏览器对照腿已由 Playwright 补上）② `not_run: Playwright unavailable`（venv 补装 python playwright + chromium 后转绿）③ `symlinks unavailable: WinError 1314`（Windows 符号链接特权缺失，平台性环境缺失，非产品失败；CI Linux 腿真跑） |
| 四路等价（test_equivalence_matrix） | exit 0；41 passed，7 skipped，0 失败 | skip 全部为「没有 tavotto-workerd 产物」（Rust 控制面腿；已在实施机后台 `cargo build`，SG15 前补跑） |
| 引擎不变式（test_invariants_engine） | exit 0；94 passed，0 skip，0 失败 | — |

基线定性：**无产品失败**；全部 skip 属环境缺失（Inkscape / Windows symlink 特权 / workerd 未构建），已逐条登记，后续不得因本功能改动而新增红。
运行器事实：pytest 9.0.3 在 `-q --no-header` 下不打印摘要行（只有进度点），结论以**退出码**为准（lessons L-002），计数从进度字符统计得出。

## 9. 退出核对

- [x] registry 73 项全有归属（本表 2.1–2.7 覆盖全部 active 条目）
- [x] manifest 属性闭集有批次归属（§3）
- [x] A/B/C 配方与可复现数据（§6/§7；语料已生成并通过 svgcheck 校验——matplotlib 自产 SVG 须先 `strip_matplotlib_doctype` 才可解析，该事实进入 SG03）
- [x] 基线结果回填且 skip 逐条定性，零产品失败（§8）
