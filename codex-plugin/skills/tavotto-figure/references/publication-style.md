# 出版样式与克制（画任何图之前读）

## 投稿默认值（就是 `lab-publication-v1` 规范）

* 宽度：单栏 **80 mm（8 cm）**、双栏 **150 mm（15 cm）**——按开工问题的答案选，
  **不出这两档之外的宽度**；比例取 16:9 / 4:3 / 1:1
* 字号：正文 **9 pt**；**最终有效字号不低于 8 pt**（正好 8 pt 通过）——这是规范里唯一的
  字号下限；缩小放进拼版的图要按缩放后的字号算
* 线宽：**0.5 / 0.75 / 1.0 / 1.5 pt** 四档里选
* 坐标轴：封闭（四条边都留）、**刻度朝内、只留主刻度线，次刻度线不画**；
  轴标题写成 `Title (unit)` 且**默认加粗**（`axes.labelweight: bold`，
  用户特殊要求才改回常规字重）
* 图例：按开工问题的答案加框或无框（默认无框）
* 字体：按开工问题的答案（默认 Times New Roman，另一个标准选项是 Arial）；
  有中文就显式加中文字体（否则导出 PDF 里是方框）
* 位图（`imshow` 等）≥ 300 dpi；交付物存矢量 PDF
* 色系：用 Scientific colour maps（batlow / vik / roma…），按 sequential /
  diverging / categorical 语义选；**不要用 jet / rainbow**
* 数学式用 mathtext（`$\mathrm{cm^{-1}}$`）

用户另有期刊要求就按用户的，并在调工具时带 `journal` 覆盖。
画完跑一次 `tavotto_preflight` 确认，别凭记忆打包票。

## 克制：数据之外的效果，一个都不擅自加

图上只画用户要的数据。**下面这些没有用户明确要求就一律不加**：

* 用背景色块/色带标注「某段数据好或差」；
* 在图里画箭头指向某个数据点或区域；
* 「peak here」「注意这里」这类说明性文字标注；
* 高亮、阴影、星号显著性标记等一切装饰性效果。

你觉得某个效果确实能帮读者理解时，**用向用户提问工具先问**（说明加什么、
为什么），用户点头才画；用户没点头就保持素图。「先加上再让用户删」不是
省事，是把用户的图改成了你的图。

## 多子图：在 matplotlib 里拼，不假手他人

任务涉及多个子图组成一张主图时：

* **主图宽度 150 mm（15 cm）**，在**一个脚本、一个 Figure** 里用
  `plt.subplots` / `GridSpec` 排版，一次 `savefig` 出整张主图；
* **绝不用别的软件拼**——不用 PIL/ImageMagick 拼贴位图、不用 LaTeX
  subfigure、不导出散件让用户自己拼。matplotlib 之外拼出来的不是矢量整图，
  Tavotto 也接不住；
* **每个子图的 x 轴与 y 轴都各自标全**：轴标题、刻度线、刻度标签一样不少
  ——同一行的子图哪怕 y 轴标题一字不差，也逐个 `set_ylabel` /
  `set_xlabel`，**不许只给最左（最下）那个留、其余留白**；
* **不共享坐标轴**：不用 `plt.subplots(..., sharex=…, sharey=…)`——它会把
  里侧子图的刻度标签藏掉。要让各子图坐标范围一致，就逐个
  `set_xlim` / `set_ylim` 对齐，刻度与轴标题仍然每个子图自己标；
* 子图各自的标题用 `ax.set_title(...)`（默认居中），**矩阵式各归各位**：
  每个标题落在自己那个子图的正上方，不挤在整图左上角，也不用
  `loc="left"`（用户点名要左对齐才用）；
* 子图另需单独交付时，每张也按 8 cm / 15 cm 两档出，同样一图一 stem；
* 子图编号 (a)(b)(c) 用户要了才加（这也算克制原则里的标注）。

以上是默认值，用户特殊要求（比如「里侧子图不要重复轴标题」）就按用户的。

## 开工三问细则（技能正文只留摘要）

