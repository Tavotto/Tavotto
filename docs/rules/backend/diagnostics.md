# 诊断包

> 原文出自 `src/tavotto/AGENTS.md`「诊断与排障」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- `engine/diagnostics.py` 出**一键诊断包**（`GET /api/diagnostics/bundle`）：
  版本 / 系统与编码 / 安装方式 / 数据目录 / 渲染解释器 + matplotlib /
  AI CLI 探测 / 项目概况 / 最近错误 + app.log + 用户配置。
  **密钥与个人路径必须先脱敏再交出去**（用户会把它贴进 issue 或发到群里）。
  `recent_projects` / `projects` **只留条数**：那是用户所有课题的名字与路径，
  排障一次都用不到（当前项目在 report.json 的 project 段里）。
- **包里的日志取自出门版 `cache/diagnostics.log`，不读 `cache/app.log`（QA 2026-09-24 REL-05-B1 / B2）**：
  `LOG.error("引擎渲染失败: %s: %s", stem, exc)` 格式化之后，用户脚本 raise 的文字与模板长在同一行，
  事后分不出哪段是用户的字；访问日志带着查询串，`worker 启动: fig_priv.py` 带着脚本名。所以在**写入那一刻**
  分：`app.setup_logging` 在完整的 app.log 之外再挂一个 handler，`ExportLogFormatter` 只信模板
  （`record.msg`，`tavotto*` 与 werkzeug 的 logger；其余 logger 正文 `…`），参数按类型换形——数原样；
  异常只留类型 + 错误 code（+ errno），**message 不出门**；绝对路径括成 `⟦…⟧`，出包时由 `_export_log_lines`
  换成 `_path_fact`（与 report 同一个 `<project:…>`）；文件名样的走 `_shorten_path_text`；请求行只留方法 +
  Flask 路由规则（`_route_rule_of`，匹配不上的 `route:<哈希>`）；键是标识符、值全是数的 JSON 原样（渲染计时）；
  其余 `str:<哈希>`；`exc_info` 走 `evidence_lines`。
  **明文放行（用户拍板 #601：有实际参考意义的不哈希，没有的哈希）**：只有调用点用 `engine/logsafe.py`
  标过的参数明文，判据是值的**出处**而不是长相——`known(value, 常量集合)`（值必须是源码里某个闭集的成员：
  解释器来源 `pool.SOURCE_LABELS`、PDF 后端 `pdfbackend.BACKENDS`、导出状态 / scope / 格式、工作目录模式、
  依赖修复目标 `deprepair.TARGETS`、包操作 `PACKAGE_OPS`、HTTP 方法……不在集合里的值原样交回、照样哈希）、
  `known_each`（一组全在集合里才明文）、`route(request.url_rule)`（只收 werkzeug 注册出来的 `Rule` 对象）、
  `version(value)`（只由 ASCII 数字、点与 PEP 440 固定后缀组成）。数 / 布尔 / None 本来就明文。`Plain` 只能
  由这几个函数构造（私有令牌）。看护（`tests/test_diagnostics_log_privacy.py`）：每个放行值明文进包，
  **同一个调用位置换成带金丝雀的自由字符串照样哈希**（放行口不许变成后门）；AST 扫全部 `logsafe.*` 调用——
  `known` 的集合必须是模块常量或字符串字面量组成的元组 / 集合（`known(x, {x})` 红），`Plain` 不许在别处构造，
  `route` 只收 `request.url_rule`。新加的日志参数想明文：先找到（或建）它所属的常量集合，再用 `known` 包；
  找不到闭集的就是自由文本，让它哈希。异常的错误 code 仍按形状放行（小写标识符），是这里唯一的形状规则。
  **不升 bundle schema**（用户拍板）：report.json 的字段形状没变，变的只是字符串内容里哪些段明文。记录行形如 `<时间> <级别> <logger> | <正文>`——` | `
  是出处标记：`recent_errors` 只原样放行这种形状的 ERROR 行，别的形状只留「时间 级别 logger: …」、
  前缀凑不齐的整行不要。前提是 `tavotto*` 的模板都是字符串字面量：`tests/test_diagnostics_log_privacy.py`
  用 AST 扫全部日志调用（f-string / 拼好的串 / 变量当模板都红），同一文件对包里每个文件与复制诊断做
  金丝雀全文搜索（桩 worker + 真 worker 两条）。完整的 app.log 不变，留给用户自己看；report.json
  的 `paths.log` 仍指向它。
- **项目在哪、叫什么不出门**（2026-09-23 beta 诊断包实测：云盘目录名带邮箱、课题目录带人名，
  以前只把主目录换成 `~`，其余原样进 report.json / app.log / 复制诊断）：
  * `project_roots(project)`：当前项目 + 最近项目的根（原样、realpath、正斜杠三种写法，长的在前）
    在**所有**文本里换成 `<project:sha1 前 10 位>`，**先于主目录替换**（先换主目录项目根就认不出了）；
    同一个项目在 report / app.log / config.json 里是同一个记号。`build_report` / `build_bundle` 的每一处
    `_redact_text` / `_redact_obj` 都带上它，新加一处漏了就是新的泄漏口。
  * 兜底两条不依赖登记：`CloudStorage/<服务商>-<账号>` 的账号段换成 `acct:<哈希>`（服务商名留着），
    邮箱一律 `<email>`——没登记成项目的路径、日志里随口的一句也不带出账号。邮箱的判据是**含 @ 的 token 整个抹**
    （`_redact_emails`，#524 / #536 评审连续五轮，每轮都是在 token 里判断地址边界时漏一类）：token =
    连续的非空白字符；一行整个是 JSON 时只在字符串字面量内容里按空白切、结构原样；`@` / `＠` /
    `\u0040` / `\uff20` / `%40` 都算 @；`"quoted local"@x` 这种 @ 前引号为奇数的并到上一个引号。
    `(user@x.com),` 连括号逗号一起抹——**不许再在 token 内部判断地址从哪到哪**。只放行负面清单
    `_not_an_email`：@ 前只有开括号（装饰器 / 提及；引号不算——`'@x.com` 的引号就是本地部分，#540）、
    域名不到两段（`localhost`、`HEAD@{0}`；`[` 开头的地址字面量 `user@[IPv6:…]` 照抹）、
    域名是 ASCII 版本号（`numpy@1.26.4`、`jsdom@30.0.1/lib/…`）。看护含跨 24 个 Unicode 类别（含 ASCII
    标点）的随机抽样性质用例（`test_any_unicode_inside_an_address_is_redacted_whole`）。
  * `_project_section`：去掉 `name`；`figures_dir` 只剩记号，另给 `location`
    （`cloud_storage` / `non_ascii` / `has_space`——真实故障来自这三样，不来自名字）；导出 / 备份 / 文档
    目录与项目设置里的路径走 `_path_fact`：记号 / `~` 之后只有 `_KNOWN_SEGMENTS`（Tavotto 自己起的目录名、
    解释器布局、系统通用目录）原样，其余 `seg:<哈希>`——导出目录能被改到 `~/Desktop/<论文题目>/`。
  * `render.worker_logs` 取会话仍按**未脱敏**的 `figures_dir` 算 `cache_digest`（脱敏只在出口）。
  * 看护：`tests/test_diagnostics_bundle.py` 的「项目路径」一节——真打开一个云盘里的项目、真导出，
    对包里每个文件与复制诊断文本全文搜索，并反证记号与 `location` 确实写出来了。
  * README 里 project 段的字段清单**从这一份 report 的键生成**（`_readme(project_keys=…)`），不手写——
    手写的那份在 #536 评审时已经漏了一半。
  * 这次换形让 report.json 与 schema 2 的读法不兼容，所以 **bundle schema 升到 3**。以后 report.json
    再改字段的形状或语义（删字段、把路径换成记号），同样要升号——读包的人靠 manifest 分辨格式，
    不靠 Tavotto 版本号猜。后端 `BUNDLE_SCHEMA_VERSION` ↔ `web/src/diagnostics/types.ts` 同名常量
    是严格同源对（`test_bundle_schema_is_one_number_on_both_sides`）。
