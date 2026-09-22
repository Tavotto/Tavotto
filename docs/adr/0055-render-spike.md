# ADR 0055：U02 render_spike——PDFium 栅格 + pikepdf/fontTools/HarfBuzz 受限 emitter 的技术证明

日期：2026-09-20 · 状态：**Accepted（技术证明；不切默认后端）**
相关：[0053 U01 合同](0053-foundation-contracts-and-preparation.md)、[0033 科学文本与字形回退](0033-scientific-text-and-font-fallback.md)、
[0045 CJK 回退链](0045-cjk-font-fallback-chain.md)、[0056 runtime_spike](0056-runtime-spike.md)；
实施包 `docs/implementation/tavotto-foundation/`（`phases/U02_spikes.md`、`01_SCOPE_AND_DECISIONS.md` D03 / D04 / D07 / D15）。

## 裁决摘要

| 问题 | 裁决 | 证据 |
|---|---|---|
| 写入器：成熟写入器适配还是受限自有 emitter | **两者各取一半**：pikepdf（qpdf）做对象模型 / 序列化 / 外来页导入（`Page.as_form_xobject` + `copy_foreign`），fontTools 做子集，HarfBuzz 做 shaping；**内容流的操作符自己写**（`scripts/dev/u02_spikes/pdfwrite.py`），只会做本轮点名的几件事。**不做通用 parser**，也不用任何「画布级」PDF 库 | `evidence/u02/render/spike.pdf`（17 KB） |
| 栅格器 | pypdfium2 5.13.0（PDFium 153.0.7999.0），只在一个**串行的 render child 子进程**里跑 | `render_child.py`，`tests/test_foundation_u02_render_child.py` |
| 默认字体来源与许可 | **Liberation 2.1.5**（三族 × 四态，SIL OFL 1.1，与 base-14 对应的 Times / Helvetica / Courier 度量兼容）+ **Noto Sans SC Regular（Sans2.004 地区子集 OTF，SIL OFL 1.1）** 做唯一一张 CJK 脸；来源 URL 与每个文件的 sha256 钉在 `fonts.py`，**字体文件不进 git** | `fonts.py`、`report.json` 的 `inputs.fonts` |
| 字体程序 | TrueType（glyf）→ `CIDFontType2` + `FontFile2`，code = 子集 GID；CFF CID-keyed → `CIDFontType0` + `FontFile3/CIDFontType0C`，code = 原 CID；变量 / 彩色 / CFF2 / 非 CID 的 CFF / 位图字体**显式拒绝** | `pdfwrite.FontFace` |
| 文字层 | HarfBuzz 的 cluster → ToUnicode（一个 glyph 对应它覆盖的**原文**，组合序列合成的一个字形回两个码位）；**不以 outline 代替**（PDFium 对象普查里 17 个 `FPDF_PAGEOBJ_TEXT`） | 三把独立读取器 + `tests/test_foundation_u02_render.py` |
| 整体 opacity | 透明组（`/Group /S /Transparency /I true`）+ ExtGState `ca`，组内 alpha 从 1 起算；导入页 opacity<1 时**整页包成组、仍是矢量**（旧后端这一档只能退位图） | 像素：组内重叠 (128,128,255) vs 逐对象 (128,64,191) |
| 导入非对称源页 | qpdf 取 TrimBox → CropBox → MediaBox 当 BBox，`page.pdf` 的 CropBox [15 10 285 170] 原样成为 form 的可见框；变换 / clip / 旋转在源空间 `cm` + `re W n` | 4 处导入、6 组像素采样、`inner_rect_bounds` 经 PDFium 报的 form 矩阵回算 |
| 目标 | 本机 macOS arm64 实测；Linux / Windows 由 `.github/workflows/foundation-u02-spikes.yml`（`workflow_dispatch` + 只在 spike 文件变动时的 `pull_request`，非 required、不进 Gate）提供，run 号见交接文件 | `evidence/u02/render/`、`evidence/u02/freeze/` |
| 默认切换 | **不切**。PyMuPDF 仍是默认后端；候选包只装在 spike venv；`pyproject.toml` 一字未动 | `git diff --stat` |

## 1. 选型：为什么是这一组

