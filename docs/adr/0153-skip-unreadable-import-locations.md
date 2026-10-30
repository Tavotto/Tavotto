# ADR 0153：import 时跳过「找到了但读不了」的位置（safe 与 native 同装），并把残余失败说成一句话

日期：2026-10-30 · 状态：**Accepted**（用户 2026-10-30 裁决 A + B；反馈包 TVD-HZ0A，0.19.1 Windows；同日修订：一键修复权限）
相关：[0014 safe / native 执行档](0014-safe-native-execution-profiles.md)、[0020 native matplotlib bridge](0020-native-matplotlib-bridge.md)、
[0057 首开环境与工作目录](0057-first-open-environment-and-workdir.md)；规则全文 `docs/rules/backend/execution-entries.md`「跳过读不了的 import 位置」。

## 动机

用户脚本从脚本目录逐级往上找 `.work_sensor_plot_deps_<日期>`，找到就 `sys.path.insert(0, dep)`。这个文件夹在项目外面，是 Codex
沙箱账户建的，当前 Windows 账户没有读权限。`import pandas` 时要去 dep 里找 `tzdata`，读 `tzdata\__init__.py` 抛
`PermissionError: [Errno 13]`；同一个 worker 里第二次运行又报 `partially initialized module 'pandas'`。

CPython 的行为是：`sys.path` 某处**找到了**模块、却读不了它，就地失败，**不会接着往后找**（mac 上 `chmod 000` 可复现）。
AI 修复换了两代受管环境，一点用都没有——换环境改变不了这个脚本自己放进 `sys.path` 的目录。用户的脚本永远不改。

## 决定

### A. 查找器：只在「读不了」时换下一个位置

`bridgeboot.UnreadableImportSkipper`（纯标准库、3.10 可跑）挂在 `sys.meta_path` 里**紧挨 `PathFinder` 之前**（`BuiltinImporter` /
`FrozenImporter` 的优先级不变）。它替 `PathFinder` 做**同一次**查找：

* 命中的 spec 是文件加载器（源码 / 无源码字节码 / 扩展）、origin 打开抛 `PermissionError`、且它的缓存字节码按 CPython 自己的规则也用不上（magic、marshal 可解、时间戳型与源码 mtime/size 相符；checked-hash 型因源码读不了而无效、unchecked 有效）
  （有效缓存 CPython 本来就能加载，不算读不了；过期或损坏的缓存会被 CPython 拒掉再去读源码，照常跳过）→ 在候选的**副本**里去掉命中的那一条（包、单文件模块、子包都认；
  相对路径与大小写按 `normcase(abspath)` 比较），继续找；
* 后面找到了可读的 → 返回它，并记一条事件；
* 后面没有可读的了（或认不出命中的是哪一条）→ 返回**最初**命中的 spec，loader 原样抛最初的 `PermissionError`；
* 其余一切（能读的路径、找不到的模块、命名空间包、别的异常、`ImportError`）一字不变，不吞不改写；`sys.path` / `__path__` 本身从不修改。

装配点**只有一个**：`bridgeboot.install_unreadable_import_skipper()`。safe worker（`worker.py` 装好 `bridgeboot` 之后）与
native bridge（`bridge_runner.py` 同位置）各调用一次；`worker.py` 另传一个回调往 worker.log 写 `[import-skip] {json}`，native 不写 stderr
（那是用户的终端）。

**这是对 native「与 `python fig.py` 一致」契约的有意偏离**：`python fig.py` 在同样的机器上会失败。偏离只发生在「本来必然失败」
的那一刻，且换来的结果是脚本原来想要的那个模块——不会出现 `python fig.py` 成功而 Tavotto 失败的反向分歧，也不会把能读的位置换成别的。

### 留痕

每次成功跳过记 `{module, entry, error}`（同一模块同一位置一次，最多 64 条）：

* worker.log：`[import-skip] …`（safe；路径由诊断包统一脱敏）；
* 回执 `runtime.import_skips`（safe 与 native，经 `figsession.runtime_report(import_skips=…)`）。`receipt.project_import_skips` 是它进入
  公开面的唯一一处：公开只有模块名（点分标识符）与 `PermissionError`；被跳过的位置（机器路径）只在 `include_private`（诊断包）里。