- **缺依赖的现场要进包（2026-09-28 Windows 实测，bundle schema 4）**：渲染报 `missing_dependency` 时
  错误响应里有模块名、体检过的系统候选（3.7.6 装了但版本不支持、WindowsApps 占位起不来）与修复 offer，
  以前诊断包里一样都没有，只看得到「3.7.6 不在支持范围」的片段，读的人以为内置 Python 坏了。现在
  `app._worker_error_payload` 在算完 offer 之后把**这份响应里已经算好的结论**记进
  `deprepair.note_missing_dependency`（进程内存、全局最多 `MISSING_DEPENDENCY_EVIDENCE_LIMIT` 条、不写盘、
  `reset_state()` 不清——用户点重试之后上一次为什么失败正是要看的），诊断包只取**当前项目**的
  （`recent_missing_dependencies`），写在 `project.missing_dependencies`。**不为诊断重新体检任何解释器**。
  当时谁在渲染由异常的产出点带上（`WorkerError.python_source`：`EngineWorker._error_of` 与
  `WorkerdWorker._to_worker_error` 两条控制面同一个答案）。出门换形在 `diagnostics._missing_dependency_for_export`，
  **字段是白名单**，值按出处放行：import 名只有标准库 / `_KNOWN_SITE_PACKAGES` / `depresolve` 的两张 curated
  表里的原样，其余 `mod:<哈希>`；脚本走 `_shorten_path_text`（与出门版日志同一个 `file:<哈希>.py`）；解释器来源
  认 `pool.SOURCE_LABELS`、体检 code 认 `projectenv` 的 `ERROR_*`、support 认 `SUPPORT_*`、版本认
  `logsafe.version`，不在集合里的 `str:<哈希>`；候选解释器路径走 `_path_fact`（`_KNOWN_SEGMENTS` 为此加了
  解释器的常见安装布局名；**只有候选解释器路径**（`_path_fact(..., interpreter=True)`）才认 `python3.EXE` / `Python37`
  这类固定词加数字的段（在 `bin/` / `Scripts/` / `WindowsApps/` / 盘符根 / `Programs/Python/` / 已原样的解释器目录之下）
  与纯版本号 `3.7.6`（在 `versions/` / `Versions/` 之下）；导出 / 备份 / 文档目录、项目设置、日志里的路径一律不认——
  那里每一段连同父目录名都可能是用户起的，叫 `Python37` / `12.34` 的目录可能是课题编号，照样哈希）。offer 的
  code / 目标 kind / 不可用原因 / 解析来源的闭集在 `deprepair`，而本模块不能 import 它（`deprepair` 的安装日志
  反过来用 `redact_text`，互相 import 会把它拉进登记过的环）——所以这几个值由**产出方**在记录那一刻按自家常量
  放行（`deprepair._evidence_value`），这里只再验一道形状（小写标识符或 `str:<十六进制>`）。看护：
  `tests/test_diagnostics_bundle.py` 末节（真走 `/api/engine/render` 出错 → 导出包 → 全文搜用户名目录 /
  项目路径 / 脚本名 / 私有模块名，并反证现场写出来了）。
- **诊断包 schema 2（ADR 0016，改前先读；schema 3 只改了 report.json 的 project 段，见上）**：老三件
  （report.json / app.log / config.json）名字与语义一个字节没动，新增
  `frontend-state.json` / `interaction-trace.jsonl` / `manifest.json`。
  `manifest.json` 自报三个 schema 版本——**读包的人不该靠 Tavotto 版本号猜格式**。
  * 前端状态只活在浏览器内存里，所以多了 `POST /api/diagnostics/bundle`
    收前端载荷；**老的 GET 原样保留**（出的包 `contains_frontend_state: false`）。
  * `engine/diagnostics_frontend.py` 是**服务端第二道校验**。理由与
    `/api/telemetry/event` 一致：这个端点接受请求体，白名单是结构性防线。
    两侧判据**刻意不同**——前端管「这种事件允许哪些字段」，后端管「任何字段的
    值只能是什么形状」+ 一张**扁平的字段名 allowlist**。后端**不复制**前端那种
    逐事件的表（迟早分叉）；两处同源对由
    `tests/test_diagnostics_bundle.py` 的两条 `*_match_frontend_*` 看护。
    `ui.unexpected_error`（PR A，ADR 0118 补充 2026-10-14）的字段只有 `source` / `error_name`（两个闭集枚举，`_ENUM_FIELDS`，
    与前端 `UNEXPECTED_ERROR_SOURCES` / `UNEXPECTED_ERROR_NAMES` 严格同源，`test_unexpected_error_enums_match_frontend`）和 `message_hash`；message / stack 原文
    不进这条通道，只走发送包 `detail-errors.json`（`sanitize_detail`）。
  * 身份字段（`*_hash` / `*_variant` / panel / file / session / version）
    **必须是 `前缀:十六进制` 的 hash**，gid 必须**小写开头**——光靠字符集挡不住
    `SUPER_SECRET_PAPER_TITLE_12345` 那种全大写下划线串。
  * 坏载荷（超限 / 畸形 JSON / 类型不对）**一律退化成不带前端那两个文件的包**，
    并在 manifest 记 `trace_truncated`。用户是来排障的，不该拿到一个 400。
  * **不写磁盘、不自动上传、不进 telemetry**。trace 只在用户点导出那一刻进 zip。（「不自动上传」的唯一例外通道是下面「发送问题反馈」：用户逐次确认才上传。）
- **报告要答得出「渲染进程死在哪一句」（#435）**：`recent_errors` 把每段 traceback 与
  它收尾的异常行配成一条（帧行不进——读的人要的是那一句，脱敏面也更小）；
  `render.worker_logs` 带**当前项目**最近 `WORKER_LOG_FILES` 份 `worker.log` 尾巴里的
  **证据块**（`evidence_lines` 只认两种结构块：Python traceback = 头 + `File "…",
  line N` 帧行 + 收尾的异常行；faulthandler 崩溃栈 = 头 + `Current thread` / 帧行 +
  `Extension modules`），块外的一切**一律略去、只留计数 `omitted`**——脚本 `print`
  的（哪怕长得像 `RuntimeError: …` 或 `[guard] …`，用户 stdout 也在这份日志里）、
  帧下面那行源码、引擎自己的标记行。**不按行首长相放行、块要完整才算**：traceback 块 =
  头 + ≥1 帧 + 合法收尾（`ExcType: …`），faulthandler 块 = 头 + ≥1 线程行 + ≥1 帧，凑不齐的
  整块按用户输出略去（一个 `print("Fatal Python error: …")` 不是通行证）；链式异常的两句
  连接语要**逐字**相同且夹在两段 traceback 之间；**收尾行只留异常类型**（`KeyError: …`）——
  用户 `traceback.print_exc()` 打出来的块结构与引擎的一模一样，来历分不出，能保证的只有
  message 不出门；**类型名本身也按闭集放行**：只有本进程 `builtins` 里的异常类原样
  （`_BUILTIN_EXCEPTIONS`，`__main__` 里定义的类打印出来与 builtins 一样不带前缀），点分名
  只留来自 `_KNOWN_SITE_PACKAGES` 的包名、其余哈希（`matplotlib.units.ConversionError` →
  `matplotlib.exc:<sha1 前 10 位>`；`__module__` 是用户能改的，`numpy.Patient_123Error` 也只剩
  `numpy.exc:…`），不带包名的一律 `exc:<sha1 前 10 位>`——`class Patient_123Error(Exception)`
  的名字是用户源码里的标识符（评审 #443 第九、十一轮）；`ImportError` / `ModuleNotFoundError` 只在 message 长成加载器那几种形状
  （`No module named 'x'` / `cannot import name 'a' from 'b'` / `DLL load failed while
  importing x`，名字是标识符）时保留**形状**——它们是普通公开异常类，用户 `raise` 的
  一样是这个类型；形状里的名字只在是标准库 / `_KNOWN_SITE_PACKAGES` 的**顶层名**时原样，
  点分名的余下部分与不认识的名字 `mod:<sha1 前 10 位>`（`No module named 'patient_123'`
  用户 raise 得出来，真缺的私有包名同样可能是项目术语，第十二轮）；哈希后的文件名只带
  `_KNOWN_EXTENSIONS` 里的扩展名（`.patient` 一个字不带）。**留下的每一行都是从解析结果重建的，不是原行过一遍替换**（评审 #443
  第七轮）：帧行只留 `File "<路径>", line N`——`in analyze_patient_123` 是用户的标识符，
  不带；文件名不论绝对 / 相对 / 虚拟（`exec(compile(src, "patient_123.py", "exec"))` 的
  相对名、`<string>`、`<frozen runpy>`）走同一条缩写 `_shorten_path_text`，不经只认绝对路径
  的 `shorten_paths`（第十轮）；崩溃头的故障名按 CPython faulthandler 的闭集放行（`Segmentation fault` /
  `access violation` / `code 0x…`…），`Py_FatalError` 的自由文本与用户 print 的一律 `…`；
  `Extension modules` 只留 `(total: N)`，名单里会有用户自己的 C 扩展名。路径缩写先整体
  处理引号里的（带空格的 `C:\Clinical Trial\…` 不能在空格处断），再处理裸路径；**出门的
  字符串没有一段是用户能起的名字**：`site-packages/<已知包>/…` 只多留一个来自闭集
  `_KNOWN_SITE_PACKAGES` 的包名，包名之后的子目录与文件名照样哈希
  （`…/site-packages/matplotlib/file:6ad788fb3e.py`——库的文件名公开可枚举，读的人拿包里的
  文件名逐个哈希就能对上；`/mnt/site-packages/numpy/private-study/patient.py` 出门只剩
  `…/site-packages/numpy/file:…py`）；`tavotto/engine/` 之后是引擎目录里**真实存在**的文件名
  （`_ENGINE_FILES`）才原样保留；其余一律 `file:<sha1 前 10 位><扩展名>`（README：文件名一律
  换成不可逆哈希）。不按「真实安装根」验：这个进程里未必装着 matplotlib（它在 worker 的
  解释器里），而路径分量的名字证明不了来历（评审 #443 第七、八轮）。
  会话 id 同样是哈希（`session:…`，目录名里带着脚本名）。扫描窗默认最后 400 行，
  但**至少回溯到最近一个崩溃头**（`_scan_start`：`all_threads=True` 一段能超过 400 行）。
  `recent_errors` 里配对的收尾句走**同一个** `_closer_for_export`（只留类型 / 加载器形状——
  「复制诊断」的文本会被贴进公开 issue，`ValueError: patient-123` 缩路径救不了），
  ERROR 行按出处放行（见上「包里的日志取自出门版」），出门版的再过一道路径缩写。**先扫 `WORKER_LOG_SCAN_LINES`（400）行再抽块、再按块截到
  `WORKER_LOG_TAIL_LINES`（`last_blocks_within`：最后那块再长也整块要）**——先按行截
  再抽块会把一段长崩溃栈截成没有头的帧行，状态机一条都不认。文件只读最后
  `WORKER_LOG_SCAN_BYTES` 字节且是 **seek 过去再读**（`_read_tail_bytes`），不是 `read_bytes()`
  整读再切——被脚本刷了几小时的 worker.log 能有几百 MB，整读会把 Flask 进程撑爆。**只看这一代**：
  worker.log 跨代追加，pool 在 spawn 前把起点落在旁边的 `worker.log.start`
  （`pool.start_log_generation`，两条控制面都在 Python 里量这个数），诊断包从
  `pool.log_generation_start` 之后读——不带边界会把上一代的 traceback 当成这一代的、新一代
  一个字没写就死时 `empty` 还报 False；排「最近三份」时起点文件的 mtime 也算，worker 没写字
  worker.log 的 mtime 不会动（第十四轮）。README 承诺包里不含
  脚本源码与数据，这条段落不许把它变成空话。三条边界（评审 #443）：**只取目录名哈希等于
  `pool.cache_digest(当前项目)` 的会话**（含 `_replay-…` 重放目录），没打开项目一份
  都不带——别的项目的脚本名与报错不跟着出门；**留下的行里绝对路径缩成
  `…/site-packages/包/模块.py` 或 `…/文件名`**（`shorten_paths`：D 盘、外接盘、
  `\\wsl.localhost\…` 上的路径 `_redact_text` 只认主目录、一个字都不动）；
  `empty` 标出「进程一个字没留下就没了」（硬崩溃的形状）。整段再过同一道
  `_redact_obj`。看护：`tests/test_diagnostics_worker_evidence.py`。
