# 布局版本、项目文件收纳与文档落盘

> 原文出自 `src/tavotto/AGENTS.md`「布局层（R18）」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。
> 看护用例（`src/tavotto/AGENTS.md` 那一行原「看护」格，2026-10-23 迁入；速查表不再列用例，加用例改这里）：`tests/test_versions.py`、`test_layout_timeline.py`、`test_document_persistence.py`、`test_package.py`、`test_ai_revert_atomic.py`、`test_project_layout_save.py`、`test_layout_session.py`、`test_layout_recovery_backend.py`、`test_layout_recovery_acceptance.py`；SVG格式隔离与共同打开顺序：`tests/test_chart_documents_v4.py`、`tests/test_layout_session_open_intent.py`、`tests/test_layout_open_intent_http.py`。

<!-- agents-route
sheet: src/tavotto/AGENTS.md
order: 200
改到: `/api/versions`、`/api/styles`、`/api/package`、`tavottofile/`、`atomicio.py`、`documents.loads_document`、自动保存槽位、`engine/layoutsession.py`、`engine/packagerelocation.py`、`engine/packageinstall.py`、`/api/layout-session`
主题: 布局版本、项目文件收纳与文档落盘 → `layout-versions-and-documents.md`
必守要点: `atomicio` / `project_layout_dir()` 唯一权威（ADR 0120）
-->

- **布局版本**：`/api/versions/<docId>` 系列，快照存项目 `tavottofile/versions/`（旧位置 `layouts/_versions/` 只读兼容，格式见下条「新存储」），
  自动检查点去重 + 滚动清理（保手动裁自动）；恢复=前端 commit（可撤销），
  与「写回原始文件」的 baked 历史完全无关。
  **上限有两条，谁先咬到算谁的**（2026-09-03，issue #221）：条数
  `VERSION_KEEP_TOTAL=120` 与字节 `VERSION_KEEP_BYTES=24 MB`。条数单独用不住
  体积——每条条目里塞的是**整份文档**，于是文件大小 = 条数 × 文档大小，而每次
  追加都要把整个文件「读 → 追加 → 裁 → 整写」（实测 1.16 MB 的文档塞满 120 条
  = 约 140 MB / 单次追加 547 ms）。`_save_versions` 逐条序列化再拼，**量大小与
  写文件共用同一批字节**（分两次序列化的话，削掉的开销会原样回来），拼出来的
  与 `dumps_json({"versions": kept})` 逐字节相同。至少留最新一条。
  **列表可以带「草图」而不带正文**（2026-09-06 审计 T04 补做）：
  `?sketch=<对象数>&sketchText=<字数>` 时每条元信息多一个 `sketch`
  （页面尺寸 + 每个对象的 type/x/y/w/h，面板另带 fileId/fileKind），
  **overrides / 脚本一个字节都不带**。这是前端列表能画缩略图而不退化成
  「一次打开拉 120 份整份文档」的做法——列表端点为了数对象数本来就已经把
  整份文件解析过一遍，草图是那份内存数据的投影（实测 24 MB 预算下
  170 → 183 ms，不需要缓存）。**尺寸由调用方给**：「一张缩略图画几个对象」
  是前端 `components/CanvasThumb.tsx` 的事，Python 侧的
  `VERSION_SKETCH_MAX_*` 只是传输封顶，不是第二份权威。schema 3 按检查点的
  `canvasId` 取那**一张**画布（缩略图画的是一张页面）；页面尺寸取不出来就
  整条不发草图——**「不知道」是独立一档**，编一个 A4 出来的话用户会看到一张
  比例是假的图而没有任何提示。坏文档只赔掉自己那张缩略图，不让整条时间线 500。
- **排版时间线（ADR 0101，2026-09-27）**：节点类型 named / moment / auto / manual 的判据只在
  `app._version_kind`（前端不从 auto + name 自己猜）。**命名节点永不自动清理**：条数上限只数
  未命名节点、`_sacrifice_order` 里没有命名节点；命名节点自己超出 `VERSION_KEEP_BYTES` 时
  **拒绝再命名**（409 `named_budget_exceeded`，写之前判、磁盘零改动），已有的一条不删，
  删名字 / 删节点永远放行。未命名的牺牲顺序：普通自动 → 关键时刻 → 手动。
  **两条条数上限在 `_save_versions` 里跑，每一次写入都过**——不靠调用方记得调
  `_prune_versions`：取消命名、`auto` 开关这类改类别的写入同样会让某一档越过上限（#679）；
  这次调用交回的那一条与最新一条不参与牺牲（`_sacrifice_order` 的 `keep`）。关键时刻是闭集
  `VERSION_MOMENTS`（与前端 `LAYOUT_MOMENTS` 严格同源），**不去重**。节点缩略图存
  `versions/thumbs/<排版 id>/<vid>.<webp|png>`，**不进时间线 JSON**（有没有 = 文件在不在；
  记进 JSON 的话每挂一张图就多一次整写），`_sweep_version_thumbs` 按目录清孤儿、只认
  `_VERSION_ID_RE` 的名字——它在时间线 JSON 提交**之后**跑，连目录都列不出来也只记日志，
  不让已成功的写入回 500（前端一重试就多一个节点，#679）；换缩略图格式先原子写新图、落地后再删旧格式（删不掉只记日志；两张同时在时取较新的那张，新图胜出，#679）；PUT 的 256 KiB 上限卡在读取本身（`_read_body_capped`，
  chunked 请求没有 Content-Length）。列表端点的 `budget` 在文件没超上限时不量 `namedBytes`（缺席，
  不冒充 0）。
- **新存储与首写迁移（ADR 0140，D1b → D2）**：`engine/versionstore.py` 是版本时间线的存储——每版一个
  自描述信封 `<doc_id>.vx/s/<vid>.json` + 小索引 `<doc_id>.vx/index.json`（唯一提交点），追加只写一个
  信封 + 索引，与历史长度无关。`app.py` 的 `/api/versions/*` 全部经 `_Timeline`：
  **读**（`_open_timeline`，不写盘）——`versionstore.has_store` 为真读新存储，否则读旧单文件
  （`_LegacyTimeline`，与迁移同一套校验 `read_legacy_versions`）；响应形状两边逐字段相同
  （`tests/test_versions_store_wiring.py` 同一组操作对拍）。
  **写**（`_timeline_for_write`，版本锁内）——旧文件先迁进新存储（`migrate_legacy`：旧文件**冻结**，从不改、
  不删、不改名；先写迁移记录 → 逐条信封 → 读回校验 → 写索引提交），之后一律写新存储；迁移在索引提交前被打断，
  读侧一直以旧文件为权威，下一次写入重跑到一致。回滚后的旧程序在冻结的旧文件上追加、再升级：迁移记录的 sha 不符 →
  只把迁移点之后新增的条目按 id 去重导入（已在新存储里删掉的不复活、已有条目不覆盖；旧程序的改名 / 删除不回流）。
  **#264**：旧文件读不出来（截断 / 乱码 / 形状不对 / 非有限数）→ 新存储开一份空历史、旧文件原样保留，
  列表响应带 `legacy_unreadable: {name, size}`（只有文件名与大小，不含路径），写入不再 409 卡死。
  新增的响应字段只有：`legacy_unreadable`，以及信封坏的那一条在列表里带 `damaged: true`（只赔掉自己，其余照常；
  取它的正文回 409 `versions_unreadable`）；索引读不出来 / 来自更新的格式同样回 409 `versions_unreadable`，零改动。
  **显式修复**（D3）：`POST /api/versions/<docId>/rebuild` 在版本锁内从信封重建索引，只写新索引、不删任何文件，
  响应带 `added` / `dropped`（见 ADR 0140 §九）。`atomicio` 不对 `os.replace` 做重试（会覆盖外部写入，#1241）。
  **策略只有一处**：`versionstore.legacy_policy`（现行条数 + 24 MB 字节策略，D2 不改语义）与常量 `KEEP_*` / `MOMENTS` /
  `VERSION_ID_RE` / `is_named` / `kind` 是唯一出处，`app` 的 `VERSION_KEEP_*` 等是别名（按 `is` 钉死），新存储经
  `app._version_policy` 调用，上限按调用时刻的 `app.VERSION_KEEP_*` 传入（测试仍可改写）；列表的 `budget` 按
  「旧文件会占的字节」（`_legacy_bytes`）算，与旧实现同值。`app._load_versions` / `_save_versions` 是旧格式读写器，
  **产品代码不再调用**，留作迁移对拍参照与测试造旧文件 / 模拟回滚后的旧程序用（D3 清理）。
  铁律：新索引**绝不与旧文件 `<doc_id>.json` 同名**（旧程序整写会毁掉它）；先信封后索引、删除先索引后信封；
  所有写在 `<doc_id>.versions` 跨进程锁内（迁移也在锁内：并发迁移会把先追加完的版本盖回去），模块内不取锁。
  项目归属旁证（`_project_owner_evidence`）认旧文件也认 `<doc_id>.vx/`。
  看护：`tests/test_versionstore.py`、`tests/test_versions_store_wiring.py`、`tests/test_versions_migrate_on_write.py`。