先读已记录的偏好（脚本路径相对本技能目录；解释器用能跑起来的那个——Windows 上 `python3`
可能是商店别名，用 `tavotto_health` 回报的 `server.python`；有 `skill_script_command` 时换成 `<命令> prefs …`）：

```
python3 scripts/prefs.py --json
```

三个键里**记录过的直接用，不再问**——唯一例外是 `width` 记的是 `ask`：
那个哨兵值的含义就是「宽度每次都问」，撞见它宽度照问（另外两个键照常）。
没记录的用宿主的**向用户提问工具**
（ask user question / request_user_input，一次问卷问齐，别拆成三轮对话）问；宿主没有专用
提问工具时，用**一条普通消息**把三件事一次问齐。宿主不能在本机执行脚本（例如 Claude Desktop
聊天）时跳过偏好文件：直接问，也**不声称已经记住**：

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

## 按期刊

Tavotto 内置了期刊标准库（每条数值都带原文出处）。用户说「投 Nature」「按 ACS 的要求」时：

1. `tavotto_list_journals { "query": "nature" }` 按名字 / 别名 / 出版社查，拿到 `id`。查到多本就把候选念给用户选，**不要猜**。
2. 之后 `tavotto_open_figure` / `tavotto_preflight` / `tavotto_export` / `tavotto_normalize_figure` 都带
   `"journal_id": "<id>"`。它等于「中性底规范 + 这本期刊的覆盖」：期刊写了的项才判，没写的不判，也不套课题组规范。
   结果里的 `journal_ref`（id / 目录版本 / 出处日期）照实告诉用户这是按哪本期刊、哪一版判的。
3. `journal_id` **不能**和 `profile_id` / `journal` 同时给（`journal_id_conflict`）。开工三问的「图例加框」在这里不用带：
   期刊规范不判图例框。
4. `journal_unknown`：库里没有这本（关了期刊同步、本机又没有缓存时，种子以外的刊也会这样报）。告诉用户，请用户给期刊的
   作图要求原文（栏宽、最小字号、dpi），再按下面手写 `journal` 覆盖——并说明这次是按用户给的数值判的，不是期刊库。
   **不要凭记忆编期刊规范。**
5. 列表里带 `downloaded: false` 的刊，标准还在服务端：第一次带它的 `journal_id` 时才下载（之后就在本机，不再下载）。
   下不来时会明确报错，不会悄悄换成别的数据：
   - `journal_quota_exceeded`：下载额度用完了。照 `error` 里的时间告诉用户「约 N 分钟后再试」（`retry_after_s` 是秒数），
     期间别重试，也别想办法换设备令牌。
   - `journal_offline`：这本还没下载到本机，又联不上网。请用户联网后再试，或换一本 `downloaded` 为真 / 内置的刊。
   - `journal_verification_failed`：这份数据没通过校验，已整份弃用。不要用它的任何数值，也不要自己补。
   这三种都**不要**改成手写 `journal` 继续判，除非用户给了期刊要求原文（按第 4 步）。

手写覆盖只写点名的键：`{"journal": {"widths_mm": {"double": 178}}}`。

### 逐条来源事实（v2，工具可用时）

`tavotto_get_journal_facts` 可用时，`{"query":"期刊名"}` 搜索 v2 目录；确认期刊后
`{"journal_id":"<v2 id>"}` 读取事实与来源。按 `next_offset` 继续直到没有下一页；每页的
`facts` / `coverage` 各自带来源，`coverage.result=not_stated` 只表示指定来源和范围内未见，
不能扩大成整个期刊未规定。读取失败或目录不可用时如实告知，不用 v1 / 种子冒充 v2 事实。

每条事实的图类、其他条件、投稿阶段、比较符和要求强度都要一起理解：修稿、录用后与生产阶段不互换；`example` 不是封闭的
字体白名单，`not_in` 是禁止项，`condition_text` 或无法判断的条件需要人工核对。**v2 是
来源信息，不会自动进入预检。** 如要用画布预检，仍单独通过 `tavotto_list_journals` 确认
v1 中对应的刊和 ID；不把 v2 ID 当作自动兼容，也不暗示 v1 能判 v2 的所有条件。
