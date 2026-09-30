# 改用户脚本：经确认的改写、备份与复原（ADR 0110 / 0094）

> 这里是这一主题规则的**唯一全文**；`src/tavotto/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **原则不变：Tavotto 为了兼容、在用户没要求时绝不动脚本。** 改脚本只有一种来由：用户在界面里
  **亲手发起、看过后端生成的逐行 diff、勾选确认**。今天唯一的入口是「找不到脚本要读的数据」里
  `via ∈ {probe, glob, native}` 条目选完数据位置之后的确认页（ADR 0110）；0094 的写回脚本落地后走
  同一个底座。没有任何自动路径：样式跟随、规范修图、AI 刷新、MCP / Codex 插件、`tavotto run`、启动迁移
  一律不得调用提交 / 复原端点。
- **共享底座 `engine/scriptbackup.py`（单文件）**：
  * `resolve(root, script)`——写哪个文件由服务端按项目 + 项目相对路径定（`projectenv.contained_path`），
    请求里不收绝对路径。拒绝：脚本或它在项目内的任一父目录是符号链接（`script_is_symlink`；指到项目外的
    在 `contained_path` 就拒成 `script_not_found`）、硬链接 `st_nlink > 1`（`script_hardlinked`）、没有写
    权限 / 只读卷（`script_readonly`）。
  * `replace(store, script, new_bytes, kind=, expect_before=)`——**两处备份全部写完并 fsync 才碰原件**：
    项目内 `tavottofile/script-backups/<slug>/<月日_时分秒>/`（路径只从 `project_layout_dir()` 取）与数据目录
    镜像 `script_backups/<项目id>/<slug>/<同名>/`，各放 `original.py` + `meta.json`。任何一处失败
    `script_backup_failed`、这次建的目录删掉、原件逐字节不变。替换经 `atomicio.write_bytes(..., mode=)`
    （保留权限位）。替换前再核一次磁盘 sha256（`script_changed_since_preview`）。
  * 某脚本的第一条标 `pristine`、永不自动清理；其余留最近 `KEEP_RECENT`（20）条。
  * 只动一份文件，不需要 0094 §五.7 的跨文件日志：崩在备份与替换之间只多一份备份，`history()` 按磁盘
    此刻的 sha256 标 `current` / `before` / `changed`。0094 PR 3 在这个库上加日志。
- **改写数据路径 `engine/scriptedit.py`**（ADR 0110 §二–§四）：
  * 规则来自 `inputremap.derive_location`（`derive` 扩到文件夹与 glob）；缺失路径集合 = 对话框那一条 +
    `inputremap.static_missing` 全部（**含 `via = open` 的**：半新半旧的脚本在终端里仍读旧位置）。
  * 每条缺失路径带 `probe_kind`（`inputremap.PROBE_KIND_OF`：键恰好是 databinding 三张探路表的并集，
    `test_every_probe_function_is_labelled_file_or_dir` 对账）：`dir`（`listdir` / `scandir` / `walk` /
    `iterdir` / `isdir`、glob）只许指认文件夹——界面只给「选择文件夹」，`_input_path_plan` 再拒文件
    （`input_remap_chosen_invalid`）；指认成文件的话改写后重跑就是 `NotADirectoryError`。`file`（`isfile`……）与 `dir`
    都再按**改写之后这一条落到哪里**判（`remap_target(…, whole=True)` 必须是文件 / 文件夹）：选了与文件同名的文件夹，
    推出的前缀规则会把常量改成那个文件夹，`isfile()` 照样 False、同一个框再弹（Codex 评 #730 P2）。
  * 候选常量：值与某条缺失路径**按路径段**相等或是其前缀、自己此刻也不存在、与规则 `from` 同一侧
    （相对 / 绝对）；写法是单个单行普通字符串 token；语境是调用实参（存图调用除外）、赋值 / return 的整个右值、
    参数默认值、`/` 或 `+` 的操作数、容器元素、字典的值。f-string / 隐式拼接 / 三引号 / 下标 / 字典键 / 比较
    不改，**看得出是路径的**逐条报原因（`SKIP_REASONS` 闭集），单词不报。
  * 新值一律写**绝对路径、`/` 分隔**（探路调用不经只读回退，相对路径在沙盒 cwd 下仍然落空）；前缀与引号
    原样，非 raw 串转义，raw 串装不下 / 编码写不下 → 这一处不改、进报告。
  * **自检（写之前）**：新字节能解析；新旧 AST 把被换的常量按 (旧值, 新值) 对调回去后 `ast.dump` 相等、
    改动个数对得上；每处替换的字节区间之外逐段逐字节相等；目标存在。行按解析器的方式分
    （`\r\n` / `\r` / `\n`），`ast` 的列是**这一行的 UTF-8 字节偏移**，换回字符列再按文件编码量；解码再编码
    回不到原字节的文件直接拒绝（`script_edit_unreadable`）。
  * `undo()`——复原时「只撤销这几处路径」：每处新字面量在原行原列、或全文唯一一处才换回，照样过自检；
    否则 `script_restore_conflict`，只剩整份恢复。复原端点走 `undo_edits_of(此刻, 记录)`，备份列表的
    `undoable` 走 `undoable(此刻, 记录)`——同一个判据，界面据它决定给不给「只撤销那几处」，不另判。
  * 备份记录的**绑定**（`scriptbackup._bound`）：`meta.id` 就是 `<slug>/<目录名>`、`slug_of(meta.script)` 就是这个
    slug——`load` 与 `history` 都先过它，对不上当不存在。复原的写目标来自记录，不绑定的话一份被改过的
    `meta.json` 能把 `original.py` 写到另一个脚本上。
- **端点与令牌**（`app.py`）：`POST /api/script-edit/input-path/preview` 不改任何字节、回 diff 与一次性令牌；
  `POST /api/script-edit/commit` 核销令牌 → 重读磁盘重算一遍，新字节 sha256 与预览时不同 →
  `script_edit_preview_stale`；`GET /api/script-backups?script=`；`POST /api/script-backups/restore`
  （`full` / `undo_edits`，两种都先把此刻的版本备份一份）。**令牌绑定浏览器会话 cookie**：只凭本机进程凭据
  （`session_client.AUTH_HEADER`，MCP / CLI 那条）的请求在预览 / 提交 / 复原上一律 403
  `script_edit_needs_ui`；令牌单次、十分钟、不出现在任何 MCP 结果里。编码 Agent 正在改同一份脚本 →
  `script_busy`；`runtime:` 资产不适用。提交 / 复原之后与 AI 改完同一顺序：`shutdown_all` → 统一刷新 →
  `panel.file_changed`。
- **按脚本的锁**（`engine/scriptlock.py`）：本机服务并发处理请求，「判一次再写」的每一段都在同一把
  `script_guard`（真实路径、同线程可重入）里做完——Agent 从「脚本在不在」到登记进 `SESSIONS`；AI 回滚从
  「是不是 AI 那一版」到写回；提交 / 复原从锁内复判 `script_busy` 到「校验和 → 备份 → 替换」。写脚本字节只经
  `scriptlock.write_script`（不持锁就抛）。持有者清单写在 `scriptlock` 的模块说明里，`tests/test_script_lock.py`
  从源码 AST 枚举调用方与它对账，并扫「目标名字带 script 的写字节调用」只许在 `scriptlock` 里。新增写脚本的
  入口：先进清单、走 `write_script`。
- **备份目录的包含性**：项目里那份（`tavottofile/script-backups/<slug>/<时间戳>/`）建之前从项目根往下逐级核对——
  任何一级是符号链接（`lstat`，指向项目里面别处也不行）、不是文件夹、或 realpath 出了项目根
  （`projectenv.contained_path`）→ `script_backup_unsafe`，脚本不改；建完时间戳目录再核一次。
- **已替换、未确认落盘**：`atomicio.write_bytes` 在 `os.replace` 成功之后才报 `dir_fsync_failed`——脚本已经是
  改后的样子，不报「没改」：`_after_script_edit` 照常（会话失效、界面刷新），响应带 `durable: false`，界面换成
  `engine.scriptEditNotDurable` 那句提醒。改写与复原两条路同一处理。
- **只改证得出只喂给读取的值**（`scriptedit._InputOnly`）：从常量往外走，穿过拼路径的写法（`Path(…)`、`/`、`+`、
  `os.path.join`、f-string、路径方法）直到碰上调用——读取（`inputremap.is_read_call`）或探路（exists / listdir /
  glob，与 `_probe_via_of_constants` 同一张表）才成立；写出（`inputremap.is_write_call`）或不认得的函数都不成立。
  赋给名字的：名字只赋值过一次（与 `static_missing` 同一个前提），且**每一处**读取都证得出；链式赋值接着追；参数
  默认值按函数体里的每一处读取追。读写混用（同一个 `DATA_DIR` 既读又写）整条不进候选；容器、`return`、`for`、
  重复赋值、属性、传进不认得的函数或脚本自己定义的函数（同名于读取表的 `def load` 也算）一律追不清，不改
  （`SKIP_CONTEXT`），不做跨函数分析。唯一例外：用户正在处理的那一条（`missing[0]`）
  整串直接做不认得的函数的实参（C++ 读取器常经包装函数打开）。不要再按「会流到写出」的写法逐一补形状（Codex 评 #730 三次）。
- **只读**：`os.access`（有效权限）、脚本与所在文件夹的写权限位（以 root 运行时 `os.access` 无视权限位）、
  只读卷三判任一不过 → `script_readonly`。
- 看护：`tests/test_script_edit.py`（候选判据、九种编码 / 换行的字节矩阵与撤销往返、自检三判、推规则扩展、
  三种实测 ENOENT 形态与归因、底座的拒绝 / 失败无残留 / 权限位 / 保留、HTTP 全流程与会话绑定、真 worker 的
  探路与 C++ 读取器、Agent 侧代码不许点名提交 / 复原端点的 AST 门禁）、`tests/test_mcp_server.py`
  （`recovery` 指向界面里的确认改写）。
