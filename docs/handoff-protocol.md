# 交接协议：外部程序怎么找到 Tavotto、怎么把一张图交给它

面向的是**调用 Tavotto 的程序**——Codex 插件、编辑器扩展、Makefile、别的
Agent，以及用户自己在终端里敲的那一行。协议版本 **v1**（`protocol: 1`）。

设计与取舍见 [ADR 0005](adr/0005-external-handoff-and-codex-plugin.md)。

---

## 1. 命令行接口

```
tavotto open <产物|脚本|目录> [--json] [--no-launch] [--desktop|--browser] [--port N]
                              [--no-probe] [--stem <名字>] [--render-wait SECONDS]
tavotto run  [--project P] [--quiet] [--status-file F] -- <python> <脚本|-m 模块> [参数…]
tavotto doctor [--json] [--write-manifest|--remove-manifest]
```

* `--no-launch` —— 只做**解析目标 + 登记注册表 +（脚本按需）safe probe**，
  不唤起任何界面（浏览器也不开）。
* 不带 `--no-launch` —— 登记完唤起界面：**装了桌面版就开原生窗口**，没装才退回浏览器。
* `--json` —— 输出一行机器可读 JSON。**成功和失败都有**，失败那行带稳定的 `code`。
* `--no-probe` —— `.py` 目标静态解不出产出时**不**试运行（只按现有登记打开）。
* `--stem <名字>` —— 脚本产出多张图时显式选哪张（只对 `.py` 目标有效）。
* `--origin-json FILE` —— **仅记录模式**（加性，`protocol` 仍为 1；ADR 0139 PR3 第一片）：读 FILE 里
  插件在**真的跑了脚本**之后收集的来源事实，按产物 PNG 的字节 sha256 写进 `data_dir()/origin/`，
  输出 `{"ok": true, "origin": {"recorded": N, "skipped": M}}`；不登记、不唤起、不执行。
  记不进去也退出 0（只是线索）；老 CLI 不认识这个参数（argparse 退出 2），插件当作没记。
  记录只做展示：不进 exact / needs_confirmation 的判定，不用来换解释器。
* `--render-wait SECONDS` —— 唤起后最多等多久，看界面对目标图的那一次渲染有没有结论
  （默认 20；0 = 不等；`--no-launch` 下无意义）。见下文「交接三态」。

`tavotto run` 是**另一条命令、另一套语义**（Beta，ADR 0021）：它不"交接一张
已经画好的图"，而是**用你自己的 Python 跑你自己的脚本**，Tavotto 接管那个
进程里创建的 Figure。完整契约见
[`docs/compatibility/tavotto-run.md`](compatibility/tavotto-run.md)。两条与本文
其余部分不同的地方值得在这里点名：

* **它没有 `--json`。** stdout 是用户程序的（`print` / `tqdm` / 二进制输出都
  在那条流上），承诺"只有一行 JSON"与那条语义直接冲突。机器可读结果走
  `--status-file <路径>`（原子写、不含 token、argv 只记数量）。
* **退出码不是 0/1 两档**：命令写错 = 2，用户取消 = 3，桌面不可用/连接失败
  = 4，界面上点了"终止脚本" = 5；**脚本一旦启动，返回的就是它自己的退出码**。

### `.py` 目标的 safe probe（2026-08-26，Compatibility Bridge PR 1）

用户显式给出 `.py` 就是运行意图。行为顺序：

```
解析项目 → 静态发现/现有注册表
  → 每张图都已有有效路由（磁盘原件 或 runtime cache）→ 直接复用
  → 否则安全试运行一次（safe 档：沙盒 cwd、savefig 拦截捕获、相对路径
    只读回退），捕获的 Figure 登记成 RuntimeFigureAsset 并物化预览 cache
```

