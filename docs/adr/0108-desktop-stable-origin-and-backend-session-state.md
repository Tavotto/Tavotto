# ADR 0108：桌面 origin 稳定与会话状态的后端权威

日期：2026-09-29 · 状态：**Accepted**（方案 A → B → C → D 由维护者 2026-09-29 在 issue #715 按推荐全部采纳；
本 ADR 随 PR-A 落地，B / C / D 的实现分别由后续 PR 兑现，见 §六「分期」）
相关：[0002 Tauri 桌面壳](0002-tauri-desktop-shell.md)（启动流程与端口，本条修订）、
[0008 统一本机会话认证](0008-unified-local-session-auth.md)（Host / Origin 边界不变，桌面端口不再每次都变）、
[0023 文档落盘的唯一权威](0023-document-persistence-authority.md)（新的会话状态文件同样只经 `atomicio`）、
[0024 保存生命周期](0024-save-lifecycle-and-external-change.md)（本机崩溃副本依赖 origin 稳定）、
[0039 离线教程项目](0039-offline-tutorial-project.md) / [0040 Onboarding](0040-onboarding-coachmarks-and-hints.md)（重置时一并清后端记录；进度格留前端）、
0096「⌘S 保存到项目」（在飞 PR #674，`projectFile.<id>` 迁后端）、issue #715 / #542 第 9 项 / #35 / #646

## 问题

桌面 sidecar 每次都绑 `127.0.0.1:0`，壳导航到 `http://127.0.0.1:<随机端口>/`。localStorage 按 origin
（协议 + 主机 + 端口）隔离，所以**每次启动都拿到一个全新的空存储**（issue #715，Windows Server 2025 实测：
WebView2 的 Local Storage 里 5 个端口 origin 与 5 次启动一一对应）。于是：

- 「上次打开的排版」（`tavotto.currentDoc` / `tavotto.projectDoc.<pj>`）读不到，每次启动都是空白新排版；
  磁盘上的自动保存（`layouts/_autosave/d_*.json`）还在，但界面没有入口找回——顶栏一直写着「已保存」；
- 导出默认值（`tavotto.export.defaults`）丢失：设过 1200 ppi 的用户重启后**静默**按 600 ppi 导出；
- ADR 0002 / 0024 里「本机崩溃副本下次启动恢复」这句在桌面版上**不成立**：崩溃兜底副本
  `tavotto.autosave.<id>` / `tavotto.recovery.<id>` 存在的前提就是后端写不进去，它只能留在前端，
  换了 origin 就再也读不到。

浏览器模式与 e2e 用固定端口（5089 顺延），所以全部测试一直是绿的。

## 裁决

两步都做，分工明确：**origin 稳定保住只能留在前端的东西；后端权威保住不该依赖 origin 的东西。**
端口被占时 origin 仍然会变（稳定是「尽量」，不是保证），所以关键状态不能只靠第一步。

### 一、桌面端口尽量稳定（PR-A，本 PR）

- **记在哪**：壳记在 `app_config_dir()/desktop-port`（与 `menu-locale` 同一处：这台机器上这个人的设置，
  不进项目数据，也不进 `data_dir()`）。JSON `{"port": N, "misses": K}`，tmp + rename 写。
- **怎么传**：stdin 首行 JSON 加 `preferred_port`（`{nonce, parent_pid, preferred_port}`）。字段名与合法范围
  （1024–65535）是**严格同源对**：生产方 `src-tauri/src/sidecar/port_memory.rs`、消费方 `src/tavotto/desktop.py`
  各读 `tests/golden/desktop_preferred_port.json`，不读对方源码。
- **sidecar 怎么绑**：`desktop.claim_listener()` 直接 `localserver.claim()`（bind + listen，选项走
  `apply_bind_options(exclusive=True)`：POSIX `SO_REUSEADDR` 让上个进程的 TIME_WAIT 挡不住同端口重启；
  Windows `SO_EXCLUSIVEADDRUSE`，别的程序抢不走、两个 sidecar 不会共用一个端口）。占不到就在
  `PREFERRED_PORT_RETRY_SECONDS`（2.5 s）内每 0.1 s 重试，再占不到退回端口 0。不「先探后绑」。
  监听 socket 交给 werkzeug 经 `fd=` 接管（与浏览器模式 `serve_browser` 同一条路）——werkzeug 自己 bind
  失败是 `sys.exit(1)`，接不住也就没法重试。
