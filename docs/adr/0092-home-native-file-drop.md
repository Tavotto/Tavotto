# ADR 0092：主页拖放拿真实路径——旁听，不接管

日期：2026-09-26 · 状态：**Accepted**
相关：[0002 Tauri 桌面壳](0002-tauri-desktop-shell.md)、[0008 会话认证](0008-unified-local-session-auth.md)、
[0040 Onboarding](0040-onboarding-coachmarks-and-hints.md)、`docs/rules/frontend/onboarding-and-activity.md`

## 问题

主页（新手 / 老手两版，PR #665）有一块「把 Python 脚本拖到这里」。用户拍板：拖进 `.py`
要**直接**打开它所在的项目，不再弹选择器。

页面拿不到路径：浏览器与 WebView 的 `DataTransfer.files` 只有名字与内容。能给路径的是
Tauri 的拖放处理器，而壳在 8985b9e9c 起就把它关了（`disable_drag_drop_handler()`）：
处理器一旦装上，`tauri-runtime-wry` 的回调**无条件返回 true**，wry 就不再把
`draggingEntered / draggingUpdated / performDragOperation` 交还给 WKWebView——页面里所有
HTML5 拖放（素材拖进画布、画布 / 图层 / 工作区列表排序）整片失效。这个开关只能在建窗口
时定，运行中切不了，所以「主页开、编辑器关」这条路不存在。

## 裁决

### 一、macOS：只旁听 `performDragOperation:`，原实现照常跑

`src-tauri/src/native_drop.rs` 在主窗口的 `WryWebView` 类上换掉 `performDragOperation:`
这一个方法：先从拖放粘贴板读 `NSFilenamesPboardType`（只有从 Finder 拖来的文件才有；
页面内部的 HTML5 拖动没有），交给 `drop_paths::classify` 分派、发 `tavotto:file-drop`，
然后**调用原实现**。Tauri 的处理器保持关闭，页面拿到的 drop 与以前逐字节相同。

装上前先记下原实现；装不上（找不到类或方法）就不装，`native_file_drop` 命令回 `false`。

### 二、视图分派放在前端

两种候选：壳按「窗口里现在是哪个视图」决定发不发；或壳一律发、前端按视图决定。选后者：

- 壳不知道页面状态，要知道就得多一条「前端告诉壳我在主页」的通道，状态两份，会漂；
- WebKit 只在页面的 `dragover` **接受**了这次拖动时才走到 `performDragOperation:`——
  编辑器画布不接受外部文件，旁听点根本不被调到；
- 前端只有主页挂着时订阅事件（`HomeView` 的 effect），离开主页即退订，事件落空。

同一次放下会从两条路到达主页：页面的 DOM `drop`（只有名字）与壳的事件（异步 IPC）。
`lib/scriptImport.createDropArbiter` 仲裁：能拿路径的壳里 DOM 那条先不降级，等 1.5 s，
期间壳的事件到了就只用它；真没等到才走降级。

### 三、路径规则（`drop_paths::classify`，纯函数，Rust 单测）

- 只认真实存在的本地**绝对**路径：原串必须是绝对路径，再 `canonicalize`（解符号链接与 `..`），
  失败的丢掉；Windows 的 `\\?\` 前缀去掉。
- 第一个 `.py` 赢：打开它的上级目录，脚本名随事件交给前端（通知里说出是哪个脚本）；
  没有 `.py` 时第一个文件夹当项目；都没有就是「不支持的类型」，前端说不收。
- `ignored` = 没被采用的条目数，前端说「拖入了 N 项，只用了 x」。

### 四、安全边界不动

- 事件只带一条路径建议；打开项目仍走 `/api/projects/open`，会话认证（ADR 0008）与后端对
  路径的校验一步不少。壳不开第二条打开项目的通道。
- 新命令 `native_file_drop` 只读、无参、回 bool；三处登记（`build.rs` / `capabilities/main.json` /
  `generate_handler`），`tests/test_desktop_file_drop.py` 看护三处与两侧同源（事件名、kind 闭集）。

### 五、其它平台与浏览器

Windows / Linux 暂不旁听（WebView2 需要 `postMessageWithAdditionalObjects` 那条路，另立项），
`native_file_drop` 回 `false`；浏览器模式同样拿不到路径。这些宿主保留降级：提示文件名、
弹选择器，拖放区的说明如实写「拖入后还要在弹出的窗口里选一次它所在的文件夹」。

## 后果

- macOS 桌面上拖 `.py` / 文件夹到主页即开；编辑器里的拖放不变。
- 换 wry 版本时要复核 `WryWebView` 仍自己实现 `performDragOperation:`（否则换掉的会是
  `WKWebView` 上的实现，影响面变大但行为仍是「旁听 + 调原实现」）。
- 真实拖放在隔离环境里验不了（不能向系统注入拖放），合入前需要一次真机手测。
