# ADR 0032：属性能力层 —— 「一段文字长什么样」全产品只有一套词汇

状态：**Accepted**
日期：2026-09-01
相关：[0030 统一检查与问题定位](0030-validation-and-problem-navigation.md)（property path 与
定位锚点读同一张表）、[0029 Style / Spec / Export 三层](0029-style-spec-profiles.md)
（Style 应用也经这一层写）、[0031 统一导出管线](0031-unified-export-pipeline.md)
（属性只改 `doc.objects` 与 `panel.overrides`，载荷在导出那一刻现取），
本轨道文档 [`docs/implementation/product-ux-reliability/`](../implementation/product-ux-reliability/STATUS.md)。

## 裁决摘要

| 问题 | 裁决 |
|---|---|
| 「一段文字长什么样」谁说了算 | **一套规范属性名 + 一套取值语义**：`web/src/lib/typography.ts`。UI 只认这里的名字 |
| 图内文字 vs 画布文字 | **同一个接口的两个适配器**（`TypographyAdapter`），不是两套控件。写入仍各走各的 writer |
| 字重 / 字形 | 两侧都是 `'normal' \| 'bold'` / `'normal' \| 'italic'`。画布那侧的 boolean **只活在磁盘上**，换算只在 `readCanvasText` / `writeCanvasText` 一对里 |
| 「不支持」「没设过」「多个值」 | **三个不同的答案**，`TypographyValue` 四档不压扁。字号 mixed 不画成 9 pt，字体 inherit 不画成一次显式设置 |
| 「能改什么」 | 图内由 **manifest** 说了算（静态表只回答「值不值得问引擎」），画布由 `TextObject` 的字段说了算 |
| property path | **只有 `propertyPathOf()` 一份**：检查报的字段名、控件挂的 `data-prop`、问题面板查的选择器同源 |
| 画布文字的字体族 | **闭集，三个通用族**（`serif` / `sans-serif` / `monospace`），与 `pdfbackend.CANVAS_TEXT_FAMILIES` 严格同源 |
| 没设过字体 | 字段**不存在**（`fontFamily?`），生效值经 `effectiveCanvasFamily()` 现算。磁盘格式不升版 |
| 装不上的字体 | **名字仍然显示 + 一条 warning**，绝不悄悄换一个再把文档改掉 |
| 校验失败 | 闭集成因（`not_a_number` / `out_of_range` / `not_an_option` / `not_a_color`），**不 clamp、不进历史、不写文档** |
| 恢复 | 删字段回到继承，**不写一个等于默认值的显式值** |
| 浮动工具条 | 与属性页**同一个适配器**。它以前是第二份实现（没有斜体、没有字体、mixed 无从谈起） |
| 科学文本 | 本轮**只定义能力**（`MathTextMode`：`inline_markup` / `engine_mathtext`），管线归 Prompt 14 |

---

## 1. 背景：两套词汇，用户说不出一句话

改造前，「一段文字长什么样」在产品里有两套互不认识的表达：

```text
图内文字（matplotlib Text）  fontsize / weight:'bold' / style:'italic' / ha / fontfamily
画布文字（TextObject）       sizePt   / bold:true      / italic:true    / align / （没有）
```

后果不是「代码重复」，是**用户说不出一句话**：

* 把图标题和压在它上面的标注一起选中，界面上没有一个控件能同时描述它们；
* 「都设成 9 pt 的加粗衬线」要在两个面板里各做一遍，而其中一个面板**根本
  没有字体这一行**——`_draw_text` 里的拉丁字体是写死的 Times；
* 浮动工具条上的文字快捷编辑是**第三份实现**：只有字号 / 加粗 / 颜色，
  没有斜体，没有字体，多选时 `o.bold = !o.bold` 与属性页的 `!bold` 会算出
  不同的结果。

## 2. 一层词汇，两个适配器

```text
                       lib/typography.ts
        规范属性名 · 取值语义 · 能力表 · property path · 校验/规整
                              │
              ┌───────────────┴───────────────┐
   useFigureTypography                 useCanvasTypography
   （manifest 说了算）                  （TextObject 字段说了算）
   setOverride / setOverrides            updateObjects
              └───────────────┬───────────────┘
                     TypographyAdapter（一个接口）
                              │
                    controls/TypographyControls
        属性页 · 图内批量 · 画布标注 · 浮动工具条 —— 同一份控件
```

控件那一侧**看不到**「这是标题还是标注」「目标是一个还是三个」。两件事都由
适配器吸收，于是「多选之后 B/I 退化成文字下拉」「标注面板没有字体这一行」
这类分叉在**结构上**不可能再出现——它们本来就是两份实现的症状，不是两份配置。

写入仍然各走各的 document action（图内 `setOverride(s)`，画布
`updateObjects`）：**界面语言统一，数据通道不混**，也就没有一条路径绕开
`documentStore.commit`。

## 3. 四档值，一档都不许压扁

```ts
{ kind: 'uniform';     value }               // 所有目标一致
{ kind: 'mixed' }                            // 目标之间不一致
{ kind: 'inherit';     value }               // 支持，但谁都没设过（显示继承来的那个）
{ kind: 'unsupported'; reason }              // 至少一个目标不支持，说得出为什么
```

压扁任意两档都会造成**数据损坏级的误导**：字号 mixed 画成 9 pt，用户看一眼
以为「它们本来就一样」，一敲回车就把两个不同的字号抹平；字体 inherit 画成一次
显式设置，以后默认族改了它就不跟了。

`unsupported` 的成因是闭集：`kind_unsupported`（这类对象结构上没有这条属性，
如画布文字的垂直对齐）、`not_in_manifest`（引擎这次没发，如只有 fontsize 的
图例）、`mixed_kinds`（选择横跨两类，这条不是两类都有）。

