---
name: tavotto-figure
description: 画 matplotlib 论文级图表，并用 Tavotto 继续微调（拖图例、改字号线宽、调刻度、按出版规范预检、导出矢量 PDF）；也把用户**已经画好的图**按投稿要求改宽度 / 字体 / 最小字号而不重画（保留式规范化）。用户要画图、出图、做 figure、画折线/柱状/散点/误差棒、做论文配图、scientific plot / publication figure，要把现有图改成 8 cm / 换 Times New Roman / 字号不小于 8 pt，或提到 Tavotto 时使用。
---

# Tavotto Figure

一句话：**数据与结构归代码，版式微调归鼠标。**

你只负责把图做成「Tavotto 能接手」的形状。图例位置、字号、线宽、刻度朝向这些，
用 Tavotto 的 MCP 工具改（在当前宿主里就能改完）——**不要在对话里追问这类参数，
更不要为这种改动重跑脚本**。唯一的例外是「开工三问」：那三件事决定整张图的
骨架，动手前先问清。

## 会话入口：先检查，不安装

1. 本会话第一次使用 Tavotto 时，优先调用 `tavotto_health`，只调用一次。宿主可能给工具名
   加命名空间前缀（如 `mcp__tavotto__tavotto_health`）——**按原始名认工具**，语义不变。
2. `ok: true`：立即进入任务；本会话绝不执行插件安装、升级、pip/pipx 或
   provision——健康的会话里一次联网安装都不该发生。
3. 工具存在但引擎不可用：按健康检查返回的错误码，只给出或执行**对应的一条**
   恢复动作（动作清单见 `references/first-run-and-recovery.md`）。缺什么修
   什么：缺引擎只修引擎，绝不顺手重装插件。
4. `desktop_only`：不要说「没有安装 Tavotto」——用户装了桌面版。桌面交接仍然
   可用；只有用户需要宿主内嵌画布/MCP 工具时，才建议 provision 或
   `pipx install "tavotto[worker]"`。其余错误码（含 `engine_too_old` / `engine_incompatible`）按 `references/first-run-and-recovery.md` 处理。
5. 当前会话没有 `tavotto_health` 这个工具：说明 Tavotto 没有在本会话加载。按**当前
   宿主**给安装说明：Codex 给 README 的两条插件安装命令（见
   `references/first-run-and-recovery.md`）；其他宿主按 `references/other-hosts.md` 里那一家
   的步骤；认不出宿主就给那里的中性步骤——**不要默认让所有人执行 `codex plugin add`**。
   然后要求**新开会话**（或按宿主要求重载 MCP 服务），然后**停止**；
   不要在旧会话里继续假装工具可用。
6. 发现插件更新：当前任务照常完成，只在收尾提醒一次（Codex：
   `codex plugin marketplace upgrade tavotto`），不自动升级、不反复提醒；
   其他宿主的升级命令见 `references/first-run-and-recovery.md` 的「插件有新版本」。
7. 健康检查或恢复失败：报告结构化错误与下一步，不循环重试，不退回源码构建。

## 什么情况下读哪份 reference

| 情况 | 读 |
| --- | --- |
| 写任何画图脚本之前 | `references/figure-contract.md`（契约详解 + 模板）、`references/publication-style.md`（默认值 / 克制 / 组图） |
| 工具缺失、引擎不可用、要装/升级/provision、工作区授权 | `references/first-run-and-recovery.md` |
| 宿主不是 Codex（Cursor / Claude / VS Code / Trae / DSH / WorkBuddy / ZCode …）的安装、重载、能力差异 | `references/other-hosts.md` |
| 要交给 Tavotto 桌面窗口 | `references/desktop-handoff.md`（含全部错误码分诊） |
| 用户撞上 Tavotto 的缺陷 | `references/issue-reporting.md` |
| 用户要改的东西鼠标改不了 | `references/compatibility.md`（能改 / 必须回代码改） |

普通画图任务只读前一行的两份；故障与交接文档**用到才读**。

## 开工三问（用提问工具，不用自由文本追问）

先读已记录的偏好（脚本路径相对本技能目录；解释器用 `tavotto_health` 回报的 `server.python`）：

```
python3 scripts/prefs.py --json
```

三个键里**记录过的直接用，不再问**——唯一例外是 `width` 记的是 `ask`：
那个哨兵值的含义就是「宽度每次都问」，撞见它宽度照问（另外两个键照常）。
没记录的用宿主的**向用户提问工具**（ask user question / request_user_input，一次问卷问齐，别拆成三轮对话）问；
宿主没有专用提问工具时，用**一条普通消息**把三件事一次问齐。宿主不能在本机执行脚本时跳过偏好文件：
直接问，也**不声称已经记住**：

1. **画幅宽度**——单栏 **8 cm** / 双栏 **15 cm**；用户不清楚就按任务推荐并说明理由，推荐项放第一位。
2. **字体**——选项**必须包括 Times New Roman 与 Arial**（默认推荐 Times New Roman）。
3. **图例加不加框**——加框 / 无框（默认推荐无框）。用户选了加框，之后调
   `tavotto_preflight` / `tavotto_export` 时带 `{"journal": {"legend_policy": {"frame": true}}}`。

