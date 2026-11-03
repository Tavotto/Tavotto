# ADR 0164：画布文字可以用「原图那个字体」（本机用户字体，只记身份）

日期：2026-11-03 · 状态：**Accepted**（维护者 2026-11-03 拍板方案；1.0 收敛纪律豁免已给：「给，三项都做」）
修订：[0060 字体政策](0060-font-policy-and-allowlist.md)（批准集合仍是默认与回退；本 ADR 在它旁边开**一条**受限的、按字节认脸的本机字体通道）
相关：[0163 文字旋转与图内文字复制为标注](0163-canvas-text-rotation-and-copy-figure-text.md)（「复制为标注」在那里，本 ADR 只提供字体底座）、[0032 属性能力层](0032-typography-capability-layer.md)、[0033 字形回退](0033-scientific-text-and-font-fallback.md)、[0045 CJK 回退链](0045-cjk-font-fallback-chain.md)、
[0072 渲染后端切换](0072-default-render-backend-cutover-and-pymupdf-retirement.md)、[0073 默认字体与排版策略](0073-default-font-layout-policy.md)、[0152 所见即所得文字编辑框](0152-wysiwyg-text-editor.md)；
`docs/rules/repo/third-path-review.md`。

## 动机

用户在编辑图内元素时想复制粘贴：图里的文字应当能「复制为画布文字标注」，且**粘出来的标注字体要和原图一模一样**。
图是 matplotlib 在 worker 里按本机字体画的（DejaVu Sans 来自用户环境的 mpl-data，也可能是 Arial / Times New Roman / 宋体 /
SimHei），而画布文字由 RenderCore 合成，字体只能是批准集合（Liberation 三族 + Noto Sans SC，ADR 0060），
`TextObject.fontFamily` 是 `serif | sans-serif | monospace` 闭集。粘出来的标注只能「像」，不能「是」。

本 PR 只做**字体底座**：让画布文字能带一条原图字体的身份、导出时用那张脸；「复制为标注」本身是另一个 PR（叠在旋转 PR 上），
它从图内文字的 manifest 数据取 `sourceFont`（`web/src/lib/sourceFont.ts::sourceFontFromElement`，本 PR 导出）。

## 要保持的不变量（第三路径审查的起点）

1. ADR 0060：不摸系统字体、不用另一张脸冒充；按 sha256 认脸（RC-022 / RC-023 / RC-037）。
2. 项目文档不带字体文件（体积、许可、可复现）；磁盘格式不升版，老文档逐字节不变。
3. 换一台没装这个字体的电脑：保留名字 + 给提示，画面用最接近的批准族画——与图内文字「装不上的字体：保留名字 + warning，
   绝不换掉再改文档」同一条规则。
4. 预览看到的字体 == 导出嵌入的字体；预览与导出对「这张脸能不能用」是同一个判据。
5. 路径、字体字节不进遥测、不进诊断包、不进文档。

冲突根因：0060 把「安全 / 许可 / 可复现」押在**一个闭集**上——这是对的——但「图里用了什么字体」是用户机器上的事实，本来就在闭集之外。
第三路径不是放宽闭集（加系统字体回退 = 静默替换），也不是禁用「复制文字」（砍能力）；而是**把「身份」与「字节」分开**：
文档只持有身份（名字 + 文件 sha256 + 面序号 + 粗斜），字节只在本机、只在被身份点名时、过一遍同一套入场检查后才读。
闭集没有被放宽：没有 `source_font` 的对象走的代码路径与 0060 逐字相同。

## 裁决

