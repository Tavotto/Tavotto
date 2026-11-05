# ADR 0169：文字标注可以选这台电脑上的字体

日期：2026-11-05 · 状态：**Accepted**（维护者 2026-11-05：「新建标注只能选三类字体的问题 做一下」；1.0 豁免见 `docs/1.0-release-readiness.md` §1.8）
修订：[0164 画布文字用原图字体](0164-canvas-text-source-font.md)（裁决 10、11 的粗斜规则与字段来源）、[0060 字体政策](0060-font-policy-and-allowlist.md)（批准集合仍是默认与回退）
相关：[0045 CJK 回退链](0045-cjk-font-fallback-chain.md)（图内文字的「英文 + 中文」两格）、`docs/rules/repo/third-path-review.md`。

## 动机

同门在 Windows 上反馈：文字标注的字体只有「衬线 / 无衬线 / 等宽」，图内文字却能选本机全部字体。论文图常见要求是英文 Times New Roman、中文宋体，标注做不到。

ADR 0164 已经有本机字体通道（文档只记身份、导出按字节认脸、别的电脑缺字体时回退并提示），但只有「复制图内文字」能走进去。

## 要保持的不变量

1. 文档不带字体文件和路径，磁盘格式不升版，没选过本机字体的文档逐字节不变。
2. 没有 `source_font` 的对象走的代码路径不变。
3. 用户字体只经 `source_font` 进 RenderCore，用到时由 `userfonts.admit` 现读字节核对；下拉说能用不算数，`admit` 说了算。
4. 不造假粗体，不按文件名猜字体。
5. 路径不出本机。

## 第三路径

开放 `TextObject.fontFamily` 闭集会让「图里写着一个名字、画出来是另一张脸」变成可能，也要在每处判字族的代码里加分支。这里不动闭集：**选本机字体 = 写一条 `sourceFont`**。下拉只是 ADR 0164 通道的第二个入口。

## 裁决

| # | 问题 | 裁决 | 落点 |
| --- | --- | --- | --- |
| 1 | 清单怎么来 | 后台线程走 `system_font_dirs()` 与定位线索目录，每张脸按 `admit` 同一组判据（`fonts.font_kind`、`embedding_refusal`、大小上限）筛；CFF 另核 CID。缓存在 `data_dir()/font-catalog.json`，文件 (size, mtime_ns) 没变不重读。扫描顺带把 sha256 写进定位索引，选中后不必再扫。作者机 426 族首次 4.7 秒，之后走缓存 | `rendercore/fontcatalog.py` |
| 2 | 请求要不要等扫描 | 不等。`GET /api/source-fonts/catalog` 立刻回当前清单和 `state`，`scanning` 时前端 2 秒后再问；60 秒内不重复走目录。第一次用到文字属性才问，启动不扫 | `app.py`、`web/src/lib/fontCatalog.ts` |
| 3 | 一行是什么 | 一族 = name 表 1 号名字（RIBBI：最多常规 / 粗 / 斜 / 粗斜四张）；「Arial Narrow」「Helvetica Neue Light」各成一族，这正是 Word 的列法。中文名取 name 表简体优先，走现有 `option_labels`，同名消歧走现有 `fontFamilyOptionLabel` | `fontcatalog._family_rows` |
| 4 | 嵌不进去的字体 | 不列（变量字体、彩色字体、非 CID 的 CFF、禁止嵌入）。选了也嵌不进去，列出来只会多一条警告 | `fontcatalog.faces_in` |
| 5 | 中文单独选 | 字体行用现有的「英文 + 中文」两格，中文格只列带中文字形的族，写 `sourceFont.cjk`。英文字体自带中文时中文格置灰，与图内文字同一规则。这台机器没有中文字体时只有一格 | `typographyAdapter.ts` |
| 6 | 英文内置、中文本机 | `sourceFont` 要求有正文脸。批准字体也有 sha256，所以正文脸点名批准字体本身（Liberation，`fonts_dir()` 登记为定位线索），不改文档形状；`isBuiltinFace` 让界面继续显示「衬线」。中文回到自动时整条 `sourceFont` 清掉，文档回到没选过的样子 | `lib/fontCatalog.ts`、`app._prepare_source_fonts` |
| 7 | B / I（修订 0164 裁决 11） | 脸在清单里：换成同族那张，这份归属来自清单而不是文件名；这一族没有那张脸时按钮置灰，悬停说「这个字体没有粗体」。脸不在清单里（别的电脑复制来的原图字体）：沿用 0164，身份作废 | `lib/fontCatalog.restyleLocalFace`、`styleMissing` |
| 8 | 缺席回退族 | 选字体时按 OS/2 panose / sFamilyClass / `post.isFixedPitch` 推出通用族写进 `fontFamily`，别的电脑缺字体时回退到最接近的批准族；说不准就保持原值 | `fontcatalog._generic_of` |
| 9 | 浏览器版 / 旧后端 | 端点 404 → 空清单，下拉仍是内置三项 | `fontCatalog.load` |

## 不做 / 边界

* 行内 `\textbf{}` / `\textit{}` 仍用批准族的粗斜（0164 裁决 10）：`typography.faces_for` 只拿到一张用户脸。Times New Roman 与 Liberation Serif 等宽，差别小；要补得让导出认识同族四张，另算。
* 中文粗体：选了本机中文字体时 `cjk` 是常规脸，加粗只换英文脸。批准集合也没有粗体中文，这不是新退化。
* 下拉沿用 `Select`（键入跳转、无搜索框），与图内文字一致；几百项要搜索框的话两边一起改。
* 缩略图仍用批准族栈（0164 同一条）。

## 验证

* `tests/test_font_catalog.py`：列出来的脸 `admit` 接受、禁止嵌入的不列、批准字体不重复、未变文件不重读、响应没有路径、内置英文 + 本机中文真实导出。
* `web/src/lib/fontCatalog.test.ts`：写入规则（选族、中文格、回到自动、B / I、清单不认识的脸）。
* `web/e2e/canvas-local-fonts.spec.ts`：真实界面选字体、选中文、加粗、导出请求带身份。
* **需要上 Windows Lab**：`simsun.ttc` / `msyh.ttc` / `times.ttf` 进清单、中文名是否正确、`fsType` 判定、`%WINDIR%\Fonts` 扫描耗时。

## 反证

| 变异 | 红在 |
| --- | --- |
| `faces_in` 不查 `embedding_refusal` | `test_a_face_that_forbids_embedding_is_not_listed` |
| `snapshot` 不滤批准字体 | `test_the_approved_faces_are_not_listed_twice` |
| `scan` 不复用未变文件 | `test_an_unchanged_file_is_not_read_again` |
| `setLocalCjkFamily(null)` 对内置正文脸只删 `cjk` | `回到自动：内置英文 + 自动 = 整条 sourceFont 清掉` |
| `restyleLocalFace` 没有那张脸时仍改 `o.bold` | `这一族没有那张脸：不动，按钮置灰` |