- **块外被略去的行留结构摘要（`[structure] …`，`_structure_summaries`）**：真实 Windows 诊断包
  `omitted: 4`、`tail` 为空——日志有内容但没有一段凑得齐 traceback 块（头被截在这一代起点之前、只剩
  一句裸 `ModuleNotFoundError: …`……），于是看不出死于什么。判据的主语：*worker 子进程*（含用户脚本
  stdout/stderr）写进 `worker.log` 的行，*导出那一刻*，按行的**形状**判。对 `_scan_evidence` 交出的
  `orphans`（块外的帧行与不缩进的行，**块内源码行不收**）按白名单抽：异常类型只认 `builtins` 异常
  （点分名取最后一段；Warning 家族与用户自定义名一律不出）；`No module named 'X'` 的 X 经
  `_import_name_for_export`（标准库 / 已知包原样、其余 `mod:<sha1 前 10 位>`，与 `missing_dependencies`
  同一个函数）；帧只出个数与分类（`user` / `engine` / `runtime` / `site:<已知包>` / `site:other`），
  **绝不出路径、文件名、函数名、源码**。其余仍略去并计入 `omitted`（原行不出，摘要是另给的）。最多留最后
  `STRUCTURE_MAX_SUMMARIES` 条，接在证据块后面，以 `[structure] ` 标明是摘要而非原文。
  **敏感运行**：日志里出现排空器写的 `[sensitive run: script output omitted]`（`_SENSITIVE_NOTICE`，与
  `worker._PRIVATE_OUTPUT_NOTICE` 同源对，测试钉相等）= 这一代是敏感运行，摘要只出异常类型名，
  不出模块名与帧分类。`lxml` 为此加进 `_KNOWN_SITE_PACKAGES`（python-docx 的依赖，缺它是高频失败）。
  tail 仍是字符串，**不升 bundle schema**。看护：`tests/test_diagnostics_worker_structure.py`。

## 速查表原要点（2026-09-25 迁入，#608）

`src/tavotto/AGENTS.md` 那一行的「必守要点」从这天起只留索引（Codex 自动拼接的 32 KiB 上限，#608）。
下面是当时写在那一格、而本文上面没有逐字出现的要点，原文照搬、一字未改；
它们与上文同等有效，改规则时一并改这里。

- 先脱敏再交出、项目清单只留条数
- 项目根在所有文本里先于主目录换成 `<project:哈希>`、项目名不出门、云盘账号与邮箱兜底抹掉（`project_roots` / `_project_section`）
- report.json 换形必升 bundle schema（现 7；5 = 新增 task-diagnostics.json；6 = project 段新增 `recent_runs`；7 = 网址只留 origin，见下）
- 服务端第二道校验刻意与前端判据不同
- 坏载荷退化成不带前端文件的包、不 400
- 不写盘不上传不进 telemetry
- traceback 配收尾异常行、worker.log 只带证据行（#435）

## 脚本运行失败进全局报告（bundle schema 6）

主语：**试运行（`/api/registry/probe`）与准备会话把 `WorkerError` 接在自己里面**——前者以 HTTP 200 + `error` 交出，
后者落成会话终局——都不经 `app._worker_error`。而 `recent_errors` 只读 `diagnostics.log` 里的 ERROR 行、
`missing_dependencies` 只由渲染端点写、脚本自己的异常由 worker 协议带回而不写 `worker.log`，所以这些运行失败多次，
全局报告里三处都是空的（2026-10 Windows 实测）。

- `project.recent_runs`（`taskdiag.run_summary`）：本项目登记表里 `script_run` + `preparation` 两类尝试的**结果分类**——
  `counts`（ready / error / cancelled / needs_input）、`by_error_code`（稳定码计数：`missing_dependency` /
  `script_no_figure` / `script_probe_failed`…）、最近 `RUN_SUMMARY_LIMIT` 条的 `kind` / `outcome` / `error_code` /
  `captured_count` / `elapsed_ms` / `at`。它是**已冻结快照的二次投影**，不另采集、不执行任何东西；值全部来自
  `diagnostic_projection` 已过形状守卫的字段，**不含**脚本名、路径、argv、图名、traceback、模块名。逐次详情仍在
  `task-diagnostics.json` 与引导卡的「下载这一次的诊断」。
- **构建后的 finalizer 失败按失败算。** 准备在 `PreparationService._finish` 就把任务诊断按「执行成功」冻成 `ready`，
  之后 `prepsession._done` 才跑登记（`register_probed` 撞 stem 等），会话终局是 partial。`_done` 在 finalizer 失败时
  调 `taskdiag.STORE.amend_post_terminal_failure`：**唯一**允许改写冻结快照的入口，只把 `ready` 改成 `error` 并写
  `error.code`（稳定码过闸），其余字段与 `recorded_at` 不动；非 ready 的终局不碰，幂等。这样 `recent_runs` 与单次
  诊断读到同一个结论，不必让 taskdiag 反向依赖会话。准备条目的 `elapsed_ms` 由投影从起止时间算出（与 `script_run` 同名同单位）。
- 缺依赖现场（`project.missing_dependencies`）现在也覆盖这两条路（`deprepair.note_missing_dependency_of`）。
  **敏感参数的运行不带模块名**：worker 在源头就把 `module` 清空，`note_missing_dependency_of` 没有模块名就不记——
  报告里仍有 `recent_runs` 的 `missing_dependency` 计数，但没有包名。
- 看护：`tests/test_diagnostics_run_failures.py`（失败运行进报告、敏感运行不泄露、金丝雀全文搜索包内每个文件）。

## 任务绑定诊断（T04，`engine/taskdiag.py`、`GET /api/diagnostics/task`、bundle schema 5）

诊断包回答「这台机器现在什么样」，采集时会探测解释器；用户在导出 / 准备 / 脚本运行失败的那个提示旁边要的是
**那一次**。所以这是另一条路，**不经 `build_report`**：

- **终局即冻结，采集零执行。** 各来源在终局那一刻写一次白名单投影（`exportjob.diagnostic_projection`、
  `preparation.diagnostic_projection`、`probe.diagnostic_projection`），存进 `taskdiag.STORE`（不可变 JSON 文本）。
  取快照只读这张表：没有解释器体检、没有安装、没有联网、不重跑脚本，也不拿此刻的环境冒充当时的
  （准备快照里的 `environment_at_plan` 是**计划那一刻**的事实）。看护 `test_collecting_a_task_diagnostic_executes_nothing`。
- **白名单是结构，不是过滤。** 投影逐字段挑：闭集枚举（`taskdiag.closed`）、稳定码（`code`，小写标识符）、
  不透明 id（`ident`）、计数、数字、安装影响摘要的十六进制摘要（`digest`）。形状不对的值**丢掉**，不哈希（低熵值的哈希可猜）；`document_revision`（整份载荷的无密钥 FNV，可离线猜解）同理不出门。
  trace 的 `facts` 不读（那里可放任意短串）；`error.params` / `message` / traceback、文件名、导出目录、脚本与入口名、
  argv（只有个数与本机不透明引用 `rc_…`）、`document_id` / `figure_id`（只在登记表里私下连重试关系，不出门）一律不进。
  新来源加字段 = 在它的 `diagnostic_projection` 里加一行并过一个形状守卫；**不许**递归复制 `to_payload()`。
  看护：`test_export_projection_never_reads_the_full_payload`（把 `to_payload` 毒化照样出快照）、各 sentinel 用例
  （原文 + 大小写 + hex + base64 + urlsafe + url 编码 + md5/sha1/sha256 都不出现）。