推荐宽度的依据、偏好的写入（`--set` / `--unset`）与「图例加框」不当违规的原因，见
`references/publication-style.md` 的「开工三问细则」。

## 图文件契约（核心，违反即死图）

1. **脚本与产物同目录，而且必须先落成文件**——绝不用 `python -c`、
   `python - <<EOF` 或临时目录出图。宿主没有本机文件写入 / 执行能力（Claude Desktop 聊天）
   时：把脚本给用户，请他存进项目目录并运行，**不要声称已经保存或运行过**；云端代码执行
   环境里的路径不是本机路径，不能传给 Tavotto。落点是用户当前目录下的 `figures/`
   （已有 `tavotto_registry.json` 的图库就沿用）。
2. **入口是无参 `main()`**，import 期零副作用。
3. **产物名写成静态可解析的字面量**，不来自 argv/时间戳/随机串；一图一 stem。
4. **交付物是矢量 PDF**（`imshow` 的位图除外，≥300 dpi）。
5. **可复现**：固定随机种子，不 `plt.show()`。
6. **只用 Matplotlib 出图、不弹窗不等输入**：tkinter / Qt 对话框、`input()`、Pillow 直接画图
   在 Tavotto 里跑不起来。依赖：处处可用的只有 numpy / matplotlib / pillow；默认环境没有的包
   （如 ROOT）与改用户已有脚本的做法，细则见 `references/figure-contract.md` 第 6 节。

## 图画完之后：MCP 工具顺序

会话入口的 `tavotto_health` 已经过了，直接开：

```
tavotto_open_figure { "project_path": "/absolute/path/to/figures", "stem": "Fig1_removal_rate" }
```

第一次 open 的工作区授权规则（绝对路径、用户确认、拒绝后不重试）在
`references/first-run-and-recovery.md` 的「工作区授权」一节。回来的是
`session_id` + `manifest`（哪些元素可改）+ 预览 SVG + 出版规范 + 预检结果。
支持 MCP Apps 的宿主会同时开出一块交互画布；没有画布的宿主用下面同一组工具照样走完，
不要把外部窗口叫作「内嵌画布」。结果带 `elided`（图很大）、或一个脚本出好几张独立图
（用 `stems` 一次全开）时，读 `references/compatibility.md` 的「大图与多图打开」。

之后按序：

* **改图** `tavotto_apply_overrides { session_id, patches, summary: true }`。
  `patches` 是 `{gid, prop, value}` 的**全量列表**——列表里没有的 `(gid, prop)`
  会自动恢复成脚本原始值，所以**每次都要发完整的一份，不要发增量**。
  gid 与 prop 从 manifest 的 `elements[].editable` 里取，别猜。`summary` 的版本差异与读回执的办法
  见 `references/compatibility.md` 的「大图与多图打开」。
* **已有图的规范化**（改宽度 / 字体 / 字号下限）走 `tavotto_normalize_figure`，
  不要用 apply 手拼——见下文「已有图的规范化」。
* **体检** `tavotto_preflight { session_id }`。四档结果：`errors`（默认阻止
  导出）、`warnings`、`not_verifiable`（查不了，需用户确认）、`suggestions`。
  把 `report` 那段念给用户听。
* **导出** `tavotto_export { session_id, formats: ["pdf","png"] }`。有 `errors`
  时它会拒绝——**先去修，或者问用户**；用户明确说「就这样导出」才带
  `explicit_confirm: true`（这次会记进 proof report）。
* **收尾** `tavotto_close_session { session_id }`。
期刊有自己的尺寸就带 `journal`（写法见 `references/publication-style.md`）。

**这些工具不会动用户的 .py 源码。** 数据、坐标范围、加删曲线/子图、colorbar
方向仍然只能回代码改（清单见 `references/compatibility.md`）。

## 已有图的规范化：保留原图，只改点名的东西

用户拿来一张**内容正确、排版已经不错**的图，只要「改成 8 cm 宽」「换 Times New
Roman」「文字不小于 8 pt」「出 300 dpi 的 PNG」这类规范化修改时，按下面走，
**不要**自己拼 `tavotto_apply_overrides`、不要重写脚本、不要重画：

1. **先识别输入**：`tavotto_open_figure` 开它（脚本必须在产物旁边，否则这张图只是位图 / 轮廓文字，改不了字号字体——如实告诉用户，不要缩放位图冒充「已改字号」）。打开回来的状态就是原图基准 B0。
2. **发起保留式规范化**：`tavotto_normalize_figure { session_id, width_mm?, height_mm?,
   font_family?, min_font_pt?, font_size_pt?, formats?, dpi? }`。只传用户点名的目标：
   只说了宽度就只传 `width_mm`（高度按原长宽比自动推）；「不小于 8 pt」传
   `min_font_pt`，**不是** `font_size_pt`（后者会把标题、标签、刻度全部压成同一个字号，
   只有用户明确要求统一字号才用）。