* 本机已有 Tavotto 实例在 `--port` 上跑时，试运行**委托给它**（同一个
  并发闸：同脚本并行第二次拿 `probe_in_progress`；热会话与 cache 留在
  实例手里，随后的交接零重跑）。没有实例才在 CLI 进程内跑，返回前
  worker 一律关净（不留 orphan），交接过去的进程读注册表 + cache，
  **绝不重复执行脚本**。
* 单张图：直接定位打开（成功 payload 的 `stem`）。
* 多张图：**不静默选第一张**。`--stem` 显式选；带界面的调用把选择信息
  交给界面的 Figure 选择器（payload 的 `pick` = 脚本相对路径，`figures`
  列出每张：`{stem, asset_id, artifact, cached}`）；`--no-launch` 的机器
  调用必须显式选，否则失败 `multiple_figures_found`（`figures` 在 extra
  里，按它重调一次 `--stem` 即可）。
* 成功 payload 另带 `probe`：`{performed, via: "remote"|"local"|null,
  entry, dropped_figures}`。

参数一律以**数组**形式传给进程，不要拼 shell 字符串：项目路径里的空格、中文、
`&` `%` `^` `<` `>` `|` 经 shell 中转会被吃掉或改写。

### `tavotto open --json` 成功时

```json
{"ok": true, "protocol": 1,
 "project": "/Users/x/我的 图库",
 "stem": "Fig1_removal_rate",
 "registry": {"registry": "/Users/x/我的 图库/tavotto_registry.json",
              "status": "created",
              "created": true,
              "added_scripts": ["fig_removal_rate.py"],
              "added_stems": {},
              "conflicts": [],
              "dynamic_names": [],
              "parameterizable": true},
 "launch": {"mode": "desktop", "app": "…/Tavotto.exe", "argv": [...],
            "via": "launchservices", "handoff": "launched",
            "pid": 4242, "ready": "process_alive", "ready_ms": 1834}}
```

`registry.status` 四种取值互斥，用来回答「注册表被动了没有、怎么动的」：

| status | 含义 |
| --- | --- |
| `already` | 这条本来就在注册表里，**一个字节都没动** |
| `created` | 项目里原本没有注册表，新建了一份 |
| `merged` | 注册表已存在，合并进了新的脚本 / stem（现有条目永远保留） |
| `unchanged` | 注册表已存在，扫完没什么可加的 |

另外两个字段只报告、不裁决：`conflicts`（两个脚本抢同一个 stem）、
`dynamic_names`（产出名要到运行期才知道，静态解不出——走界面里的「试运行探测」）。

**唯一的成功判据是 `registry.parameterizable === true`。** false 表示这张图在
Tavotto 里只能当素材排版、双击进不去图内编辑，多半是脚本没跟产物放在同一个目录。

`launch.mode`：`desktop` / `browser-existing` / `browser-new`。

**桌面模式的 `ok: true` 是等出来的，不是「命令发出去了」**（2026-08-20 起）：
`tavotto open` 会等到桌面进程存在且活过稳定窗口（或单实例转发完成）才返回。
随附字段：`via`（`launchservices`=macOS 经 `open -na` 交给 launchd；
`spawn`=Windows / 裸二进制覆盖直接拉起）、`handoff`（`launched`=新起的窗口；
`forwarded`=argv 转发给了已在跑的实例）、`pid`、`ready`
（`process_alive` / `forwarder_exited` / `unverified`——最后一种是进程表
查不了、只能相信 LaunchServices 的退出码，如实标注）、`ready_ms`（就绪耗时）。
macOS 上**不再直接 exec 包内二进制**：GUI 进程会继承调用方的执行上下文，从
受限环境（沙箱 shell、无 Aqua 会话）直接 exec 会在 AppKit `RegisterApplication`
处 SIGABRT——转发实例也一样，NSApplication 初始化先于单实例检查。`open -na`
把 spawn 委托给 launchd，两种场景（新起 / 转发）都覆盖。

