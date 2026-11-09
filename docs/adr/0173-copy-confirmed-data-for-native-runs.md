# ADR 0173：native 缺数据时，经用户确认把他指认过的数据复制到脚本要读的原路径

- 状态：已接受（2026-11-09，维护者拍板方案 C 与界面 c1–c4）
- 相关：[0106 数据改指](0106-missing-input-relink.md)、[0110 经确认改写脚本里的路径](0110-rewrite-missing-input-path-in-script.md)（含 2026-11-09「桌面 native 执行的已观察缺失读取」）、[0047 在脚本目录里运行](0047-safe-profile-project-workdir.md)、[0127 执行策略与统一重试守卫](0127-execution-policy-and-unified-retry-guard.md)、issue #1813、#1817

## 问题

一份诊断（Windows，issue #1813）里，项目已有数据改指规则，safe 执行读到了改指的数据，之后因别的原因回退 native；
native 去读**脚本写的原路径**，路径不存在，`FileNotFoundError`，终局 `script_error`。改指表只对 safe 起作用（0106 §三），
所以用户明明已经指认过那份数据，native 仍然读不到，卡片只剩一段报错。

## 为什么不让 native 用改指

0106 把「native 不改指」写成合同：native 是用户自己的 `python fig.py`，读什么就是什么。#1813 的合成实验
（可行性，没有实现）说明在 native 里装 Python 层读取钩子能救回 `open()`，但：`Path.exists()` 仍为假、子进程读不到、
目录遍历 / C++ 读取器 / 映射代次竞态都不覆盖，而且它**悄悄改变 native 的读语义**。盲区一旦存在，用户看到的是「有时好用」，
比一个说得清的出路更糟。所以不装钩子。

0110 / #1817 已经给了一条退路：经用户逐行确认，把脚本里那一处路径常量改成数据现在的位置。它可靠，但**改了用户的脚本**，
而且用户要先读懂预览。用户已经亲手指认过数据时，更小的动作是：**把那份文件复制到脚本要读的位置**，脚本与数据都按原样被读。

## 决定

普通（native）运行失败，且 **#1817 的观察**确证是只读打开某个**绝对路径**落空（不另造观察），并且该路径有用户确认过的数据身份时，
卡片多给一个提议：「复制过去再运行」。用户点了才复制；复制不成落到下面的终态之一，**没有任何一条路停在报错**。

### 一、何时提议（`inputcopy.offer`，挂在 `app._native_missing_input_offer` 产出的载荷 `copy` 字段上）

全部满足才提议，否则载荷里没有 `copy`，卡片走 0110 / #1817 的原有入口：

1. #1817 的条件已经成立（同一异常对象、读且路径一致、对应当前脚本中的读取常量、运行不敏感）；
2. 脚本读的那串是**本机意义上的绝对路径**（POSIX 以 `/` 开头；Windows 有盘符且有根）；
3. 改指表命中它（`figcapture.remap_target`），命中的那份**存在**。用户刚在指认框选了文件 = 已经写入同一张表，不是另一条来路。

能判出「复制注定做不成」的两种（源不是普通文件、目标父目录不存在）直接给终态 c4，免得用户白点一次。

### 二、复制的条件与边界（`inputcopy.run`，`POST /api/engine/input-copy`）

- **请求里没有路径。**提议载荷带一个不透明令牌 `copy.token`（服务端观察到 native 读取落空那一刻存下的 `(项目, 脚本, 读取路径)`，进程内、有上限、按项目绑定），
  `POST /api/engine/input-copy` 只收 `{token, elevate}`：读取路径取自服务端存的那份，**源永远来自改指表**，用户可控的字符串到不了任何文件系统调用
  （CodeQL `py/path-injection` 的污点断在这里；选令牌而不是「在脚本读取常量里找相等项」，是因为 native 观察到的是完整读取路径，
  脚本里可能只有它的目录常量）。端点只认窗口里的会话（与确认改写脚本同一道门，`_script_edit_binding`）。撤销只按设置里存的记录取目标。
- 只复制**文件**；源是目录 → 不复制（c4）。目标**已存在**（任何形态）→ 不复制、不覆盖、视作成功直接重跑；悬空符号链接不穿过去写（c4）。
  父目录不存在 → 不建多级目录（c4）。
- 先用普通权限；**原子写**：同目录临时名写满、`fsync`，再以不覆盖的方式发布（POSIX `link` + 删临时名；盘不支持硬链接退成「查无再 rename」；
  Windows `os.rename` / `MoveFileExW` 不带 `REPLACE_EXISTING`）。失败清临时文件。
- 成功后前端自动重跑。记一条**本机**可撤销记录（项目设置 `input_copies`：目标、源、大小、sha256、方式、时间）；
  撤销（`DELETE /api/engine/input-copy`）只删「大小与指纹都还对得上」的那份。记录不进诊断包、不进项目文件、不上传；
  诊断隐私口径不变：原路径之外没有新内容进诊断。（本 PR 只给后端与接口；设置里的列表与撤销按钮另行拍板。）

### 三、终态表