- **为什么要重试**：应用内更新装完走 `process::exit` 重启，旧壳不走 `RunEvent::Exit`，旧 sidecar 要等
  stdin EOF 才开始关——新 sidecar bind 时端口可能还没放出来。只给两三秒：再长用户看到的是 splash 多停几秒。
- **握手文件格式不变**（ready / port / pid / error）。壳按握手里的**实际**端口导航；Host / Origin 校验钉的
  也是实际端口（`state.port`），ADR 0008 的边界一个字不改。
- **首次端口**：在 20000–29999 里随机挑——三个平台的临时端口段（Linux 32768–60999、Windows / macOS
  49152–65535）之外，被别的程序随手占掉的机会最小。首次挑的端口正好被占：这次用系统分配的，**不记**
  （不把临时端口当成「这台机器上的端口」），下次重新随机。
- **被占时**：本次用实际拿到的端口，记住的端口不变，只记一次落空；**连续 3 次**落空才改记为实际端口。
  中间拿到一次，计数清零。记忆策略全是 Rust 纯函数（`preferred` / `after_launch` / `parse`），文件读写只在
  `load` / `store` 两处；坏文件 = 没有记录，只意味着重新随机，绝不卡住启动。
- **端口被占时 origin 仍会变**：这是有意接受的残余。壳的 `lang=` 查询参数（ADR 0002 / docs/i18n.md）因此
  **保留**——它是语言偏好不随 origin 走的那一份。

### 二、前端存储的处置（全量清单）

| 处置 | 键 |
| --- | --- |
| **迁后端**（PR-B） | `currentDoc`、`projectDoc.<pj>`；`docIndex`（它还决定删哪些磁盘槽位——改由后端计算，前端不再按本机索引删磁盘）；`projectFile.<id>`（ADR 0096 / #674）；`export.defaults`（丢了会静默导出错误结果） |
| **留前端，靠稳定端口保住** | `autosave.<id>`、`recovery.<id>`（ADR 0024 §4：它们存在的前提就是后端写不进去）；`onboarding`、`update.dismissed`；`ui`、`inspector`、`assetUsed`、`readinessDismissed`、`ai.*`、`locale`、`tabs.<id>`、`workspace.<id>` |
| **不管** | `__MM_PREVIEW_TRACE__`；sessionStorage 的 `tavotto:project`、`tavotto:skip-restore`（会话级，本来就不跨重启） |

调研中纠正的两个前提：孤儿自动保存里的 `project` 字段是排版自己的 `{id, name}`，不是项目路径——
`_autosave/` 里的文件没有记录属于哪个项目（`PUT /api/autosave` 本来就带 pj，后端认得出，只是没往下记）；
启动时恢复读的是全局键 `tavotto.currentDoc`（`restoreSession`），`projectDoc.<pj>` 只在切项目时用——两个都要迁。

### 三、会话状态以后端为准（PR-B）

- 新建纯标准库模块 `engine/layoutsession.py`，文件 `data_dir()/state/layout-sessions.json`，只经 `atomicio` 写
  （ADR 0023），键是 `normalize_path_identity(项目路径)`。内容：`last{doc_id, name, at}`、`owners{doc_id → 项目}`
  （在 `api_autosave_put` 里按 pj 记），以及 #674 的 `bindings`。
- **不能放**在 `_autosave/`、`layouts/`、项目的 `tavottofile/`：前两者是文档目录（ADR 0023 的保留文件名枚举），
  后者会随项目拷走、把「这台机器上最后打开的是哪份」带到别人机器上。
- 接口 `GET /api/layout-session`、`PUT /api/layout-session/last`，在 ADR 0008 的认证之后，不开任何旁路。
- 前端 `projectDocs.ts` 后端优先、localStorage 只当缓存；后端 404（老后端）退回旧逻辑。`restoreSession` /
  `adoptNow` 改读后端；`lastDocumentIssue` 与 `DocumentBanner` 的机制不变。教程重置时一并清这两条记录（ADR 0039）。
