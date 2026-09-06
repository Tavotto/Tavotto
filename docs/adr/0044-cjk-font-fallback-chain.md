# ADR 0044：图内中文的字体回退链——汉字不再画成方框

日期：2026-09-06 · 状态：**Accepted**
相关：[0033 科学文本与字形回退](0033-scientific-text-and-font-fallback.md)（本 ADR **取代**其 §7 第 1 条）、
[0032 属性能力层](0032-typography-capability-layer.md)、[0030 统一检查](0030-validation-and-problem-navigation.md)。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| 图内文字（matplotlib）里的汉字 | 每段文字的族列表后面接一条**按平台探测**的中日韩尾巴，matplotlib 逐字形退过去（`overrides.cjk_fallback_tail()`） |
| 候选名单 | `overrides.CJK_FALLBACK_CANDIDATES`：macOS PingFang SC → Hiragino Sans GB → STHeiti → Heiti SC → Songti SC → Arial Unicode MS；Windows Microsoft YaHei → SimHei → DengXian → SimSun → KaiTi；Linux Noto Sans CJK SC → Source Han Sans → WenQuanYi → Noto Serif CJK SC → Droid Sans Fallback → AR PL UMing。本平台那组在前，其它平台的跟在后 |
| 进链的条件 | `findfont(fallback_to_default=False)` 真解析得到。**只有装了的才进链**，所以链上没有 matplotlib 会 warn "not found" 的名字 |
| 什么时候接 | **脚本跑完之后、采 baseline 之前**（`figsession.instrument_all()`）：逐个 Text 补（脚本给单个 Text 传的 `fontfamily=`、脚本整个覆盖掉的 `rcParams['font.family']` 都只有事后补才管用），再把 rcParams 补齐给之后才创建的文字（懒建的刻度标签、override 新加的标注） |
| 「当前值」 | 用户 / 脚本设的族仍在最前，`get_fontfamily()[0]` 不变，manifest 与预检报的都是它；下拉里能钉住的中文字体与自动回退的候选是**同一张表** |
| 汉字由尾巴画出算不算「换了脸」 | **不算**（与画布侧 `glyphplan.substituted_chars` 同一个裁决）：那张脸对整台机器恒定、是唯一画得出它的，逐条挂建议只会训练用户忽略问题面板。值得说的是**由谁画的**：manifest 新增 `cjk_family` |
| `cjk-fallback-missing` 的主语 | **真正画出汉字的那张脸**（`cjk_family`，没有就是正文族）对出版规范 `cjk_fallback.accepted`。接手的脸在白名单里就不响；不在就报**那张脸**（`cjkFallbackUnaccepted`），不再说「会是方框」 |
| 字体来源 | 不变：**本仓库不分发任何字体**。候选全是系统自带或用户自装的 |
| 诊断开关 | `TAVOTTO_CJK_FALLBACK=0` 关掉中日韩尾巴（只留 DejaVu Sans）。测试用它造「一张中文字体都没有」的世界；不是产品设置 |

## 1. 背景：先量，再改

用户反馈第 3 条：「图内中文无法正常渲染」。实测（macOS，matplotlib 3.10.8）：

| 层 | 现象 | 结论 |
|---|---|---|
| ① matplotlib | `plt.title("中文标题")` 走完引擎，manifest 把四个字全列进 `glyphs_missing`；PDF 只嵌了 `DejaVuSans`；PNG 上「中」与「文」是**逐像素相同**的两张位图（同一个 .notdef 空心框）；matplotlib 自己 warn 了 `Glyph 20013 … missing from font(s) DejaVu Sans.`，那条只落在 worker 的 stderr 日志里 | **坏在这一层**。`FONT_FALLBACK_TAIL` 只有 DejaVu Sans，而且尾巴只在用户改字体时才接上——脚本原样创建的 Text 连 DejaVu 尾巴都没有 |
| ② 出版规范 / 预检 | `glyph-missing`（error）与 `cjk-fallback-missing`（warn）都响，此刻都是**对的**；但后者的判据是「正文族名在不在中日韩白名单里」，一旦回退链把字画出来了它仍然会说「导出 PDF 里会是方框」 | 回退链落地之后**会误报**，主语要改成真正画出汉字的那张脸 |
| ③ 画布文字（PyMuPDF） | 三个通用族的 `text_plan("中文标题")` 都是 `cjk` 层；导出的 PDF 嵌了 `Droid Sans Fallback Regular`，文本层读回原字符串 | **没坏**，一行没改 |
| ④ 前端 | `canvas_coverage.json` 把汉字判成 `cjk` 层；图内文字的预览是 matplotlib 出的 SVG（`svg.fonttype=path`），前端不自己排字 | **没坏**；只需认得 manifest 新字段 `cjk_family` 并镜像预检那条规则 |

顺带量到两条 matplotlib 机制，回退链的形状由它们决定：

* **尾巴必须在 `font.family`（Text 的族列表）里，放进 `font.sans-serif` 没用**。
  `_find_fonts_by_props` 对族列表里的每一项各解析出**一个**文件，通用族 `sans-serif`
  只会取 `font.sans-serif` 里得分最高的那一个——列表再长也只是一个候选池，不是回退链。
* **Type 42 嵌入 .ttc 没问题**（Hiragino Sans GB.ttc / Songti.ttc / STHEITI.ttf /
  Arial Unicode.ttf 四种都嵌成 Type0 子集，文本层读回 `中文标题 Ab`），而且尾巴对
  没有汉字的图**一个像素都不改**（拉丁图两种配置的 PNG 逐像素相同）。

## 2. 为什么推翻 ADR 0033 §7 第 1 条

那条的理由是「尾巴里不放平台相关的中文字体：同一份文档在两台机器上会画出不同的字，
比一条说得清楚的问题项更坏」。它成立的前提是用户会去下拉里选一个中文字体。实际
发生的是用户把「中文全是方框」当成产品不支持中文（反馈原话）——**一盏红灯没有替代
「字画出来」**。

两台机器画出不同的字这个代价仍然在，处理方式从「不做」改成「说出来 + 能钉住」：

* manifest 的 `cjk_family` 说得出这一台机器用的是哪张脸；
* 下拉里能选的中文字体与回退候选是同一张表，用户选中的正是自动回退本来会用的那张，
  写回脚本之后就跨机器确定了；
* 出版规范的白名单继续把关：期刊要求方正小标宋时，Hiragino 画出来的中文照样被报。

## 3. 明确没做的

1. **不内置任何字体**（体积与许可证，ADR 0033 §6 原样保留）。没装任何中文字体的机器
   （Pyodide playground、没装 Noto CJK 的 Linux）行为退回改造前：逐字报 `glyph-missing`。
2. **画布文字不动**：它走 PyMuPDF 自带的 CJK 脸，本来就画得出。
3. **不区分简繁日韩的偏好**：候选按「简体优先、无衬线优先、系统自带优先」排一次，
   繁体 / 日文 / 韩文文字由链上第一张有那个字形的脸接住，不另设偏好。

## 4. 看护

* `tests/test_cjk_figure_text.py`：manifest / PDF 文本层 / PNG（「中」≠「文」）/ 预检四把尺子，
  加反向对照（关掉尾巴 ⇒ 「中」==「文」）与拉丁图像素不变；
* `tests/test_glyph_coverage_figure.py` 在 `TAVOTTO_CJK_FALLBACK=0` 下继续量「方框被报成问题」；
* `tests/golden/preflight_vectors.json` 新增 `cjk-drawn-by-accepted-fallback-face` /
  `cjk-drawn-by-unaccepted-fallback-face`，pytest 与 vitest 各跑一遍。
