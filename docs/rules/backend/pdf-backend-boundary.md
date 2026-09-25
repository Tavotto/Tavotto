# PDF 后端边界（许可证相关，勿破坏）

> 原文出自 `src/tavotto/AGENTS.md`「PDF 后端边界（许可证相关，勿破坏）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **`src/tavotto/pdfbackend/__init__.py` 是与实现无关的契约层**（probe_asset / pdf_fonts /
  render_preview_png / text_width / text_plan / missing_glyphs / coverage_ranges / compare_png /
  compose + original_* / annotate_asset + mm2pt / hex2rgb + 四个常量，19 项）。`app.py` /
  `engine/artifactcheck.py` / `engine/tutorial.py` / MCP bridge / 两个生成脚本只认这些名字，
  不认识任何实现模块。**唯一的实现是 `rendercore/facade.py`**（pikepdf / HarfBuzz / PDFium render
  child + 批准字体，ADR 0067 / 0072）：契约函数显式委托（`_impl()`），四个常量经 PEP 562。
- **PyMuPDF 已退役（U10，ADR 0072，2026-09-22）**：旧实现模块 `pymupdf_backend`（`pdfbackend/` 下）已删除，
  `pymupdf` 不在 `dependencies` / `requirements.txt` / wheel 的运行时 Requires-Dist / 冻结产物里
  （只剩 `legacy-pymupdf` extra 给测试的独立读取器与 `scripts/dev/` 的历史差分用，D15）。
  看护是 `scripts/ci/retirement_scan.py` 的五把尺子——应用源码 AST（`SOURCE_ROOTS`，含
  `importlib.import_module` / `__import__` 字面量）、发行版声明的运行时依赖闭包（递归、无 extra；
  同一 site-packages 里的读取器不算）、干净新进程（`-I`）装 meta_path 阻断器后跑主要路径（契约层
  14 条 + doctor + MCP bridge；阻断器先在标准库模块上证明会咬）、产物 native 名单（零 mupdf、PDFium +
  qpdf 在、字体 13 张）+ wheel METADATA、SBOM——`tests/test_retirement_scan.py` 随 pytest 跑前三把，
  ci.yml 的 package / windows-exe-smoke / macos-app-smoke 各扫自己的产物。**主语是应用 / 发行 /
  runtime 闭包，不是硬盘**：`tests/**` 的读取器、`scripts/dev/**`、维护者资产脚本
  （`build_dmg_background.py` 那几份）、用户科学脚本自己的 `import fitz`、git 历史与文档字样都不是残留，
  扫描器头部逐类写明（`--selftest` 有正负例）。
- **选择开关只有一处**：`TAVOTTO_RENDER_BACKEND`（`pdfbackend.selected()` / `BACKEND_DEFAULT` /
  `BACKENDS` 闭集，今天只有 `rendercore`）。**选定即定、不静默回退**（06 §1）：实现被选中而它的包 /
  批准字体不在，`CandidatePackagesMissing` / `FontsUnavailable` 原样抛出，绝不换成别的库；写着 `pymupdf`
  的旧配置当场 `BackendSelectionError(backend_retired)`（`app.py` 的漏斗转成 500 + 稳定 code，两种语言
  文案），不认识的取值 `backend_unknown`——不猜不退默认。开关的形状保留是为了下一次换实现仍只改这一处。
  层规则：新核心零边进 `pdfbackend/`（契约层认识实现是反方向）；`pdfbackend_impl` 层随旧实现删除
  （`tests/support/importgraph.py`）。看护 `tests/test_rendercore_facade.py`（开关 + 对退役前冻结的
  `tests/fixtures/legacy_pymupdf/oracle.json` 的返回值对拍）、`tests/test_rendercore_model.py`。
  **按名字装载的两个后果**（2026-09-22，#476 冒烟腿 + e2e）：① PyInstaller 静态分析看不见 `importlib`
  这条边，`packaging/tavotto.spec` 的 hiddenimports 从契约层 `_IMPL_MODULES` 铺进去、不抄第二份，退役模块的
  hidden import 随模块一起消失（`tests/test_runtime_build.py::test_spec_ships_every_backend_the_contract_layer_can_select`）；
  ② 装载是惰性的，`app.main()` 起服务前 `pdfbackend.warm()` 一次——第一次 probe 不再多付 import 的延迟，选了
  退役 / 不认识 / 装不上的后端在启动时就报、不退默认（`warm` 不在 `__all__`）。
- **旧行为的参照只在批准资产里**（06 §3）：`tests/fixtures/legacy_pymupdf/`（退役前一提交上旧后端跑出的
  `oracle.json` / `preview_300.png` / `calibration/<case>.pdf`）与 `evidence/u10/*.pymupdf.json`
  （旧覆盖表 / 旧向量）。需要「旧实现当年怎么做」时读它们，不重新 import 旧库；重生成只能在装了
  `tavotto[legacy-pymupdf]` 的环境里回到那个提交跑（正常开发不需要）。