- 槽位清理只由后端做；没有归属的旧槽位在 PR-C 上线前**不参与** 64 份的兜底清理（它们正是要找回的东西）。
- 测试：vitest 用「有状态的假后端 + `localStorage.clear()` + 重新加载模块」模拟换 origin；e2e 在同一服务上开一个
  新的浏览器 context。两者在 main 上都必红。

### 四、找回入口（PR-C，等 #668 / #674 / #679 合入后做）

「打开排版」对话框里加一组「只在本机的排版（未存进项目）」，可打开 / 存进项目 / 删除；下面一个折叠组「本机上
其他排版（不知道属于哪个项目）」。启动时没恢复出排版、但这个项目有可找回的，横幅提示一次。旧槽位按面板的
`fileId` 只标「可能属于」，**绝不自动打开**。新文案统一用「排版」。

### 五、真窗口用例（PR-D，并入 #542 第 9 项）

Windows 实测：`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` 会被 Tauri 自带的参数覆盖，HKCU 策略无效；
HKLM 策略 `SOFTWARE\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments`（值名 `Tavotto.exe`）有效。
msedgedriver 153 以 `debuggerAddress` 附着模式能驱动真实窗口：本 issue 场景下「重启」用例红、同 origin 刷新的
对照用例绿。

### 六、分期

| PR | 内容 | 状态 |
| --- | --- | --- |
| A | §一 稳定端口 + 本 ADR + 被修订 ADR 的注记 | 本 PR（`Refs #715`） |
| B | §二「迁后端」一栏 + §三 | 后续 PR |
| C | §四 找回入口 | 后续 PR，依赖 #668 / #674 / #679 |
| D | §五 真窗口用例 | 后续 PR，并入 #542 第 9 项 |

A 先合：它不改任何数据格式，合入后的第一次启动就开始记端口，此后崩溃兜底副本才真正「下次启动能恢复」。
B 之前的过渡期里，端口被占的那一次启动仍会丢「上次打开的排版」——这是已知残余，由 B 关掉。

## 被修订的 ADR

在各自末尾加了修订注记（指回本条）：0002（启动流程 / 端口一行 / 崩溃副本恢复的前提）、0008（桌面端口不再每次都变）、
0023（新状态文件的落盘）、0024（§4 本机副本依赖 origin 稳定；§9「没有 index.json」由 `layout-sessions.json` 部分改写）、
0039（重置时清后端记录）、0040（`tavotto.onboarding` 的存续依赖 origin 稳定）。
**0096 在飞**（#674，尚未进 main）：它的 `projectFile.<id>` 按 §二 迁后端，修订注记随 #674 与 PR-B 中后合的那个补上。

## 不做

- 不让端口「保证」稳定（比如被占时去杀占用者、或顺延扫描 20000 段）：杀别人的进程不可接受；顺延扫描换来的
  仍是一个新 origin，与退回系统分配没有区别。
- 不把崩溃兜底副本搬到后端：它存在的场景正是后端写不进去。
- 不在浏览器模式做任何改动：它已经用固定首选端口 5089（ADR 0008）。

## 验证（PR-A）

- `tests/test_desktop_sidecar.py`：空闲的建议端口被采用；被占时回退且 Host 校验跟实际端口走；两个 sidecar 建议同一
  端口时后来的退回；被占后在重试窗口内释放仍拿到原端口；停下的 sidecar 立刻放端口；真进程两次启动落在同一端口
  （第一次关闭时带一条开着的 SSE，服务端留下 TIME_WAIT——POSIX 上先证明不带 `SO_REUSEADDR` 的 bind 确实被挡，
  前提成立才看结论）；stdin 字段按 golden 同源（非法值忽略、不连累凭据）。
- `src-tauri/src/sidecar/port_memory.rs` 单测：首次范围、连续 3 次落空才改记、中间命中清零、首次落空不记、
  坏文件回退、读写往返、golden 同源。
- 每条判据都做了变异反证（PR 正文有表）。**Windows 上 `SO_EXCLUSIVEADDRUSE` 碰上 TIME_WAIT 能否立刻重绑**
  只有 CI Windows 腿（backend-platforms，需 full-ci）上那条真进程用例的结论算数。
