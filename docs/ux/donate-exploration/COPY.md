# Support page — final copy / 最终文案

English is the primary language of the international site; the Chinese is written for Chinese readers, not
translated word for word. British spelling follows the README (millimetre, colour, rasterise).
英文为国际官网主语言；中文按中文读者重写，不逐字直译。

Placeholders in square brackets must be resolved before launch. 方括号内为上线前必须替换的占位。

---

## Masthead · 刊头

| EN | 中文 |
| --- | --- |
| Support the project | 支持这个项目 |
| Support Tavotto ↓ | 支持 Tavotto ↓ |
| Skip to the support form | 跳到支持表单 |

## I — The promise · 承诺

**Discovery is hard enough. Expressing it shouldn’t be.**
**探索已经足够艰难，表达不该再添阻碍。**

> **Tavotto is an open-source editor for scientific figures.** It lets you finish the figure your script drew
> without rewriting the script. This page is about why we build it, and how you can help.

> **Tavotto 是一个开源的科研图形编辑器。** 它让你直接完成脚本画出的那张图，而不必一次次重写脚本。
> 这一页讲我们为什么做它，以及你可以怎样帮助它继续下去。

Figure caption · 图注
- **Fig. 1** Dose–response, illustrative data drawn for this page. Points, mean ± s.d. of three replicates;
  line, four-parameter Hill fit. The legend is selected: in Tavotto, everything around the data can be shaped.
- **图 1** 剂量–反应关系，为本页绘制的示意数据。点为三次重复的均值 ± 标准差；线为四参数 Hill 拟合。
  图例处于选中状态：在 Tavotto 里，数据周围的一切都可以被塑造。

## II — The problem · 问题

**The last millimetre shouldn’t take another night.**
**最后一毫米，不该再耗掉一个夜晚。**

Every published figure carries hours that nobody sees.

A legend that covers the last data point. Tick labels a point too small for the journal. Panel (b) sitting a
millimetre lower than panel (a). Each fix means changing a line, running the script again, opening the file and
looking. Then doing it again.

The science was finished long before the figure was. The distance between a result and a figure that shows it
well is where Tavotto works.

每一张发表出来的图，背后都有无人看见的几个小时。

图例压住了最后一个数据点；刻度标签比期刊要求小了一号；(b) 面板比 (a) 低了一毫米。每一处修改，都意味着
改一行代码、重新运行脚本、打开文件、再看一眼。然后，再来一遍。

科学结论早已完成，图却还没有。结果与一张恰当呈现它的图之间的这段距离，正是 Tavotto 工作的地方。

Strip caption · 缩略图说明
- Nine runs, two hours and forty minutes, for a legend that moved three millimetres. A reconstruction of an
  ordinary evening.
- 九次运行，两小时四十分钟，只为把图例挪动三毫米。——一个普通夜晚的复原。

## III — The principle · 原则

**Keep the source. Shape the figure.**
**保留脚本，雕琢图形。**

Tavotto opens the figure your script has already drawn, and lets you shape it directly.

Legends, axes, type, and the arrangement of panels on a page measured in millimetres. Each change is kept beside
the figure rather than written into your code, and replayed whenever the script runs again.

Tavotto 打开脚本已经画好的图，让你直接塑造它。

图例、坐标轴、文字，以及面板在以毫米度量的页面上的排布。每一处修改都保存在图的旁边，而不是写进你的代码；
脚本再次运行时，这些修改会被重新应用。

| Step | EN | 中文 |
| --- | --- | --- |
| 01 | **As the script drew it.** A figure the way a script usually leaves it: correct, complete, and not yet ready. The legend covers the last points of panel (b). The axis labels are small. The panels don’t line up. | **脚本画出的样子。** 这是脚本通常留下的一张图：正确、完整，但还没有准备好。图例压住了 (b) 面板最后几个点，坐标轴标签太小，两个面板也没有对齐。 |
| 02 | **Move the legend.** Pick it up and put it where it belongs. Its position is kept, in millimetres, beside the figure. Nothing in the script has changed. | **移动图例。** 把它拿起来，放到该在的位置。位置以毫米记录在图的旁边。脚本一个字都没有改。 |
| 03 | **Set the type.** Axis labels from 7 to 9 pt, as the journal asks. The same words your script wrote, at the size the page needs. | **调整字号。** 坐标轴标签从 7 pt 调到期刊要求的 9 pt。还是脚本写下的那些字，只是换成了版面需要的大小。 |
| 04 | **Align the panels.** Panel (b) rises to meet panel (a), and both receive their labels. The page is measured in millimetres, so what aligns here aligns in print. | **对齐面板。** (b) 面板上移，与 (a) 对齐，两者都加上面板标号。页面以毫米度量——在这里对齐的，印出来也对齐。 |
| 05 | **The data stays where it was.** Curves, points and error bars come from your script, and only from your script. Tavotto makes a figure easier to shape — never its data easier to change. | **数据留在原处。** 曲线、数据点与误差棒来自你的脚本，也只来自你的脚本。Tavotto 让图更容易被塑造，但绝不让数据更容易被改动。 |