**「能不能改」有两层，缺一不可**：只有静态表会摆出一个点了没反应的控件；
只有 manifest 则说不出「这类对象本来就没有这条属性」。

## 4. 画布文字的字体族：闭集是一句能力承诺

`TextObject` 新增可选字段 `fontFamily?: 'serif' | 'sans-serif' | 'monospace'`。

**为什么只有三个通用族**，不是系统字体列表：合成与写回跑在 Flask 进程里，
那里**没有 matplotlib**（`src/tavotto/AGENTS.md` 的进程边界），画字只能用
PyMuPDF 自带的 base-14——它恰好就是这三个族（Times / Helvetica / Courier）。
把「Times New Roman」摆进这个下拉，得到的会是「界面上选得中、导出时悄悄换
一个」，那正是这一层要消灭的东西。这条闭集与
`pdfbackend.CANVAS_TEXT_FAMILIES` **严格同源**，顺序也比——第一个是「没设过时
生效的那个」。

**没设过 ≠ 设成 serif**：字段缺席时 `effectiveCanvasFamily()` 现算默认族，
文档里一个字节不多，导出载荷里一个字段不多，老文档发出去的字节逐字不变。
写回默认值时**删字段**，不写一个等价的显式值。

**中日韩那一半不跟着族走**：实测 PyMuPDF 1.28.2 的 `china-ss` / `china-s` /
`china-ssb` / `china-sb` 四个别名回的是同一个 `Droid Sans Fallback Regular`。
旧注释写的「CJK 走宋体」是一句没量过的断言，本轮按实测改掉了。

**新增一条能违反规范的路，就要把检查的范围一起扩过去**：标注能设字体之后，
`font-family-substituted` 这条规则必须看得见它们，两侧求值器同时加、golden
向量新增两条（既有 21 条一条没变）。

## 5. property path：报字段名的和挂锚点的读同一张表

Prompt 11（ADR 0030）定下的定位链是
`issue → 画布 → 模式 → 对象 → 视口 → 选中 → Inspector → 属性字段`，最后一跳
靠 `data-prop` 选择器。**这一跳本轮之前是断的**：文字工具条把六条属性从
平铺列表里摘走了（平铺那一份是有 `data-prop` 的），却没有把锚点一起带过来。
于是每一条图内排版问题（`font-too-small` / `font-family-substituted` /
`text-weight-policy`）点「定位」都只是选中对象，焦点没落到字段上——**而界面
宣称定位成功了**。

处置有三层，缺一层就会再断一次：

1. property path 只有 `propertyPathOf(kind, prop)` 一份；
2. 控件按它挂锚点（`TypographyControls` 的 `Anchor`），B / I 用
   `display:contents` 各挂各的（`text-weight-policy` 报的是 `weight`）；
3. `TEXT_BAR_PROPS`（「平铺列表要让出哪几条」）**从同一张表算出来**，
   不手抄——手抄的那份会在加一条属性时忘记更新，症状是同一个属性出两套控件。

顺带把 `focusField()` 的谎言改掉：它回的 `focusedField: boolean` **恒为
true**（只要 `propertyPath` 非空就 true，真正的查找排在 rAF 里、结果没人看得
见），而注释写着「找不到就如实回 false」。现在是三档
`none` / `focused` / `requested`，说的是它做得到的那一句。

## 6. 事务：连续输入合并，invalid 不进历史

画布对象**没有预览平面**（`TextView` 直接读文档），所以画布这一侧的手势只管
事务边界，不碰渲染；图内那一侧照旧走 `elementWrite.useFieldGesture`
（预览 + 事务 + 定稿渲染）。两侧的「一轮」用同一个安静时长（450 ms）——不一样
的话同一个动作在两个面板里的撤销粒度会不同。

* 一次点击 / 一轮拖动 / 一串打字 = **一条历史**；
* 多对象一次修改 = **一条历史**，撤销一次全组回滚；
* 别处的离散动作（对齐、撤销、版本恢复）会先 `finishActiveGesture()`；
* **invalid 输入一个字都不写**：不开事务、不 commit、不进历史。校验在
  `coerceTypography()` 里，**不 clamp**——把 500 pt 悄悄改成 400 等于替用户
  按了一个他没按的键。

**`write()` 自己会开一轮**，不依赖调用方先喊 `beginGesture()`：数字框只有
拖动才发 `onScrubStart`，打字那条路没有。（第一版用例自己先调了
`beginGesture`，把这件事挡在了判据外面——变异反证里那条改动活了下来。）

## 7. 没有做的事

* **「新建标注时套用当前 Style」没有做。** 本仓库里 Style 是**一次性应用**，
  不是文档上的绑定（ADR 0029 绑的是 Spec，不是 Style），因此「当前 Style」
  这个概念不存在。把它做成本机 UI 偏好会让**同一个动作在两台机器上建出不同
  的对象**，比现状更坏。本轮的处置是把新建默认值收敛成
  `canvasTextDefaults()` 一处（Style 应用也经同一层写），真正的绑定留给后续
  阶段与用户裁决。
* **科学文本管线归 Prompt 14。** 这里只给出能力名字（`MathTextMode`），
  两类对象的上下标压根不是同一件事，含糊成一个 `supportsMath: boolean` 会在
  14 那边被迫拆开重来。
* **`text_weight_policy` 里的 `annotation` 一档仍然没有执行者。** 规范文件里
  声明了「标注一律常规字重」，而 `addSubLabels()` 造出来的 (a)(b)(c) 按惯例
  是加粗的。现在就执行会让每一份既有文档立刻多出一批警告；这是**规范范围**
  的问题，不是这一层的问题，留待与用户确认。