- **重试是新 attempt。** 每次尝试各有 id 与自己的快照；`retry_of` 在**写入新快照时**连到同一主语（同文档 / 同图 / 同脚本）
  最近一次失败，旧快照不被改写；读旧失败时 `later_attempts` 现算（≤ 8 条）。后来的成功不会清掉旧失败的 request / error。
- **认领。** 登记按 `(项目, 类别, id)`；端点只用请求的项目取。别的项目的 id、没有过的 id 一律 404 + `reason: not_found`，
  「过期」（`expired`）只对记过它的项目说得出——墓碑也按项目查。`ExportJob.project_id` 同时让 `/api/export/state`、
  `/api/export/cancel` 不再跨项目读取 / 取消（没记项目的作业不设防，与旧行为一致）。不带 `ref` = 本项目这一类最近一次失败。
- **预算（`taskdiag` 常量，改了要同步 `tests/test_task_diagnostics.py`）：** 48 条、单条 6 KiB、总 128 KiB、保留 6 h、墓碑 128、
  全局包里最多 8 条、整份文档 64 KiB。超单条上限先截 `stages` / `outputs` 的中间（头尾留着），再塌成核心字段；
  一律带 `truncated` / `truncated_fields`，不静默变短。逐出先逐「没出问题」的，刚写入的这条不逐。只收终局：活跃作业不在表里，
  清理动不到它们。
- **全局包。** `GET/POST /api/diagnostics/bundle` 多一个 `task-diagnostics.json`（本项目最近 8 条，失败在前；同一份冻结投影，
  不重新采集），manifest 加 `contains_task_snapshots` / `task_snapshot_count`；report.json 形状没变，但新增文件算 schema 5。
- **前端：** `components/TaskDiagnostic.tsx`（折叠标题 + 一个下载按钮；项目取组件出现那一刻的；记录没有 / 过期如实说，
  不去拿当前诊断包冒充）。导出失败 / 部分失败、脚本行「详情」里已挂；T09 的准备面板直接用
  `<TaskDiagnostic kind="preparation" refId={report.provider.attempt_id} />`。

字段隐私审查表与调用链见 T04 交接。

## 发送问题反馈（`engine/diagsend.py`，`/api/diagnostics/send*`，ADR 0118；发行版默认开启入口，每次发送仍逐次确认）

「诊断包不自动上传」不变；用户**当次确认**后把包发给维护者的国内接收服务（`Tavotto/infra` 的 `diagnostics-api` v1）是它的**唯一**例外通道。
契约出处是 infra 仓库 diagnostics-api 文档目录里的 openapi.json（v1.0.0）（客户端严格按它写，字段名 / 顺序 / 枚举逐字；改契约先走 infra 的向后兼容版本与协同 PR）。

- **两步，只有第二步出网。** `prepare`（`POST /api/diagnostics/send/prepare`，载荷同导出）在本机生成并保管**一份**包（`app._build_diagnostics_bundle`——
  与导出共用，包的内容与脱敏只此一份），回「会发送哪几类内容」（`ENTRY_KINDS` 闭集 id）；`GET .../<id>/bundle` 给用户同一字节去保存。
  `POST .../<id>/send` 的请求体 `confirm` 必须是字面 `true`，才起发送线程（init → 直传 COS → complete）。备包 / 看包 / 状态 / 取消 / 丢弃都不出网。
  包超过 64 MiB（2026-10-13 起，原 10 MiB；以 `upload.max_bytes` 为准）**不保管不裁剪**，回 `too_large`。**同一项目同一时刻一个会话**；重复点发送回当前状态，不开第二条线程。
  会话注册表按请求的项目上下文分键（`_SESSIONS[scope]`，#927；`scope` = `app._diag_send_scope()`：`?pj=` / `X-Tavotto-Project` 指名的项目 id，不带则默认项目）：
  备包 / 状态 / 看包 / 发送 / 取消 / 丢弃都只认**本项目**的会话——B 项目备包不丢弃 A 的包，A 在发送中 B 照常备包；别的项目拿着 A 的 id 查 / 取消 / 丢弃一律 404。发送线程与异步取消回调捕获的是会话对象本身（身份判据 `SendSession.dropped`，不再是全局单例比较）。
- **逐次授权，什么都不自己继续。** 没有记住的选择、没有启动补发、状态只在内存；取消 / 关窗（`discard`）/ 失败 / 进程退出后不会有请求。一次发送内的有界重试只有
  `complete` 丢响应（网络 / 超时）与 `in_progress` / `upload_missing`（≤ `COMPLETE_ATTEMPTS`）；429 / 503 与其余失败交还用户去点。用户再点：内容没变且服务端会话还开着
  复用 `client_request_id`，否则换新的 UUIDv4（`REOPEN_ON`、取消后、内容变了）。`client_request_id` 绝不取遥测 `install_id`。
- **目的地由代码钉死。** 默认开启（G-DIAG 开放，2026-10-13，ADR 0118 修订）：`resolve_target()` 在 `TAVOTTO_NO_DIAG_UPLOAD=1` 或 `TAVOTTO_DIAG_UPLOAD=0` 时关；
  未设 / 空 `TAVOTTO_DIAG_ENDPOINT` 用内置 `PRODUCTION_ENDPOINT`（`https://tavotto.cn`）；设了就只认它——主机 ∈ `ENDPOINT_HOSTS`（`{tavotto.cn}`）或回环开发模式
  （另设 `TAVOTTO_DIAG_DEV_LOOPBACK=1`），不合规**关闭且不回退内置端点**。`TAVOTTO_DIAG_UPLOAD=1` 不再是开启条件（无效果）。
  **测试进程默认硬关**：`tests/conftest.py` 设 `TAVOTTO_NO_DIAG_UPLOAD=1` 并让会话级探针拦下任何发往生产主机的请求（命中判红）；诊断发送用例在 fixture 里摘掉它并指向回环模拟端，e2e `fixtures.ts` 同样默认硬关。`upload.url` 只认 COS 桶域名（回环模式只认本端点 `/v1/local-upload`）；**不跟随任何重定向**
  （`_NoRedirect` 必须**抛**，返回 None 会让默认处理器把 301/302/303 的 POST 改成 GET 跟过去）；证书只经 `tlstrust`（出站 HTTPS 第五处，清单同步）。
- **秘密不落地。** 令牌 / 上传 URL 与表单 / 说明文字只在 `SendSession` 私有字段；`public()`、日志（模板字面量 + `logsafe.known` 闭集参数，不带异常文本）、诊断包、遥测里都没有。
  不调 `telemetry.capture`（用户文本不进 PostHog）；元数据只用 `os` / `arch` / `distribution` / 版本串 / `bundle_schema` / `size` / 本机 `sha256` 白名单。
- **失败码**是客户端的闭集（`FAILURES`，25 个，值 = 用户能不能再点）；`web` 的 `settings.diagnostics.send.failure.*` 与它严格同源（前端用例读这个文件对拍键集）。
- 看护：`tests/test_diag_send.py`（对端是 `tests/support/diag_fakes.py` 的本地模拟：零请求、主路径、幂等重试、每个状态码、SSRF / 重定向 / 字段注入、取消、超限与坏包、
  金丝雀不泄漏、契约对账）、`tests/test_diag_send_api.py`（Flask 层）、`tests/test_outbound_https_trust.py`（第五处出站）。真实 COS C1–C16 已于 2026-10-13 全部 PASS
  （证据在 infra 仓库 diagnostics-api 文档目录里的真实 COS 验收清单，infra#19）；模拟契约通过不等于真 COS 通过，改上传路径后要按那份清单重验。真机 WebView 逐次确认联调**未做（NOT_RUN）**。

## 网址只留 origin（bundle schema 7，Codex #923 P2）

自定义 AI 接口的 `base_url` 可能带凭据：userinfo（`user:pass@`）、查询串（`?access_token=` / `?key=`）、片段（`#token=`）、路径段（`/v1/<key>`）——按**键名**认密钥的 `_redact_obj` 认不出。
按出处放行：`report.json` 的 `ai_endpoints[].base_url` 与 `_redact_obj` 里**任何键名以 `url` / `uri` / `endpoint` 结尾的字符串值**，一律经 `_url_fact`，只留 `scheme://host[:port]`
（解析不出 / 不是 http(s)/ws(s) 的 `url:<sha1 前 10 位>`）。这是 report 字段值的语义变化，所以 bundle schema 升到 7（`web/src/diagnostics/types.ts` 同步）。
新加任何网址形字段：键名以 url/uri/endpoint 结尾即自动受益；叫别的名字就在构造处显式过 `_url_fact`。看护：`tests/test_diagnostics_bundle.py` 的网址金丝雀用例（导出包与发送包全文搜）。
- **取消与「其实已送达」**（Codex #923）：`complete` 响应丢了、用户在退避里取消时，服务端 cancel 回 409 `already_complete`——`_cancel_remote` 回 True，`_finish` 按**成功**收尾（保留报告编号、`cancel_raced`，界面说「取消时报告已经送达」），不发布 cancelled；`in_progress` 再问一次。契约里 cancel 的错误码只有 `already_complete` 带「已完成」语义。失败态里取消走异步路径，同样在回来后把会话改成 done。