Stage captions · 舞台状态行
1. As drawn by fig_dose.py — no changes yet / fig_dose.py 画出的原样——尚无修改
2. Legend moved to the upper left · data unchanged / 图例移至左上 · 数据未变
3. Axis labels 7 → 9 pt · data unchanged / 坐标轴标签 7 → 9 pt · 数据未变
4. Panel (b) aligned to (a), labels added · data unchanged / (b) 与 (a) 对齐，加上标号 · 数据未变
5. Four changes kept beside the figure · script and data unchanged / 四处修改保存在图旁 · 脚本与数据均未改变

## IV — The invisible work · 看不见的工作

**Good tools take work you may never see.**
**好的工具，背后有许多你看不见的工作。**

A figure editor is mostly made of things that should never be noticed.

They are noticed only when they fail: a font that turns into boxes, an export that quietly rasterises the text, a
change that is lost on the next run. Here is some of that work, as it stands in October 2026.

一个图形编辑器，大部分由本不该被注意到的东西构成。

只有出错时它们才会被注意到：字体变成方框，导出时文字被悄悄栅格化，一处修改在下一次运行时丢失。
下面是其中一部分工作，截至 2026 年 10 月。

Trace caption · 记录线说明：A good day, recorded. Eight things happen; nothing appears to. /
记录下来的顺利的一天：发生了八件事，看起来却什么都没发生。

| # | EN | 中文 | Source in repo · 依据 |
| --- | --- | --- | --- |
| 01 | **Your Python, as it is.** Python 3.10 to 3.14 and matplotlib 3.8 to 3.11 — in the runtime Tavotto carries, or in your project’s own environment, which is in beta. | **就用你现有的 Python。** Python 3.10 至 3.14，matplotlib 3.8 至 3.11——可以用 Tavotto 自带的运行环境，也可以用你项目自己的环境（Beta）。 | PRODUCT.md · Operating Context |
| 02 | **Tested where you work.** macOS on Apple silicon and Intel, and Windows x64: each with an installer, CI checks and a smoke test of the real build. Linux runs from PyPI in a browser tab, in beta. | **在你工作的地方测试。** macOS（Apple 芯片与 Intel）与 Windows x64：都有安装包、CI 检查，并对真实构建产物做冒烟测试。Linux 通过 PyPI 在浏览器标签页中运行，目前为 Beta。 | docs/support-matrix.json |
| 03 | **Nothing written until it is proven.** Before a change is written back into a figure file, the script runs again from scratch and the result is compared with what you saw — geometry and pixels. If anything differs, the file is left exactly as it was. | **未经证明，一字不写。** 在把修改写回图形文件之前，脚本会从头再运行一次，结果与你所见逐一比对——几何与像素。只要有任何不同，原文件就保持原样，一字不动。 | AGENTS.md · 写回事务不变式 |
| 04 | **Text that stays text.** PDF export embeds each vector panel as it was drawn, so text remains real, selectable, searchable text. PNG and TIFF are rasterised from that same PDF. | **文字始终是文字。** PDF 导出按原样嵌入每个矢量面板，文字始终是真实的、可选中、可搜索的文字。PNG 与 TIFF 从同一份 PDF 栅格化而来。 | README · Export for publication |
| 05 | **Checked before it leaves.** Before export, the figure is checked against a versioned publication profile: one set of rules, read by both the engine and the interface. | **离开之前，先做检查。** 导出前，图会依据一份带版本的出版规范接受检查：同一套规则，引擎与界面共读。 | PRODUCT.md · Positioning |
| 06 | **Failure, said plainly.** When a refresh fails, the status says so. Approximate previews are marked as approximate. Properties Tavotto cannot edit are listed, not hidden. | **失败就说失败。** 刷新失败时，状态如实说明。近似预览会标明是近似。Tavotto 无法编辑的属性会被列出来，而不是藏起来。 | PRODUCT.md · Principle 4 |
| 07 | **Your figures stay on your machine.** Everything runs locally. Telemetry is off until you say yes, and your scripts, paths and the text inside your figures are structurally unable to leave. | **你的图留在你的电脑上。** 一切都在本机运行。在你同意之前，遥测保持关闭；你的脚本、路径与图中的文字，在结构上就无法被发送出去。 | AGENTS.md · 隐私 |
| 08 | **Decisions, written down.** More than a hundred architecture decision records, kept in public beside the code — including the ones that say what Tavotto will not do. | **每个决定，都有记录。** 一百多份架构决策记录，与代码一起公开存放——也包括那些写明 Tavotto 不做什么的决定。 | docs/adr/（111 份） |

**Much of this work is invisible when everything goes right. All of it matters.**
**一切顺利时，这些工作大多不为人见。但每一项都很重要。**

## V — The invitation · 邀请

**Help us keep building.**
**帮助我们继续做下去。**