**唤起时找桌面 App 的顺序**与找 CLI 是两件事，但同样不能只认惯例位置
（用户会把 `Tavotto.app` 拖出 `/Applications`、会装在非默认盘）：
`TAVOTTO_DESKTOP_APP` → 冻结产物里**自己身边那个壳**（打包时定死的相对位置，
最准）→ 安装清单里核实过的 `desktop` → 惯例位置。少了中间两条，发现链找得到
CLI、唤起却静默退回浏览器模式——用户明明装了桌面版却看不到窗口。

### 来源记录（ADR 0139 PR3，加性）

插件 `handoff.py` 只在**这次真的跑了脚本**（输出 `ran: true`）时，让跑脚本的那个 Python 进程在退出时
把「我用的环境」写进旁路 JSON（`PYTHONPATH` 前置一个临时 `sitecustomize`，先链回用户自己的；脚本、argv、cwd、
stdout 不变），再把其中白名单字段和**这次真的重写了的 PNG**（跑前跑后 mtime/size 快照之差）交给
`tavotto open --origin-json`。字段（闭集）：python_version、matplotlib_version、backend、figure_dpi
（= 脚本进程退出时刻 `rcParams["figure.dpi"]` 的值）。**不记**savefig 的 dpi / bbox_inches 等——逐次
`savefig(...)` 的实参探针看不到，记 rc 值会误导。**不记**解释器绝对路径、argv、环境变量。插件输出多一个
`origin: {"recorded": N}`（或 `{"recorded": 0, "reason": ...}`）。产物没重跑、脚本失败、脚本没导入
matplotlib 一律不写（诚实缺失）；用户在终端里直接跑出来的图没有记录。第一片只记录与展示；保存瞬间
证据、按记录选解释器是后续，需单独 ADR 与用户授权。

### 交接三态：`launch.render`（加性字段，`protocol` 仍为 1）

`ok: true` 的含义**没有变**：CLI 层 = 解析、登记、唤起成功且（桌面模式）进程活着；插件层 =
`parameterizable === true`。**两层都不含「图已渲染」**（#961：交接早已返回 `ok: true`，sidecar
却因缺引擎文件渲染失败）。要知道渲染结果，读 `launch.render`：

```json
"launch": {"mode": "desktop", …, "render": {"status": "ok", "verified": true, "waited_ms": 2310,
           "stem": "Fig1_removal_rate", "registry_match": true, "manifest_elements": 42}}
```

| `render.status` | 含义 | 附带字段 |
| --- | --- | --- |
| `ok` | 界面对目标 stem 的那次渲染成功了 | `verified`、`stem`、`registry_match`、`manifest_elements` |
| `failed` | 实例**明确报了渲染失败** | `code`：worker 的稳定码（`engine_files_incomplete`、`missing_dependency`、选中原件核对不上的 `artifact_source_unavailable`…；不合规的串落成 `render_failed`） |
| `unverified` | 没法下结论——**不是失败** | `reason`：`not_waited` / `timeout` / `channel_unavailable` / `no_target_stem` / `not_renderable` / `already_displayed` |

`verified` 为 true 当且仅当：渲染成功 ∧ 结论里的 stem == 目标 stem ∧ 注册表里确有这个 stem ∧ manifest 非空
（判据唯一在 `engine/handoffreceipt.py::summarize`，调用方不要自己再判一遍）。`waited_ms` 是实际等了多久。

