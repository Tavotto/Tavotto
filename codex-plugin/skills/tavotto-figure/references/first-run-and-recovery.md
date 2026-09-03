# 首次使用与恢复（只在会话入口判到异常时读）

本文件展开 SKILL.md「会话入口」各状态的恢复动作。**健康会话一个字都不用读。**

## 插件没有在本会话加载（工具列表里没有 `tavotto_health`）

说明插件不在本会话里。给用户 README「在 Codex 中第一次使用 Tavotto」的两条
安装命令（分开跑，别用 `&&`——不同 shell 兼容，也好判断是哪一步失败）：

```sh
codex plugin marketplace add Tavotto/Tavotto --sparse .agents/plugins --sparse codex-plugin
codex plugin add tavotto@tavotto
```

`--sparse` 必须同时带 `.agents/plugins` 与 `codex-plugin`：市场清单引用的是
仓库内的本地插件目录，少一个 checkout 里就没有插件本体。

然后**要求用户新开一个 Codex 会话，并停止当前任务**。已经开着的会话不会
重新加载插件的 skill 与 MCP 工具；`codex plugin list` 显示 enabled 也不代表
当前会话拿得到工具。**不要在旧会话里继续假装工具可用**，也不要替用户在本会话
里重装第二遍。

## 工具在、引擎不可用（`tavotto_health` 回 `ok: false`）

按返回的 `code` 只做**对应的一条**恢复动作，不做全套重装：

* `desktop_only` —— 用户装了 Tavotto 桌面版，**不要说「没有安装 Tavotto」**。
  桌面交接（`scripts/handoff.py`）此刻就能用。只有用户明确要 Codex 内嵌画布 /
  MCP 工具时，才给这两条里的一条（不是都给）：

  ```sh
  python3 <插件目录>/mcp/server.py --provision      # 插件自管 venv，零手工配置
  pipx install "tavotto[worker]"                     # 或者装 pip 形态的引擎
  ```

  装完**新开会话**才拿得到工具。
* `tavotto_missing` —— 机器上确实没有 Tavotto。按用户的需求引导：只要桌面收尾
  就装桌面版（<https://github.com/Tavotto/Tavotto/releases>），要 Codex 内嵌
  工具就 `pipx install "tavotto[worker]"`。
* `engine_unavailable`（`TAVOTTO_MCP_PYTHON` 指错了）—— 指名道姓地把它报给
  用户，让用户改环境变量或去掉；不要悄悄换别的解释器。
* 其它 code —— 把 `code` + health 输出里的 `recovery` 步骤原样转达。

**引擎缺失只修引擎**：插件已经加载了，绝不因为引擎不可用而重装插件或
marketplace add/upgrade。

## 插件有新版本（`update.status == "available"`）

当前任务照常做完。收尾时提醒**一次**：

```sh
codex plugin marketplace upgrade tavotto
```

升级后同样要新开会话。不自动升级、不反复提醒、不为此打断手里的活。`update`
里若还有 `tavotto` 字段，那是说本机 Tavotto 版本低于新插件的要求——让用户去
Releases 更新 Tavotto（**跟插件是两码事，别混着说**）。

## 离线 / 网络失败

安装、升级、marketplace 都是尽力而为：失败就报一句，**不循环重试，不退回
clone 源码或本地构建**。已经画好的图和脚本都在磁盘上，联网恢复后重跑同一条
命令即可。

## 工作区授权（每个连接第一次 open 时）

看 `tavotto_health` 的 `root_authority`。`roots` 为空且 client 声明了
`elicitation` 时：第一次 `tavotto_open_figure` 必须传**绝对、已存在**的项目
路径，让 Codex 显示规范路径请用户确认——模型给的路径只是候选，不能自证权限。
授权失败**分档**，每档一个稳定 `code`、一个 `disposition`（谁该动手）和一句
`recovery`（下一步）。**把 `recovery` 转达给用户，别把 `code` 念出来**；不要
自动重试，也不要改用 shell 绕过。health 已给出恰好一个可信根时才可以用相对路径。

| `code` | `disposition` | 下一步 |
| --- | --- | --- |
| `workspace_confirmation_declined` | `ask_user_again` | 用户看着框拒绝了：换个目录再问一次 |
| `workspace_confirmation_cancelled` | `ask_user_again` | 框被关掉：可交互会话里请用户重新发起；`codex exec` 拿不到确认 |
| `workspace_confirmation_no_response` | `fix_host_wiring` | **框从没到过用户面前**（超时/断开）：查宿主接线，别再让用户点 |
| `workspace_confirmation_error` | `fix_host_wiring` | 宿主回了错误：看宿主日志 |
| `workspace_confirmation_stale` | `ask_user_again` | 批准的目录在授权落地前变了：核对路径后重新批准 |
| `workspace_confirmation_required` | `send_absolute_path` | 还没给出可展示的目录：改传绝对、已存在的路径 |
| `workspace_roots_no_response` / `workspace_roots_error` | `fix_host_wiring` | 宿主声明了 roots 却没给出目录：查宿主接线 |
| `path_out_of_scope` | `narrow_the_path` | 路径越界：改用 `roots` 里列出的目录 |
| `no_workspace_root` | `configure_roots` | 宿主什么都没给：让用户设 `TAVOTTO_MCP_ROOTS` 后重启 |

**`fix_host_wiring` 那几档不是用户拒绝**：再让用户点多少次都不会有提示，只能
去查宿主，或退回 `TAVOTTO_MCP_ROOTS`。这两件事的处置相反，别混着说。

## 三条铁律

* 引擎不可用 ≠ 可以拿桌面窗口或浏览器顶替内嵌画布——那是两条路，不许冒充；
* 插件 enabled ≠ 工具可用：装完插件/引擎必须**新开会话**；
* 工具回了结构化错误就把 `code` + 恢复步骤转达给用户，绝不自己编一个成功。