### 取消与完成的竞态表（`engine/diagsend.py`，Codex #923）

取消结果是**三态**（`_cancel_remote`）：确认已取消（2xx 含幂等重放；404 = 服务端没有这份）/ 确认已送达（409 `already_complete`）/ **无法确认**（传输错误、超时、5xx、429、其他 4xx、`in_progress` 重问一次后仍不定）。
「无法确认」**绝不**当「确认取消」：若 `complete` 已发出过（`SendSession.complete_sent`，哪怕响应丢了），会话进入 **`unknown`（结果未知）**——保留报告编号，界面说「无法确认报告是否已送达；如需删除，可在 GitHub Issues 里只附上报告编号」，
不许再发一份（`start` 回 `result_unknown`）；`complete` 从未发出（还在上传）则如实是 `cancelled`（没有什么可能已送达）。所有异步回调只改**自己捕获的那个会话对象**，且异步取消的回调要求会话尚未被丢弃（`not sess.dropped`）——会话被丢弃 / 替换后迟到的答复不碰现役会话。

| 阶段（取消那一刻） | 2xx | 409 already_complete | 409 in_progress（问两次） | 404 | 传输错误 / 超时 / 5xx / 429 |
|---|---|---|---|---|---|
| 上传中（complete 从未发出） | cancelled | （不可能发生；按 done） | cancelled | cancelled | **cancelled**（无物可能已送达） |
| complete 已发、未落地（响应/请求丢了）退避中 | cancelled | done（`cancel_raced`） | **unknown** | cancelled | **unknown** |
| complete 已发、已落地（响应丢了）退避中 | （不可能：服务端已 complete） | **done**（`cancel_raced`，保留编号，不发布 cancelled） | **unknown** | cancelled | **unknown**（报告其实存下了，不能说「没有发送」） |
| 失败态（complete 发出过，异步取消，先停在 `cancelling`） | cancelled | done | unknown | cancelled | unknown |
| 失败态 / 备好态（complete 从未发出） | cancelled（立即） | 迟到答复若会话仍是当前 → done | cancelled | cancelled | cancelled |
| 已完成（done） | 不发任何取消请求 | — | — | — | — |

| 会话身份 | 期望 |
|---|---|
| 异步取消答复到达时仍是 `_CURRENT` | 按上表定论 |
| 已被丢弃 / 被 B 替换 | **忽略**：B 的状态、报告编号、`cancel_raced` 一个字都不变（测试把 B 放在「已取消」——旧实现会被改成 done 的那个状态） |

看护：`tests/test_diag_send.py::TestCancel` 的 `test_race_table_*`（参数化：10 + 4 + 4 + 超时 + 替换各一）；反证：把「无法确认」映射回 cancelled / 传输错误当已取消 / 回调改写 `_CURRENT` 都红。

**不变量（`_settle` 是发送线程唯一的收尾点；`_cancel_remote_async` 是失败态 / 备好态取消的收尾点，同一套三态映射）**

* **I1** `complete` 发出过（`SendSession.complete_sent`，含 init 回 `verifying`）之后，任何终态路径——用户取消、请求失败、校验失败（摘要不符）、超时、进程内异常——都经三态对账决定最终状态；
  报告编号不清空；最终状态只能是 `done` / `unknown` / `cancelled`（仅服务端确认取消，且是用户取消）/ `failed`（仅服务端确认没存，或可重试的失败而我们没去取消）。
  「无法确认」绝不当作「确认没存」。非用户取消的失败遇上服务端 `already_complete`（如摘要不符）→ `unknown`：报告在但内容我们不能确认。
* **I2** 用户取消一旦置位（`sess.cancel`）优先于请求自己的失败（含 429 / 503 / 传输错误等可重试码）：请求结束后先看取消事件再对账；服务端有未完成的会话就尽力 cancel 关掉（不管 complete 发没发过）。成功永远是 `done`（取消事件赶在成功之后只记 `cancel_raced`）。
* **I3** 会话过期只针对**闲置**：`touched`（API 调用 / 发送开始 / 终态）起算；发送中、取消中永不过期；`done` / `unknown` 留 `FINISHED_KEEP_S`（24 h）——每次读取都刷新，所以界面开着就不会被回收，成功送达的编号不会因跨过 30 分钟被藏成 404。

补充的竞态行：

| 场景 | 期望 |
|---|---|
| init / 上传 / complete 在途时用户取消，请求随后以 429 / 503 / 传输错误结束 | `cancelled`（complete 在途且取消答复无法确认时 `unknown`）；服务端有会话就 cancel 关掉；绝不是 `failed` |
| complete 回了但摘要不符（`digest_mismatch`，非用户取消） | 取消答复 already_complete → `unknown`（保留编号）；2xx/404 → `failed`；无法确认 → `unknown` |
| 发送跨过 30 分钟边界 | 发送中不过期；结束后 status 返回 `done` 与编号，24 h 内不回收 |
| 终态路径 × 取消答复 × complete_sent | `tests/test_diag_send.py::TestTerminalInvariants::test_i1_*`：6 种触发 × 5 种答复 × 2 种 complete_sent 穷举 |

状态赋值点清单（每处受哪条不变量约束）：
`start()` 置 sending（仅在非 sending / unknown / done 时；换 id 才清编号——用户明确的新一次发送）→ I3 刷新 `touched`；`cancel()`：sending→cancelling（I2）、失败 / 备好态按 complete_sent 走 `_cancel_remote_async`（I1）或直接 unknown（够不着服务端）；
`discard()`：`cancelling` / 发送线程在跑 → 标 `discard_after` 等收尾；否则 `_discard_locked`（complete 发出过的走 settle 再丢，见 #928 一节）；`_init` 回 complete（幂等重放）：置 done 后由 `_settle` 统一收尾；`_settle`：**所有**线程终态；`_cancel_remote_async` 回调：认会话身份 + 三态映射；`_expire_locked`：I3。

* **动作代次（Codex #923，send / cancel 乱序）**：本机的 send 与 cancel 是两个独立 POST，可能在不同服务线程上颠倒顺序。`SendSession.gen` 单调：`cancel` / `discard` 各加一，`prepare` 与状态都回它；`start` 带着调用方看到的代次，
  对不上 → 409 `stale_action`；**取消过的会话（`cancelled`）不可被 `start` 复活**（409 `session_cancelled`，连没带代次的老客户端也不行），要再发必须重新「准备」得到新会话（新授权、新 `client_request_id`）。
  前端 send 带上代次，看到 `cancelled` 就自动重新备包。看护：`test_reordered_send_and_cancel_never_uploads`、`test_reordered_across_two_threads`、`test_stale_generation_is_rejected_*`、`test_a_cancelled_session_is_not_revivable_*`（零 init 请求、会话仍 cancelled）。

### 失败后重试 / 丢弃 / 迟到 start 的对账（#928，Codex #923 三条 P2）

| 场景 | 期望 |
|---|---|
| 可重试 failed（`complete_sent`、编号仍在）后**改类别 / 附言**再发 | 发送线程先 `_reconcile_previous` 按 I1 对账上一次尝试（cancel 三态）：服务端确认已存 → `done`，报告编号是首份的，**不上传第二份**（改的参数没发出去，界面如实显示已送达的那份）；确认没存（2xx / 404）→ 才换新 `client_request_id` 重传；无法确认 → `unknown`（保留首份编号，不发第二份）。令牌已不在而 `complete_sent` → 直接 `result_unknown`。失败码属 `REOPEN_ON`（服务端会话已关）不对账。参数没变的重试仍复用同一个 id（幂等重放，不对账）。 |
| complete 发出过的 `prepared` / `failed` 会话被**丢弃**（关对话框、切项目、TTL 回收） | `_discard_locked` 不再「cancel + 立刻移出 + 清编号」：会话先停在 `cancelling` 并标 `discard_after`，等取消答复定论（done / unknown / cancelled）再丢弃；丢弃时 `_archive_locked` 把「done / unknown 且有编号」（或 complete 发出过的）留成**结果快照** `_OUTCOMES[scope]`（只有状态与编号，没有包与令牌，每项目一份），`status(pid)` 在会话没了之后仍回这份快照——用户凭编号才能要求删除。发送线程 / 取消回调在 `discard_after` 下收尾时同理。`done` / `unknown` 闲置满 `FINISHED_KEEP_S` 的回收**不**留快照（I3 原样）。 |
| 对话框已关（`discard_after`）、会话还在 `sending` / `cancelling` 时同项目再备包 | 不再 409：旧会话脱离注册表自己收尾并留快照，新会话立即可用；对话框还开着（无 `discard_after`）的发送 / 取消中仍 409 `send_in_progress`。 |
| 迟到的 start（取消先被处理）被引擎拒 409 `stale_action` / `session_cancelled` | 前端（`DiagnosticsSendDialog`）把这两个 code 视为**取消流程的一部分**，不是失败：进入 `cancelling`，继续轮询到 `cancelled` 后自动重新备包（新会话、新授权）；绝不显示「内部错误」、不停轮询。 |

不变量未改：I1–I3 与「取消优先」(I2) 原样；新增的只是 I1 在两个入口（改参数重试、丢弃）上的落实。看护：`tests/test_diag_send.py::TestReconcileAfterFailure` / `TestDiscardKeepsDeliveryOutcome`、
`tests/test_diag_send_api.py::TestPerProjectSessions`（两个项目并行准备 / 一个发送中另一个准备 / 跨项目 404）、`DiagnosticsSendDialog.test.tsx` 的「迟到的 start」两条。