确认信号怎么来的：实例在 `/api/engine/render` 收尾时把结论记进进程内的有界结论表；CLI 唤起**之前**取 `since`
（留 2 秒回拨余量），唤起后有界轮询 `GET /api/handoff/render-status?project=&stem=&since=`，拿到 `since` 之后该项目 + stem
的**第一份**结论即返回。端点**经认证**（Host / Origin 照旧）：凭据是浏览器模式的完整 secret（`port-<端口>.json`），或桌面
sidecar 发布的**只读、只对这一个端点有效**的 token（`<数据目录>/session/handoff-status-<端口>.json`，0600）。
**CLI 不往磁盘写任何东西**；只记录界面本来就会做的那次渲染，不为验证执行用户脚本（信任锚与会话凭据文件同一个论证，
见 ADR 0131 第六节「实现」与 ADR 0008 修订）。找不到任何凭据 / 连不上实例 / 被拒落成 `unverified/channel_unavailable`；
连上了但到点没有结论（实例在跑但没有给出结论）落成 `unverified/timeout`；
目录级 / 多 Figure 交接没有目标 stem，落成 `unverified/no_target_stem`；登记结果已是不可参数化（`registry.parameterizable === false`，界面不会为它发起引擎渲染）时**不等待**，直接 `unverified/not_renderable`。

**重复交接已显示着的同一张图（2026-10-18，#1154）**：前端只选中、不再渲染，所以结论表里没有 `since` 之后的新结论，
CLI 过去会白等满 `--render-wait`（默认 20 秒）再报 `unverified/timeout`。现在前端命中已显示的面板时会经**会话认证**的
`POST /api/handoff/display-ack` 回一条确认：上一次渲染成功就按当前时间记一条同形状的 `ok`（`verified` 仍只由 `summarize` 判）；
失败记 `failed` + 稳定码；显示着的内容不是这一版的精确渲染（过期等）则记 `unverified/already_displayed`——实例明确应答了、
但没有可凭的渲染结论，**不是失败，也不是 verified**。老前端不发确认时与从前一样等到超时。这个写入口 scoped 只读 token **不能**用。

### `tavotto open --json` 失败时

```json
{"ok": false, "protocol": 1, "code": "registry_write_failed", "error": "注册表写不进去 …"}
```

`error` 是给人看的中文，**随时可能改**；`code` 是给机器看的，稳定：

| code | 什么情况 | 调用方该怎么办 |
| --- | --- | --- |
| `empty_path` | 路径是空的 | 修调用 |
| `path_not_found` | 路径不存在 | 先把图画出来，或修路径 |
| `unsupported_file` | 既不是图、也不是 `.py`、也不是目录 | 换个目标 |
| `registry_invalid` | `tavotto_registry.json` 不是合法 JSON / 有重复 stem | 让用户修那个文件，**Tavotto 绝不重写用户手写的注册表** |
| `registry_write_failed` | 图库目录不可写 | 提示用户改目录权限，或把图放到可写的目录 |
| `project_unreadable` | 图库目录读不了 | 同上 |
| `desktop_missing` | 给了 `--desktop` 但没装桌面版 | 去装，或去掉 `--desktop` |
| `launch_failed` | 界面在、但起不起来 / **起来就崩**（权限、杀软、可执行位丢了、SIGABRT）；或桌面应用**已在运行**、转发这次交接的进程却卡住不退（随附 `reason: "forward_stuck"`，CLI 已结束该进程，**不再算「已唤起」**） | 报给用户，**别当成「没装」**；随附 `app` / `exit_code` / `signal` / `log_path` / `retryable`；`forward_stuck` 时多半是环境挡住了进程间转发，在沙箱外重跑同一条命令 |
| `sandboxed_launch` | 当前进程在**受限沙箱**里（Windows：受限令牌（`IsTokenRestricted`）或 AppContainer，如 Windows 版 Codex 的默认沙箱），桌面应用在这里起不来、也转发不到已开着的窗口——CLI **不拉进程**就返回 | **在沙箱外重跑同一条命令**（Windows 版 Codex：申请 `sandbox_permissions: "require_escalated"` 并写一句理由），不要让用户手动打开 Tavotto；随附 `sandbox`（`restricted_token` / `app_container`）/ `app` / `log_path` / `retryable: true` |
| `launch_timeout` | 唤起后进程在限期内没有出现 | 让用户看 `log_path`；`retryable: true`，可重试一次 |
| `remote_open_failed` | 已在运行的实例打不开这个项目 | 把 `error` 转达给用户 |
| `bad_launch_mode` | `--desktop` 与 `--browser` 同时给了 | 修调用 |
| `script_no_figure` | 脚本跑通了但没捕获到任何 Figure（或 `--no-probe` 下静态解不出） | 确认脚本真的创建 matplotlib Figure；或去掉 `--no-probe` |
| `script_probe_failed` | 试运行失败（脚本自身报错等；`traceback` 在 extra） | 把报错转达给用户；素材库脚本区有诊断详情 |
| `execution_timeout` / `execution_cancelled` | 试运行超时 / 被取消 | 转达；超时先查死循环 |
| `multiple_figures_found` | 多张图 + `--no-launch`（没有界面接选择器） | 按 extra 的 `figures` 重调一次 `--stem` |
| `invalid_stem` | `--stem` 不在该脚本的产出里（或对非 `.py` 目标给了 `--stem`） | 按 extra 的 `stems` 修调用 |
| `runtime_asset_failed` | 捕获到了图却没能登记成可打开的素材 | 报给用户（罕见；带 `stems`） |
| `multiple_stem_conflict` | 产出的图名已被别的脚本登记 | 让用户在注册表里手工裁决归属 |
| `native_run_required` | 缺依赖（extra 带 `module` 与原始 `probe_code`）——safe 档修不了「项目要自己的环境」 | 引导换渲染环境；native 运行（`tavotto run`）是后续版本 |
| `probe_in_progress` | 同一脚本已有一次试运行在进行中（素材库/另一个调用方） | `retryable: true`，稍后重试 |