### B. 跳过之后仍失败：先走「缺包」一键安装，兜底才是一句话

用户硬性要求（2026-10-30）：失败卡必须带一个点了就能解决的主按钮，不出难题。查找器跳完所有位置仍没有可读的模块时，对当前解释器来说
这个模块就等于「缺包」：装进受管环境后，下次运行时查找器跳过读不了的那一处，就能找到新装的这份（Windows 11 真机已验证）。

worker / bridge 在脚本失败时判一次（`bridgeboot.unreadable_folder_fact`）：异常链里有带文件名的 `PermissionError`（errno 13）、
文件是查找器当场登记过的「命中了但读不了」的模块文件（脚本自己读写该目录下数据文件撞到的权限错误不算 import 失败）、落在某个**脚本运行期间新加进 `sys.path`** 的目录下、该目录在项目根之外（且不包住项目）、也不在解释器自己的前缀 / 包目录里。
成立则随错误带 `unreadable_folder: {folder: 目录名, module: 读不了的顶层模块名?}`（只有名字，不带路径；敏感运行里这个键过不了白名单）。

父进程在错误收口（`pool._error_of` / `pool._worker_error`，native 在 `app.py` 的脚本错误出口，同一个 `pool.unreadable_folder_error`）：

1. **`module` 能映射到 PyPI 包名**（`depresolve` 现有的 curated 表或同名预填，装不了的如 ROOT 除外），且这个 (模块, 文件夹) 第一次出现 →
   产出**现有的 `missing_dependency` 错误**（带模块名），用户看到现有的「安装」卡，一键装进受管环境。`app.py` 再用可信解析核一次：
   给不出安装名（没有 `requirement`）就不出「换环境」的缺依赖卡，改落到第 2 条。
2. **映射不到、或同一 (模块, 文件夹) 已送去安装过仍然失败**（记账只在依赖安装**真正成功提交**之后——`deprepair._run_install` 的两个成功终态调 `pool.note_unreadable_install`——并按项目区分；给错误分类从不记账，用户重试渲染仍有安装出路；不循环出安装卡）→ 稳定码 `import_folder_unreadable`：
   默认只露一句话（「脚本加载的项目外文件夹“X”读不了」+ 该做什么）。Windows 上主按钮是「修复权限」（见下「修订 2026-10-30：一键修复权限」）；
   其它平台没有令牌、没有按钮，保持一句话。

AI 修复：`fixsession.SYSTEM_PROMPT` 的 3g 写明——装进 Tavotto 受管环境有效（读不了的那一处会被跳过）；单纯换解释器无效；
不改用户脚本，不动那个文件夹。

## 后果

* 少数「本来就该失败」的 import 现在会成功——前提是后面确有另一个可读位置；读的永远是同一个模块名下 `sys.path` 顺序里下一个可读者。
* 每个未缓存的 import 多一次 `open(origin)`；热路径实测见 `tests/test_unreadable_import_skip.py::test_overhead_is_small`。
* 无新增遥测、无新增端点、无存储格式变化；回滚 = 删掉两处装配调用。

## 修订 2026-10-30：一键修复权限（Windows）

用户硬性要求：兼容性问题一定要让小白点一两下就能解决，不出难题。上面 B.2 的兜底卡（装了包同一处仍读不了、或模块装不了）原先只能告诉用户
「去给账户开放读取权限」——对小白就是难题。这一节给它一个主按钮「修复权限」。

**这是在用户点击确认之后，修改项目外一个文件夹的权限**——Tavotto 此前从不改项目外的任何东西，所以把边界写死在这里：

