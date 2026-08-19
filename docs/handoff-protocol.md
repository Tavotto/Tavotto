# 交接协议：外部程序怎么找到 Magplot、怎么把一张图交给它

面向的是**调用 Magplot 的程序**——Codex 插件、编辑器扩展、Makefile、别的
Agent，以及用户自己在终端里敲的那一行。协议版本 **v1**（`protocol: 1`）。

设计与取舍见 [ADR 0005](adr/0005-external-handoff-and-codex-plugin.md)。

---

## 1. 命令行接口

```
magplot open <产物|脚本|目录> [--json] [--no-launch] [--desktop|--browser] [--port N]
magplot doctor [--json] [--write-manifest|--remove-manifest]
```

* `--no-launch` —— 只做**解析目标 + 登记注册表**，不唤起任何界面（浏览器也不开）。
* 不带 `--no-launch` —— 登记完唤起界面：**装了桌面版就开原生窗口**，没装才退回浏览器。
* `--json` —— 输出一行机器可读 JSON。**成功和失败都有**，失败那行带稳定的 `code`。

参数一律以**数组**形式传给进程，不要拼 shell 字符串：项目路径里的空格、中文、
`&` `%` `^` `<` `>` `|` 经 shell 中转会被吃掉或改写。

### `magplot open --json` 成功时

