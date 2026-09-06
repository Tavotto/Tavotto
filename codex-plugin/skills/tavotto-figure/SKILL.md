---
name: tavotto-figure
description: 画 matplotlib 论文级图表，并用 Tavotto 继续微调（拖图例、改字号线宽、调刻度、按出版规范预检、导出矢量 PDF）。用户要画图、出图、做 figure、画折线/柱状/散点/误差棒、做论文配图、scientific plot / publication figure，或提到 Tavotto 时使用。
---

# Tavotto Figure

一句话：**数据与结构归代码，版式微调归鼠标。**

你只负责把图做成「Tavotto 能接手」的形状。图例位置、字号、线宽、刻度朝向这些，
用 Tavotto 的 MCP 工具改（在 Codex 里就能改完）——**不要在对话里追问这类参数，
更不要为这种改动重跑脚本**。唯一的例外是「开工三问」：那三件事决定整张图的
骨架，动手前先问清。

## 会话入口：先检查，不安装

1. 本会话第一次使用 Tavotto 时，优先调用 `tavotto_health`，只调用一次。
2. `ok: true`：立即进入任务；本会话绝不执行插件安装、升级、pip/pipx 或
   provision——健康的会话里一次联网安装都不该发生。
3. 工具存在但引擎不可用：按健康检查返回的错误码，只给出或执行**对应的一条**
   恢复动作（动作清单见 `references/first-run-and-recovery.md`）。缺什么修
   什么：缺引擎只修引擎，绝不顺手重装插件。
4. `desktop_only`：不要说「没有安装 Tavotto」——用户装了桌面版。桌面交接仍然
   可用；只有用户需要 Codex 内嵌画布/MCP 工具时，才建议 provision 或
   `pipx install "tavotto[worker]"`。
5. 当前会话没有 `tavotto_health` 这个工具：说明插件没有在本会话加载。给出
   README 的两条插件安装命令（见 `references/first-run-and-recovery.md`），
   要求**新开会话**，然后**停止**；不要在旧会话里继续假装工具可用。
6. 发现插件更新：当前任务照常完成，只在收尾提醒一次
   `codex plugin marketplace upgrade tavotto`，不自动升级、不反复提醒。
7. 健康检查或恢复失败：报告结构化错误与下一步，不循环重试，不退回源码构建。

## 什么情况下读哪份 reference

| 情况 | 读 |
| --- | --- |
| 写任何画图脚本之前 | `references/figure-contract.md`（契约详解 + 模板）、`references/publication-style.md`（默认值 / 克制 / 组图） |
| 工具缺失、引擎不可用、要装/升级/provision、工作区授权 | `references/first-run-and-recovery.md` |
| 要交给 Tavotto 桌面窗口 | `references/desktop-handoff.md`（含全部错误码分诊） |
| 用户撞上 Tavotto 的缺陷 | `references/issue-reporting.md` |
| 用户要改的东西鼠标改不了 | `references/compatibility.md`（能改 / 必须回代码改） |

普通画图任务只读前一行的两份；故障与交接文档**用到才读**。

## 开工三问（用提问工具，不用自由文本追问）

先读已记录的偏好（脚本路径相对本技能目录）：

```
python3 scripts/prefs.py --json
```

三个键里**记录过的直接用，不再问**——唯一例外是 `width` 记的是 `ask`：
那个哨兵值的含义就是「宽度每次都问」，撞见它宽度照问（另外两个键照常）。
没记录的用宿主的**向用户提问工具**
（ask user question / request_user_input，一次问卷问齐，别拆成三轮对话）问：

1. **画幅宽度**——选项：单栏 **8 cm**、双栏 **15 cm**。用户不清楚就按这次
   任务自己推荐并说明理由：单张简单曲线/单组对比 → 单栏 8 cm；曲线多、
   面板多、横向信息密 → 双栏 15 cm。推荐项放在第一位。
2. **字体**——选项**必须包括 Times New Roman 与 Arial**（默认推荐
   Times New Roman）；用户点名其它字体就用其它字体。
3. **图例加不加框**——加框 / 无框（默认推荐无框）。用户选了加框，之后调
   `tavotto_preflight` / `tavotto_export` 时带
   `{"journal": {"legend_policy": {"frame": true}}}`——那是用户自己点的头，
   别让预检把它当违规每次都报一条 warning。

用户在回答里表示「以后都这样 / 记住」时才写偏好（`width` 记 `single` /
`double`；用户明确说「宽度每次都问」记 `ask`）：

```
python3 scripts/prefs.py --set font="Times New Roman" --set legend_frame=off --json
```

用户改主意时用 `--set` 覆盖或 `--unset` 退回「下次再问」。偏好写不进去
（`saved: false`）不算错误——下次重新问就是了。

## 图文件契约（核心，违反即死图）

1. **脚本与产物同目录，而且必须先落成文件**——绝不用 `python -c`、
   `python - <<EOF` 或临时目录出图。落点是用户当前目录下的 `figures/`
   （已有 `tavotto_registry.json` 的图库就沿用）。
2. **入口是无参 `main()`**，import 期零副作用。
3. **产物名写成静态可解析的字面量**，不来自 argv/时间戳/随机串；一图一 stem。
4. **交付物是矢量 PDF**（`imshow` 的位图除外，≥300 dpi）。
5. **可复现**：固定随机种子，不 `plt.show()`。

展开说明与模板在 `references/figure-contract.md`；出版默认值（尺寸/字号/线宽/
刻度/色系）、克制原则（数据之外的效果一个都不擅自加）与多子图组图规则在
`references/publication-style.md`——写脚本前都要读。

## 图画完之后：MCP 工具顺序

会话入口的 `tavotto_health` 已经过了，直接开：