| # | 问题 | 裁决 | 落点 |
| --- | --- | --- | --- |
| 1 | 字体身份怎么从 worker 拿到 | manifest 的文字元素新增 `face_src`（matplotlib 解析链第一张脸）与 `cjk_src`（回退链上画了第一个中日韩字符的那张），各为 `{family, sha256, face, bold, italic}`：sha256 是**字体文件字节**的哈希（worker 里按 (路径, mtime, size, 面) 缓存，上限 192 MiB），`face` 是 `.ttc` 里的面序号（沿用 `font_face_index`），粗斜取 FT2Font 的 `style_flags`。与 `font_faces` / `_glyph_scan` 走同一条 `_resolved_font_paths`，所以「报的」== 「matplotlib 实际画的」。**解析用文字自己的完整 `FontProperties`（粗细 / 斜体 / 变体 / 拉伸 / 字号）**，不是只有族列表——`fontweight='bold'` 的普通文字画的是 Bold 文件，身份必须是它（Codex #1671）。解析不出 / 文件读不了 → 键缺席，不编造。**路径不进 manifest** | `engine/manifest.py::face_sources` |
| 2 | 本机怎么由 sha256 找到文件 | Flask / 合成进程里的 `FontLocator`：持久化索引 `data_dir()/font-locator.json`（路径 → size / mtime_ns / sha256，只存本机）；查找 = 查索引并核 stat → 扫「线索目录」→ 扫系统字体目录（macOS `/System/Library/Fonts`、`…/Supplemental`、`/Library/Fonts`、`~/Library/Fonts`；Windows `%WINDIR%\Fonts`、`%LOCALAPPDATA%\Microsoft\Windows\Fonts`；Linux `/usr/share/fonts`、`/usr/local/share/fonts`、`~/.fonts`、`~/.local/share/fonts`）。**只按 sha256 认**：族名 / 文件名从不参与，同名不同字节是另一张脸。**线索目录**由 `app._prepare_source_fonts()` 从项目解释器与内置 runtime 的**路径**推出 `site-packages/matplotlib/mpl-data/fonts/ttf`（只算路径，不 import、不执行、不起进程）——DejaVu 不在任何系统目录里，这是最常见的原图字体。每次真正使用前**重算 sha256 并核对**（陈旧索引 / 被换过的文件都在这里被拦） | `rendercore/userfonts.py` |
| 3 | 与建议「由 worker 报告填充」的偏离 | 不让 worker 报路径：worker 响应有三条解析路径（Python 池 `_parse_line`、workerd、native 会话），在每条上剥路径都是一个漏就泄漏的设计；而路径一旦进了 manifest 就会流向前端、诊断包。改为 Flask 侧自己从**解释器路径**推线索目录。代价：脚本 `fname=` 指到项目目录里的字体、`addfont` 来的非标准位置的字体，首版找不到（报 `not_found`，名字保留、回退）。后续若要补，走「手动指定字体目录」设置项，不走 worker 报路径 | `app.py::_prepare_source_fonts` |
| 4 | 安全与许可 | 只读；单文件 ≤ 192 MiB；只认 TrueType / OpenType / TTC 结构（`.ttc` 现在能按面序号读，`fonts._tables` 支持字体集）；变量字体（`fvar` / `CFF2`）、彩色（`COLR` / `SVG ` / `CBDT` / `sbix`）、位图字体、非 CID 的 CFF、cmap 读不出（只认 format 4 / 12）→ `unsupported_format`，与批准字体同一条格式边界。**OS/2 `fsType` 位 1（Restricted License）、位 9（只允许位图嵌入）、位 8（禁止子集，而我们只嵌子集）→ 不嵌入**，原因 `embedding_restricted`，按缺失处理；位 2（Preview & Print）与位 3（Editable）允许（PDF 里嵌入的是只读子集）。子集嵌入与批准字体用同一个 `HbFace.subset`（`recalcTimestamp=False`，ToUnicode / ActualText 同一套），与 matplotlib 自己嵌图内字体的做法一致 | `rendercore/userfonts.py::admit` / `embedding_refusal` |
| 5 | 缺失 / 不可用时 | 这一个对象回退成 `fontFamily` 对应的批准脸（CJK 回批准的 Noto），`CompiledPage.problems` 记一条 `source_font_unavailable`（`reason` 取自闭集 `sourcefont.REASONS`：`not_found / embedding_restricted / too_large / unsupported_format / unreadable / invalid`），导出警告里是 `<对象id>: source_font_unavailable <名字> (<原因>)`。**文档对象一个字节不改**（测试钉着）；不报错、不中断导出 | `rendercore/plan.py::_user_faces` |
| 6 | 预览同源 | 前端不再「用 CSS 系统字体栈猜」：`lib/sourceFontCache.ts` 问 `POST /api/source-fonts/status`（判据与导出是**同一个函数** `HbFaceProvider.user_face`），状态为 `ok` 才向 `GET /api/source-fonts/<sha256>/<face>` 取字节（`.ttc` 取单脸 sfnt；禁止嵌入的脸不供），用 `FontFace` API 以由身份派生的族名 `tvsrc-<sha前16>-<面>` 注册，并按这张脸自己的粗斜声明 `weight` / `style`（浏览器不再合成第二次粗体）。字节只来自本机服务（会话认证之内、不走网络）。**预览字体栈 = `[正文脸, CJK 脸（`sourceFont.cjk`，同样的状态 + 字节端点与同一个缓存模块）, 批准族栈]`（`lib/sourceFontPreview.ts::previewStackOf`），两张脸各自独立回退，与导出 `_user_faces` 的 `user_primary` / `user_cjk` 同序同判据：CJK 脸不可用 = 汉字走批准族（导出回批准的 Noto）**。取字节失败 / 浏览器拒收 → 状态仍 `ok`、画面按 `fontFamily` 的批准栈显示，导出不受影响（导出读本机文件，不经浏览器） | `web/src/lib/sourceFontCache.ts`、`canvas/TextView.tsx`、`app.py::api_source_fonts_*` |
| 7 | `test_no_web_font_is_fetched_or_embedded` | **不删、不放宽原断言**（`@font-face` / 字体 CDN / woff 仍一个都不许）。判据的**主语**从「前端永远不出现字体」改成「前端加载字体字节的代码只有一处，字节只来自本机 `/api/source-fonts/`」：新增 `test_font_bytes_are_loaded_only_from_the_local_source_font_endpoint`（AST / 文本：`new FontFace` / `document.fonts.add` 只许出现在 `sourceFontCache.ts`；该文件无 http(s) / data: 来源；取字节函数只拼 `/api/source-fonts/`）。`test_canvas_faces_all_come_from_the_allowlist_registry` 同理：除 `fonts.py` 外多放行 `userfonts.py`，并新增 `test_user_fonts_enter_rendercore_only_through_a_source_font_object` 钉住入口唯一（`userfonts.admit` 只被 `hbshaper.user_face` 调；`user_face` 在 rendercore 里只被 `plan` 调） | `tests/test_font_provenance.py` |
| 8 | 浏览器 playground（/try，Pyodide） | **首版不支持**：没有本机文件、没有这组端点（404 / 网络错误）→ 状态 `unsupported`，画面与导出都按 `fontFamily` 的批准族，名字保留，属性页与预检各有一条提示（`textSourceFontUnsupported`）。不为它在 Pyodide 里塞字体字节 | `lib/sourceFontCache.ts` |
| 9 | 字形覆盖 | 用户脸缺字 → 走既有分层：`glyphplan.layer_of` 的 primary（用户脸）→ cjk（原图的 CJK 脸 `cjk_src`，缺席则批准的 Noto Sans SC）→ missing；缺字照旧写 .notdef、问题码 `glyph_missing`，不暗中换脸。**前端覆盖表（`canvas_coverage.json`）只对批准集合生成，不适用于用户脸**：有 `source_font` 且本机可用时，预检的两个求值器都跳过 `glyph-missing` / `glyph-substituted` 的**预测**（`source_font_in_use` 事实），以导出时后端的 `glyph_missing` 为准；不为用户脸生成/下发覆盖表（可能几万码位，且是机器相关的事实） | `engine/preflight.py`、`web/src/lib/preflight.ts` |
| 10 | 行内粗斜变体 | 用户字体是**单个文件**；`\textbf{}` / `\textit{}` 要的变体不是原图那张脸时，用 `fontFamily` 的批准粗/斜体，**不按名字猜同族兄弟文件、不造假粗体**。即：原图字体只负责这个对象的「基础样式」 | `typography.faces_for` |
| 11 | 文档字段 | `TextObject.sourceFont?: {family, sha256, face, bold?, italic?, cjk?: {family, sha256, face}}`，**可选**，缺席 = 现状逐字不变；`fontFamily` 继续当回退族；导出载荷 `source_font` 缺席时不发。`family` 只用于显示，**不参与认脸**。属性页字体行显示 `sourceFont.family`（选项最前）；本机不可用时复用既有的 `options_unavailable` 机制（名字保留 + 「字体缺失」标记与 warning，不换掉再改文档）；用户在属性页**改字体族 / 粗细 / 斜体就清掉它**（粗斜是这张脸自己的一部分，改了就不再是原图那张脸）；选它自己的名字 = 不改；「恢复」= 清掉；**「恢复」与写入走同一套失效规则**（`lib/typography.ts::dropSourceFontIfFaceChanged`，`writeCanvasText` 与 `resetCanvasText` 共用）：恢复斜体后斜体的原图字体跟着作废，不再继续用着斜体文件。形状校验前后端各一份、共用向量 `tests/golden/source_font_vectors.json` | `types/document.ts`、`lib/sourceFont.ts`、`rendercore/sourcefont.py` |
| 12 | 问题面板 | 新增预检项 `source-font-unavailable`（等级默认 warn，登记进三份 severity 表；`fix: none`——修法是装回字体或自己改族，没有安全的自动修）。本机状态是机器相关的事实，由 spec 的 `source_font_missing` 带进两个求值器（与 `math_errors` 同一模式），不进文档。文案按原因分七条 key（`textSourceFont*`，中英） | `engine/preflight.py`、`web/src/lib/preflight.ts`、`validation.ts`、`errors.json` |
| 13 | 隐私 | 路径只在本机的定位索引与内存里；manifest / 文档 / 载荷 / 问题 / 警告 / 端点响应里**没有路径**（`test_problems_and_warnings_never_carry_a_local_path`、`test_status_reports_ok_missing_and_restricted_without_any_path`、`test_the_worker_side_cap_and_the_privacy_shape…`）。`font-locator.json` 在 `data_dir()` 根下，诊断包只收既有的日志与白名单事实、不收它；`UserFontUnavailable.detail`（含路径）只给调用栈、不写日志、不进问题。遥测：不新增事件，不新增字段；字体名 / sha256 不进遥测。manifest 里的 `face_src.family` 与既有的 `face`（族名）同一敏感度，进诊断包时走既有脱敏 | — |