- **论文样式**：`/api/styles`（`layouts/_styles.json`）；前端按角色映射成
  override / 标注属性一次 commit 应用，绝不写回源文件。
- **项目包**：`POST /api/package` 打 zip（layout+素材+脚本+sha1 清单）；
  `POST /api/package/open` 检视（缺失/sha1 漂移），素材永不自动写入图库。
- **有界项目包容器**：`documentpackage`是ZIP路径/别名/成员/目录/展开/压缩预算和CRC的唯一权威，六项`PackageBudget`由调用者明确提供；不任意extract。`staged_package`立即流入临时helper资源，任何add异常锁定整包失败，关闭并验证后经atomicio原子发布；不能因调用者捕获add异常而发布半包。新文件发布用`publish_file_if_absent`，同名并发不覆盖；允许替换须显式overwrite。严格v4 reader核现品牌/版本/format信封，普通旧包不自动套用新低容量上限。看护`tests/test_document_package.py`。
- **输出预算与旧包文件名**：`packagebudget.output_budget`从调用者完整资源plan的实际成员名/字节量推导DEFLATE/ZIP64上界，不新增旧合法项目的容量政策；reader块大小只取`documentpackage.ARCHIVE_CHUNK_BYTES`。写入提供已知file_size，让stdlib决定ZIP64，默认小包字节不变。只有旧只读包输出显式`legacy_names=True`保当前平台合法文件名的精确区别；默认writer及所有公开reader仍按portable/NFC-casefold校验，不从上传manifest自动选择兼容模式。两模式均拒绝跳径、特殊文件、完全重复和文件目录冲突；caller仍负责canonical源授权。看护`tests/test_package_budget.py`及上述容器测试。
- **旧项目包HTTP原子输出**：`api_package`先保留经过containment校验的canonical资源路径与原逻辑ID，由caller持有data_dir自有快照暂存至整个输出结束，沿ZipFile原规范化组完整plan后推导预算；最终ZIP仅验证封口后发布，不覆盖同名旧包。文件名增加随机后缀，保原成功响应键与layout/manifest语义。快照准备失败沿旧整组warning；真实ZIP add/close/publish故障必须失败，不能吞成成功半包。检视仍只读，不把这条当v4混合安装完成；看护`tests/test_package_atomic_http.py`。
- **显式Figure配对预览**：`POST /api/documents/v4/figure-preview`与原`preview_png`的显式`figure_snapshot`分支沿原`svgstore.load_document_snapshot`与配对物化口，按完整ref、来源、有序patch与本项目归档核验，不起worker、不借当前同名文件或脚本。SVG响应回显已核完整ref与`preview_source`，图幅由PDF契约层独立测量；PNG回显完整ref header，两者no-store。坏配对明确失败，不回退别的历史；同owner新执行不删除旧归档显示资格，原`_load_svg_snapshot`的current/导出撤旧门仍完整保留。归档字节没有原可编辑manifest，不提供伪清单，PNG归档分支拒绝`with_manifest`及混用计算/采用上下文；无ref旧路径保持。前端显示、缓存、几何权威与明确编辑来源须分别接入，不能由本端点成功推断已恢复编辑能力。看护`tests/test_figure_snapshot_preview.py`。
- **v4项目包导出**：`POST /api/documents/v4/package`显式进入同一v4格式/会话域与认证，冻结全部画布的完整计算INPUT，沿chartpackage唯一manifest与预算工厂写包；不执行用户脚本。普通素材/登记脚本只读containment返回的canonical路径；已有Figure配对沿svgstore核验，显式坏引用拒绝，去重资源仍逐个回填每个面板引用。原SVG缺席但计算附件有效不算缺件，普通素材/脚本/未具配对的执行图则显式missing，允许备份但不声称完整搬迁。成功保旧name/url/assets/missing并加document_format='v4'与去重INPUT数chart_sources；assets为普通file资源数。旧默认导出与只读open不变。registry/数据/运行配置闭包及生产安装另行验收，不把包导出称作已安装。看护`tests/test_chart_package_http.py`。
- **包内执行事实**：可选`execution`的闭式v1由`packageexecution`唯一校验，documentpackage清单和chartpackage资源核验都消费它；登记/argv/cwd/改指沿原权威。导出收集器携带已知数据/helper与非敏感原配置ID，不搬机器授权，不执行脚本；静态绑定或成功输入观察均不证明任意Python依赖完整，无完整声明时准确`coverage=incomplete`，响应`execution_coverage`供界面展示。已知缺资源仍只进missing；file资源计数含数据。无脚本包省略该字段，旧包不变。看护`tests/test_package_execution.py`、`tests/test_package_execution_export.py`、`tests/test_package_execution_http.py`；完整声明/外部输入及安装仍须后续验收。
- **显式捕获资源包**：有正式捕获附件时，`execution`选择闭式v2：原字段加非空`captures`，
  每行只有完整`snapshot_ref`与`revision`，按snapshotId唯一；同图不同patch/历史输入保独立ref。
  `VERSION=1`仍是缺省，旧v1闭集/字节/输出容量不变。graph与原字节继续原`figure_snapshot`资源树，
  不将大graph复制进manifest，不新增资源store。`packageexecution`沿svgstore原引用权威核完整ref，
  经metadata.input_archive→原canonical graph→每SHA/size blob核关联；v2全部带附件档案的输入并集须精确闭包，
  原render孤儿仍按旧合同。writer封口及Prepared reader与正式HTTP检视共用同一语义消费者，验证先于发布。
  按实际面板ref分离历史捕获和当前contexts；同script未捕获面板仍需当前资源，不借历史blob消除缺件。
  reader从正文重新核未捕获面板的实际脚本使用，发送者遗漏contexts也不能消除该脚本缺件。
  原rc_ID的现有非敏感argv可作可携建议，缺失/敏感/不可读分别报缺口；公开receipt不证明参数原文，
  不从default/新声明/当前文件补历史。声明文件集齐备不证明未知IO通道，v2总体coverage仍incomplete。
  新检视`captured_inputs`逐ref报告已核scope与文件/prefix数，通道及配置缺口按snapshotId区分。
  图中旧绝对请求只作来源事实，检视不resolve/open。`remap:<sha256>`保opaque域，prefix允许根空目录；
  可恢复逻辑成员沿`packagemembers.project_member_key`核路径、别名、父目录和文件/目录冲突，
  跨capture不混合同名不同字节；已有shadow `script_source`角色与数据file角色保持分域。
  native公开spec的cwd_mode恒sandbox，不能据此拒其实际project/project_root捕获scope。
  显式v2输出用`packagebudget.portable_output_budget`把精确输出边界与原输入政策求交集；
  整包资源加metadata超额拒绝、原输出不变，不把单声明额度当整包额度，也不给旧输出新增低cap。
  看护`tests/test_package_execution_captures.py`、`tests/test_package_execution_captures_export.py`、
  `tests/test_package_portable_budget.py`、`tests/test_svg_input_package_e2e.py`、
  `tests/test_package_execution_capture_reader.py`。这不授新根安装、冷重执行或写回资格。