| 触发 | 终态 | 卡片 | 出路 |
| --- | --- | --- | --- |
| 条件满足，还没点 | `ask` | c1 脚本要从 {path} 读数据 | 主〔复制过去再运行〕次〔换一份数据〕；无 AI、无「稍后」 |
| 目标此刻已存在 | `exists` | —— | 不动它，直接重跑 |
| 复制成功 | `done` | —— | 直接重跑，记撤销记录 |
| 没权限（`EACCES`；Windows `ERROR_ACCESS_DENIED`；macOS 非受保护位置的 `EPERM`） | `denied` | c2 没有权限写到 {dir} | 主〔授权后复制〕次〔改脚本里的路径〕〔让 AI 修复〕 |
| macOS 目标在受保护位置（桌面 / 文稿 / 下载 / iCloud 云盘 / 外置卷）得 `EPERM` | `macos_protected` | c3 {dir} 受系统保护，写不进去 | 主〔打开系统设置〕（完全磁盘访问；不提权，管理员密码解决不了 TCC）次〔改脚本里的路径〕〔让 AI 修复〕 |
| 授权被取消 / 非管理员 / 提权后仍失败 / 超时 / 目标被提权拒绝 | `auth_failed` | c4 没能把数据复制到 {dir} | 〔改脚本里的路径〕〔让 AI 修复〕 |
| 只读介质、写满、网络盘拒绝、Windows 受控文件夹访问 | `auth_failed` | c4 | 同上；**不**提示授权（授权救不了前两种；后两种授权后仍失败也落 c4） |
| 源不是文件、父目录不存在、悬空链接、没有源 | `auth_failed` | c4 | 同上 |

「改脚本里的路径」直接进 #1817 / 0110 的逐行预览：用已指认的源，**不再弹选择器**（`preview` 端点原样）；预览一处都改不了时落回指认框并带原因。
任何终态都有至少两个可点的按钮；c4 永远有两条出路。

### 四、提权只做这一次复制

- **Windows**：引擎用 `ShellExecuteExW("runas")` 拉起**壳自己的可执行文件**（壳在启动 sidecar 时放进环境变量 `TAVOTTO_SHELL_EXE`），
  固定子命令 `--elevated-copy <源> <目标>`。`ShellExecute` 只收一整串参数，所以按 `CommandLineToArgvW` 的规则逐个加引号
  （`subprocess.list2cmdline`），并先拒掉引号 / 控制字符 / 非绝对路径；不经 `cmd` / PowerShell。壳的 `main()` 在崩溃过滤器与 MSIX 脱离之后、建
  Builder 之前调用 `elevated_copy::run_if_requested()`：命令行是这个子命令就**只做一次复制并退出**，不开窗口、不起 sidecar、
  不读写配置、不碰退出标记（`exit_guard_tests` 登记了这一处 `process::exit`）。助手**自己再校验一次**：两个路径绝对且无 `..`；
  源是普通文件；父目录存在；目标一个字节都还没有（含悬空链接）；目标不在 `%SystemRoot%` 下、扩展名不是可执行 / 脚本 / 快捷方式一类。
  退出码 `0` 成功 / `10` 已存在 / `11` 参数不合 / `12` 拒绝 / `13` 失败，与 `inputcopy.HELPER_*` 同源（`tests/test_input_copy.py` 读 Rust 源码对）。
  超时 180 秒不再等；用户取消（`ERROR_CANCELLED`）→ c4。
- **macOS**：`/usr/bin/osascript` 跑一段**固定文本**的 AppleScript（`do shell script "/bin/cp -n " & quoted form of … with administrator privileges`），
  源与目标作为 `argv` 位置参数传入——路径永远不出现在脚本文本里，也不经 shell 拼接；`cp -n` 不覆盖，随后 `chown` 给当前用户。
  之所以选 osascript 而不是 Authorization Services / 特权助手：它是系统自带、弹的是系统授权窗口、不需要在安装包里新增签名的特权 helper（那需要
  SMJobBless / SMAppService 与新的公证面）。反证用例真跑一遍同一段脚本（去掉提权）验 `quoted form` 挡得住空格、引号、分号、`$()`。
  提权后核对：目标存在且与源逐字节相同，否则按失败。
- 两个平台引擎侧都先判 `elevation_refused`（同一张扩展名表 + 系统目录），Windows 助手再判一次。

### 五、不做的事

- 不让 native 读改指表 / 不装读取钩子；不改 safe 的文件系统边界。
- 不复制目录；不建多级目录；不覆盖；不按同名搜索；不自动复制（每次都是用户点一下）。
- 不提权做任何别的事；不缓存授权；不对 macOS TCC 位置提权。
- 浏览器模式没有壳：普通复制照常，提权没有助手 → c4。

## 实现与看护

- 引擎 `src/tavotto/engine/inputcopy.py`；端点与载荷在 `app.py`；前端 `PreparationCard` / `preparationText` / `inputCopyStore`；壳 `src-tauri/src/elevated_copy.rs`。
- 看护：`tests/test_input_copy.py`（边界与每个终态、提权命令形状、Rust 同源）、`tests/test_input_copy_e2e.py`（真 native 运行 → 提议 → 复制 → 重跑成功）、
  `web/src/components/PreparationCard.test.tsx`「缺数据 · 复制提议」、Rust 单测 `elevated_copy::tests`。
- Windows Lab 能测：普通权限复制、ACL 拒绝 → `denied`、助手子命令（非提权）的校验与不覆盖。**UAC 真弹窗 Lab 测不了**，靠助手单测 + 命令形状单测 + 类型检查。

## 风险（交回时逐条说明）

提权助手是一个以管理员身份写文件的入口：路径来自「用户确认过的改指规则 + 脚本里的读取常量」，用户点的是 c1 上写着完整目标路径的按钮，
UAC 窗口本身不显示路径。缓解：助手固定子命令、只收两个路径、自己再校验、拒绝系统目录与可执行类扩展名、不覆盖、不建目录。
残余：用户确认的路径本身若在某个管理员可写的敏感位置（非系统目录、非可执行扩展名），仍会被写入一份用户自己指认的数据文件。