* **为什么可以接受**：① 只改**这一个**目录（失败卡点名的那个依赖夹），不改它的父目录，不碰目录外任何东西——**不跟随链接**：
  目录里只要有符号链接 / 联接点（重解析点）就整个拒绝（`repair_has_links`），条目超过 20000 也拒绝（`repair_too_large`），
  两条命令都带 `/L`（对链接本身操作）；② 只做「恢复继承」：`icacls <目录> /setowner *S-1-5-32-544 /T /C /L /Q`
  把属主设成 Administrators（用 SID，**不依赖系统语言**；不再用 `takeown`，它的确认值 `/D Y` 随语言变，德文要 `J`），再
  `icacls <目录> /reset /T /C /L /Q` 让它重新继承父目录的权限——不加任何特殊授权、不放开给 Everyone；③ 用户亲手点按钮，再亲手过一次 UAC
  （`ShellExecuteEx runas`）；取消 / 不是管理员就什么都不改，卡片如实说一句，不重复弹窗；④ 目录本来就是脚本自己放进 `sys.path` 的、
  用户的 Codex 沙箱账户创建的依赖夹（`pip --target` 先装进不继承权限的 `mkdtemp` 再 move 进去，所以属主是沙箱账户）。
* **令牌，不是路径**：worker / bridge 在失败事实里另带私有键 `unreadable_folder_private.{path, file}`（敏感运行的白名单不放行它）；
  父进程 `pool.unreadable_folder_error` 把它换成 `folderrepair.issue()` 的服务端令牌就地丢弃——完整路径只活在 `folderrepair` 的进程内存里，
  不进 `err.extra`、公开投影、遥测与日志。令牌不透明、一次性、绑定（项目、目录真实路径）、15 分钟过期；非 Windows 不发。
  失败参数 `params.repair_token` 只有前端的按钮用它。
* **接口只收令牌**：`POST /api/engine/folder-repair {token}`；要「用户在页面上点了」（`_page_authenticated`，本机凭据头 / MCP / 无会话状态 fail closed）。
  动手前 `folderrepair.validate_target` 对**真实路径**再验一遍：本地绝对路径、是目录、不是盘符根、不是用户主目录本身 / 主目录的祖先 /
  主目录下的标准文件夹（Desktop、Documents……）、不是系统目录（Windows、Program Files、ProgramData）、仍在项目之外且不包住项目、
  与登记那刻的真实路径一致（挡住登记后换成联接点）；通过后 `scan_for_links` 不跟随地遍历整棵树查重解析点（当前账户列不出的子目录跳过，交给下一条）。命令只对验过的真实路径动手；还要登记的那个文件此刻**确实仍读不了**，
  读得了就不弹 UAC。
* **提权在 sidecar 发起**（Flask 父进程，纯标准库 `ctypes`），不放桌面壳：浏览器模式同样能用，不新增 `#[tauri::command]` 与 ACL。
  两条命令包进**一个** `powershell.exe -EncodedCommand`，脚本在任何改动之前先以管理员身份再递归复查一遍重解析点（`Get-ChildItem -Recurse -Force -Attributes ReparsePoint`，不加 `-FollowSymlink`；有就 `exit 201`、什么都不改，防预检与执行之间被换成链接；复查自己失败 `exit 202`）（每个参数是单引号字面量，不经 cmd 的 `%` / `&` / `^` 展开），只弹一次 UAC；
  系统工具与 PowerShell 都用 `SystemRoot\System32` 下的绝对路径，不吃 PATH。跑完再复核那个文件是否读得了：退出码 0 但仍读不了也算失败。
* **结局**（稳定码，文案固定、不带参数）：`repair_cancelled`（UAC 取消）、`repair_not_elevated`（没拿到管理员权限）、`repair_failed`
  （命令失败 / 仍读不了）、`repair_has_links`、`repair_too_large`、`repair_token_invalid`、`repair_target_rejected`、`repair_not_confirmed`、`repair_in_progress`、`repair_unsupported`。
  取消与没拿到提权不作废令牌（可自己再点一次，每次点击至多一次 UAC）；跑过命令（成败）、目标被拒、发现链接 / 超限就作废。
* **前端**：准备卡 `folder_unreadable` 态——一句话点名文件夹 + 主按钮「修复权限」（没有令牌 = 原来的通用失败卡）；成功后沿用卡上
  「再试一次」同一个动作重跑一次；失败 / 取消的一句话放在卡片标题上，不自动重试。

后果：新增一个会改项目外权限的端点，但被令牌、页面点击、路径再验、一次 UAC 四道门夹着；回滚 = 删 `folderrepair` 与卡片的一个主按钮，
失败卡退回一句话。看护 `tests/test_folder_repair.py`（含 argv 精确断言与路径验证每一条的拒绝用例）。