- **计算来源包叶子**：`chartpackage`沿上述容器与`documentassets`唯一字节权威验证schema4完整INPUT；内容寻址chart行从正文引用派生，额外资源使用闭式chart_source/figure_snapshot/file/script变体，不接受caller覆盖派生chart行。检视只写data_dir自有暂存，Prepared正文/清单每次给副本，退出后拒用；verify回调必传，前后重核附件/资源，失败不发布目标。`install_chart_sources`只发布不可变chart附件并明确返回未安装的remaining_resources，不能宣称普通文件/脚本已安装；普通资源及正文事务由调用者完成，已发布的不可变附件不回删。writer临时封口后严格inspect再原子发布最终名。看护`tests/test_chart_package.py`。
  输出调用者可显式传`budget`或`budget_factory`，恰一；后者只收本模块最终唯一layout/manifest UTF-8字节、完整INPUT及各资源真实尺寸的只读census，一次返回六预算。类型/回调错误在创建输出前拒绝；factory后仍沿原装载、严格inspect与发布链核实际摘要，不能靠清单声明尺寸或静态预算绕过验证。静态预算旧调用/字节保持；不复制manifest结构给app。
- **项目包资源目标投影**：`PreparedChartPackage.plan_project_resources`在活跃上下文内先复核全部资源与manifest，再将assets/scripts映到同一项目相对根；原模块级函数保类型拒绝后委托。上层检视首尾消费同一对象方法，类型引用不产生反向运行时依赖；writer/reader仍用同一语义检视。原拼写且同SHA/长度可复用；跨namespace异字节、case/NFC文件或祖先目录别名、文件/目录冲突均在零目标写入时拒绝。比较键只由`documentpackage.project_member_key`调用原严格成员规则派生，不复制portable策略或允许legacy开关。返回计划不等于安装成功，调用方发布前仍须核生命周期/来源并安全发布。看护`tests/test_chart_package_targets.py`及`tests/test_import_architecture.py`。
  成员路径判据、元数据成员名与`PackageError`的实现唯一位于纯标准库`packagemembers`，`documentpackage`保原API同对象导出；执行清单及可携运行配置直接依赖该叶子，避免容器→执行清单→配置→容器的环。strict/legacy路径判据、异常字段与ZIP输出保持原合同，搬移不扩大任何名字或提取权限。看护`tests/test_package_member_authority.py`及原import架构门禁。
- **v4只读产品检视**：`POST /api/documents/v4/package/inspect`只收一个multipart `package`文件，沿同一认证/格式域；实际请求流、表单内存和包的六项预算均有界，临时文件只在data_dir并随请求回收。`packagebudget.input_budget`集中输入政策，不从不可信清单反推预算，也不改变旧open/输出预算。普通资源沿`DocumentPackage.copy_required`的同一CRC/解压计数器流式暂存和摘要核对，不整份读大型数据。`packageinspection`复用活跃Prepared/目标计划，返回完整正文/原清单、全部资源目标、重新计算的缺件和明确缺口。已声明missing及runtime脚本仅作候选，与实际plan核缺席；已携文件清除旧缺件，完整INPUT的chart-only原件不算缺件。非portable/opaque声明保原清单并报未核缺口，不作本机路径或运行授权。Figure树按svgstore全量核档案/marker/条目/blob，再用共享SVG安全解析器与pdfbackend核全部图像内容；正文显式ref须与source/patch完全相符。历史owner不等于当前登记/授权，合法旧v4/无execution/依赖不完整备份仍可检视；原coverage是发送者声明，完整输入声明未验证时另给缺口。检视不安装、不写项目/正文/本机配置、不运行用户脚本，也不授予冷重放或写回资格。看护`tests/test_package_streaming.py`、`tests/test_chart_package_inspect_http.py`。
- **v4资源安全安装**：`POST /api/documents/v4/package/install`沿检视的同一有界上传、格式域和认证，
  额外只接受一个闭式JSON `options`（绝对新目录`destination`与可选本机批准`remap_rules`）。
  `packageinstall`消费活跃Prepared和原迁移结果；原受检流复制到同父私有stage，保普通资源原字节，
  registry沿原权威生成，普通资源同名时只允许语义一致且保原bytes。声明式图表沿原renderer核完整
  INPUT与原序overrides；上传stage只提供资源，解释器根只取可信caller或自有空engine根，不能选择
  上传项目的.venv，不执行包内用户脚本。检视仍只接受原单文件合同。
  发布前`verify_publication_copy`沿原资源摘要权威与同一闭合成员清点核实际副本，允许明确生成的
  registry元信息但不覆盖包内成员；callback不得改正文。每文件/子目录同步后再核目录身份和副本，
  经原atomicio排他发布；目标存在（含并发/链接）零覆盖，只清未发布且身份未变的本attempt stage。
  发布后才沿原runconfig导入非敏感建议、原inputremap写本机批准规则并核唯一实际指纹；
  不搬默认配置、cwd授权、执行成功记录、current marker或发送者机器信任。
  既有本机元信息在发布/import和项目锁之前从原default_selection/get.public/workdir.state只读投影，
  保原配置并返回`needs_review`，不额外公开argv，也不把旧默认视为新包执行事实。
  已知缺件拒绝完整安装，unknown coverage仍如实incomplete。资源发布、配置导入和后续正文writer
  是独立阶段：`resources_installed`不表示正文落盘，始终`document_saved=false`；
  配置区分`pending/needs_review/ready`。发布后的durability、Prepared出口或上传清理失败不得回删目录
  或丢回执，返回实际`path`及`publication_unconfirmed/installation_unconfirmed`（清理另标
  `upload_cleanup=pending`），HTTP503不能被消费者解读成目标未创建。只有资源及配置均确认才201。
  不调用open_project、touch_recent或自动保存；界面采用与完整迁移资格另验。
  看护`tests/test_package_install.py`、`tests/test_chart_package_install_http.py`、
  `tests/test_atomicio_directory.py`、`tests/test_package_install_e2e.py`。
- **文档显式引用全质量 Figure 配对（X03，2026-10-15）**：面板 `figureSnapshot` 是版本 1 的闭集
  `{version, sourceId, patchHash, receiptId, snapshotId}`，可选 `executionOwner={script, run_config}`；
  owner 只从已验归档派生、纳入内容 ID，不信客户端自报。导出 wire 字段为 `figure_snapshot`。
  后端 `svgstore.validate_reference` 核闭集、类型与 hash；引用必须与该面板的源和规范化 patch 匹配，
  不从预览或磁盘 current marker 推造。引用指向保存时的 PDF/SVG **同一版本**，包括后来的 PDF-only 导出；
  冷态无当前回执时可按明确引用读档，已知新回执则必须相符（授权边界见 `export-pipeline.md`）。
  **保存先冻结、再单次原子写正文**：命名保存和自动保存都在文档锁内冻结，成功的引用随正文一起落盘，
  不先写一份无引用正文再补写。某来源冻结失败时清掉**本次待保存正文**里的旧引用，返回
  `svg_snapshot_warnings`，文档仍保存（该字段只是后端事实，**前端保存 / 另存为 / 顶栏都不展示**；缺快照到真正导出 SVG 时由 `svg_snapshot_missing` 一句话说明（没有脚本的来源仍报中性的 `svg_source_unsupported`））；成功响应的 `figure_snapshots` 是这次正文实际写下的引用表。
  前端只把响应合回同一项目 / 文档加载轮次且源、patch 与执行代仍匹配的面板，不让晚到的保存响应复活旧引用。
  可信 `figure_snapshot_invalidation` 的实际执行事实、源或 override 改变会清除相关引用（含非当前画布）；
  不能从 HTTP 成功、descriptors 或 ready 猜执行；失败、零图、部分输出、开始后取消也撤该 owner 的旧输出全集，
  未开始的依赖/权限门与取消不撤。owner 按项目内 script + opaque run_config 隔离，未知 native 配置只撤已接纳精确 receipt，不宽清后来重绑定的同名来源。
  纯几何变化保留。实际重跑的失效属于正文变化：走普通 dirty / 自动保存队列，并标脏已绑定的项目文件；不新增撤销条目。
  即使面板当前已无引用，也必须排一笔后续保存，避免前次在途保存把上一轮运行的引用最后写回磁盘。
  只有已落盘保存响应的引用合并可用 `snapshotSeq` 跳过再次保存；执行失效不能共用此旁路。
  实际执行事件先经项目/恢复归属接纳；同 execution ID 只补新增旧 receipt 事实，不反复撤本代新引用；
  增量也只作用首次真实消费的文档安装代，后来主动打开历史文档不重放旧事件。
  在途保存晚回旧 owner/receipt 时拒合并，并按请求之后的新代次排一次纠正保存，不自循环。