### `tavotto doctor --json`

不起界面、不起服务、不联网的健康检查。安装器装完跑的就是它。

```json
{"ok": true, "product": "Tavotto", "version": "0.7.0", "protocol": 1,
 "executable": "…", "frozen": true,
 "cli": "…/sidecar/Tavotto/tavotto-cli.exe",
 "desktop": "…/Tavotto.exe",
 "install_dir": "…",
 "manifest": {"path": "…/install.json", "action": "write", "written": true},
 "problems": []}
```

退出码 0 = 这套装置能用；1 = 有硬伤。`problems` 的每一条都是
`{"code", "message"}`，顶层 `code` 是其中第一条（最常问的就是「到底哪儿不对」，
不该逼调用方先翻数组）：

| code | 什么情况 | 还能用吗 |
| --- | --- | --- |
| `manifest_write_failed` | 配置目录写不进去 | 能——已知安装位置那条腿还在 |
| `bundled_cli_missing` | 这个安装包里没有 `tavotto-cli` | 不能，要重装 |
| `bad_manifest_action` | `--write-manifest` 与 `--remove-manifest` 同时给了 | 调用方自己拼错了参数 |

**给了 `--json` 就一律回 JSON**，参数拼错也不例外（与 `tavotto open` 同一条纪律）
——那条恰恰是调用方自己出的错，最该被程序读懂。

`--write-manifest` / `--remove-manifest` 分别给安装器与卸载器用。

---

## 2. 怎么找到 tavotto 命令行

**先说结论：只装了桌面程序，也能找到。** 顺序如下，前面的赢：

| # | 来源 | `source` | 说明 |
| --- | --- | --- | --- |
| 1 | `TAVOTTO_CLI` 环境变量 | `env` | 高级覆盖，用户指定的永远第一 |
| 2 | PATH 里的 `tavotto` | `path` | `pip` / `pipx` 装的 |
| 3 | 安装清单 `install.json` 里的 `cli` | `manifest` | 桌面版装完就有 |
| 4 | 已知安装位置里的 `tavotto-cli` | `install` | 清单丢了照样能找到 |
| 5 | HKCU 记着的安装位置（Windows） | `registry` | 只当补充，不是唯一依据 |
| 6 | 当前解释器里的 `tavotto` 模块 | `module` | 开发态 / 装在同一个环境里 |