3. **它自己会**只改点名的属性、在真实渲染上检测、做有预算的局部适配、核验最终文件，四件事同时成立才提交，否则回到 B0、不出文件（细则见 `references/compatibility.md` 的「保留式规范化」）。
4. **按结果说话**，工具调用成功 / 文件生成了都不是「任务完成」——看 `exit`：
   * `done`：说清**明确要求的修改**、**必要的局部调整**（多少 mm）、**保留的原有问题**
     （`verdict.issues.*.unchanged`，不顺手修）、规范与用户要求冲突之处（`profile_conflicts`）。
   * `constraint_conflict` / `budget_exceeded`：装不下。念出具体冲突，**列出需要放宽的最小约束**
     让用户选，**不要自己放宽**。
   * `font_unavailable`：这台机器没装那个字体，Tavotto 没有替代。告诉用户装字体或换字体；
     **不要**自己换一个相近字体再宣称达标。
   * `requires_authorization` / `protected_changed`：需要用户明确提出新要求。
   * `acceptance_failed`：文件没过验收、没有交付；把 `failed_files` 的原因如实报告。
   各档的完整话术读 `references/compatibility.md` 的「已有图的规范化：完整话术」。
5. **提交之后会话受修改约定保护**：再调 `tavotto_apply_overrides` 改约定之外的东西会被
   拒（`requires_authorization`）。只有用户**明确**提出了新要求，才带
   `user_authorized: true` 重发（约定解除、验收报告作废，导出时会如实标出）。

**禁止的绕路**（后端只能守住它管辖的那条路，其余靠这里）：不改用户的 .py 源码、
不另写一份绘图脚本替代原图、不整份替换 SVG、不用 Pillow / OpenCV / 截图缩放重建
图形、不套全局主题或 rcParams 风格、不为「消除拥挤」删曲线 / 删图例项 / 删刻度 /
截断文字、不改配色 / 数据 / 坐标范围 / 子图结构。这些都不是「规范化」，是重做。
`tavotto_export` 直接导出仍然可以，但它回执里的 `normalized.verified` 会说明这次
导的是不是通过验收的那一版——不是就别把它当规范化结果交出去。

## 交接给 Tavotto 桌面窗口

只在用户明确要外部窗口、或需求超出 MCP 工具能力时（多图拼版、画布标注、
(a)(b) 编号、版本历史、写回原图）才交接。**执行前先读 `references/desktop-handoff.md`**
（含 Windows 版 Codex 的沙箱外运行、`sandboxed_launch`、全部 `error_code` 分诊）：

```
python3 scripts/handoff.py <脚本路径>
```

**退出码非零就是没做完**；输出是一行 JSON，你必须读。退出码 0 只说交接成了，
**不说图画出来了**：再读 `render_success`（`false` = 界面明确渲染失败，`null` = 还不知道，
不是失败）和 `verified_success`，没到 `true` 就不要对用户说「已在 Tavotto 里渲染好」。

## 完成判据：画完之后就收手

回一句话：这张图画了什么、落在哪个文件、预检怎么样。然后按下表分工，
**不要默认重写代码**：

| 用户想改 | 谁来做 |
| --- | --- |
| 已有图改宽度 / 高度 / 字体 / 最小字号（保留原图） | `tavotto_normalize_figure`，按 `exit` 说话 |
| 图例挪位置 / 字号 / 线宽 / 颜色 / marker 样式；刻度朝内朝外、刻度标签字号；标题、轴标签的文字与位置 | `tavotto_apply_overrides`（或画布里拖） |
| 「这图合不合投稿规范」 | `tavotto_preflight` |
| 出图 | `tavotto_export`（PDF/SVG/EPS 是真矢量，PNG/TIFF 按 dpi 栅格化） |
| 多图拼版、加箭头标注、加 (a)(b) 编号、写回原图 | Tavotto 桌面窗口（`handoff.py`） |
| 数据本身、坐标范围、对数/线性、加一条新曲线；colorbar 方向、子图数量与结构 | 代码（回来改脚本） |

## 修改绘图脚本之后：让 Tavotto 自己更新

新建、修改、重命名或删除了绘图脚本（用户要求改数据 / 坐标范围 / 加曲线这类必须回代码的改动之后），
保存代码后调 `tavotto_refresh_project`（有会话传 `session_id`，否则传 `project_path`），
再按返回的 `registry` / `readiness` 逐张交代。**不要要求用户手动刷新或重启 Tavotto。**
`editable` / `needs_probe` / `conflict` 各怎么说、`delivered: local` 的含义、开着的会话要不要重开，
读 `references/compatibility.md` 的「修改绘图脚本之后」。用户撞上 Tavotto 自身的缺陷时，按
`references/issue-reporting.md` 写脱敏的 issue 草稿——**用户明确允许才外发**。
