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

### 六、拖入 / 选中的是 .py：打开之后运行**那个**脚本（2026-10-21 补）

只打开上级目录不够：同一文件夹里拖进不同脚本，打开的都是同一个项目，画布恢复的是上次的排版，
用户看到的永远是第一张图，通知却说「已打开 b.py」（2026-10-21 合伙人诊断包：同一项目两次打开、
之后零次运行）。

入口给出的是一个 `.py`（系统拖放的 `script` 事件、带 file:// 路径的页面 drop、桌面壳的原生文件选择器）时，
项目打开成功之后接着对这个脚本发起**脚本库 ▶ 同一条**「打开并运行」——
`projectPreparationStore.open(scriptTarget(<文件名>, { reuse: true }), { run: true })`：检查 → 认领后端的
`run_when_ready` → 运行，恰好一张图就**放进当前画布**并进入编辑，多张图 / 缺参数 / 缺依赖由准备卡片接着说。
不另开第二条运行路径，面板身份、去重、safe→native 回退都沿用那一条。

- 脚本相对项目根就是它的文件名：打开的项目根正是它的上级目录。
- 只给目录的入口（拖文件夹、目录浏览器、浏览器模式退回的选择器）不运行任何脚本，不替用户挑。
- 项目没打开成（`openPath` 回 false）就不运行。
- 文件名带首尾空白（` plot.py`）只开项目、不运行：后端各脚本端点按约定 `strip()` 脚本名，会找不到它或跑错同目录的 `plot.py`（Codex #1267，后端统一支持见 #1278）。
- 看护：`HomeView.test.tsx`（壳事件 / 页面 drop / 选择器三处各自对哪个脚本发起、文件夹与失败不发起）、
  `e2e/home-drop-run.spec.ts`（真后端：同一文件夹先后拖入 a、b，各跑一次，画布上两张图都在）。

## 后果

- macOS 桌面上拖 `.py` / 文件夹到主页即开，`.py` 还会接着运行并把图放进当前画布（§六）；编辑器里的拖放不变（2026-10-27 修订：编辑器画布也收 `.py`，见文末）。
- 换 wry 版本时要复核 `WryWebView` 仍自己实现 `performDragOperation:`（否则换掉的会是
  `WKWebView` 上的实现，影响面变大但行为仍是「旁听 + 调原实现」）。
- 真实拖放在隔离环境里验不了（不能向系统注入拖放），合入前需要一次真机手测。

## 修订：编辑器画布也收 `.py`（2026-10-27）

用户拍板：在编辑器里把 `.py` 从系统拖到某张画布上，是**明确要放进这张画布**——运行它，跑出来的图放进这张画布、落在放下的位置，
不按「打开 = 换看那一张」新建画布。脚本库 ▶、主页拖入、弹窗里的「运行」等其余入口照旧新建画布。

- 画布的 dragover 现在接受系统文件（`useCanvasScriptDrop.acceptsScriptDrag`；素材卡的内部拖动照旧走 `application/x-panel-id`），
  于是 macOS 壳的旁听点在编辑器里也会被调到。编辑器订阅 `tavotto:file-drop`，但**只认与画布 DOM `drop` 配上对的那一条**
  （`lib/canvasScriptDrop.createCanvasDropPairer`）：页面别处接受了文件拖放（如联系作者的附件区）时壳同样会发，那不是拖到画布上，不运行。
- 只运行当前项目里的脚本（`projectRelativeScript` 按字面比项目根；项目外不切项目、不运行，说一句回主页拖入）。拿不到路径的宿主：
  桌面壳退回系统选择器，浏览器说清去左栏脚本库运行。
- 意图记在准备会话条目上（`PrepEntry.placeIn`：画布 id + 页面毫米落点）。卡上「继续」与 AI 修好后的验证重跑是同一个意图的延续，
  原样带过去，所以修好后照样放回这张画布；`enterEdit` 先切回那张画布再放图（期间用户切走了也一样），那张画布已经不在了就按普通「打开」走。