第 5 条补的是「装在非默认目录 **且** 清单没写成」这一格——安装器明确保留了
历史/自定义安装位置，少了它那些机器上就只剩 `tavotto_missing`。**两侧都要有**：
只有一侧实现的话，同一台机器上 Tavotto 自己找得到、插件却说没装。

唯一权威实现是 `src/tavotto/engine/locate.py`。Codex 插件跑在用户机器上、
import 不到 tavotto，所以
`codex-plugin/skills/tavotto-figure/scripts/handoff.py` 里有一份镜像；两侧由
`tests/test_install_locate.py::test_plugin_mirrors_the_locator` 在一整张环境
矩阵上逐条比对，**改一边必须同步另一边**。

### 为什么桌面版要另带一个 `tavotto-cli`

装出来的 `Tavotto.exe`（Tauri 壳）和它旁边的 sidecar 都是 **GUI 子系统**的
可执行文件。没有真终端时它们的 `sys.stdout` 是 `None`，`packaging/entry.py`
会把输出改道到 `app.log`——调用方 `capture_output` 拿到的是**空的 stdout**，
不是那行 JSON。所以安装包里另出一个 `console=True` 的 `tavotto-cli`：与
sidecar 同一份 `_internal/`，只多一个 ~1.5 MB 的 bootloader。

**不要把 GUI 可执行文件当命令行调**，哪怕它看起来接受同样的参数。

### 已知安装位置

| 平台 | 安装根 | CLI |
| --- | --- | --- |
| Windows | `%LOCALAPPDATA%\Tavotto`（当前用户安装，默认） | `<根>\sidecar\Tavotto\tavotto-cli.exe` |
| Windows | `%PROGRAMFILES%\Tavotto`、`%PROGRAMFILES(X86)%\Tavotto`（历史上管理员装的） | 同上 |
| macOS | `/Applications/Tavotto.app`、`~/Applications/Tavotto.app` | `<根>/Contents/Resources/sidecar/Tavotto/tavotto-cli` |

`sidecar/Tavotto` 这一段的唯一出处是 `src-tauri/tauri.conf.json` 的
`bundle.resources`；Rust 壳、Python 定位器、NSIS 安装段三处都按它找，
`tests/test_nsis_template.py::test_sidecar_layout_has_a_single_source_of_truth` 看护。

### 安装清单 `install.json`

落点是**用户配置目录**（`engine/config.config_dir()`），不是安装目录——
Windows 上安装目录可能在 Program Files（只读），卸载后也会被删掉：

| 平台 | 路径 |
| --- | --- |
| Windows | `%APPDATA%\Tavotto\install.json` |
| macOS | `~/Library/Application Support/Tavotto/install.json` |
| Linux | `$XDG_CONFIG_HOME/tavotto/install.json`（缺省 `~/.config/tavotto/`） |

```json
{"protocol": 1, "product": "Tavotto", "version": "0.7.0",
 "cli": "C:\\Users\\张三\\AppData\\Local\\Tavotto\\sidecar\\Tavotto\\tavotto-cli.exe",
 "desktop": "C:\\Users\\张三\\AppData\\Local\\Tavotto\\Tavotto.exe",
 "install_dir": "C:\\Users\\张三\\AppData\\Local\\Tavotto",
 "source": "installer", "updated": "2026-08-18T09:00:00Z"}
```

谁写它：

* **安装器**——NSIS 装完跑一次 `tavotto-cli doctor --json --write-manifest`
  （让 CLI 自己写，NSIS 不拼 JSON：安装目录可能带空格和中文）；
* **应用自己**——每次启动刷新一遍。用户会把 `.app` 拖到别处、会用免安装形态、
  macOS 压根没有装完的钩子，刷一遍清单就永远指着最后真跑起来过的那一套；
