# 布局版本、项目文件收纳与文档落盘

> 原文出自 `src/tavotto/AGENTS.md`「布局层（R18）」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

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
  无 override 静态源的精确 hash 配对授权另见 `export-pipeline.md`；项目包打开仍只检视、不自动安装资产。
  看护：`tests/test_svgstore.py`、`tests/test_export_svg.py`、`tests/test_export_request.py`、
  `web/src/store/figureSnapshot.test.ts`、`web/src/store/figureSnapshotInvalidation.test.ts`、
  `web/src/store/projectSave.test.ts`、`web/src/lib/autosave/diskWriter.test.ts`。
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