| 组件 | 版本（钉死） | 许可证 | 传递依赖（运行时） | 用途 |
|---|---|---|---|---|
| pypdfium2 | 5.13.0（PDFium 153.0.7999.0） | Apache-2.0 / BSD-3-Clause（PDFium 本身 BSD-3-Clause） | 无 | 栅格化（PNG/TIFF 的像素源）、独立读取（文字层、对象普查） |
| pikepdf | 10.13.0.post1（libqpdf 12.3.2） | MPL-2.0（qpdf Apache-2.0） | **Pillow ≥10、lxml ≥4.8、packaging** | 对象模型 / 序列化 / 外来页 → Form XObject |
| fontTools | 4.65.0 | MIT | 无 | 读字体表、子集（glyf 与 CFF 两条路） |
| uharfbuzz | 0.56.1（HarfBuzz 14.4.0） | Apache-2.0（HarfBuzz MIT-old） | 无 | shaping：glyph / cluster / advance / offset |
| pdfminer.six（只读） | 20251107 | MIT | charset-normalizer、cryptography | 独立文字层读取器 #2 |
| pypdf（只读） | 6.7.5 → 6.16.1（2026-09-22 Dependabot #484 安全升级；报告重生成，52/52 与产物字节不变） | BSD-3-Clause | 无 | 独立字体结构读取器 |
| PyInstaller（只构建） | 6.19.0 | GPL-2.0-or-later + 例外 | — | 最小候选 freeze |

宏观取舍：

* **不用 PyMuPDF 之外的另一个「画布级」PDF 库**（reportlab / fpdf2 / borb）：它们要么自己排字、不走 HarfBuzz（RC-027 / RC-030 要求度量与落笔同一份 shaped plan），要么许可证是 LGPL / AGPL，要么导入外来页仍要再拉一个库。
* **pikepdf 的代价要写明**：它硬依赖 **Pillow**（`export-pipeline.md` 说父进程「别为 TIFF 引进 Pillow」——本轮 TIFF 编码器仍是纯标准库的 `tiffwrite.py`，Pillow 只是 pikepdf 的传递依赖，不是我们调用的东西）与 **lxml**（XMP，本轮不用）。wheel 体积：pikepdf 1.8 MB + Pillow 4.8 MB + lxml 8.6 MB + packaging 0.1 MB；pypdfium2 3.5 MB；fontTools 3.1 MB；uharfbuzz 3.3 MB。U07 若嫌 lxml / Pillow 重，替代路线是 **pypdf 做对象模型**（纯 Python、BSD，无传递依赖）——它写对象 / 流 / xref 也够用，但外来页 → Form XObject 要自己拼（qpdf 帮我们处理了 /Rotate、页盒选择与资源搬运）。本 ADR 不替 U07 定这一条，只把两条路的代价摆出来。
* **MPL-2.0（pikepdf）与 §3.2**：可执行形态分发时要告知接收者如何取得 Source Code Form——与 `COMMERCIALIZATION_DEPENDENCY_AUDIT.md` 里 5 个 MPL crate 同一类义务，落在 #182 的 NOTICE 生成里；不是阻塞项。

## 2. 实测：输入 / 产物 / 退出码（macOS 14+ arm64，本机 2026-09-20）

命令（全部在 spike venv 里；主仓库 `.venv` 零改动）：

```sh
S=<scratch>; python -m venv $S/spike-venv && $S/spike-venv/bin/pip install -r scripts/dev/u02_spikes/requirements.txt
PYTHONPATH=scripts:src $S/spike-venv/bin/python -m dev.u02_spikes.fonts --dest $S/fonts                       # 退出 0
PYTHONPATH=scripts:src $S/spike-venv/bin/python -m dev.u02_spikes.render_spike --fonts $S/fonts \
    --out docs/implementation/tavotto-foundation/evidence/u02/render                                        # 退出 0，ALL OK 52/52
PYTHONPATH=src $S/spike-venv/bin/python -m pytest tests/test_foundation_u02_render.py tests/test_foundation_u02_render_child.py  # 全过，真 child 用例不 skip
PYTHONPATH=scripts:src $S/spike-venv/bin/python -m dev.u02_spikes.freeze_spike --fonts $S/fonts \
    --pdf docs/implementation/tavotto-foundation/evidence/u02/render/spike.pdf \
    --out docs/implementation/tavotto-foundation/evidence/u02/freeze                                        # 退出 0，7/7
```

