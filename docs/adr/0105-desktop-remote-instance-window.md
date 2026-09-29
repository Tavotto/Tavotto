# ADR 0105：桌面版「连接远程实例」——单独的无痕窗口，本机文件类能力一条不给

日期：2026-09-28 · 状态：**Accepted**
相关：[0002 Tauri 桌面壳](0002-tauri-desktop-shell.md)、[0008 会话认证](0008-unified-local-session-auth.md)、
[0092 主页拖放拿真实路径](0092-home-native-file-drop.md)、PR #594（`--no-browser` 再打印新登录地址）

## 问题

脚本、数据、Python 环境与算力常在服务器上。0.17.0 已经能远程用：服务器上
`tavotto --no-browser`，本机 `ssh -L 5089:127.0.0.1:5089`，浏览器打开登录地址——
2026-09-24 实测运行、编辑、写回、文件变动事件全部通过。缺的是**桌面版**：壳永远只加载
自己拉起的本机 sidecar，没有连别的引擎的入口。

不做「SFTP 把文件拉到本机」：Tavotto 要**运行**脚本，脚本依赖的数据与环境在服务器上，
拉下来多半跑不起来；写回也要从「verify 不过则原文件零改动」变成「本地副本 + 上传」，
多出上传中断与服务器端并发修改两类失败。引擎留在服务器上跑是唯一不动写回事务的路。

**1.0 收敛纪律**：这是扩大产品能力。2026-09-28 用户看过方案草稿后明确要求做第一步
（「连接远程实例开始做吧」），按用户授权登记；第二步（壳里托管 ssh）仍待定。

## 裁决

### 一、单独的窗口，不复用主窗口

「文件 → 连接远程实例…」开一个 label 为 `remote` 的窗口，先停在壳自带的
`shell/connect.html`（tauri:// 源），用户粘进服务器打印的登录地址，`connect_remote`
命令验过后把这个窗口导航过去。主窗口与本机 sidecar 一个字节不动。

不复用主窗口：主窗口的 ACL 带着全部本机文件类能力（目录选择、在 Finder 中显示、
拖放路径、Codex 集成安装、应用内更新）。Tauri 的 capability 按**窗口 label + 源**
授予，而两种引擎都在 `http://127.0.0.1:*` 上——同一个窗口里按源分不开，只能按窗口分。

### 二、无痕存储（`incognito(true)`）

cookie 按主机隔离、**不按端口**：本机 sidecar 与远程引擎都往 `127.0.0.1` 写同名的
`tavotto_session`。共用一个存储的后果有两个：连上远程就把本机会话顶掉（主窗口满屏 401）；
本机会话 token 随每个请求发到远程引擎——服务器上别的用户若抢先占了那个端口，就拿到了它。
无痕存储每个窗口一份、关窗即清。代价：关掉远程窗口再开要一个新的登录地址——服务器上
再运行一次 `tavotto --no-browser` 就打印新的（#594）。

### 三、远程窗口的权限是一张闭集

| capability | 作用域 | 权限 |
| --- | --- | --- |
| `remote-connect.json` | `remote` 窗口的壳自带页面（`local: true`，无 `remote`） | `allow-connect-remote` |
| `remote-engine.json` | `remote` 窗口的 `http://127.0.0.1:*`（`local: false`） | `core:event:allow-listen`、`core:event:allow-unlisten`、`allow-arm-close-guard`、`allow-resolve-close-request` |

- 事件**只许听不许发**：`core:event:default` 含 emit，远程页面能借它给主窗口伪造
  `tavotto:open`（打开任意本机目录）或 `tavotto:menu`。
- 主窗口拿不到 `allow-connect-remote`。
- `tests/test_desktop_remote.py` 把这张表钉成闭集：多一条、换成 default、给了别的窗口都红。

### 四、前端：`isDesktop()` 改义为「壳 + 本机引擎」

壳往远程窗口注入 `window.__TAVOTTO_REMOTE_ENGINE__ = true`（`REMOTE_WINDOW_MARKER`）。
`web/src/lib/desktop.ts`：

- `hasDesktopShell()`：有壳就是真。菜单转发、关窗询问按它判——远程窗口照样收菜单、照样问关窗。
- `isDesktop()`：有壳**且不是**远程窗口。本机文件类能力全部按它判；所有既有调用点（导出后
  在 Finder 中显示、原生目录选择、Codex 面板、应用内更新……）不改一行就在远程窗口里退回
  浏览器模式的行为——远程引擎本来就是浏览器模式，这些回退正是为它写的。

