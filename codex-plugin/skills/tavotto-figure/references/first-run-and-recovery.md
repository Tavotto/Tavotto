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

### 插件明明装着、也新开过会话，工具还是一个都没有（多见于 Windows）

这时候**不是插件没装**，是 Codex 起 MCP server 的那一跳没起来：插件清单里钉的
启动命令是 `python3`，Windows 上这个名字常常指向微软商店的 App Execution Alias
——命令**存在**、启动起来只有一个 9009 且什么都不打印。于是插件的启动器一次都
没跑起来，连会说人话的降级 server 都没有，Codex 那边也不会为此报任何错。

给用户这两条里的一条（**不要重装插件、不要 marketplace upgrade**）：

```sh
tavotto codex doctor      # 只诊断：指名道姓说出是哪条启动命令起不来
tavotto codex install     # 修：把已装副本的启动命令钉到一个验证过的解释器
```

跑完**新开一次 Codex 会话**。用户只装了桌面版、PATH 上没有 `tavotto` 时，用桌面
版自带的 `tavotto-cli` 跑同样的子命令。

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

升级后同样要新开会话。**Windows 上升级完还要再跑一次 `tavotto codex install`**
——升级会把插件目录整个换掉，之前钉进已装副本的启动命令会跟着被换回 `python3`。
不自动升级、不反复提醒、不为此打断手里的活。`update`
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
遇到 `workspace_confirmation_declined` / `workspace_confirmation_cancelled` /
`workspace_confirmation_error` 后**不要自动重试**，等用户主动重新发起；也不要
改用 shell 绕过。health 已给出恰好一个可信根时才可以用相对路径。

## 三条铁律

* 引擎不可用 ≠ 可以拿桌面窗口或浏览器顶替内嵌画布——那是两条路，不许冒充；
* 插件 enabled ≠ 工具可用：装完插件/引擎必须**新开会话**；
* 工具回了结构化错误就把 `code` + 恢复步骤转达给用户，绝不自己编一个成功。