* **卸载器**——`doctor --json --remove-manifest`，且**必须在删文件之前**跑。

`protocol` 对不上就当没有这份清单。读的一方还要**核实里面的路径还在**：
清单是缓存不是真相，卸载 / 手工删目录 / 从备份还原配置都会留下一份指向
不存在文件的清单。

### 三件明确不做的事

* **不改用户的 PATH。** 要写注册表、广播 `WM_SETTINGCHANGE`、处理 1024 字符
  截断，卸载时还要准确摘掉自己那一段——每一步都可能把用户的 PATH 弄坏，
  而清单 + 已知安装位置已经够了。
* **不把 Windows 注册表当唯一依据。** 企业策略能锁它。它只是第 5 条补充，
  用来发现装到非默认位置的安装。
* **不要求管理员权限。** 安装是 currentUser，清单落在用户配置目录。

---

## 2.5 桌面壳的 argv 契约

`tavotto open` / `tavotto run` 唤起桌面时用的是同一份 argv 契约。生产侧唯一
出处是 `engine/handoff.desktop_argv()`，消费侧是
`src-tauri/src/main.rs::parse_open_args()`——**严格同源对**，两侧各有一条用例
（`tests/test_handoff.py` 与 `main.rs` 的 `#[test]`）。

```
Tavotto --open <项目目录> [--stem <名字> | --pick-script <脚本相对路径>]
                          [--native-session <32 位十六进制 ID>]
```

* `--stem` 与 `--pick-script` **互斥**（定得下来一张就不需要选择器）；
* `--native-session` 与它们**不互斥**——那两个说的是"打开哪张图"，这个说的是
  "有一条 `tavotto run` 会话在等你确认"；
* **argv 上只有一个不透明 ID**。token、端口、解释器路径、完整命令全部在
  `<数据目录>/session/native/<ID>.json` 里（目录 0700、文件 0600、一次性、
  有时效）。理由很直接：同一台机器上 `ps` 对别的用户可见，而那个文件不是
  （ADR 0021 §4，与 ADR 0008 的本机会话凭据同一个安全论证）。

---

## 3. Codex 插件的出口

`codex-plugin/skills/tavotto-figure/scripts/handoff.py` 输出一行 JSON，
退出码即分诊结果：

| 退出码 | 含义 |
| --- | --- |
| 0 | 交接成功且**可参数化** |
| 1 | 脚本运行失败（`error_code: script_failed`，`stderr` 里是尾部输出） |
| 2 | 路径不对 / `tavotto open` 失败（见下） |
| 3 | 这台机器上用不了 Tavotto（`tavotto_missing`、`desktop_found_cli_missing`） |
| 4 | 交接了，但这张图不可参数化（`not_parameterizable`） |

`tavotto open` 给了具体 `code` 时，插件**把它原样当成 `error_code`**
（`registry_write_failed`、`path_not_found`、`cli_exec_failed`…），同时保留在
`code` 字段里；CLI 没给 code（老版本 / 没有输出）才回落到 `open_failed`。
调用方因此只看一个字段就能分诊——把具体 code 压成 `open_failed`、真相藏进第二层，
等于让对面多猜一次，而 SKILL.md 里教 Codex 的恰恰是按 `error_code` 分支。

成功时 `tavotto: {"source": ..., "cmd": ...}` 说明是从哪条腿找到的 CLI。

**交接三态（加性，退出码与 `ok` 不变）**——payload 顶层另有三个字段，把「交接了」与「图画出来了」拆开说：