```
tavotto_open_figure { "project_path": "/absolute/path/to/figures", "stem": "Fig1_removal_rate" }
```

第一次 open 的工作区授权规则（绝对路径、用户确认、拒绝后不重试）在
`references/first-run-and-recovery.md` 的「工作区授权」一节。回来的是
`session_id` + `manifest`（哪些元素可改）+ 预览 SVG + 出版规范 + 预检结果。
支持 UI 的 Codex 会同时开出一块交互画布，用户可以直接拖。

**一个脚本出好几张独立图时不要开 N 次**——`stems` 一次全开，拿回 N 个各自
可编辑的 `session_id`（不知道有哪些就 `discover_stems: true`，它只认注册表里
已登记且产物在磁盘上的那些）：

```
tavotto_open_figure { "project_path": "/absolute/path/to/figures",
                      "stems": ["XPS_C_Ti_700C", "XPS_C_Ti_800C"] }
```

批量回的是每张的**摘要 + session_id**（没有 manifest/SVG，也不挂画布——要在
画布里改哪一张，就用 `stem` 单独开那一张）。结局看 `status`：`done` /
`partial` / `failed`。**`partial` 是「其余都开着」**，失败那张带自己的
`stem` 与 code，重试它一张即可，别把整批重开。

之后按序：

* **改图** `tavotto_apply_overrides { session_id, patches }`。
  `patches` 是 `{gid, prop, value}` 的**全量列表**——列表里没有的 `(gid, prop)`
  会自动恢复成脚本原始值，所以**每次都要发完整的一份，不要发增量**。
  gid 与 prop 从 manifest 的 `elements[].editable` 里取，别猜。
* **体检** `tavotto_preflight { session_id }`。四档结果：`errors`（默认阻止
  导出）、`warnings`、`not_verifiable`（查不了，需用户确认）、`suggestions`。
  把 `report` 那段念给用户听。
* **导出** `tavotto_export { session_id, formats: ["pdf","png"] }`。有 `errors`
  时它会拒绝——**先去修，或者问用户**；用户明确说「就这样导出」才带
  `explicit_confirm: true`（这次会记进 proof report）。
* **收尾** `tavotto_close_session { session_id }`。
* **改了 .py 之后** `tavotto_refresh_project { session_id }`——让 Tavotto 界面自己更新，
  见下文「修改绘图脚本之后」。

期刊有自己的尺寸就带 `journal`，只覆盖点名的键：
`{"journal": {"widths_mm": {"double": 178}}}`。

**这些工具不会动用户的 .py 源码。** 数据、坐标范围、加删曲线/子图、colorbar
方向仍然只能回代码改（清单见 `references/compatibility.md`）。

## 交接给 Tavotto 桌面窗口

只在用户明确要外部窗口、或需求超出 MCP 工具能力时（多图拼版、画布标注、
(a)(b) 编号、版本历史、写回原图）才交接：

```
python3 scripts/handoff.py <脚本路径>
```

**退出码非零就是没做完**；输出是一行 JSON，你必须读——`parameterizable`
的判定与全部 `error_code` 的分诊在 `references/desktop-handoff.md`。

## 完成判据：画完之后就收手

回一句话：这张图画了什么、落在哪个文件、预检怎么样。然后按下表分工，
**不要默认重写代码**：

| 用户想改 | 谁来做 |
| --- | --- |
| 图例挪位置 / 字号 / 线宽 / 颜色 / marker 样式 | `tavotto_apply_overrides`（或画布里拖） |
| 刻度朝内朝外、刻度标签字号 | `tavotto_apply_overrides` |
| 标题、轴标签的文字与位置 | `tavotto_apply_overrides` |
| 「这图合不合投稿规范」 | `tavotto_preflight` |
| 出图 | `tavotto_export`（PDF 是真矢量） |
| 多图拼版、加箭头标注、加 (a)(b) 编号、写回原图 | Tavotto 桌面窗口（`handoff.py`） |
| 数据本身、坐标范围、对数/线性、加一条新曲线 | 代码（回来改脚本） |
| colorbar 方向、子图数量与结构 | 代码 |

## 修改绘图脚本之后：让 Tavotto 自己更新

新建、修改、重命名或删除了绘图脚本（用户要求改数据 / 坐标范围 / 加曲线这类必须回
代码的改动之后），按这七步收尾——**不要要求用户手动刷新或重启 Tavotto**：

1. 保存代码（脚本与产物同目录，契约不变）；
2. 调 `tavotto_refresh_project`（有会话就传 `session_id`，否则传 `project_path`）；
3. 读返回的 `registry`（新增 / 移除 / 变更了哪些脚本）与 `readiness`（每张图的状态）；
4. 状态是 `editable` 的图：告诉用户 Tavotto 已更新、可以直接在里面改；
5. 状态是 `needs_probe` 的：说明需要用户在 Tavotto 里点一次「试运行并连接」——**不要猜**
   它会产出哪张图，也不要替用户跑脚本；
6. 状态是 `conflict` 的：把候选脚本列给用户，**不自动裁决**；
7. `delivered: local` 说明 Tavotto 没开着（或是桌面版）：刷新已在本地完成，用户下次打开
   项目时自动生效；开着的桌面版会由它自己的 watcher 在两秒内跟上。

这条工具**不运行脚本**。开着的 MCP 会话仍然端着改动前的图：要在会话里继续用
`tavotto_apply_overrides` 改这张图，就 `tavotto_close_session` 再 `tavotto_open_figure`
重开一次；只是让 Tavotto 界面跟上，则刷新就够了。用户撞上 Tavotto 自身的缺陷时，按
`references/issue-reporting.md` 写脱敏的 issue 草稿——**用户明确允许才外发**。