## 发送包的 `detail-…` / `optional-…`（bundle schema 8，ADR 0118 修订 2026-10-13，`engine/diagdetail.py`）

**只有发送路径用。** `app._build_diagnostics_bundle(frontend, dropped, detail)`：`detail=None` 是导出（严格脱敏、`GET/POST /api/diagnostics/bundle`，`manifest.contains_detail=false`、没有任何 `detail-…` / `optional-…` 条目，且**不读**载荷里的 `detail` / `detail_options`）；只有 `prepare` 传 `DetailRequest`。
`report.json` 在两条路径上是同一份 `build_report`，老文件字节不变；manifest 多 `contains_detail` / `detail_files`（路径 + 字节）/ `detail_options` / `truncated`；发送包的 README 取代「不包含」一节（对它不成立）。

- **十二类默认 + 三个勾选项**（`PRIORITY`，数字大的先被截；`CONTAINERS` 里的类别各打成一个内层 zip，其余一类一个扁平条目）：1 `detail-errors.json`（app.log 里 ERROR/CRITICAL 记录 + worker.log traceback 块 + 前端未捕获错误，原文）；2 `detail-{app,sidecar,worker}-log.txt`（worker 最近几份合成一个条目、每份前一行出处；seek 读尾部，sidecar 路径 `handoff.sidecar_log_path`，worker 只取当前项目名下的 `diagnostics.owned_worker_logs`，与出门版 `worker_log_tails` 同一判据）；
  3 `detail-state.zip`（内层 zip；`layoutsession.state_path().parent` 下除锁文件外的文件 + `index.json` 清单含损坏副本名 / 大小 / mtime）；4 `detail-requests.json`（`RequestRing`，app.py 的 before/after_request，定长 400、不写盘、不含请求体 / 查询串 / 实参，`/api/diagnostics/send*` 轮询不记，错误码只收 `^[a-z][a-z0-9_]{0,63}$` 的响应 `code`）；
  5 `detail-overrides.zip`（内层 zip；前端送来的**正在编辑 / 选中面板**的 overrides 热态 + 该面板 stem 在 `baked_overrides/<项目>.json` 的写回基线）；6 `detail-render-env.json`（`pool.find_worker_python()` 的解释器上跑**一次** ≤10 s 的只读子进程；失败如实记 `reason`，不让备包失败）；
  7 `detail-runs.json`（`taskdiag.bundle_section(limit=24)` + app.log 里渲染 / 导出 / 试运行相关记录原文）；8 `detail-shell.json`（版本 / 安装方式 / 系统 / 架构 / Rosetta / 渠道（按版本串推断）/ 前端送来的壳版本与**本窗口会话内**最近一次应用内更新结果；取不到的写 `unknown`，**不改 Rust**）；
  9 `detail-storage.json`（数据目录与项目目录所在盘剩余空间、可写、云同步目录、Windows `LongPathsEnabled`）；10 `detail-frontend-console.json`（console.error/warn 环 40 条，全局不随切项目清）；11 `detail-display.json`；12 `detail-network.json`（代理去 userinfo，`NO_PROXY` / `no_proxy` 的主机名与 IP **一律不写、只记条目数 `no_proxy_entries`**；两个域名的系统解析（线程里做、≤2 s，超时记 unknown；超时的线程还活着时同一主机不再起新的，`reason: previous_lookup_pending`，遗留线程以主机数为上界）是否落在 `198.18.0.0/15`、时区）。
  勾选：13 A `optional-source.zip`（内层 zip；判据 = 当前项目**登记的脚本**，当前面板的脚本排最前，其余按 mtime；解析后必须仍在项目目录内）；14 B `optional-crash-reports.zip`（内层 zip；macOS `DiagnosticReports` / Windows `CrashDumps` 与 WER `ReportArchive`，7 天内，进程名 Tavotto/workerd，python/sidecar 只在报告点名 tavotto 时收；文本报告过脱敏；**Windows 崩溃转储 `.dmp` 原样收、没法脱敏**（用户裁决，确认框写明），python 的转储靠内容里点名 tavotto（UTF-8/UTF-16LE）认归属，WER 含 ReportQueue；按文件整份取舍、新的优先、不截半个，放不下的进索引与 `truncated`（`Item.dropped_bytes`）；没勾零字节）；15 C `optional-corrupt-sessions.zip`（内层 zip；损坏副本内容）。
- **选项只认字面 `true`**（`parse_options`），默认全 false、服务端不记忆；载荷被整份丢弃（超 `MAX_SEND_REQUEST_BYTES` = 1 MiB）时退回全 false。前端 `detail` 子对象由 `diagnostics_frontend.sanitize_detail` 只按类型 / 长度 / 条数限界，**不按形状拒绝文字**（与老字段的判据刻意不同），导出路径根本不读它。
- **始终处理 `Scrubber`**（发送包每个新文件）：家目录各写法（正反斜杠、JSON 转义）→ `~`、用户名（≥3 且非 user/root/admin 等通用名）→ `<user>`、`key=…` / `token=…` / `Authorization:` / Cookie 行 / URL userinfo / JWT / 复用 `_SECRET_VALUE`；结构化数据按键名（`api_key`、`token`、`secret`、`password`、`auth`、`cookie`、`nonce`、`credential`、`signature`……）整体换。
  **正则必须线性**：键名前后通配有上界 40、起点锚在标识符开头、URL scheme 有上界（`test_scrubber_is_linear_on_long_tokens`——无界版本在 80 万字符的十六进制串上跑了数十秒）。**不做**邮箱 / 云盘账号替换（会把装饰器 `@app.route` 之类整词吞掉；维护者要看原文）。源码文件用 `source_mode`：只换带引号的字面量，`token = self._next()` 这类表达式原样。
- **没勾 A = 零源码字节**：除不放脚本文件外，日志 / 报错 / 运行记录里 traceback 的源码行与 `^^^` 指示符一律剥掉（`strip_source_lines`，保留 `File …, line N` 帧行与异常行）。看护 `test_source_is_zero_bytes_when_unticked_everywhere`（金丝雀在 worker.log traceback 里，勾了才出现）。
  **`strip_source_lines` 是白名单**：从 `Traceback (most recent call last):` / `Exception Group Traceback`、`File "…", line N` 帧行或 `file.py:N: XxxWarning` 起，先剥 ExceptionGroup 的装饰前缀（`|`、`+ `、`+-+---- 1 ----`）再分类——缩进的行只留帧行与 `...` / `[Previous line repeated N more times]`，其余（源码行、`^^^`、局部变量 repr）一律删；不缩进的行（块头、收尾异常行、链式连接语、`add_note` 的 note、group 分隔行）原样保留，装饰前缀还回去。看护 `TestExceptionGroupTracebacks`（普通、链式、嵌套两层 group、note；errors 与 app / worker / sidecar 三处日志尾，没勾 0 金丝雀、勾了不剥）。