- **项目配对存储与不可变 archive**：`engine/svgstore.py` 只有收到应用校验过的 `storage_root` 才使用
  项目 `tavottofile/figure_snapshots/`；缺省仍是数据目录下带 TTL 的缓存，不能把二者混为永久项目资产。
  项目身份可搬迁（不要求旧机器绝对路径相等），源 / patch / 静态原件 hash / 输入改指仍逐项核对。
  `snapshots/` 的内容寻址 ID 覆盖完整来源 identity、执行回执身份及 generation、PDF/SVG 两份字节 hash与可选 owner；
  归档与 blob 读回均核完整性。新运行 `activate` 只撤销旧代 current lookup，**不删除不可变 archive**，
  历史文档的明确引用仍可在其授权条件下读到保存版本；archive 的保留不等于旧图获准冒充当前运行。
  项目资产**没有 TTL，也不为新资产自动驱逐已有 archive**。容量先判再写，`MAX_SNAPSHOTS=256` 数的是
  current 与 archive 元数据**合计**（首次保存一个变体通常占两条），并非 256 个都可同时激活的变体；
  总预算 512 MiB，相同内容的 blob 共用字节成本。满额只拒新增，已有归档及其引用的字节保持原样。
  磁盘写入沿用锁、受限路径与 `atomicio`，只读项目副本也可核验不可变引用，不因读档创建写锁文件。
  自动捕获 / 保存 / 导出不暗中运行脚本。旧项目无明确引用时，执行源需当前进程已观察的回执，
  否则由用户明确再运行并接纳，不能借 current marker 或 preview metadata 猜旧配对仍是当前版本。
  无明确引用的静态源仍按当前原件精确 hash 授权，见 `export-pipeline.md`；明确文档引用由
  `load_document_snapshot`核原archive完整ref、source/patch、历史静态hash及实际当前remap指纹，
  不要求一份当前原件同时等于所有历史版本。原`load_snapshot`的逐维精确查询不变。
  原图、画布、保存和打包必须消费同一历史PDF/SVG配对；当前普通file资源仍独立核SHA，
  不因历史渲染对存在而声称历史磁盘原件已恢复。项目包打开仍只检视、不自动安装资产。
  看护：`tests/test_svgstore.py`、`tests/test_export_svg.py`、`tests/test_export_request.py`、
  `web/src/store/figureSnapshot.test.ts`、`web/src/store/figureSnapshotInvalidation.test.ts`、
  `web/src/store/projectSave.test.ts`、`web/src/lib/autosave/diskWriter.test.ts`。
- **Figure捕获输入的正式冷存**：`svgstore.save`的私有`captured_input_archive`仅用于项目`storage_root`，
  不塞入公开receipt。有附件才写metadata v2及`input_archive={version:1,revision,graph_size_bytes}`，
  并将完整descriptor绑定snapshot ID；引用和marker仍v1，无附件旧公式/字节保持，不给旧archive补签。
  `svgstore_inputs`只管理同一store内`inputs/graphs/<revisionhex>.json`与
  `inputs/blobs/<sha[:2]>/<sha[2:]>`，复用原guard和atomicio无覆盖发布；不是第二个store或锁。
  图的canonical bytes、逻辑成员预算、资源闭包及受检读取只消费`inputdeclaration`；复制时同流核SHA/size及
  regular/no-follow/起止身份，零字节合法，不先verify再无核copy。receipt revision与显式script/run_config须一致，
  opaque sourceId不推归属。图内原请求字符串不产生本机读权。
  `svgstore.input_store_budget()`是默认累计输入政策入口：单声明沿projectcopy的50,000成员/2GiB，
  累计去重blob另限50,000份/2GiB，单graph与累计graph均64MiB，graph数沿MAX_SNAPSHOTS；
  这是新增输入维度，不改原render 512MiB、metadata 1MiB及旧目录4,096项，也不扩大整包上限。
  准入先核既有typed树全部成员及最终去重预算，输入全部发布后才进入原metadata/current流程。
  输入阶段失败清本attempt成员，旧marker不动；后续immutable archive已提交而entry失败时，必须保其输入，
  不把原多文件store夸成整库ACID。GC从所有archive及仍current entries追引用，不凭marker单独删历史输入。
  输入暂存名消费atomicio原`<目标名>.<pid>.<序号>.tmp`权威。持原guard的mutation在准入前只回收typed目录内
  合法目标对应、regular/no-reparse且身份未变的中断tmp，再按实际已提交记录清理完整无引用孤儿；
  不需第二锁、PID租约或时间阈值。只读检视不忽略、不删除半成品；未知名字和链接继续拒绝。
  `load_input_archive`按完整历史ref只读，其他load/inspect在有附件时核全闭包；full inspect也核孤儿成员，
  输入总量单列`inputs.total_bytes`，不喂入PDF/SVG解析器。无TTL、无原源回读、不授执行权，返Path仍非租约。
  app的执行源、明确运行后自动捕获与静态PDF/SVG绑定，在实际ExecutionReceipt仍在手时以私有keyword
  将附件交给同一`_save_svg_snapshot`；FrozenSource继续只携公开receipt，不把Path/graph塞进渲染wire。
  同receipt的旧v1缓存不能阻挡本次实际附件生成独立v2引用，旧archive不覆；已有同graph附件或没有新附件才沿原早退。
  `_package_svg_assets`从正式load或文档显式引用reader取得已核附件并完整转交staging save，复制时再核实际流，
  不回读当前原文件、不重跑脚本。无附件不新增下游keyword或响应字段，原best-effort保存/严格显式引用边界保持。
  应用接线与资源包核验不等于声明UI、引用搬迁或安装已完成。看护：
  `tests/test_inputdeclaration_resources.py`、`tests/test_svgstore_inputs.py`、`tests/test_svgstore_input_archive.py`。
  应用接线看护：`tests/test_svg_input_capture_handoff.py`、`tests/test_svg_input_cold_staging.py`、`tests/test_svg_input_save_e2e.py`。
- **迁移只归档，不授当前执行权**：`svgstore.save(archive_only=True)`只用于显式项目store，
  与activate互斥；沿原锁、预算及发布链写不可变档案，不写entry/marker，缺省行为不变。
  `packagerelocation.prepare_relocation`消费活跃Prepared与原核验权威，仅在data_dir自有暂存准备
  完整oldRef→newRef和全部正文位置；保原序patch、receipt、配对字节及捕获图。新根整份规则
  只经`inputremap.fingerprint_rules`生成一个指纹，调用者安装时须与实际设置核对。多个旧ref
  可收敛为一个新ref，映射不丢行，captures集合从新store重算。历史输入按选中ref惰性物化，
  同时至多一份，经原freezer在进入/正常退出核同一事实；script影子与项目数据角色分开。
  publication_files是核闭合成员后的受检流计划，排除锁及临时view；再次复制仍核实际字节。
  不写最终目录、正文、registry、默认运行配置或机器授权，不把历史view当当前运行输入。
  看护：`tests/test_svgstore_archive_only.py`、`tests/test_package_relocation.py`、
  `tests/test_package_relocation_e2e.py`、`tests/test_svg_document_snapshot_sources.py`。
- **项目文件统一收纳在项目内的 `tavottofile/`（2026-08-17 定版）**：命名画布
  布局直接放 `tavottofile/`，导出默认 `tavottofile/export/`（settings.export_dir
  可覆盖；建不出来退回数据目录，测试读响应里的 export_dir 而不是猜路径），
  布局版本历史 `tavottofile/versions/`。**这条规则的唯一出处是
  `project_layout_dir()`**，`project_status()` 用 `document_dir` 把它交给界面
  （「另存为」那一屏要回答「存到哪」，审计 T04）——前端自己拼
  `<项目>/tavottofile` 抄不到下面这两条分支。旧位置（项目 `canvases/`、项目同级
  `<项目名>-exports/`、数据目录 layouts/ 与 layouts/_versions/）只读兼容、
  合并列出，重名以 tavottofile 为准；**素材扫描的 EXCLUDE_DIRS 必须含
  tavottofile**，否则导出成图会混进素材面板。autosave / styles 等
  跨项目或内部机制仍留在数据目录。