## 不做 / 边界

* **不做「复制为画布文字标注」**：另一个 PR。它的取值入口是 `sourceFontFromElement(el)`（`face_src` / `cjk_src` → `sourceFont`）。
* 写回脚本的标注（`writeBackAnnotations`）不带 `sourceFont`：写回脚本的是 matplotlib 文字，字体由脚本自己的族链决定，本 PR 不动。
* 项目目录里的字体文件（`fname=`）与非标准位置的字体：首版找不到（见裁决 3）。
* 缩略图（`CanvasThumb`）仍用批准族栈，不加载用户字体（缩略图不是预览的权威）。
* 非 CID 的 CFF（多数 `.otf`，如 Source Han 之外的许多 OTF）与变量字体不支持（`unsupported_format`）；这是写入器的既有边界（`pdfwriter` 只有 TrueType→CIDFontType2 与 CFF CID→CIDFontType0 两条路），要救需要另行扩写入器，不在本 PR。
* 许可判断只看 OS/2 `fsType` 位，不读字体许可证文本，也不替用户做法律判断；位 2 / 位 3 允许嵌入是 OpenType 规范的语义。

## 验证与 Windows Lab 清单

macOS 已测：DejaVu（mpl-data）嵌入与按字体程序比对、`.ttc` 面序号、fsType 全部档位的反证、同名异字节、陈旧索引、超限、变量字体、端点三类响应、预检双侧向量。
**需要上 Windows Lab 的点**（macOS 上不生效）：`system_font_dirs()` 的 Windows 分支（`%WINDIR%\Fonts`、`%LOCALAPPDATA%\Microsoft\Windows\Fonts`，含非 ASCII 用户名）；`matplotlib_font_dirs()` 对 `Scripts\python.exe` / `Lib\site-packages` 的路径推导（含 MSIX 重定向的 `LOCALAPPDATA`）；系统 `.ttc`（`msyh.ttc` / `simsun.ttc` / `times.ttf`）的面序号与 sha256；系统字体文件被占用时只读打开不失败；`font-locator.json` 写在 MSIX 虚拟化的数据目录里仍能读回。