- **体积（`fit`）**：每类一个原始字节份额（`SHARE`）；总 ZIP 超 `MAX_BYTES - FIT_SLACK`（64 - 1 = 63 MiB）时按 `PRIORITY` 从大到小截（能缩的按比例缩，不能缩的从末尾整份丢，缩到 <2 KiB 整类去掉），记 `truncated: [{kind, original_bytes, kept_bytes}]`；核心文件不在 `fit` 里；全截光仍超限走 `diagsend.too_large`。`report.json` 只算一次（`build_bundle(report=…)`）。
- **源码脱敏分区（`scrub_script`）**：`.py` 用 `tokenize` 找注释与字符串字面量，那些区间走完整 KV 替换（`# api_key=REAL` 不带引号，源码模式会放过它），可执行代码走源码模式（`token = self._next()` 原样）；tokenize 失败的 `.py` 里带 `#` 的行整行按完整模式；非 `.py` 一律完整模式。
- **源码代码区按 tokenize 的结构判定，不靠正则**：`_secret_string_spans` 把 `.py` 的 token 并成逻辑行，字符串字面量（含 b/r/u/f/rb 前缀、f-string 整段、隐式拼接）处在秘密上下文就整段换成 `"<redacted>"`——赋值 / 注解赋值 / 增量赋值 / 关键字参数的目标含秘密名（`api_key = b"…"`、`token: str = "…"`、`config["password"] = "…"`、`self.api_key = …`、`f(api_key=…)`）、dict 字面量里键含秘密名的值、紧跟在秘密名字符串参数后的值（`setdefault("api_key", …)`）。数字 / 非字符串表达式不动（`token = self._next()`）。看护 `TestSecretShapeMatrix` 的 Python 字面量形状（每个键 × 22 种写法）与最终 ZIP 全文搜索。
- **隐私契约的层次（主控裁决）**：默认收集的内容按秘密形状矩阵**严格**清理，漏形状是 P1；**勾选的源码与崩溃报告是用户逐次确认的原样附件，清理只是尽力而为、不作保证**（确认框如实写明）。以后在源码里新发现的密钥形状按 **P2 补形状**处理（并入矩阵），不再当作隐私契约破口。`_in_secret_call`：被调用名（属性链任意一段）含秘密词的调用，其直接字符串实参（位置与关键字）整段换掉（`set_password("…")`、`client.set_token(…)`、`auth("u", "pw")`）。私钥 armor 块按**标签里的词**判定（含 PRIVATE / SECRET / ENCRYPTED / KEY BLOCK / OPENSSH / PUTTY 且不含 PUBLIC：PEM 私钥、加密私钥、OpenSSH、SSH2、PGP 私钥块，armor 头字段与 CRC 行在块内一起抹；公钥、证书、`PGP PUBLIC KEY BLOCK`、`PGP SIGNATURE` 保留，排查 TLS 有用），PuTTY `.ppk` 按 `Private-Lines: N` 抹其后 N 行（`_redact_ppk`）；块只认与 BEGIN **同一标签**的 END（大小写 / 空白变体算同一个，夹在中间的别的 END 不算收尾），找不到配对的 END 就从 BEGIN 抹到输入末尾（线性，不设窗口）。
- **渲染环境探的是项目实际用的解释器**：`pool.resolve_worker_python(figures_dir, discover=False)`（项目级决策唯一出处，不起发现子进程），`detail-render-env.json` 记 `source`（项目 venv / 记住的 / 全局 / 内置）与 `scope`。  焦点面板是 native 执行配置时（`enginesession.profile_of` + `nativesession.REGISTRY.route_for`，只读已有登记）：报那条会话已有的回执 / 脱敏诊断，并对**该会话的解释器**跑同一个只读探针，`inspected: "native_session"`；拿不到会话时如实写 `inspected: "safe_pool"`、`native_interpreter: "unknown"`。`execution_profile` 一并记下。
- **结构化数据的键也过 `text()`**：`Scrubber.obj` 对每个字符串键先清理再写回（`{"https://alice:REAL@proxy": true}`、`{"api_key=REAL": true}`），是否秘密键用**原始键**判断；清理后撞键追加 `#2`、`#3`，不丢条目。文件名 / 条目名是我们生成或已做家目录替换的，不走这条。看护 `TestSecretsInDictKeys`。
- **秘密键下的占位符是显式白名单**：`_BORING_VALUE` 只认我们自己产出的记号（`<redacted>` `***` `<user>` `<email>` `<project:十六进制>`、`seg:` `str:` `file:` `mod:` `url:` `acct:` + 十六进制、`~`、`...` `…`）与 true/false/null/none、空值；任意 `<…>`（`password=<REAL_SECRET>`）一律抹。看护 `TestPlaceholderWhitelist`。
- **备包请求的序号（`seq`）**：前端每次备包先向引擎要一个序号（`POST /api/diagnostics/send/seq`，`diagsend.issue_prepare_seq`：进程内单调计数、至少比本项目已登记的大 1，**不看墙钟**——时钟回拨 / WebView 重载不会让对话框卡在 stale_prepare），再带 `?seq=` 备包，引擎按项目记最大值（`diagsend.begin_prepare`，请求到达时登记、`prepare()` 登记会话前再比一次）；比已见过的更旧就 409 `stale_prepare`、丢弃那份包，不替换新会话。前端把它当作被取代、静默忽略（`isStalePrepare`）。没带 `seq` 的老客户端不参与排序。备包绑定调度时采到的项目：载荷采集期间切了项目就丢弃，请求显式写那个项目，不对新项目备包（`DiagnosticsSendDialog`）。看护 `TestStalePrepareRace` 与 `SendReportButton.test.tsx`。
- **键名识别不设长度窗口**：`_KV_SECRET` 只负责找「键 token + 分隔符」的位置（token = 一段连续的 `[\w.\-]`，整段贪婪取，起点由 lookbehind 钉住，线性）；是不是秘密键由 `is_secret_key(整个 token)` 判，与结构化侧同一个判据，再长的键也判得出。看护 `TestLongKeys`。
- **写出的脚本一律 UTF-8**：按声明编码解码后，前两行（PEP 263）里的 `coding:` 声明改写成 `utf-8`（`_declare_utf8`，行里其余内容不动，index 里仍记原 `encoding`）。
- **名字也是文本**：从用户可控字符串（图 / 面板、脚本、状态文件、崩溃报告、损坏副本、WER 目录名）派生出的内层 zip 成员名与各 index.json 的名字字段，一律先过 `Scrubber.text()` 再 `_safe_name()`（`_member_name` / `_clean_name`，顺序不能反：`_safe_name` 会把 `token=REAL` 的 `=` 换成 `_`）；清理后撞名（含 `index.json`）追加 `-2`…，清理过的名字保留常见扩展名；index 里同时给不可逆的 `id`（原名 sha1 前 10 位）供维护者对应多份。服务商令牌的起点不用 `\\b`（`exe_ghp_…`）。看护 `TestNamesDerivedFromUserStringsAreScrubbed`。
- **换行在入口规范化**：所有按行处理的清理与分类（`strip_source_lines`、`Scrubber.text` / `json_or_text`、`_tail_text`、`scrub_script`）入口先过 `_nl`（`\r\n`、单独 `\r` 都变 `\n`），不在各个正则里补 `\r?`；Windows 日志是 CRLF，行尾带 `\r` 曾让帧行 / 块头匹配失败、源码漏进发送包。输出一律 `\n`。看护 `TestLineEndingsAreNormalisedAtTheEntrance`（`\n` / `\r\n` / `\r` 参数化，macOS 上复现）。
- **默认收集的自由文本一律 fail-closed（主控裁决）**：日志尾、console message / stack、errors、崩溃报告文本、解析失败的状态 JSON 兜底。命中「秘密键 + 分隔符」后，替换单位是**从值起点到行尾**，再由唯一的值定界扫描器 `_scan_value` 判断行尾之后是否还在同一个值里：同时跟踪所有开括号（闭括号必须与栈顶种类匹配，不匹配 = 结构不确定，一直抹到输入末尾）、单 / 双 / 三引号（含 `b` `r` `u` `f` 前缀；`don't` 里的撇号不算字符串开头）、反斜杠转义；行尾时栈非空或引号未闭合就继续往下抹，直到配平或输入末尾。栈为空时遇到的闭括号关的是我们没跟踪的外层结构，忽略。**牺牲**：含秘密键的那一行，键之后的剩余部分不再保留（`{"token": "x", "n": 1} tail` → `{"token": <redacted>`）。Cookie / Authorization 头行也走同一个扫描器（值可以是跨行的三引号串）。YAML 值从下一行才开始的写法（行尾只有注释 `password: # prod` 按空值处理，注释先剥；tab 与空格混用的缩进宽度没法比，fail-closed 当作更深；`|` `>` 加 `-` `+` 数字修饰、行尾空值、行尾 `\`）：键行之后所有缩进更深的行与续行都当值抹掉，遇到缩进不深于键行的非空行才停，空行不终止（`_yaml_block_end`）。**按键豁免，不按值类型豁免**：秘密键下的数字一律抹（`password=123456`、`pin=1234`、`otp: 593201`、`password=[123456]`，文本与 `obj()` 两侧同判据）；只有 true/false/null/none、空值、已有占位符（`<redacted>` `***` `<…>` `...`）、空括号 / 只含布尔·null 的括号段原样保留。计数 / 配置类键名（去掉分隔符后以 count/len/length/size/limit/max/min/index/idx/ttl/timeout/expires/expiry/age/version/retries/bits/used 的最后一个词，或以 max/min/num 为第一个词（按词切，`token_admin` 不会因 `min` 放行；`number` `num` `code` `value` `val` 刻意不在表里，`pin_number` `pin_code` `password_value` 仍是秘密键）：`max_tokens` `token_count` `key_size` `password_max_len`）不当秘密键，数字与非数字都保留（`is_count_key`，集中在 `is_secret_key`）。URL userinfo 不设长度窗口：`scheme://` 之后到第一个空白 / 引号 / `<>` 之前出现 `@`，就把 scheme 之后到最后一个 `@` 全部换掉（`_redact_userinfo`，已扫过的区间不重扫，线性）。结构化路径（`obj()`、能 `json.loads` 的状态文件）与源码路径（tokenize）不受此条影响。看护 `TestFailClosedScanner`（三引号含前缀、括号种类错配、跨行未闭合、撇号、userinfo > 4096 与多个 `@`、线性）。
- **已知局限（默认收集的自由文本，规则做不到的；不是 bug，新发现的同类按 P2 补形状，不再当隐私契约破口）**。缓解统一是：发送永远逐次确认、确认框折叠区可展开看文件清单、可先「保存这份诊断包」自己检查同一份字节；勾选的源码与崩溃报告本来就是尽力而为。
  1. 没有键名可依、也不是已知令牌前缀的秘密：单独出现的 base64 串、随机口令、`user pass` 这样没有标签的行——规则只认「秘密词 + 分隔符」「PEM」「JWT」「长十六进制」「服务商令牌前缀」。
  2. 键名不含秘密词：`dsn` / `pw` / `pin` / `otp` / `mnemonic` / `seed_phrase` / `connection_string` / `database_url` 已补（短词只认整词或 `_` `-` `.` 分隔，避免 `pine` / `pinyin` 误伤；驼峰 `userPin` 只在结构化路径里认）；其余自定义名字（`conn=`、`seed=`、`auth_hdr` 之外的缩写）认不出。
  3. 秘密在键之前（`abc123 is the api key`）、键与值分在两行且没有分隔符。
  4. 键不是秘密词、值才是（`{"field": "password", "value": "abc"}`）。
  5. 用户自己写进报错文字的口令，`password` 后面没有 `:` / `=`（`invalid password hunter2`）。
  6. UTF-16 等非 UTF-8 编码的日志、被截断在多字节字符中间的文本、JSON 里 `\uXXXX` 转义过的键名（中文键）。
  7. PEM 以外的私钥格式（没有 `-----BEGIN` 头的 OpenSSH 私钥 base64）。
  8. 家目录与用户名之外的个人信息（邮箱、真名）按已定口径保留。
  9. 分隔符：已认全角冒号 `：` / `＝` 与 NBSP、`\u2000-\u200a`、`\u202f`、`\u205f`、全角空格；其他不可见分隔符认不出。
