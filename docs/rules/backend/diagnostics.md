# 诊断包

> 原文出自 `src/tavotto/AGENTS.md`「诊断与排障」（2026-09-17 指导文档治理时按主题拆出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- `engine/diagnostics.py` 出**一键诊断包**（`GET /api/diagnostics/bundle`）：
  版本 / 系统与编码 / 安装方式 / 数据目录 / 渲染解释器 + matplotlib /
  AI CLI 探测 / 项目概况 / 最近错误 + app.log + 用户配置。
  **密钥与个人路径必须先脱敏再交出去**（用户会把它贴进 issue 或发到群里）。
  `recent_projects` / `projects` **只留条数**：那是用户所有课题的名字与路径，
  排障一次都用不到（当前项目在 report.json 的 project 段里）。
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
  * 身份字段（`*_hash` / `*_variant` / panel / file / session / version）
    **必须是 `前缀:十六进制` 的 hash**，gid 必须**小写开头**——光靠字符集挡不住
    `SUPER_SECRET_PAPER_TITLE_12345` 那种全大写下划线串。
  * 坏载荷（超限 / 畸形 JSON / 类型不对）**一律退化成不带前端那两个文件的包**，
    并在 manifest 记 `trace_truncated`。用户是来排障的，不该拿到一个 400。
  * **不写磁盘、不自动上传、不进 telemetry**。trace 只在用户点导出那一刻进 zip。
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
  ERROR 行是应用自己的日志语句，过路径缩写。**先扫 `WORKER_LOG_SCAN_LINES`（400）行再抽块、再按块截到
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