- **文档类落盘只有一份实现：`engine/atomicio.py`（ADR 0023，2026-08-29 定版）**。
  tmp → flush → **fsync 文件** → `os.replace` → fsync 目录 → 失败清 tmp +
  抛 `AtomicWriteError(code, …)`；序列化走 `allow_nan=False`，
  **NaN/∞ 在碰磁盘之前就被拒**（写出去的 `NaN` 不是 JSON，浏览器
  `JSON.parse` 读不动，表现为"这份文档打不开"而文件看着好端端的）。
  新增写入点一律调它，**再抄一遍 tmp+replace 视为回退**。
  schema 判据同理只有 `engine/documents.py` 一份。
  收纳目录里 Tavotto 自己的文件（现只有 `_styles.json`）由
  `RESERVED_DOCUMENT_FILENAMES` **枚举**——不要改成「`_` 开头」的前缀规则，
  画布名净化会把 `（图一）` 变成 `_图一_`，前缀规则会把用户的文档藏起来。
- **排他目录发布**：`atomicio.publish_directory_if_absent`仅接同父不同名的真实自有目录，
  拒symlink/reparse；调用方先受检复制并同步各文件/子目录，叶子再同步stage、复核源身份，
  委托原排他rename，最后同步parent。默认文件发布调用/IO不变；POSIX目录不尝试硬链接，
  不回退覆盖式rename。碰撞保原FileExistsError，真实故障沿AtomicWriteError；parent同步失败
  可能已发布，调用方须依目录身份保留目标/回执，不能递归伪回滚。该叶子不复制或清理目录。
- **读侧也有一道非有限数闸：`documents.loads_document()`**（2026-09-03，
  issue #222）。写侧挡住的只是**我们自己写出去的**那份；外部工具往
  `tavottofile/*.json` 写一个 `NaN` 之后，Python 的 `json.loads` 照读不误，而
  每一份经过后端交给浏览器的字节都会在 `JSON.parse` 上炸掉。**读文档的 JSON
  一律走它**，新增读取点别用裸 `json.loads`。消费点四个拒（命名画布 GET /
  自动保存 GET / 版本时间线 / 项目包里的 layout.json），两个**有意**只当
  「读不出来」（`document_summary` 的契约就是读不出来 → `None`，它的两个调用方
  都不能抛；`_autosave_newer_than` 是旧前端的兜底，不能因为一个坏槽位把用户
  锁死）。`DocumentError` 是 `ValueError` 的子类——`except ValueError` 会把这道
  闸整个吞掉，`_load_versions` 里单独 re-raise 就是为这个（吞掉的后果不是
  少显示几条：下一次创建检查点会在空列表上整份写回）。
- **「另存为」与自动保存共用同一份冲突判据**（2026-09-03，issue #222）：
  `POST /api/layouts/<name>?base_revision=…` 走 `_revision_conflict` +
  `REVISION_ABSENT` + `_external_change`，**不许写第二份**。锁是
  `_document_lock(path)`（按落盘路径，自动保存 / 另存为 / 槽位清理共用，
  **不可重入**）。GET 交出 `X-Tavotto-Revision` 且不用 `send_file`
  （句柄会在 Windows 上挡住下一次 `os.replace`）。
- **⌘S 写回项目里绑定的排版文件（ADR 0096，2026-09-26）**：同一个端点
  `POST /api/layouts/<name>?target=project`，**不加端点**。这一档必须开着项目
  （`current_ctx()`，否则 409 `no_project`——不许静默退回数据目录）；冲突判据、锁、
  原子写与另存为一字不差（`_revision_conflict` / `_document_lock` / `atomicio`）。
  开着项目时（含另存为）落点只能在当前项目的 `tavottofile/` 下：`_project_layout_target`
  拒两种符号链接逃逸（`tavottofile/` 解析后不在项目根内、目标文件本身是链接）→
  400 `layout_outside_project`；只读卷 / 无写权限（EROFS / EACCES / EPERM）→ 403
  `layout_read_only`，磁盘满照旧 atomicio 的通用映射。响应带 `name` / `file`（相对项目根），
  GET 开着项目时带 `X-Tavotto-Layout-File`（⌘S 会写到哪，百分号编码）——界面原样说，不自己拼。
  **自动保存仍只写数据目录的槽位**，不写项目文件。
- **写前判冲突读修订号用 `atomicio.existing_revision()`，不用 `content_revision()`**（#674 评审 P1）：
  后者把「不存在」与「读不出」（mode 000、被别的程序独占）揉成一个 `None`，而 `_revision_conflict`
  对 `None` 的解释是「被删了，放行重建」——一份读不出却换得掉的文件会被闭眼整份覆盖。
  `existing_revision` 只有 `FileNotFoundError` 才回 `None`，别的 `OSError` 抛出，调用方回 409
  `revision_unreadable`、一个字节都不写。另存为 / ⌘S（`api_layout_save`）与自动保存 PUT 两处都走它；
  `content_revision` 只留给「报告当前修订号」这类不据以写入的读。
- **自动保存槽位有磁盘兜底上限**（2026-09-03，issue #221）：
  `AUTOSAVE_KEEP_SLOTS=64` / `AUTOSAVE_KEEP_BYTES=64 MB`，写完之后在锁**外**
  跑 `_prune_autosave_slots()`，按 mtime 从旧到新删，永不动刚写的那一份，
  删之前在该文件自己的锁里重新 stat 一次。~~这是兜底不是主路径：主清理在
  前端（被 `tavotto.docIndex` 的 12 条挤出去的槽位会被 DELETE 掉）~~——**2026-09-29 起（#715 PR-B，
  ADR 0108（`docs/adr/0108-desktop-stable-origin-and-backend-session-state.md`）§三）这是唯一的清理路径**，前端不再按本机索引删磁盘；参与清理的只有**记着归属**的槽位
  （见下条），各项目「上次开着的」那一份永不删，**没有归属的旧槽位在找回入口（PR-C）上线前一个都不删**
  （它们多半正是换了 origin 之后界面上找不回来的排版）。
  同一次清理顺带跑 `atomicio.reap_orphan_tmps()`（QA 2026-09-24 SCI-05-B2）：
  进程被杀在 `os.replace` 之前留下的 `<doc>.json.<pid>.<n>.tmp` 不以 `.json`
  结尾，上限永远数不到。判据是**年龄 + pid 已死**——一小时内一律不碰、pid
  还活着的留到一天后（pid 复用 / Windows 量不了存活）；只认 `_next_tmp` 起的
  名字，命名与判据同在 `atomicio`。
