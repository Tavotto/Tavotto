# PDF 后端边界（许可证相关，勿破坏）

> 原文出自 `src/tavotto/AGENTS.md`「PDF 后端边界（许可证相关，勿破坏）」（2026-09-17 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- `src/tavotto/pdfbackend/pymupdf_backend.py` 是**全仓库唯一** import pymupdf 的
  模块；`__init__.py` 是与实现无关的契约层（probe_asset / render_preview_png /
  text_width / text_plan / missing_glyphs / coverage_ranges / compose +
  mm2pt / hex2rgb）。`app.py` 只认这些名字。
- **契约层按一条写明的策略在两个实现之间选一个（U08 起，ADR 0067）**：
  `TAVOTTO_RENDER_BACKEND` ∈ {`pymupdf`（默认；未设 / 空串同义）, `rendercore`}——候选实现是
  `rendercore/facade.py`（pikepdf / HarfBuzz / PDFium render child，零 pymupdf），19 个契约名同签名
  同返回结构。契约函数显式委托（`_impl()`），四个常量经 PEP 562；八个调用点（app.py 五处、
  `artifactcheck` 两处、`tutorial`、MCP bridge、两个生成脚本）**一行不改**就同时切换。**选定即定、不静默
  回退**（06 §1）：候选被选中而候选包 / 批准字体缺席，`CandidatePackagesMissing` / `FontsUnavailable`
  原样抛出，绝不换回 PyMuPDF；不认识的取值 `BackendSelectionError(backend_unknown)`，不猜不退默认。
  层规则：`pymupdf_backend.py`（`pdfbackend_impl` 层）与新核心两个方向零边，契约层是唯一同时认识
  两边的模块。看护 `tests/test_rendercore_facade.py`、`tests/test_rendercore_model.py`。
- **字形归属计划（ADR 0033）**：一个字符由哪张脸画出来，只有
  `tavotto/glyphplan.py` 一份判据（四层 primary/cjk/fallback/missing，顺序不可
  交换）。落笔、量宽、预检、前端预览读同一份计划。`ord(ch) > 0x2E80` 只保留为
  **换行单元**的判据，**不再当覆盖判据用**——它量的是码位，不是「这张脸画不
  画得出这个字」。浏览器没有字体引擎，读的是生成物
  `pdfbackend/canvas_coverage.json`（`scripts/gen_canvas_coverage.py --check`
  看住它与真字体一致）。**本仓库不分发任何字体**，看护
  `tests/test_font_provenance.py`。
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
- 为什么在意：PDF 库是可替换的实现细节，收敛成单一模块后换后端只需重写这一个
  文件，上层零改动。**别在 app.py 或别处新写 `import pymupdf`**——那会把这条
  边界废掉。许可证说明见 `docs/legal/LICENSING.md`。
- **图内元素的命中判据跟渲染器走，不跟直觉走**（`web/src/lib/pathGeom.ts`）：
  填充用 **nonzero** 缠绕数（实测 matplotlib 3.10.8 + Agg：同向嵌套的中心
  像素是实心的，反向才是洞；even-odd 会让点在填了色的像素上选不中），
  填充路径没有 CLOSEPOLY 时按**隐式闭合**处理，框选**先把选择框裁进 clip**
  再比（否则只与不可见的延长线相交也算命中），命中容差取「可用性容差」与
  **描边半宽**的大者（`stroke_pt` 由 `engine/pathgeom.py` 随几何下发，
  前端不推算）。共线线段必须再比一维区间，否则框选会收走老远的水平/垂直线。
- 面板的项目路径解析与引擎重渲染留在 app 层的 `_resolve_panel_source` 回调里，
  后端只管画。几何公式仍与前端严格同源，pytest 用 get_drawings() 做几何级看护。
- **`/api/render` 的磁盘缓存键 = `sha1(id|内容 sha1|宽度|后端-版本)`**（2026-08-18）。
  **不许用 mtime 当身份**：它回答的是「什么时候被碰过」，内容没变而 mtime 变了
  （touch / 从备份还原 / 同步工具）会白丢一张 3200px 预览；换了 PyMuPDF 版本
  像素可能已经不同却照旧命中，所以 `BACKEND_NAME/BACKEND_VERSION` 进契约层。
  内容哈希走进程内 `(mtime,size)→sha1` memo（memo 失效信号 ≠ 身份）。写入一律
  临时文件 + `os.replace`（同键并发会读到半个 PNG），零字节缓存当场删掉重建
  ——临时文件后缀**必须还是 .png**，后端按扩展名定格式。
  **Windows 上 `os.replace` 盖不掉正被读的目标**（werkzeug 的 `send_file` 拿着
  没有 FILE_SHARE_DELETE 的句柄 → WinError 5，并发请求当场 500）：撞上就
  **退让**给已经在磁盘上的那份——键含内容哈希，同键必然逐字节相同；只有目标
  不存在或是零字节时才重试，重试完仍不行照旧抛出（假装成功 = 一个永远画不
  出来的面板）。看护 `tests/test_render_cache.py` 与
  `tests/test_windows_regressions.py`。**候选后端下 `/api/render` 走 `rendercore.preview.PreviewCache`**
  （同一条纪律，键多三维：PDFium 版本 / 字体政策 / 背景；`source_sha1` 的 memo 经 `identity=` 注入）；
  child 有界队列满 → 503 + `Retry-After: 1`（背压不是故障）。看护 `tests/test_rendercore_app.py`。