ACL 是第二道：前端判据写错了，本机命令也 invoke 不动（点了没反应，而不是越权）。

### 五、连之前问机器要能力标记，不按版本号猜

页面由远程引擎供，壳管不了它的前端是哪一版。0.17.0 的前端不认得远程窗口，会去调本机
对话框（被 ACL 拒，用户看到一串失败）。所以 `connect_remote` 先 `GET /api/version`
（公开端点），要求 `features` 里有 `desktop-remote-window`，没有就回 `too_old`，
`connect.html` 提示在服务器上 `pip install -U tavotto`。字面量两侧各一份
（`app.py::DESKTOP_REMOTE_WINDOW_FEATURE` / `remote.rs::REMOTE_WINDOW_FEATURE`），由用例比对。

### 六、菜单跟焦点、关窗询问按窗口

- `tavotto:menu` 发给当前聚焦的窗口：远程窗口在前台时 ⌘S 存的是远程的排版。
- 关窗询问闸从一个变成按窗口 label 一份（`close_gates`）；询问只发给被拦的窗口，看门狗
  只关它。「拦的入口只有 `hold_window` 一处」不变。
- 远程窗口销毁时收掉它的闸与放行端口。
- **结果按窗口代次认领**：label `remote` 可复用，关了再开是同名的另一个窗口。每次开窗、每次
  销毁都换一代（`remote::RemoteSlot`）；`connect_remote` 探测前记下代次，回来时代次不符
  （途中关窗 / 重开）就整个丢掉——不放行端口、不导航，导航也只对发起调用的那个窗口实例做。
  导航守卫只放行本代的端口。关窗闸在销毁时只复位不删，代号接着数，上一个窗口还在睡的看门狗
  关不到同名的新窗口。

### 七、只收 `127.0.0.1`

登录地址只认 `http://127.0.0.1:<端口>/…#dnonce=<[A-Za-z0-9_-]+>`：引擎的 Host 校验只认
这一种写法（ADR 0008），壳也绝不自己去连外部主机——转发由 ssh 负责。导航地址由解析出的
端口与口令**重新拼**，用户粘进来的原文不交给 webview。导航守卫按窗口各持一个端口：远程
窗口没连上时引擎源一个都不放，连上后只放那个端口的根路径。端口等于本机 sidecar 时拒绝
（`is_local_engine`）。

## 不做 / 留待以后

- **壳里托管 ssh**（第二步）：壳 spawn 系统 `ssh -L p:127.0.0.1:p host tavotto --no-browser --port p`，
  自己取登录地址。需要引擎给一种机器可读的登录地址输出、只支持密钥 / agent 认证、处理非交互
  shell 的 PATH。未排期。
- **Host 校验放宽到任意本地端口**：`-L 15089:…:5089` 仍然 403，用户要让两边端口相同。
  `connect.html` 的第 2 步写明了；改 Host 规则要重新过 ADR 0008 的威胁模型，不在这里做。
- **远程窗口的界面语言**：无痕存储里没有 localStorage 偏好；壳只在用户亲手选过语言时带
  `?lang=`（与主窗口落地 URL 同一条规则），否则跟随系统语言。
- **多个远程窗口**：一次一个。已开着时菜单项只把它提到前台。

## 验收

- Rust：`remote.rs` 的登录地址解析矩阵（localhost / `[::1]` / 外部主机 / 缺口令 / 口令带
  URL 字符）、`/api/version` 回应判据（新 / 老 / 陌生服务 / 403 / 空回应）、对真 TCP 端口的
  探测与 Host 头；`main.rs` 的菜单跟焦点、导航按窗口端口、`shell-connect-remote` 由壳处理。
- Python（`tests/test_desktop_remote.py`）：能力标记与窗口标记两侧同源、错误 code 两门语言
  齐全、远程窗口权限闭集、远程窗口建造链带 `incognito(true)` + 标记 + 导航守卫且不装
  native_drop、`desktop.ts` 每个本机能力按 `isDesktop()`、壳能力按 `hasDesktopShell()`。
  每条都做过一次变异反证（14 条，全部打红）。
- vitest（`desktop.test.ts`）：远程窗口下本机能力一次 IPC 都不发、菜单与关窗询问照常。