输入：`tests/fixtures/foundation/pdf_png_assets/page.pdf`（U00 夹具，MediaBox 300×200、CropBox [15 10 285 170]）、
手写的 `evidence/u02/render/truth.json`（页面 400×320 pt；4 处导入 A/B/C/F；透明组 D 与逐对象对照 E；4 行文字）。

产物（进 git）：`spike.pdf` 17 983 B、`spike_pdfium.png` 800×640 RGBA 30 KB、`report.json`（版本 / hash / 写入事实 / 52 条核对）。
**同一输入两次运行字节相同**（fontTools 子集要 `recalcTimestamp=False`，否则 `head.modified` 让字体程序每次不同）。

### 2.1 三把独立读取器各说了什么

* **PDFium 栅格**（scale 2）：13 个采样点全部在 ±6/255 内命中手算颜色；其中透明组 D 的重叠区 (128,128,255) 与逐对象 E 的 (128,64,191) **不同**（这一条不成立时「透明组」什么都没证明）；导入页 C（整页 50%）的内部重叠区 (191,128,191) 与逐对象 alpha 会给的 (159,96,191) 不同。文字四行在基线上 0.7 em 内有墨。
* **PDFium 对象普查**：`{'form': 6, 'text': 17, 'path': 16}`；导入页里源页蓝矩形的包围盒经 PDFium 自己报的 form 矩阵回算，与真值预测的 [32.5,175,82.5,205]（A）/ [201.25,187.5,216.25,212.5]（B，旋转 90°）逐个 <0.6 pt。
* **PDFium 文字层** `get_text_range()`：`Tavotto U02: efficient flow, café — Δλ = 532 nm, μ ≈ 1.5×10⁵` / `E = mc2` + `, H2O, kBT` / `图一：温度 T (K) 随时间变化` / `Sans Italic αβγ — 中文回退不换族`；导入的四页各带一份 `U00 fixture: y = 3x + 1`——**外来页的文字层随 Form XObject 一起保留**。
* **pdfminer.six** `extract_text()`：同上四行，且 `E = mc2, H2O, kBT` 在一行（PDFium 把上标断成两段，pdfminer 没有）。
* **pypdf 字体结构**：4 个 Type0 / Identity-H / 带 ToUnicode / 六字母子集前缀；`LiberationSans-Italic`、`LiberationSerif`、`LiberationSerif-Bold` → CIDFontType2 + FontFile2 + `/CIDToGIDMap /Identity`；`NotoSansSC-Regular` → CIDFontType0 + FontFile3 `/Subtype /CIDFontType0C`。

### 2.2 子集与文字层的数字

| 脸 | 源 glyph 数 | 子集 glyph 数 | 用到 | 字体程序字节 |
|---|---|---|---|---|
| LiberationSerif-Regular | 2 602 | 38 | 37 | 4 276 |
| LiberationSerif-Bold | 2 602 | 6 | 5 | 1 304 |
| LiberationSans-Italic | 2 622 | 15 | 14 | 2 248 |
| NotoSansSC-Regular（CFF CID） | 31 036 | 19 | 18 | 2 998 |

`café`（分解形式）经 HarfBuzz 合成为一个 `eacute` 字形：那个 code 的 ToUnicode 是 `e` + U+0301 两个码位（原文），文字层抽回的也是分解形式。
**Liberation 2.1.5 没有 `liga`**（GSUB 只有 ccmp / dlig / subs / sups）：`efficient` 的 fi **不连字**——所以旧字体名之外还要记一条：换成 Liberation 后拉丁文字层里不会出现 fi/fl 连字。

### 2.3 render child（`render_child.py`）

* 行分隔 JSON 协议，op 闭集 `ping / render / close`；父进程一把锁串行化，child 单线程。
* **超时**：deadline 到 → kill → `wait()` reap（`last_exit` 记退出码）→ 本次 `render_child_timeout` → 下一次自动重启。
* **崩溃 / 外力杀死**：读线程见 EOF → `render_child_died` → 下一次重启；两次请求之间被杀的 child 在下一次请求前就被 `poll()` 发现并重启（最多失败一次）。
* **像素预算**：父侧按 `page_size_pt` 精确判、child 打开页面后再判一次（父侧被绕过时 child 仍挡）；`RenderChildError` 码闭集 6 个。
* **内存上限**：child 用 `RLIMIT_AS`——**macOS 内核不强制**，实测 `setrlimit` 在 macOS 上要么报 "current limit exceeds maximum limit"、要么设了不生效；**像素预算是 macOS 上唯一有效的护栏**。Linux 上的结论看 §7 的 CI run。
* 真 child 用例（spike venv 里）：渲染 evidence PDF 到 400×320、像素预算 child 侧拒绝、坏 PDF 报 `render_failed` 且 child 不死、0.1 ms 超时 → kill → reap → 下一次恢复。
* 假 child 用例（任何机器）：8 线程 × 5 请求无串包且 seq 严格 1..40；超时 / 崩溃 / 外杀 / 父侧预算 / close / **256 KiB 的 chatty 输出被并发排空**（这一条是 `tests/test_source_hygiene.py` 对 scripts/ 里 `stdout=PIPE` 唯一例外的动态前提）。