- **会话状态以后端为准（2026-09-29，#715 PR-B，ADR 0108（`docs/adr/0108-desktop-stable-origin-and-backend-session-state.md`）§三；2026-10-11 文档恢复可靠性修复重写了读取、last 语义、锁与提交顺序，ADR 0120（`docs/adr/0120-document-restore-verdict-and-recoverable-last.md`），见下）**：`engine/layoutsession.py`（纯标准库）
  是 `data_dir()/state/layout-sessions.json` 的唯一读写方（`state_path()` 是位置的唯一出处）。内容：
  `projects{<normalize_path_identity(项目路径)>: {last, prev, pending}}`、`no_project{...}`（没开项目那一组）、
  指针形状都是 `{doc_id, name, at}`；`owners{槽位 doc_id → 项目键 | null}`（`null` = 确认过写它时没开项目；没有这条 = 不知道，两档）；
  `owner_revisions{槽位 doc_id → 记归属那一刻正文的 revision}`。`version` 仍是 1（新字段是增量，不升级、不碰正文 schema）。
  #674 的 `bindings` 未进 main，本文件暂不带。**不能放**在 `_autosave/`（清理按文件名扫）、`layouts/`、
  项目 `tavottofile/`（会随项目拷到别人机器上）。
  - **读取结果四档**（`_read()` 回 `(state, status)`，`GET /api/layout-session` 的 `index`）。**这一档替代了旧合同
    「读坏了（不是 JSON / 形状不对 / 非有限数）当成空的，下一次写整份替换」**——那条把「文件不存在」「读不动」
    「字节坏」「来自更新版本」「某个无关字段是 NaN」全部当成首次空状态，下一次普通写入就把一份可能完整、
    可能更新的文件整份抹掉：`missing`（不存在，首次空）；`ok`（逐字段清洗：坏字段丢、其余保留，**未知顶层键与组内
    未知键原样保留**，非有限数的字段丢弃；NaN 用标准 `json.loads` 接受，不再整份作废）；`recovered`（字节坏，原字节先存
    `state/layout-sessions.corrupt-<ms>-<sha256 前 12 位>-<primary|bak>.json`——**按内容命名**，主文件与 bak 同一毫秒里字节不同也不会互相替换；
    相同字节只存一份、写完读回核对相等才算保存成功、最多留 3 份且不裁本次加载存下的——再用 `layout-sessions.json.bak`，
    那是每次成功写主文件后同 payload 再写的一份）；`degraded`（读失败拿不到字节 / 字节坏且无可用 bak / `version` 比
    `FORMAT_VERSION` 新）。读失败与未来格式**拒绝一切写回**——**未知 `version` 永远不用 v1 覆盖**，这条对 `.bak` 同样成立：主文件坏而 bak 是更新版本写的、主文件不在而 bak 是未来格式或读不动、
    bak 存在却读不动，都按只读处理（正文照存，`metadata:"failed"`）；坏 bak 的字节同样先留副本、留不下就拒写；导出默认值文件来自未来 / 读不动时也不整份替换（503）（`SessionStateUnavailable`，是 `OSError`）：拿空状态覆盖一份
    可能完整的文件，就是把一次读故障变成永久丢数据；字节坏的那种原字节已备份，写入放行。degraded 时槽位清理一个都不删
    （不知道谁受保护就当都受保护）。备份都在 `state/`，不在 `_autosave/`。只读路径（`read_status` / `read_readonly`，
    候选发现与 `/owner` 用）**不写任何东西、不拿锁**（主文件只被整份原子替换，读到的永远是完整的一份）。
  - **`last` 只指向可恢复的正文**（R3）。每组三个指针：`last`（durable）、`prev`（上一份 durable，容量 1）、
    `pending`（最新的打开意图但槽位还没有有效正文，容量 1，**不替换** durable last）。`PUT /api/layout-session/last`
    在槽位的 `_document_lock` 里验「槽位文件在、能读、过 `loads_document` + `validate_document`」（`_slot_recoverable`）再调
    `set_last(..., slot_ok=)`：通过 → durable（`prev ← 旧 last`、`last ← 新`、同一份的 pending 清掉），回
    `{ok, last, state:"durable"}`；不通过 → 只写 pending，回 `{ok, last:<durable 或 null>, pending, state:"pending"}`。
    **pending 只是「最新的、还没 durable 的意图」**：任何更晚的 durable `set_last(X)` 都清掉本组 pending（不管它指着谁），
    否则 A→B(pending)→A(durable) 之后 B 迟到的首次保存会晋升过期的 pending、把 last 顶回 B。
    **durable `set_last` 总是前进 `at` 并写盘，哪怕 (doc_id, name) 没变**：`at` 是前端判「谁的打开更晚」的排序令牌，再次明确打开
    同一份排版必须让它严格前进（`max(now_ms, 本组 last/pending 的 at + 1)`，同一毫秒、时钟回拨也一样），否则别的 profile 里还悬着的
    pending 会被当成更新的而翻案；`prev` 不动、pending 清掉。晋升（`commit_slot`）同样严格前进。「同值不写」只留给每次保存都调的
    `record_owner` / `commit_slot` 的归属部分（以及同值的 pending 意图）；前端自己对每帧推送去重。
    **验证到发布到底串行了什么**：验槽位 + `set_last` 在槽位的 `_document_lock` 里（**只在本进程内**互斥），所以本进程的清理 /
    DELETE 删不掉验证与发布之间的槽位；**别的进程**（共用数据目录）不持这把锁，所以发布时 `set_last` 在**跨进程会话锁**里
    再调一次 `slot_check`（app 传的只 stat 回调：文件还在、`(mtime_ns, size)` 与验证时一致），不是 → 只记 pending。
    所有槽位删除（`DELETE`、清理的 `remove_slot_unless_protected`、教程重置的 `delete_slot`）都把 unlink 放在跨进程会话锁里，
    于是「删」与「发布」被同一把跨进程锁串行：要么发布先到（删除方在锁里重判保护，删不掉），要么删除先到（发布看到槽位没了，记 pending）。
    没有新增锁；`slot_check` 不解析正文、不取文档锁。验证（读整份正文）本身仍在会话锁之外，所以内容被别的进程**改成另一份有效正文**
    不在这里拦（签名变了会降级为 pending）。
    旧前端的正常顺序是「先 PUT last、再首次自动保存」：early PUT 只记 pending，**首次正文落盘成功的那个提交**
    （`commit_slot`）把匹配的 pending 晋升为 last——不留悬空 last，也不靠前端记得补一次。`last` 对旧前端仍是 `{doc_id,name,at}`。
    **不要**只在 `set_last` 里「文件不存在就拒绝」：那会让旧前端的 early PUT 永远失败又不补记。
    `GET` 回 `{last, prev, pending, index}`，三个指针回之前都按 owner 冲突过滤；`prev` / `pending` 指着的槽位不在时照常回。
    逐指针在槽位文档锁内重读会话状态和正文 revision；指针换槽位就释放旧锁、重试新槽位（连续变动时报
    `index: degraded`）。旧记录没有 `owner_revisions` 时也不能拿 F 的旧归属快照拼上 G 的新正文 revision。
    清理保护 = 各组 `last` + `prev` + `pending`。
  - **owner 与正文按同一个顺序提交**（R6）。`PUT /api/autosave/<id>` 在**同一把 `_document_lock(p)`** 里：判冲突 → 写正文 →
    读 revision → `commit_slot(doc_id, key, revision)`（记归属、记 `owner_revisions[doc]=revision`、晋升 pending）。
    以前归属在锁外记，F/G 两个项目同 docId 交错保存时旧请求的归属回调能晚于新正文落地、把归属改回旧项目（A24）。
    **锁顺序全局固定：槽位文档锁 → 会话锁**——自动保存、DELETE（先文档锁再 `forget_documents`）、清理
    （`remove_slot_unless_protected`）、`PUT last`（验槽位 + `set_last`）、教程重置（`forget_project` + 删槽位）都按它；会话锁内不读
    任何正文、不调需要文档锁的函数，要槽位 revision 的判据由调用方在锁外算好传进来。`_document_lock` 不可重入，别在持锁时再取别的路径的锁。
    响应 `{ok, saved_at, revision, metadata}`：`metadata` = `"ok"` / `"failed"`（会话状态写不进去或读不到可信状态）/
    `"owner_unknown"`（pj 已失效，按保数据合同照写正文，归属不知道、不记、不晋升）。**元数据失败不让这次保存失败，
    revision 一律如实回**（客户端据此推进基线，不会误以为正文没写进去）；下一次保存补记。**这不是跨文件崩溃事务**：
    进程死在「正文已落盘、元数据未提交」之间时，靠 `owner_revisions[doc]` 与槽位当前 revision 对不上来识别——
    该归属是**待核对**（`owner_is_stale`），处理**不对称，安全优先**：① `set_last` 里待核对**不解除**冲突——记录说是别的项目的，
    正文被外部动过也不能让另一个项目认领（409 `layout_foreign`）；② `view_for` / `last_for` 对**任何**项目（含老主人）都不回待核对的指针，
    不自动恢复未核对的内容；③ `/owner` 回 `{owner:"unknown", evidence:null, verified:false}`，不再往下翻旁证；
    ④ 候选发现里它对每个项目都只出现在 `scope=unknown`（用户可「恢复副本」）、绝不在 `scope=project`。
    **没有 `owner_revisions` 的旧记录（含 scalar owners）按现状信任**——兼容边界，不是保证。比较 revision 在会话锁外读文件。
  - **槽位文档锁也是跨进程的**（2026-10-11）：`_document_lock(path)` = 64 个条带，每个条带 = 进程内线程锁 +
    `state/doclocks/<条带>.lock` 上的文件锁（与会话锁共用 `layoutsession.acquire_file_lock`）。两个后端进程共用一个数据目录、
    从同一个 `base_revision` 保存同一个 doc id 时，「判冲突 → 写正文 → 读 revision → `commit_slot`」是同一个原子段，
    恰好一个成功、另一个 409，归属与 `owner_revisions` 对着最终正文（否则 F 会把 G 的正文 revision 记成自己的）。
    条带下标 = `zlib.crc32(NFC+casefold 后的文件名)`（组合 / 分解写法在 macOS 上是同一个文件）：**不能用 `hash()`**（每个进程盐不同），也**不能用整条路径字符串**（符号链接别名 / 大小写不同的写法
    会让两个进程选到不同的锁）；锁文件（会话锁与条带锁）都在**数据目录 realpath** 下的 `state/`，别名写法打开同一个文件。自动保存 / 另存为 / ⌘S / 槽位清理 / DELETE / `PUT last` /
    教程重置共用这套条带；**没有任何一条路同时持两个文档锁**（不可重入；清理一次只处理一个槽位），
    顺序全局是 文档锁 → 会话锁。等不到（30 s，要大于持锁方的合理停顿）抛 `SessionStateUnavailable`，
    `@app.errorhandler` 回 503 `session_unavailable`，此时一个字节没写；清理里等不到只是这次不清它。锁文件打不开
    **失败即关门**：锁文件打不开、文件系统拒绝咨询锁（ENOLCK / EOPNOTSUPP / EINVAL）也抛 `SessionStateUnavailable`，不退回只有线程锁。
    真两进程回归 `tests/test_layout_recovery_backend.py`（POSIX）；Windows 的 `msvcrt` 腿只有 CI 验证。
  - **版本时间线的互斥也是跨进程的**（审计 D0，2026-10-20）：`/api/versions/<docId>` 的创建 / 改名 / 复制 / 删除 / 缩略图都是
    「读整份 → 改 → 整写」，原来只被进程内 `threading.Lock()` 保护，两个后端进程共用数据目录时后写的整份盖掉先写的（两边都 200）。
    现在统一经 `_versions_lock(doc_id)` = 复用 `_document_lock`，锁名取 `<doc_id>.versions`（**不用 `<doc_id>.json`**：条带只看文件名，
    用 `.json` 会与同一文档的自动保存共用条带，版本追加几十 MB 的读写期间自动保存被堵）。不可重入，时间线端点持锁期间不取别的文档锁 / 会话锁；
    等不到（30 s）→ 503 `session_unavailable`，历史零写入。版本 id = `v<毫秒>-<6 位随机进程区分段><序号>`（仍匹配 `_VERSION_ID_RE`），
    并在锁内对已有 id 核重名（`_fresh_version_id`），撞了就重取、不覆盖。**这只修丢更新；整份写回的存储形状（#263）问题仍在。**
    回归 `tests/test_versions_multiprocess.py`（真两进程 POSIX；Windows 的 `msvcrt` 腿只有 CI 验证）。
  - **`/owner` 与候选发现读的是一致的快照**：两者都在**槽位的文档锁**里同时读会话状态（owners / owner_revisions / status，只读、不拿会话锁）
    和槽位正文——别的项目的保存（正文 + `commit_slot` 都在这把锁里）插不进两次读之间，否则会拿 G 的新正文去对还没有 G 归属的旧状态，
    把别人的槽位报成 this。`/owner` 回的 `revision` 就是这份快照里正文的。候选发现**按条目**各取自己的槽位锁、各读一次状态
    （页级快照做不到：它在条目被改写之前读的）；一页最多 `limit` 次短暂持锁。`/owner` 锁等不到回 `unknown`（`revision:null`），
    候选等不到整页回 503 `candidates_unavailable`——都不猜。这两个只读端点因此会建（空的）锁文件，锁文件是互斥基础设施不是状态。
  - **教程副本的发布与清理意图标记也是跨进程串行的**：`engine/tutorial.ensure_tutorial_copy` 在 `state/tutorial.lock`
    （`layoutsession.acquire_file_lock`，30 s）里跑。两个进程同时首次打开 / 重置教程时，副本 rename 与共享标记
    `state/tutorial-cleanup-pending` 的写入 / 失败回滚必须串行，否则一个进程发布后退出、另一个进程 rename 失败的回滚分支会把对方
    留下的共享标记删掉，下次打开就漏掉旧固定 ID 槽位的清理。拿不到锁回 409 `tutorial_locked`，绝不在无锁时继续。
  - **跨进程文件锁**（C2）：模块锁 `_LOCK` = 进程内线程锁 + `state/layout-sessions.lock` 的文件锁（POSIX `fcntl.flock`、
    Windows `msvcrt.locking`，纯标准库，非阻塞 + 10 ms 重试），多个进程共用一个数据目录时读改写不丢更新；拿不到（5 s）抛
    `SessionStateUnavailable`。**锁文件打不开 / 文件系统不支持咨询锁（网络盘等）时同样抛它，绝不退回只有线程锁**（会静默丢更新、互相覆盖）；
    每个进程只记一次日志。**数据目录所在文件系统不支持咨询锁 = 不支持的部署**：保存会可见地 503 失败（前端显示保存失败并保留本机崩溃副本），
    清理跳过、会话读回 `index:"degraded"`，而不是冒静默覆盖的险。导出默认值
    （`export-defaults.json`）不在这次范围，仍只有线程锁。`tests/test_layout_recovery_backend.py` 有真两进程交错回归（POSIX 跑过；
    Windows 的 `msvcrt` 那条腿留给 CI，本机未验证）。
  - 端点：`GET /api/layout-session`、`PUT /api/layout-session/last` 按 `_request_ctx()` 认项目（指名不存在的
    项目 409、没有项目 = `no_project` 组），回给前端的只有 `{doc_id, name, at}`、不带路径；会话状态写不进去 `PUT last` 回
    503 `session_unavailable`；导出默认值
    `GET/PUT /api/preferences/export-defaults`（`state/export-defaults.json`，只守「不大的 JSON 对象」，
    语义在前端 `lib/exportDefaults.ts`）。都在 ADR 0008 认证之下，错误码复用 `bad_request`。
  - `DELETE` 与槽位清理删掉的槽位，归属（含 `owner_revisions`）与指向它的 last / prev / pending 一并清；清理顺带丢掉磁盘上已不在的槽位的归属。
    清理删每个槽位走 `remove_slot_unless_protected`：**在会话状态锁里**重判「此刻是不是某组的 last / prev / pending」→ 删 → 忘掉记录，
    `protected_doc_ids()` 只是快照，并发的 `set_last` 能在快照与删除之间把旧槽位记成新 last（或推成 prev）。
    丢「磁盘上已不在的槽位」的归属同理：扫描快照只挑候选，`drop_owners_not_in` 在锁里当场再看文件
    （快照之后并发保存刚建出的槽位不丢归属，否则它永远不进配额）。**没有归属的老槽位仍一个都不删**——候选发现没有放宽这一条。
  - 教程重置 / 资源升级换副本（`_clear_tutorial_local_state`）一并清教程项目的 last / prev / pending 与教程画布槽位的归属（ADR 0039 §5）。
  - **别的项目的排版不认（2026-10-01，#715 Windows 真机验收 P1）**：判据只有 `owner_conflict(state, doc_id, key)`
    （owners 里**确知**是别的项目 / 别的组；没有记录 = 不知道，不算；待核对的归属也不算「确知」）。`set_last` 撞上它抛 `ForeignLayoutError`、
    一个字节不写，端点回 `409 layout_foreign`（不显示给用户，前端据此作废本机缓存那一条、不重推）；`last_for`
    读到指着别的项目槽位的旧记录（修复前写下的）回 `None`。前端的同一条判据是 `web/src/lib/docOwnership.ts`
    （按本机「最近文档」索引），两侧互为纵深。自动保存 PUT 照旧按 pj 改记归属，前端负责只在用户真改过 / ⌘S 之后
    才以当前项目的 pj 写（只打开不算，#773 Codex P2）；失效的 pj 记成「不知道」，不覆盖已有归属。
    前端本机不知道归属时的证据端点 `GET /api/layout-session/owner?doc_id=`（`_layout_owner_evidence`），回
    `{owner, evidence, verified}`：owners 有记录即定论（别的项目 = `other`，素材再像也不翻案；`verified` = revision 对得上或旧记录）→
    当前项目 `tavottofile/versions/<id>.json` → 槽位里非 runtime 面板的素材**全部**在当前项目目录内且至少一张
    （这两条 `verified:false`）；都没有回 `unknown`。只读、不改任何记录。
  - **候选发现 `GET /api/layout-session/candidates?scope=project|unknown&limit=50&cursor=`（P03，只读）**：索引丢了 / last 指不到时，
    从 `AUTOSAVE_DIR` 的**真实槽位**里列出能找回的（不要求先知道 docId，owners 只是线索：不存在的文件不列）。
    只枚举 docId 形状的 `*.json` 普通文件（不跟随符号链接、不接受任何路径），按 mtime 新→旧、游标分页（游标是
    `(mtime_ns, 文件名)` 的不透明编码）；每页最多读 `limit`（≤100）个文件、字节有预算（至少读一个），单文件 > 32 MB 不读、
    `status:"too_large"`；目录条目超过 20000 时只看了目录原序的前一部分，响应带 **`truncated: true`**（游标另带第一页的目录指纹，后面的页重算，对不上——翻页期间有槽位被保存 / 新建 / 删除，
    按 mtime 的顺序变了、某行可能被漏——也标 `truncated: true` 并照常返回这一页，之后每一页都保持）（`next_cursor` 为 null 也不等于列完了，
    调用方不得据此自动采用唯一候选或判定为空）；一页里被 scope 过滤掉的不返回，所以一页可能不满、`next_cursor` 仍非空——调用方循环翻页。
    每项 `{doc_id, name, updatedAt, mtime, revision, schema, status: ok|invalid|too_new|too_large, canvases, objects, owner:
    this|unknown, evidence, runtime:[{script,figure}], panels:[…], sketch?}`（非 ok 的 `canvases`/`objects` 是 `null`，不冒充 0；
    `runtime`/`panels` 最多 5 个、绝对路径只留最后一段；`sketch` 复用时间线的 `_version_sketch`，不带 overrides / 脚本）。
    `scope=project` 只回 `this`（owners 可信记录 / 时间线 / 素材全在项目里），`scope=unknown` 只回 `unknown`（含待核对的归属）；
    `owner=other` 两个 scope 都不回。**runtime 文档**（`runtime:<脚本>#<图名>`）没有 owners / last / 时间线就只出现在 `unknown`——
    `/owner` 与这里都不为 runtime 面板作证，也不凭脚本同名 / fingerprint / mtime 自动归项目。一个坏文件只让自己
    `invalid`；不写任何东西、不回路径、不运行脚本。**恢复副本不加端点**：前端读源正文（带 revision）→ 用户确认时再读一次比对 →
    固定目标 id 走正常 `switchDocument` + 自动保存，源槽位与源归属不动。
  - 看护 `tests/test_layout_session.py`（模块与旧端点）与 `tests/test_layout_recovery_backend.py`（读取四档、last/pending 晋升、owner 同序、
    清理保护、候选发现、跨进程）。