- **引号串的定界只有 `_scan_value` 一处**（旧的 `_quote_end` / `_container_end` 已并入）：`\\` 转义下一个字符。状态文件整份能 `json.loads` 就走结构化 `obj()` 再序列化（`json_or_text`），解析失败才退回文本。看护 `test_values_with_escaped_quotes_and_backslashes`、`test_state_json_goes_through_the_structured_path`、`test_backslash_heavy_input_stays_linear`。
- **值形状里的 Python repr**：字符串前缀（`b` / `rb` / `u` / `f`…）紧跟引号 → 前缀与整个引号串一起换；包装调用（`SecretStr('x')`、`bytearray(b'x')`、`Token(value='x')`）与 `[` / `{` 同一套配平扫描（`(` 也计入 `_container_end`，不配平抹到输入末尾）；`<Name '…'>` 形状的 repr 抹到配对的 `>` 或行尾。源码模式的代码区不按调用处理（字符串实参归 tokenize 结构判定）。看护 `test_python_repr_values_in_logs`（34 键 × 13 种 repr）+ 最终 ZIP 全文搜索。
- **声明从最终保留的条目推导**：manifest 的 `detail_files`（最终 `detail.files`）、`detail_options`（`options_of(kept)`——附件被 `fit` 整类丢掉就是 false）、`truncated`（`fit` 的记录，含采集阶段的 `dropped_bytes`）、各内层 `index.json`（`_reconcile_index` 装箱时对账）、prepare 响应的 `entries` / `options` / `truncated`（读最终 ZIP 与 manifest）、README 的勾选项一行、确认框（以 `p.options` 为准）。**数量上限**（源码 100、损坏副本 3、崩溃报告 40、worker 日志 3 份、overrides 面板 3 张）之外没收的，在 index / 文件头里写 `skipped_over_count_cap`，字节计入 `dropped_bytes`。看护 `TestDeclarationsMatchTheFinalContents`。
- **渲染探针不写用户环境、结果可能与渲染时不同**（#1012 3、5）：字体缓存落点换到父进程在可信临时目录下建的 `mplcache`（`TAVOTTO_PROBE_FONTCACHE`；已有的 `fontlist-v*.json` 复制过去照样命中，缺的在里面建，随可信目录一起删），`matplotlibrc` / `configdir` / `cachedir` / rcParams 仍取真实目录；空缓存目录前后列表不变（看护 `TestIssue1012BackendItems`）。safe 池与 native 会话的探针结果都带 `may_differ: "user_import_paths_not_reproduced"`（探针 `-E` 在项目外跑，看不到 worker / 会话会优先导入的项目目录 / `PYTHONPATH`；native 启动环境里没复现的变量另记 `may_differ_detail`），并把每个会放到导入路径前面的根——项目根、**焦点脚本所在目录**（`worker.py` 同样插到 `sys.path` 最前）、探针 cwd、native 会话的项目根与 cwd——第一层里可能遮挡 `matplotlib` / `numpy` / 已装包的同名文件或包（`matplotlib.py`、`matplotlib/`……）列进 `shadowing_candidates`——**只看文件名，不 import、不读内容、不执行**。
- **渲染探针的启动与收尾**：探针脚本写进我们自己建的 0700 临时目录（`tavotto-probe-script-*`），`python -E -s -B <脚本>` 启动、`PYTHONSAFEPATH=1`，继承环境先剔除全部 `PYTHON*`（只留编码 / 字节码类）与 `__PYVENV_LAUNCHER__`（`_probe_base_env`，Flask 进程的 `PYTHONPATH` / `PYTHONSTARTUP` 不会进探针），cwd 不进 `sys.path`（项目里的 `json.py` 等不会在 prepare 时被执行）；用完用有界重试的 `_rmtree_with_retry` 删（仓库里没有现成的目录树重试删除，这是唯一一份），删不掉在结果里记 `probe_cleanup: "failed"` 并写一条日志，下次探测前 `_sweep_probe_leftovers` 只清我们自己前缀、属于当前用户、0700、超过 1 小时的遗留。native 会话的工作目录不存在 / 读不了时，版本类事实在一个中性空目录里取，`probe_cwd` 写 `null` 加原因，依赖 cwd 的维度标 `unavailable: "native_cwd_unavailable"`。
- **读文件要稳（`FileId` / `_stable_candidates` / `_read_capped`，各读盘类别共用：状态、损坏副本、源码、崩溃报告 / 转储）**：枚举时逐个 stat 登记 `(size, mtime_ns, ino)`，排序只用登记到的值、不再碰文件系统；读之前的 stat 必须与登记一致，读取上限 = 登记大小与剩余份额的较小者（整份取舍的最多 +1 字节作变化证据），读之后再核对；任何文件的失败只让它自己出局——读不到 `read_failed`、读取期间消失 / 变大 / 被替换 `changed_while_reading`，都不是 `skipped_over_budget`，也不让整个类别变 `error.json`。日志尾（追加写，按 `limit` 从尾部读）与 `_mentions_tavotto`（只扫头部 4 MiB）读取本身有上限、不要求身份稳定。看护 `TestFilesChangingDuringCollection`（四个类别 × 消失 / 变大）。
- **整份丢弃要记账**：份额阶段丢掉的 item 计入 `dropped_bytes`（manifest `truncated.original_bytes` 因此是全部原始字节）；内层 zip 的 `index.json` 在装箱时核对（`_reconcile_index`），对应文件不在 zip 里的条目标 `skipped_over_budget: true`，index 与 zip 实际内容一致。
- **秘密形状矩阵（`TestSecretShapeMatrix`）**：键形状（`token` `api_key` `apiKey` `API-KEY` `auth` `Authorization` `x-api-key` `password` `pwd` `passphrase` `session_id` `nonce` `cookie`…，大小写 / 驼峰 / 连字符）× 值形状（裸值、单双引号、数字、list / dict / 嵌套容器、Bearer / Basic、转义的 JSON 值）× 载体（日志文本、JSON 结构 `Scrubber.obj`、源码注释·文档字符串·代码、console、overrides、状态 JSON、崩溃报告），再加无键名可依的形状（URL userinfo、查询串、Cookie / Authorization 行、JWT、长十六进制、PEM 私钥块、AWS / GitHub / Slack / Google 令牌前缀）；最终 ZIP（含内层 zip）全文搜金丝雀 0 命中。
  结构化数据里秘密键名下的值**不论类型**整体换掉（`Scrubber.obj`，键名归一化后比词表，容器里全是数字 / 布尔才保留）；文本里值以 `[` / `{` 开头时整个配平括号段换掉（`_container_end` 线性扫描，8 KiB 上界，不配平抹到行尾）。
  **勾选项走查询串** `?options=source,…`（`options_from_query`），载荷超 1 MiB 被整份丢弃时它仍在；前端 `fitDetailToBudget` 先把 detail 截进 900 KiB 预算并把截过什么记进 `client_truncated`；界面以引擎回的 `options` 为准，不一致说一句「附加内容太大，这次没能附上」。
- **采集失败不让备包失败**：每类包在 try 里，失败变成该类的一份 `{"collection_error": …}`。测试进程里渲染环境子进程与 DNS 默认被 `tests/conftest.py` 的 autouse 桩换掉（真探测的用例自己取回原函数）。
- **ZIP 形状由 infra 诊断服务的 ZIP 检查决定**（文字出处，不写跨仓库路径）：条目名匹配 `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`（**无目录 / 斜杠**）、整包 ≤ 32 条、单条目 `file_size > 1024 且 > 200 × compress_size` 被拒、单条目解压后 ≤ 128 MiB、总解压后 ≤ 256 MiB、`manifest.json` ≤ 64 KiB（所以 manifest 里只列顶层条目，内层清单放内层 zip 的 `index.json`）。
  常量在 `diagsend.ZIP_RULES`，出包后 `diagdetail.ratio_safe`（压缩比超限的条目改存 STORED）+ `diagsend.zip_problem`（同判据自检，`inspect_bundle` 里不合规 = `bundle_invalid` 内部错误，不发送）。`diagsend.ENTRY_KINDS` 只收精确的扁平名；`MAX_BUNDLE_ENTRIES` = 32。
- 看护：`tests/test_diag_send_detail.py`（每类在且非空、家目录 / 用户名 / 秘密金丝雀全文 0、源码金丝雀、选项默认 false 不记忆、截断顺序与 64 MiB 上限、ZIP 规则（最大配置 ≤ 32 条且名字合规、重复日志不触发压缩比、自检）、导出路径无 detail 且忽略 detail 载荷、请求环不含查询串 / 请求体、探针失败降级、`sanitize_detail` 限界）。