```json
{"ok": true, "protocol": 1,
 "project": "/Users/x/我的 图库",
 "stem": "Fig1_removal_rate",
 "registry": {"registry": "/Users/x/我的 图库/mm_registry.json",
              "status": "created",
              "created": true,
              "added_scripts": ["fig_removal_rate.py"],
              "added_stems": {},
              "conflicts": [],
              "dynamic_names": [],
              "parameterizable": true},
 "launch": {"mode": "desktop", "app": "…/Magplot.exe", "argv": [...]}}
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
Magplot 里只能当素材排版、双击进不去图内编辑，多半是脚本没跟产物放在同一个目录。

`launch.mode`：`desktop` / `browser-existing` / `browser-new`。

**唤起时找桌面 App 的顺序**与找 CLI 是两件事，但同样不能只认惯例位置
（用户会把 `Magplot.app` 拖出 `/Applications`、会装在非默认盘）：
`MAGPLOT_DESKTOP_APP` → 冻结产物里**自己身边那个壳**（打包时定死的相对位置，
最准）→ 安装清单里核实过的 `desktop` → 惯例位置。少了中间两条，发现链找得到
CLI、唤起却静默退回浏览器模式——用户明明装了桌面版却看不到窗口。

### `magplot open --json` 失败时

```json
{"ok": false, "protocol": 1, "code": "registry_write_failed", "error": "注册表写不进去 …"}
```

`error` 是给人看的中文，**随时可能改**；`code` 是给机器看的，稳定：

| code | 什么情况 | 调用方该怎么办 |
| --- | --- | --- |
| `empty_path` | 路径是空的 | 修调用 |
| `path_not_found` | 路径不存在 | 先把图画出来，或修路径 |
| `unsupported_file` | 既不是图、也不是 `.py`、也不是目录 | 换个目标 |
| `registry_invalid` | `mm_registry.json` 不是合法 JSON / 有重复 stem | 让用户修那个文件，**Magplot 绝不重写用户手写的注册表** |
| `registry_write_failed` | 图库目录不可写 | 提示用户改目录权限，或把图放到可写的目录 |
| `project_unreadable` | 图库目录读不了 | 同上 |
| `desktop_missing` | 给了 `--desktop` 但没装桌面版 | 去装，或去掉 `--desktop` |
| `launch_failed` | 界面在、但起不起来（权限 / 杀软 / 可执行位丢了） | 报给用户，**别当成「没装」** |
| `remote_open_failed` | 已在运行的实例打不开这个项目 | 把 `error` 转达给用户 |
| `bad_launch_mode` | `--desktop` 与 `--browser` 同时给了 | 修调用 |

### `magplot doctor --json`

不起界面、不起服务、不联网的健康检查。安装器装完跑的就是它。

```json
{"ok": true, "product": "Magplot", "version": "0.7.0", "protocol": 1,
 "executable": "…", "frozen": true,
 "cli": "…/sidecar/Magplot/magplot-cli.exe",
 "desktop": "…/Magplot.exe",
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
| `bundled_cli_missing` | 这个安装包里没有 `magplot-cli` | 不能，要重装 |
| `bad_manifest_action` | `--write-manifest` 与 `--remove-manifest` 同时给了 | 调用方自己拼错了参数 |

**给了 `--json` 就一律回 JSON**，参数拼错也不例外（与 `magplot open` 同一条纪律）
——那条恰恰是调用方自己出的错，最该被程序读懂。

`--write-manifest` / `--remove-manifest` 分别给安装器与卸载器用。

---

## 2. 怎么找到 magplot 命令行

**先说结论：只装了桌面程序，也能找到。** 顺序如下，前面的赢：

| # | 来源 | `source` | 说明 |
| --- | --- | --- | --- |
| 1 | `MAGPLOT_CLI` 环境变量 | `env` | 高级覆盖，用户指定的永远第一 |
| 2 | PATH 里的 `magplot` | `path` | `pip` / `pipx` 装的 |
| 3 | 安装清单 `install.json` 里的 `cli` | `manifest` | 桌面版装完就有 |
| 4 | 已知安装位置里的 `magplot-cli` | `install` | 清单丢了照样能找到 |
| 5 | HKCU 记着的安装位置（Windows） | `registry` | 只当补充，不是唯一依据 |
| 6 | 当前解释器里的 `magplot` 模块 | `module` | 开发态 / 装在同一个环境里 |

第 5 条补的是「装在非默认目录 **且** 清单没写成」这一格——安装器明确保留了
历史/自定义安装位置，少了它那些机器上就只剩 `magplot_missing`。**两侧都要有**：
只有一侧实现的话，同一台机器上 Magplot 自己找得到、插件却说没装。

唯一权威实现是 `src/magplot/engine/locate.py`。Codex 插件跑在用户机器上、
import 不到 magplot，所以
`codex-plugin/skills/magplot-figure/scripts/handoff.py` 里有一份镜像；两侧由
`tests/test_install_locate.py::test_plugin_mirrors_the_locator` 在一整张环境
矩阵上逐条比对，**改一边必须同步另一边**。

### 为什么桌面版要另带一个 `magplot-cli`

装出来的 `Magplot.exe`（Tauri 壳）和它旁边的 sidecar 都是 **GUI 子系统**的
可执行文件。没有真终端时它们的 `sys.stdout` 是 `None`，`packaging/entry.py`
会把输出改道到 `app.log`——调用方 `capture_output` 拿到的是**空的 stdout**，
不是那行 JSON。所以安装包里另出一个 `console=True` 的 `magplot-cli`：与
sidecar 同一份 `_internal/`，只多一个 ~1.5 MB 的 bootloader。

**不要把 GUI 可执行文件当命令行调**，哪怕它看起来接受同样的参数。

### 已知安装位置

| 平台 | 安装根 | CLI |
| --- | --- | --- |
| Windows | `%LOCALAPPDATA%\Magplot`（当前用户安装，默认） | `<根>\sidecar\Magplot\magplot-cli.exe` |
| Windows | `%PROGRAMFILES%\Magplot`、`%PROGRAMFILES(X86)%\Magplot`（历史上管理员装的） | 同上 |
| macOS | `/Applications/Magplot.app`、`~/Applications/Magplot.app` | `<根>/Contents/Resources/sidecar/Magplot/magplot-cli` |

`sidecar/Magplot` 这一段的唯一出处是 `src-tauri/tauri.conf.json` 的
`bundle.resources`；Rust 壳、Python 定位器、NSIS 安装段三处都按它找，
`tests/test_nsis_template.py::test_sidecar_layout_has_a_single_source_of_truth` 看护。

### 安装清单 `install.json`

落点是**用户配置目录**（`engine/config.config_dir()`），不是安装目录——
Windows 上安装目录可能在 Program Files（只读），卸载后也会被删掉：

| 平台 | 路径 |
| --- | --- |
| Windows | `%APPDATA%\Magplot\install.json` |
| macOS | `~/Library/Application Support/Magplot/install.json` |
| Linux | `$XDG_CONFIG_HOME/magplot/install.json`（缺省 `~/.config/magplot/`） |

```json
{"protocol": 1, "product": "Magplot", "version": "0.7.0",
 "cli": "C:\\Users\\张三\\AppData\\Local\\Magplot\\sidecar\\Magplot\\magplot-cli.exe",
 "desktop": "C:\\Users\\张三\\AppData\\Local\\Magplot\\Magplot.exe",
 "install_dir": "C:\\Users\\张三\\AppData\\Local\\Magplot",
 "source": "installer", "updated": "2026-08-18T09:00:00Z"}
```

谁写它：

* **安装器**——NSIS 装完跑一次 `magplot-cli doctor --json --write-manifest`
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

## 3. Codex 插件的出口

`codex-plugin/skills/magplot-figure/scripts/handoff.py` 输出一行 JSON，
退出码即分诊结果：

| 退出码 | 含义 |
| --- | --- |
| 0 | 交接成功且**可参数化** |
| 1 | 脚本运行失败（`error_code: script_failed`，`stderr` 里是尾部输出） |
| 2 | 路径不对 / `magplot open` 失败（见下） |
| 3 | 这台机器上用不了 Magplot（`magplot_missing`、`desktop_found_cli_missing`） |
| 4 | 交接了，但这张图不可参数化（`not_parameterizable`） |

`magplot open` 给了具体 `code` 时，插件**把它原样当成 `error_code`**
（`registry_write_failed`、`path_not_found`、`cli_exec_failed`…），同时保留在
`code` 字段里；CLI 没给 code（老版本 / 没有输出）才回落到 `open_failed`。
调用方因此只看一个字段就能分诊——把具体 code 压成 `open_failed`、真相藏进第二层，
等于让对面多猜一次，而 SKILL.md 里教 Codex 的恰恰是按 `error_code` 分支。

成功时 `magplot: {"source": ..., "cmd": ...}` 说明是从哪条腿找到的 CLI。

---

## 4. 用户看到这些提示该怎么办

### `magplot_missing` —— 这台机器上没有 Magplot

装一个：桌面版 <https://github.com/erwanjun/magplot/releases>，或命令行版
`pipx install magplot`。**图已经画出来了**，脚本和产物都在原处，装完重新执行
同一条命令即可。

装了却还报这个，按顺序看：

1. `magplot doctor --json` 能跑吗？跑不了就是安装不完整，重装一次。
2. 装到了非默认位置？用 `MAGPLOT_CLI` 指到 `magplot-cli`
   （Windows 在 `<安装目录>\sidecar\Magplot\magplot-cli.exe`）。
3. 清单在不在？`%APPDATA%\Magplot\install.json`。不在就跑一次
   `<安装目录>\sidecar\Magplot\magplot-cli.exe doctor --json --write-manifest`。

### `desktop_found_cli_missing` —— 装了桌面版，但那一版没带命令行

**不是没装。** 这是 v0.7.0 及更早的安装包——里面只有 GUI 可执行文件。
到 Releases 装一次最新版即可；急用的话 `pipx install magplot`，或把
`MAGPLOT_CLI` 指到任何一个可用的 `magplot` 命令行。

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

多半是 `MAGPLOT_CLI` 指到了一个不存在或没有可执行位的文件。
清掉这个变量让自动发现接手，或者把它指对。

---

## 5. 高级：`MAGPLOT_CLI`

指到一个 `magplot` 命令行的可执行文件，**优先于其它一切**。用于：

* 同时装了好几个版本，想固定用某一个；
* 装在自动发现覆盖不到的位置（自定义目录、网络盘、容器挂载）；
* 开发时指到工作副本的 `.venv/bin/magplot`。

它只影响「用哪个命令行」，不影响这个命令行自己怎么找项目、怎么唤起界面。
指错了会得到 `cli_exec_failed`，**不会**静默退回自动发现——显式指定的东西
出了问题就该报出来。

另有 `MAGPLOT_DESKTOP_APP`（指到桌面 App 可执行文件，影响唤起哪一个窗口）
与 `MAGPLOT_CONFIG_DIR`（改配置目录，连带改清单落点）。