- **字形归属计划（ADR 0033）**：一个字符由哪张脸画出来，只有
  `tavotto/glyphplan.py` 一份判据（四层 primary/cjk/fallback/missing，顺序不可
  交换）。落笔、量宽、预检、前端预览读同一份计划。`ord(ch) > 0x2E80` 只保留为
  **换行单元**的判据，**不再当覆盖判据用**——它量的是码位，不是「这张脸画不
  画得出这个字」。浏览器没有字体引擎，读的是生成物
  `pdfbackend/canvas_coverage.json`（`scripts/gen_canvas_coverage.py --check`
  看住它与真字体一致）。**git 里不含任何字体二进制**；发行物（wheel / 桌面包）只带 allowlist 按 sha256
  钉住的 13 张 OFL 脸，构建时由 `scripts/fetch_fonts.py` 取进包里（ADR 0060 / 0072）。看护
  `tests/test_font_provenance.py`（git 零字体 + 包内恰好是 allowlist）与 `tests/test_rendercore_fonts.py`（每条打包步骤之前取字体）。
- **本机字体族随 manifest 下发（2026-09-13，用户反馈「支持的字体太少」）**：
  `manifest.installed_font_families()` 问的是 matplotlib 自己的 `fontManager.ttflist`
  （它认得的就是渲染时解析得到的，所以列出来的每一个都画得出来；AFM 不列，`.` 开头的
  macOS 内部字体不列，名字含 `?` 的——FreeType 读不出 name 表、实测本机 20 个只有
  中文名的字体全读成 `????`——不列），进程内只算一次，放在 manifest **顶层**
  `font_families`，整份只发一次：按元素塞进 `fontfamily.options` 会让 88 个文字元素的
  manifest 多出半兆。`options` 仍只有首选项（`_family_options`：三个通用族 + 装了的
  具名候选 + 脚本自己那个），前端在 `lib/typography.withMachineFamilies` 一处并表。
  同族两条修正：`font_installed` 用**列表形式**问 `findfont`（`FontProperties(family="A-B")`
  只给 family 一个参数时会被当成 fontconfig 模式解析，连字符当场 ParseException）；
  `options_unavailable` 只在真画不出时才发，不再把「不在首选项里」当「没装」。
  看护 `tests/test_font_family_options.py`。
- **色图字段的两条只读事实（2026-09-13，用户反馈「自定义色块显示成 from_list」）**：
  `ListedColormap([...])` 的名字是 matplotlib 给的默认词（3.10 叫 `from_list`，
  3.11.2 起叫 `unnamed`），不在注册表里，`set_cmap(name)` 当场 ValueError——它
  **不是一个能写进 override 的取值**。**「自定义」的判据是对象不是名字**
  （`_registered_colormap`：名字查得到、且 `matplotlib.colormaps[name] == cm`——
  `Colormap.__eq__` 比整张查找表；注册表按名取出的是副本，`is` 恒假）：钉任何一个
  默认名字面量都只在一档 matplotlib 上对，而 `ListedColormap([...], name="viridis")`
  / `get_cmap("viridis", 5)` 的名字在注册表里、写回去却是另一张图，也算自定义；
  用户 `register` 过的按注册表算（可写）。`value` / 事实里的 `name` 原样透传
  matplotlib 的词，前端只拿它对事实、当可达名，**不拿它判自定义**。
  `_cmap_field` 是 `cmap` enum 的唯一构造处（Collection / AxesImage / 色条共用）：
  `options` 只放写得进去的名字（`_cmap_options`：注册过但不在 `CMAPS` 白名单里的留着，
  没注册的不放；自定义色图顶着注册名时那个名字仍在表里，选它 = 换成真正的那张）；
  自定义、或名字不在白名单里时发 `cmap_current`（`_cmap_needs_facts`；`_cmap_facts`：
  `custom` / `stops` / `discrete`——格数 ≤ 32 的 ListedColormap 逐格给色、其余九点
  采样，与前端离线表同一口径）；换走之后发 `cmap_original`（同一套事实 + `name`，
  发不发问同一条 `_cmap_needs_facts`；判据与 `_marker_original` 同一条：
  `state.applied` 里有，原值取 `state.originals`），色条 ↔ mappable 别名组里任一
  gid 上有 override 都算（`_cmap_alias_gids`）。前端据此显示「自定义」、画真实
  渐变条、列一格「脚本原样」（选它 = 清 override）；自定义与选项表里的一格同名时
  「自定义」那格选中、同名选项不选中。看护 `tests/test_cmap_facts.py`。