| 字段 | 取值 |
| --- | --- |
| `launch_success` | `true` = CLI 报成功且唤起了界面；`null` = 没唤起（`--no-launch`）；`false` = 没走到唤起（路径错、脚本失败、没有 Tavotto、`tavotto open` 失败） |
| `render_success` | `true` = 界面对目标图的那次渲染成功；`false` = 界面**明确报了渲染失败**（`render.code` 是稳定码）；`null` = **不知道**（没等到结论、老 CLI、没唤起）——`null` 不是失败 |
| `verified_success` | 仅当 CLI 的 `launch.render.verified === true` 才为 `true`；其余一律 `false` |

外加 `render`：CLI 的 `launch.render` 原样透传。**新插件读老 CLI**（没有 `launch.render`）：补成
`{"status": "unverified", "reason": "cli_without_render_signal"}`，`render_success: null`、`verified_success: false`。
**老插件读新 CLI**：多出的字段被忽略，行为与从前一致。#961 的形状是 `launch_success: true`、
`render_success: false`、`render.code: "engine_files_incomplete"`——而退出码仍是 0（不改 `ok` 的公开语义），
所以**调用方必须读这三个字段，别只看退出码**。

---

## 4. 用户看到这些提示该怎么办

### `tavotto_missing` —— 这台机器上没有 Tavotto

装一个：桌面版 <https://github.com/Tavotto/Tavotto/releases>，或命令行版
`pipx install tavotto`。**图已经画出来了**，脚本和产物都在原处，装完重新执行
同一条命令即可。

装了却还报这个，按顺序看：

1. `tavotto doctor --json` 能跑吗？跑不了就是安装不完整，重装一次。
2. 装到了非默认位置？用 `TAVOTTO_CLI` 指到 `tavotto-cli`
   （Windows 在 `<安装目录>\sidecar\Tavotto\tavotto-cli.exe`）。
3. 清单在不在？`%APPDATA%\Tavotto\install.json`。不在就跑一次
   `<安装目录>\sidecar\Tavotto\tavotto-cli.exe doctor --json --write-manifest`。

### `desktop_found_cli_missing` —— 装了桌面版，但那一版没带命令行

**不是没装。** 这是 v0.7.0 及更早的安装包——里面只有 GUI 可执行文件。
到 Releases 装一次最新版即可；急用的话 `pipx install tavotto`，或把
`TAVOTTO_CLI` 指到任何一个可用的 `tavotto` 命令行。

### `registry_write_failed` —— 注册表写不进去

图库目录不可写（只读介质、权限不对、目录被别的进程占着）。**原文件零改动**。
把图和脚本放到一个可写的目录，或修好那个目录的权限，然后重新交接。
错误信息里带着具体是哪个文件。

### `not_parameterizable` —— 图进去了，但只能当素材排版

产出它的 `.py` 没跟产物放在同一个目录，或者产物名要到运行期才知道
（来自 `sys.argv`、时间戳、遍历数据目录）。把脚本挪到产物旁边、把产物名写成
字面量，然后重新交接。名字真的只有运行期才知道时，走界面里的
**设置 → 脚本注册表 → 试运行探测**。

### `cli_exec_failed` —— 找到了命令行，但起不来

多半是 `TAVOTTO_CLI` 指到了一个不存在或没有可执行位的文件。
清掉这个变量让自动发现接手，或者把它指对。

---

## 5. 高级：`TAVOTTO_CLI`

指到一个 `tavotto` 命令行的可执行文件，**优先于其它一切**。用于：

* 同时装了好几个版本，想固定用某一个；
* 装在自动发现覆盖不到的位置（自定义目录、网络盘、容器挂载）；
* 开发时指到工作副本的 `.venv/bin/tavotto`。

它只影响「用哪个命令行」，不影响这个命令行自己怎么找项目、怎么唤起界面。
指错了会得到 `cli_exec_failed`，**不会**静默退回自动发现——显式指定的东西
出了问题就该报出来。

另有 `TAVOTTO_DESKTOP_APP`（指到桌面 App 可执行文件，影响唤起哪一个窗口）
与 `TAVOTTO_CONFIG_DIR`（改配置目录，连带改清单落点）。