### 2.4 最小候选 freeze（`freeze_spike.py`，PyInstaller 6.19.0 onedir）

macOS arm64：构建 12.7 s，产物 120 个文件 69 MB；`libpdfium.dylib` 与 13 个字体文件 + 2 份 LICENSE 全部在 `_internal/`；冻结的 exe 在**干净环境**（无 PYTHONPATH、无 spike venv）里以 `--render-child` 再起自己当 child，渲染 evidence PDF 到 400×320（3.6 ms）。7/7。这不是 `packaging/tavotto.spec`（一字未改），只回答「候选 native 库 + 字体数据 + child 自起」过不过 PyInstaller。

## 3. 反证（变异一次就红）

写入侧的六条变异各重生成一次 evidence 再跑 spike 自己的读取器与 `tests/test_foundation_u02_render.py`：

| 变异 | spike 读取器 | 纯标准库用例 |
|---|---|---|
| ToUnicode 映射全空 | 退出 1（文字层三把尺子全红） | 4 行文字解码 + 组合序列 + cmap 交叉核对红 |
| 子集后继续引用旧 GID（RC-034） | 退出 1（ink 消失） | `test_truetype_codes_are_the_subset_gids_the_embedded_cmap_agrees_with` 红（嵌入子集自己的 cmap 说 U → GID 与 code 不一致） |
| 透明组不带 `/Group` | 退出 1（C 与 D 的像素） | 矩阵 / 组 / 像素三条红 |
| 旋转方向反了 | **第一版绿**——采样点经写入侧记的矩阵映射，矩阵错了采样点跟着错，颜色照样对（自证）；改成读取侧**独立重算**矩阵后退出 1 | B 的矩阵与像素红（用例一开始就是独立公式） |
| 有 crop 不裁剪 | 退出 1（F 的 crop 外采样点有墨） | F 红 |
| 字体程序为空 | 退出 1（pypdf 结构） | 收集期 zlib 错 → 红 |

render child 客户端的四条变异：去锁（串行用例红）、超时不 kill（timeout 用例红）、父侧不判预算（预算用例红）、kill 后不 `wait()`（`last_exit` 断言红）；「等子进程退出才读」（hygiene 判据说的死锁形状）→ chatty 用例超时红。

## 4. 字体：许可与义务、以及会变的旧基线（D07）

**许可**：Liberation 2.1.5 与 Noto Sans CJK 都是 **SIL OFL 1.1**（字体文件 `name` 表 ID 13 与随包 LICENSE 全文一致）。义务（`fonts.OFL_OBLIGATIONS`）：随发行物附许可证全文与版权声明；嵌入文档（含子集）被 OFL §1 明确允许；不改字体、不用 Reserved Font Name（Liberation 的 RFN 是 Arimo / Tinos / Cousine，Noto 的是 "Noto"，我们都不碰）；不单独出售字体。**分发方式**：本阶段字体不进 git（`test_font_provenance.py` 继续成立），由 `fonts.py` 按 sha256 下载；U06 决定进包时的落点（PyInstaller datas 已验证可行）并把两份 LICENSE 写进 #182 的 NOTICE 链。

**会变的旧字体名 / 布局基线**（换脸不是「实现细节全部照抄」，按 03 §6 一次批准迁移）：