**用户字体按字节加载、不持有文件**：`admit` 用到那一刻读一次整份字节，大小上限、sha256、sfnt 解析、HarfBuzz（`hb.Blob(data)`）、fontTools（`BytesIO`，含子集化）都用这同一份字节，读完即关，进程不留指向用户字体文件的句柄或映射（Windows 上否则用户更新 / 卸载字体报「文件正在使用」，替换文件失败）；批准字体仍按路径加载。内存有界：用户脸最多 32 张、字节总量 384 MiB，超出按最久未用丢弃（当前这张保留，被丢的下次重新入场）。

## 反证（新增核心不变式测试提交前手工反证过一次，每条变异一次就红）

| 变异 | 红在 |
| --- | --- |
| `admit` 不在使用的那一刻读字节重算 sha256（`if digest != ref.sha256` 改成 `if False`） | `test_same_size_and_mtime_but_different_bytes_is_caught_at_the_point_of_use`（第一版只靠索引的 stat 去核，这条变异**活了下来**，才补了这条用例并把 `admit` 改成无视索引现读字节） |
| fsType 的 Restricted 位不查 | `test_a_face_that_forbids_embedding_is_not_embedded[2]` / `[6]`、`test_fs_type_policy_is_exactly_the_documented_bits`、`test_problems_and_warnings_never_carry_a_local_path` |
| 定位器不比 sha256（扫到的第一个字体文件就算） | `test_the_locator_never_matches_by_name` 与六条嵌入用例 |
| 失败时改写文档对象（`pop("source_font")`） | `test_a_missing_font_falls_back_to_the_approved_family_and_reports` |
| `HbFace` 对用户字体改回按路径加载（`lazy=True` 的文件句柄） | `test_embedding_a_users_face_leaves_no_open_handle_on_the_file`（macOS 上枚举 fd 即红；Linux 另查 maps；映射在 macOS 不可枚举，Windows 靠 `os.replace`） |
| `user_face` 在 `plan` 之外被调用 | `test_user_fonts_enter_rendercore_only_through_a_source_font_object` |
| manifest 取 `style_flags` 不先取 `.value`（matplotlib 3.11 的 `StyleFlags` 是 `enum.Flag`，`int()` 抛 TypeError） | `test_the_reported_identity_is_the_file_the_renderer_resolves_and_carries_no_path`；**这一条在完整回归里先于反证被 `test_invariants_engine.py::test_every_enum_option_can_actually_be_applied` 抓到**（采集失败拖垮了整份 manifest），所以现在 `face_sources` 的调用外面还有 `try/except`：身份是附加事实，采集失败只是少一个键 |
| 写入层改粗细时不清 `sourceFont` | `typography.test.ts`「换了族 / 粗细 / 斜体 = 换脸，身份清掉」 |
