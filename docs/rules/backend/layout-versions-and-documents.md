# 布局版本、项目文件收纳与文档落盘

> 原文出自 `src/tavotto/AGENTS.md`「布局层（R18）」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **布局版本**：`/api/versions/<docId>` 系列，快照存 `layouts/_versions/`，
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
- **论文样式**：`/api/styles`（`layouts/_styles.json`）；前端按角色映射成
  override / 标注属性一次 commit 应用，绝不写回源文件。
- **项目包**：`POST /api/package` 打 zip（layout+素材+脚本+sha1 清单）；
  `POST /api/package/open` 检视（缺失/sha1 漂移），素材永不自动写入图库。
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
- **会话状态以后端为准（2026-09-29，#715 PR-B，ADR 0108（`docs/adr/0108-desktop-stable-origin-and-backend-session-state.md`）§三）**：`engine/layoutsession.py`（纯标准库）
  是 `data_dir()/state/layout-sessions.json` 的唯一读写方（`state_path()` 是位置的唯一出处）。内容：
  `projects{<normalize_path_identity(项目路径)>: {last{doc_id, name, at}}}`、`no_project{last}`（没开项目那一组）、
  `owners{槽位 doc_id → 项目键 | null}`（`null` = 确认过写它时没开项目；没有这条 = 不知道，两档）。
  #674 的 `bindings` 未进 main，本文件暂不带。**不能放**在 `_autosave/`（清理按文件名扫）、`layouts/`、
  项目 `tavottofile/`（会随项目拷到别人机器上）。
  - 写一律 `atomicio`，模块锁管本进程读改写；**同值不写**（`record_owner` 在每次自动保存都会被调）。
    读坏了（不是 JSON / 形状不对 / 非有限数）当成空的，下一次写整份替换——这是「上次停在哪」的提示，
    不值得让任何一次保存失败。
  - 端点：`GET /api/layout-session`、`PUT /api/layout-session/last` 按 `_request_ctx()` 认项目（指名不存在的
    项目 409、没有项目 = `no_project` 组），回给前端的只有 `{doc_id, name, at}`、不带路径；导出默认值
    `GET/PUT /api/preferences/export-defaults`（`state/export-defaults.json`，只守「不大的 JSON 对象」，
    语义在前端 `lib/exportDefaults.ts`）。都在 ADR 0008 认证之下，错误码复用 `bad_request`。
  - `PUT /api/autosave/<id>` 写成功后按 pj 记归属（`_record_autosave_owner`）：pj 指名的项目已经不在时**照写不拒**
    （那是用户的工作），归属记成「不知道」即不记；记不下只打日志。`DELETE` 与槽位清理删掉的槽位，
    归属与指向它的 last 一并清；清理顺带丢掉磁盘上已不在的槽位的归属。清理删每个槽位走
    `remove_slot_unless_protected`：**在会话状态锁里**重判「此刻是不是某组的 last」→ 删 → 忘掉记录，
    `protected_doc_ids()` 只是快照，并发的 `set_last` 能在快照与删除之间把旧槽位记成新 last。
    丢「磁盘上已不在的槽位」的归属同理：扫描快照只挑候选，`drop_owners_not_in` 在锁里当场再看文件
    （快照之后并发保存刚建出的槽位不丢归属，否则它永远不进配额）。
  - 教程重置 / 资源升级换副本（`_clear_tutorial_local_state`）一并清教程项目的 last 与教程画布槽位的归属（ADR 0039 §5）。
  - 看护 `tests/test_layout_session.py`。
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