| 现有断言 | 现值 | 迁移后 |
|---|---|---|
| `tests/test_typography_families.py:49-53,69,106` | base-14 名 `Times-Roman` / `Helvetica` / `Helvetica-Bold` / `Courier-Oblique` | `LiberationSerif` / `LiberationSans` / `LiberationSans-Bold` / `LiberationMono-Italic`（子集前缀另算） |
| `tests/test_glyph_plan.py:151-156,276` | `pdf_fonts()` 回 base-14 名 | Liberation PostScript 名 |
| `tests/test_compose_text.py`（`mm2pt`、asc/desc 反算） | base-14 的 AFM 上升 / 下降 | Liberation 的 `OS/2` sTypoAscender/Descender（Times ↔ Liberation Serif 度量兼容，**advance 相同、asc/desc 不同**） |
| `pdfbackend/canvas_coverage.json` + `tests/golden/glyph_plan_vectors.json` | PyMuPDF base-14 / Droid Sans Fallback 的覆盖 | 由 Liberation + Noto Sans SC 重生成（`gen_canvas_coverage.py --check` 先红再 `--write`，diff 进 PR） |
| `test_font_provenance.py` 的「只允许 PyMuPDF / matplotlib / 用户字体」 | 三档 | 加第四档：清单里 sha256 钉住的 OFL 字体 |
| CJK 脸 | `china-ss` = Droid Sans Fallback（Apache-2.0） | Noto Sans SC（OFL）；「换族不换 CJK」语义不变 |
| 位置 / 内容 / 框尺寸 | — | **保留**（D07）；换行变化逐例审查——Liberation 与对应 base-14 同 advance，预期换行不变，但要量不要信 |

## 5. 明确没做 / 边界

* 图像（PNG 位图放置）、Arrow / Shape 路径、画布毫米 / 顶原点坐标合同、多页——U06 / U07。
* 合成上下标的文本层是 `E = mc2`（与旧后端一致）；`ActualText` 归 U06（RC-036）。
* HarfBuzz 的 `y_offset` 没写进 TJ（只处理 x_offset / x_advance）；竖排、bidi、非 CID 的 CFF、变量 / 彩色字体一律显式拒绝，不是「支持」。
* 一个 cluster 出多个 glyph（组合标记未合成时）只给第一个 glyph 写 ToUnicode——文本层不重复吐字，但那一段的 ActualText 语义留待 U06。
* Windows / Linux 目标：见 §7；本机只有 macOS arm64。
* 内存上限在 macOS 上没有有效机制（§2.3）。

## 6. 后果

* 加了：`scripts/dev/u02_spikes/`（`hashcheck` / `fonts` / `pdfwrite` / `render_spike` / `render_child` / `freeze_spike` / `requirements.txt`）、`tests/test_foundation_u02_render.py`、`tests/test_foundation_u02_render_child.py`、`evidence/u02/render/`、`evidence/u02/freeze/`、`.github/workflows/foundation-u02-spikes.yml`（dispatch + spike 文件 paths 过滤的 pull_request；非 required）。
* 没加：新的产品 import 边（`src/tavotto` 不 import spike）、新的运行时依赖、新的 required job、默认后端切换。
* `tests/test_source_hygiene.py` 的 `stdout=PIPE` 判据多了**一个文件**的例外（`render_child.py`），前提由 chatty 用例动态看住；搬进 `src/` 时删掉那条例外。
* U06 拿走：字体清单与 hash、两条字体程序路径、ToUnicode 的 cluster 写法、D07 迁移表；U07 拿走：透明组 / 导入页 / clip 的 emitter 形状、render child 的四条路径与 freeze 结论。

## 7. 其它目标（CI dispatch）

`foundation-u02-spikes.yml` 在 ubuntu-latest / windows-latest / macos-latest 上各跑一遍全套（fonts → render_spike → U02 用例 → freeze_spike → runtime_spike），产物为 `u02-evidence-<os>` 工件。`gh workflow run` 只认默认分支上已登记的 workflow，所以合入前靠 `pull_request`（paths 过滤）触发。run 35507598899 三腿全过；逐腿数字在 `docs/implementation/tavotto-foundation/handoffs/U02_spikes.md`「其它目标」表。要点：`spike.pdf` 在三个平台**逐字节相同**（写入侧跨平台可复现）；PDFium 的 PNG **跨平台不同、同平台可复现**（U07 的像素门要按平台分基线）；`libpdfium.{so,dll,dylib}` 三平台都过 PyInstaller 且冻结 exe 自起 child 成功；`RLIMIT_AS` Linux = set、Windows = unsupported、macOS = 内核不强制。