- **显式计算图表格式（ADR 0182）**：v4端点复用上述view/锁/认证/原子写，目录和namespace由documentformat唯一派生，不给旧端点发现schema4，也不借旧版本目录。v4布局清单的document_dir是后端派生的实际命名保存目录，前端不拼路径；legacy清单形状不变。
  同一会话文件的document_open_intents在每项目保存共享sequence/latest/witness，只有明确打开分新序，普通保存不能让旧意图变新。witness只覆盖该项目两个namespace实际last/pending；旧程序混写导致不一致时stale，普通写不洗白。view_for默认旧四键，只有显式include_open_intent=True（HTTP query=1）增加open_intent；ok含共同snapshot及latest，untracked/stale无猜测latest，unavailable无猜测snapshot。客户端两GET快照一致才自动采用共同latest，不能比较两组at/mtime。自动repair只确认当前同地址意图或晋升，不能创建新序；被取代回superseded、缺证据回unconfirmed且零写，future/非法账本仍拒写。明确请求跨格式共用发送顺序；保存队列/revision仍按格式隔离。完整开关、线格式和旧客户端边界见ADR 0182，专项看护见本页头部。
- **命名文件关联（ADR0096/0108）**：`layoutsession`在既有会话文件/锁/atomicio里以(project,namespace,working docId)保存版本化`document_project_files`；不进schema3/4，不以标题/mtime/portable project.id猜目标。`read_project_file`核实际AS revision，外改/旧客户端混写使轴unknown/dirty且保原named revision；未知future只读unavailable、修改503，不洗白。用户轴为每editor实例身份+序数，只比较同actor顺序或精确base_axis，派生回执不推进轴、不同actor不能取max。
  显式GET `/api/layout-session/project-file/<doc_id>`无缓存；reserve捕获规范name/file、真实已刷AS revision/axis及绑定代次，只有pending intent变，不提前绑定；named POST可选`working_doc_id`/`binding_intent`，正文成功后按完整服务器票据CAS关联；显式confirm在实际named锁内核读到的revision；cancel仅清匹配当前seq的pending。旧请求缺省URL/body/实参数量/响应不增字段；v4别名共用view/认证/namespace。
  AS可选`project_file_edit`是闭式axis/base_axis/base_revision，调用公共`validate_project_file_edit`，同文档锁内以实际旧revision prepare→原子写正文→同证据commit_slot。拒绝轴/未知谱系409 stale_write且正文零改；坏输入400。锁序始终document→session，禁止嵌套两个不可重入document stripe锁。正文成功而元信息失败如实回revision并单列project_file unavailable/metadata failed，不能报正文失败诱导覆盖；失效pj仍沿原保正文合同但不记未知归属。显式打开应在首次AS之前捕获安装基线票据，不能用等待期间已编辑的轴冒充named同步基线。看护`tests/test_layout_project_file_binding.py`、`tests/test_layout_project_file_http.py`。
  既有“重新加载/明确覆盖”的用户裁决可显式追加`project_file_recovery={revision,expected}`：revision为此次实际观察的AS正文（真实缺席为null），expected为原样完整GET view，含旧tracked revision/unknown。同AS锁内以实际正文重算同一view，规范JSON严格型别CAS防bool/int/float混同及绑定ABA；新轴必须是不同于current/named synced的非null显式分支。只有本次正文成功后才能提交新轴，保named目标/revision/synced，仍dirty；自动重试不得重捕proof或清掉query。写前拒绝409且零正文，后写元信息/目标竞态失配仍200+failed并保真实body revision；正常同址pending晋升继续沿commit_slot，不分新打开序。无edit/无有效项目的proof400，future503，缺省旧路径不变。看护`tests/test_layout_project_file_recovery_http.py`。
- 前端文档模型的对应字段（lockedGids / layoutGroups 等）见 `web/AGENTS.md`。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 版本上限条数 + 字节两条；命名节点不参与裁剪、超上限拒绝再命名（ADR 0101）
- 文档落盘只有 `atomicio`（NaN/∞ 落盘前拒）、读侧同样有闸
- `project_layout_dir()` 是收纳规则唯一出处
- 另存为与自动保存共用 `_revision_conflict`、锁不可重入、GET 不用 `send_file`
- 槽位清理顺带 `atomicio.reap_orphan_tmps`（只认 `_next_tmp` 的名字，年龄 + pid 已死，一天后只看年龄）
- ⌘S 写回项目（ADR 0096）：`target=project` 必须开着项目、落点只在 `tavottofile/`（符号链接逃逸拒）、只读卷有自己的 code

## 速查表原要点（#815 精简时迁入，原文照搬）

- 文档落盘只有 `atomicio`；`project_layout_dir()` 是收纳规则唯一出处；会话状态以后端为准、槽位清理只动有归属的