Tavotto is free and open source, under the AGPL. It will stay that way.

What it needs to keep going is time: time to follow Python and matplotlib as they change, to test on the machines
researchers actually use, and to do the slow, careful work described above.

If Tavotto has given you back an evening — or if you believe researchers deserve tools made with this much care —
you can help pay for that time.

Tavotto 是免费的开源软件，采用 AGPL 许可证，并将一直如此。

让它继续走下去，需要的是时间：跟上 Python 与 matplotlib 的变化，在科研人员真正使用的电脑上测试，以及完成
上面那些缓慢而细致的工作。

如果 Tavotto 曾为你省下一个夜晚——或者你相信，科研人员值得拥有被如此认真对待的工具——你可以帮我们支付
这些时间。

**Where it goes · 资金去向**
- Before this page goes live, we will publish how contributions are used, and report on it as we go.
  [Link to the funding statement — to be written]
- 本页正式上线之前，我们会公开说明捐赠资金的用途，并持续报告。［资金用途说明的链接——待撰写］

**Form · 表单**

| EN | 中文 |
| --- | --- |
| Demo · Payments are disabled in this prototype. | 演示 · 本原型不进行任何真实付款。 |
| How often: Once / Monthly | 支持方式：单次 / 每月 |
| Amount · Currency (USD $, EUR €, CNY ¥) · Other amount | 金额 · 币种 · 其他金额 |
| Presets: $10 · $25 · $50 · $100 (EUR alike; CNY ¥50 · ¥100 · ¥200 · ¥500) | 预设金额同左 |
| You are giving $25, once. | 一次性支持 ¥100。 |
| You are giving $25 each month, until you stop it. | 每月支持 ¥100，可随时停止。 |
| Choose or enter an amount. / Enter an amount of at least $1. | 请选择或输入一个金额。/ 请输入不少于 ¥1 的金额。 |
| Continue to payment · Demo | 继续付款 · 演示 |
| This is a demo. No payment was taken and nothing left this page. In the live version, this step would continue to the payment provider. | 这只是演示：没有发生任何付款，也没有任何数据离开这个页面。正式上线时，这一步会进入支付服务商的页面。 |

**Terms beside the button · 按钮下的条款**
- A voluntary contribution to Tavotto’s development. It is not a purchase: it does not buy a licence, a future
  paid edition, or any feature. We make no claim that it is tax-deductible. Monthly support can be stopped at any
  time.
- 这是对 Tavotto 开发的自愿支持，而不是购买：它不对应任何授权、未来的付费版本或功能。我们不声称它可以抵税。
  每月支持可随时停止。

**Other ways to help · 其他帮助方式**
- Star the repository / 在 GitHub 上标星
- Tell us about a figure Tavotto gets wrong / 告诉我们 Tavotto 处理错的图
- Send this page to a colleague (Link copied) / 把这一页发给同行（链接已复制）

## VI — The continuation · 延续

**The next discovery is still unwritten.**
**下一个发现，尚未写下。**

Figure annotation · 图注：most recent measurement / 最近一次测量

Tavotto exists so that whatever is found next can be shown as clearly as it was found. Thank you for reading this
far — and, if you can, for helping.

Tavotto 的存在，是为了让下一次的发现，能像它被发现时那样清晰地呈现出来。感谢你读到这里——如果可以，
也感谢你的支持。

Support Tavotto ↑ / 支持 Tavotto ↑

## Footer · 页脚

- Tavotto™ is an open-source editor for scientific figures, released under AGPL-3.0-only. · Source code ·
  Releases · Privacy · Design prototype · October 2026 · Payments are disabled.
- Tavotto™ 是一个开源的科研图形编辑器，以 AGPL-3.0-only 许可证发布。· 源代码 · 下载 · 隐私 ·
  设计原型 · 2026 年 10 月 · 不进行任何真实付款。

---

## Direction A and C first-screen copy · 方向 A、C 首屏文案（英文，仅供对比）

**A** — Running head: “An open letter on scientific expression · Support · No. 01 · Oct 2026”. Footnote:
“¹ Tavotto is an open-source editor for scientific figures. It keeps your script as the source and lets you shape
the figure that script drew — never the data in it. What follows explains why it exists, and asks for your help.”

**C** — “To everyone who has stayed up late to move a legend, / Discovery is hard enough. Expressing it shouldn’t
be. / You know the evening we mean. The analysis is done and the result is real. What remains is a legend that
covers the last point, a label one size too small, a panel a millimetre out of line — and a script you run again,
and again, to fix them. / Tavotto is an open-source editor for that last stretch. It opens the figure your script
drew and lets you shape it directly, while the script stays the source and the data stays exactly as it was
measured. / We build it in the open, carefully, and we would like to keep doing so. This letter explains why, and
asks for your help. / With respect for the work you do, [Name of the maintainer], for the Tavotto project.”
Margin: “Nothing on this page is for sale. Support is voluntary; Tavotto stays free and open source either way.”