- **图内中文的回退链（ADR 0045）**：脚本跑完、采 baseline 之前
  （`figsession.instrument_all()`）给每段图内文字的族列表接上 DejaVu Sans + 本机
  探测到的中日韩脸（`overrides.cjk_fallback_tail()`，候选按平台分组、只有装了的
  才进链）。**尾巴必须在 `font.family` 列表里**，塞进 `font.sans-serif` 不是回退链
  （通用族只解析出一个文件）。用户 / 脚本设的族仍是 `get_fontfamily()[0]`；汉字由
  尾巴画出**不算**「换了脸」，manifest 报 `cjk_family`，`cjk-fallback-missing`
  的主语是它而不是正文族名。`TAVOTTO_CJK_FALLBACK=0` 关掉尾巴——测试用它造
  「没有中文字体」的世界（`test_glyph_coverage_figure.py`），不是产品设置。
  看护 `tests/test_cjk_figure_text.py`。
- 为什么在意：PDF 库是可替换的实现细节，收敛成契约层之后换后端只需新写一个实现模块，
  上层零改动——U10 就是这么把 PyMuPDF 换成 RenderCore 的（`app.py` 一行契约调用没改）。
  **别在 app.py 或别处直接 import pikepdf / pypdfium2 / uharfbuzz**——那会把这条边界废掉；
  它们只在 `rendercore/` 的 native 适配层里出现（`tests/test_rendercore_model.py`）。
  许可证说明见 `docs/legal/LICENSING.md`。
- **图内元素的命中判据跟渲染器走，不跟直觉走**（`web/src/lib/pathGeom.ts`）：
  填充用 **nonzero** 缠绕数（实测 matplotlib 3.10.8 + Agg：同向嵌套的中心
  像素是实心的，反向才是洞；even-odd 会让点在填了色的像素上选不中），
  填充路径没有 CLOSEPOLY 时按**隐式闭合**处理，框选**先把选择框裁进 clip**
  再比（否则只与不可见的延长线相交也算命中），命中容差取「可用性容差」与
  **描边半宽**的大者（`stroke_pt` 由 `engine/pathgeom.py` 随几何下发，
  前端不推算）。共线线段必须再比一维区间，否则框选会收走老远的水平/垂直线。
- 面板的项目路径解析与引擎重渲染留在 app 层的 `_resolve_panel_source` 回调里，
  后端只管画。几何公式仍与前端严格同源，pytest 用 get_drawings() 做几何级看护。
- **`/api/render` 的磁盘缓存是 `rendercore.preview.PreviewCache`**（U08 接入、U10 起唯一一条路）：
  键 = `sha1(源 id | 内容 sha256 | 页号 | 宽 | 背景 | rendercore 名-版本 | PDFium 版本 | 字体政策版本)`。
  **不许用 mtime 当身份**：它回答的是「什么时候被碰过」，不是「里面是什么」（touch / 从备份还原 /
  同步工具）会白丢一张 3200px 预览；换了 build / 换了字体集合像素可能已经不同却照旧命中，所以后端
  身份与字体政策版本进键——U10 切默认就是一次这样的变化，旧后端留下的缓存自然不命中、只占预算按
  mtime 淘汰。身份从 PreviewCache **自己抄出来的那份源字节**上算（键与渲染绑同一份字节，「同 tick
  同尺寸改写」的窗口结构上不存在）；同键并发只渲染一次；写入一律临时文件 + `os.replace`（同键并发会
  读到半个 PNG），零字节缓存当场删掉重建——临时文件后缀**必须还是 .png**。
  **Windows 上 `os.replace` 盖不掉正被读的目标**（werkzeug 的 `send_file` 拿着没有 FILE_SHARE_DELETE
  的句柄 → WinError 5，并发请求当场 500）：撞上就**退让**给已经在磁盘上的那份——键含内容哈希，同键必然
  逐字节相同；只有目标不存在或是零字节时才重试，重试完仍不行照旧抛出（假装成功 = 一个永远画不出来的
  面板）。child 有界队列满 → 503 + `Retry-After: 1`（背压不是故障）。看护 `tests/test_render_cache.py`
  （真实端点的用户合同）、`tests/test_rendercore_preview.py`（键 / 去重 / 退让 / 抄字节）、
  `tests/test_windows_regressions.py`、`tests/test_rendercore_app.py`。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 契约层 19 项唯一实现 `rendercore/facade`（U10 起，ADR 0072）
- 开关 `TAVOTTO_RENDER_BACKEND` 闭集只有 `rendercore`、`pymupdf` 报 `backend_retired`、选定即定不静默回退
- 应用闭包零 pymupdf 由 `scripts/ci/retirement_scan.py` 五把尺子看护（主语是闭包不是硬盘；旧行为参照只在 `tests/fixtures/legacy_pymupdf/`）
- 字形归属四层只在 `glyphplan.py`、`fallback` 恒空
- 本机字体族在 manifest 顶层 `font_families` 只发一次
- 「自定义色图」判对象不判名（`_registered_colormap`）
- CJK 回退尾巴必须在 `font.family` 列表里
- 预览缓存是 `rendercore.preview.PreviewCache`（键含内容身份 / build / 字体政策，不用 mtime，Windows 退让）
