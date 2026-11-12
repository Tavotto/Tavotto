# ADR 0169：文字标注可以选这台电脑上的字体

日期：2026-11-05 · 状态：**Accepted**（维护者 2026-11-05：「新建标注只能选三类字体的问题 做一下」；1.0 豁免见 `docs/1.0-release-readiness.md` §1.8）
修订 2026-11-05（维护者：「继续完成 行内 \textbf{} 用本机粗体、中文粗体、字体下拉的搜索框」）：见文末「修订：同族脸与搜索框」。
修订：[0164 画布文字用原图字体](0164-canvas-text-source-font.md)（裁决 10、11 的粗斜规则与字段来源）、[0060 字体政策](0060-font-policy-and-allowlist.md)（批准集合仍是默认与回退）
相关：[0045 CJK 回退链](0045-cjk-font-fallback-chain.md)（图内文字的「英文 + 中文」两格）、`docs/rules/repo/third-path-review.md`。

## 动机

同门在 Windows 上反馈：文字标注的字体只有「衬线 / 无衬线 / 等宽」，图内文字却能选本机全部字体。论文图常见要求是英文 Times New Roman、中文宋体，标注做不到。

ADR 0164 已经有本机字体通道（文档只记身份、导出按字节认脸、别的电脑缺字体时回退并提示），但只有「复制图内文字」能走进去。

## 要保持的不变量

1. 文档不带字体文件和路径，磁盘格式不升版，没选过本机字体的文档逐字节不变。
2. 没有 `source_font` 的对象走的代码路径不变。
3. 用户字体只经 `source_font` 进 RenderCore，用到时由 `userfonts.admit` 现读字节核对；下拉说能用不算数，`admit` 说了算。
4. 不按文件名猜字体。~~不造假粗体~~：修订 2026-11-05 改为同族没有的粗斜由写入器模拟，见文末裁决 18。
5. 路径不出本机。

## 第三路径

开放 `TextObject.fontFamily` 闭集会让「图里写着一个名字、画出来是另一张脸」变成可能，也要在每处判字族的代码里加分支。这里不动闭集：**选本机字体 = 写一条 `sourceFont`**。下拉只是 ADR 0164 通道的第二个入口。

## 裁决

| # | 问题 | 裁决 | 落点 |
| --- | --- | --- | --- |
| 1 | 清单怎么来 | 后台线程走 `system_font_dirs()` 与定位线索目录，每张脸按 `admit` 同一组判据（`fonts.font_kind`、`embedding_refusal`、大小上限）筛；CFF 另核 CID。缓存在 `data_dir()/font-catalog.json`，文件 (size, mtime_ns) 没变不重读。扫描顺带把 sha256 写进定位索引，选中后不必再扫。作者机 426 族首次 4.7 秒，之后走缓存 | `rendercore/fontcatalog.py` |
| 2 | 请求要不要等扫描 | 不等。`GET /api/source-fonts/catalog` 立刻回当前清单和 `state`，`scanning` 时前端 2 秒后再问；60 秒内不重复走目录。第一次用到文字属性才问，启动不扫；字体控件每次挂载时若距上次问完已满 60 秒就再问一次（装了新字体不必重启应用），同一时刻只有一个请求在飞，失败不重试，不做定时轮询 | `app.py`、`web/src/lib/fontCatalog.ts` |
| 3 | 一行是什么 | 一族 = name 表 1 号名字（RIBBI：最多常规 / 粗 / 斜 / 粗斜四张）；「Arial Narrow」「Helvetica Neue Light」各成一族，这正是 Word 的列法。中文名取 name 表简体优先，走现有 `option_labels`，同名消歧走现有 `fontFamilyOptionLabel` | `fontcatalog._family_rows` |
| 4 | 嵌不进去的字体 | 不列（变量字体、彩色字体、非 CID 的 CFF、禁止嵌入）。选了也嵌不进去，列出来只会多一条警告 | `fontcatalog.faces_in` |
| 5 | 中文单独选 | 字体行用现有的「英文 + 中文」两格，中文格只列带中文字形的族，写 `sourceFont.cjk`。英文字体自带中文时中文格置灰，与图内文字同一规则。这台机器没有中文字体时只有一格 | `typographyAdapter.ts` |
| 6 | 英文内置、中文本机 | `sourceFont` 要求有正文脸。批准字体也有 sha256，所以正文脸点名批准字体本身（Liberation，`fonts_dir()` 登记为定位线索），不改文档形状；`isBuiltinFace` 让界面继续显示「衬线」。中文回到自动时整条 `sourceFont` 清掉，文档回到没选过的样子 | `lib/fontCatalog.ts`、`app._prepare_source_fonts` |
| 7 | B / I（修订 0164 裁决 11） | 脸在清单里：换成同族那张，这份归属来自清单而不是文件名；这一族没有那张脸时~~按钮置灰~~（修订：模拟，见裁决 18）。脸不在清单里（别的电脑复制来的原图字体）：沿用 0164，身份作废 | `lib/fontCatalog.restyleLocalFace`、`styleMissing` |
| 8 | 缺席回退族 | 选字体时按 OS/2 panose / sFamilyClass / `post.isFixedPitch` 推出通用族写进 `fontFamily`，别的电脑缺字体时回退到最接近的批准族；说不准就保持原值 | `fontcatalog._generic_of` |
| 9 | 浏览器版 / 旧后端 | 端点 404 → 空清单，下拉仍是内置三项 | `fontCatalog.load` |
| 10 | 单项恢复英文 | 有中文脸时只换英文：中文脸保住，正文脸换回默认族（`serif`）的批准字体，`fontFamily` 删掉回到继承；「两项都恢复」仍整条清掉。正文脸是批准字体且 `fontFamily` 没设时英文格不算「改过」。中文脸本机缺失或清单里没有时中文格照留，选项带当前名字并标「字体缺失」 | `fontCatalog.resetLocalFamily`、`typographyAdapter.ts` |

## 不做 / 边界

* 缩略图仍用批准族栈（0164 同一条）。

## 验证

* `tests/test_font_catalog.py`：列出来的脸 `admit` 接受、禁止嵌入的不列、批准字体不重复、未变文件不重读、响应没有路径、内置英文 + 本机中文真实导出。
* `web/src/lib/fontCatalog.test.ts`：写入规则（选族、中文格、回到自动、B / I、清单不认识的脸）。
* `web/e2e/canvas-local-fonts.spec.ts`：真实界面选字体、选中文、加粗、导出请求带身份。
* `tests/test_rendercore_userfonts_windows.py`（真 Windows）：Times New Roman 四张脸、宋体、微软雅黑进清单且中文名正确，清单里每张脸 `admit` 都接受，Times + 宋体真实导出两张脸都嵌入。Windows Lab run `20261105T032645Z-a8181e`：53 passed，0 skipped。未覆盖 WebView2 真窗口与 Windows 10 / 11 客户端差异。

## 反证

| 变异 | 红在 |
| --- | --- |
| `faces_in` 不查 `embedding_refusal` | `test_a_face_that_forbids_embedding_is_not_listed` |
| `snapshot` 不滤批准字体 | `test_the_approved_faces_are_not_listed_twice` |
| `scan` 不复用未变文件 | `test_an_unchanged_file_is_not_read_again` |
| `setLocalCjkFamily(null)` 对内置正文脸只删 `cjk` | `回到自动：内置英文 + 自动 = 整条 sourceFont 清掉` |
| `restyleLocalFace` 没有那张脸时仍改 `o.bold` | `这一族没有那张脸：不动，按钮置灰` |

## 修订：同族脸与搜索框（2026-11-05）

原来三条「不做」：行内粗斜用批准脸、中文不加粗、下拉没有搜索框。

| # | 问题 | 裁决 | 落点 |
| --- | --- | --- | --- |
| 11 | 同族脸从哪来（修订 0164 裁决 10） | 正文脸和 `cjk` 各加可选 `siblings: [{sha256, face, bold?, italic?}]`（最多 3 张），选字体时从清单写入。身份进文档，导出不依赖清单是否扫完，换电脑结果也可判。没有 `siblings` 的文档（复制来的原图字体）走 0164 原路径；文档不升版，老版本读到会忽略 | `sourcefont.py`、`lib/sourceFont.ts`、`lib/fontCatalog.ts` |
| 12 | 行内 `\textbf{}` / `\textit{}` | 合并后的粗斜等于对象自己的 → 正文脸；同族有正好这张 → 用它；否则模拟（裁决 18）；只有这张脸在本机用不了才回批准脸 | `typography.faces_for`、`plan._user_faces` |
| 13 | 中文粗体 | `cjk` 留常规脸，要画粗时按 (粗, 斜) → (粗, 常规) → 常规脸取。对象加粗和行内 `\textbf{}` 同一条路。这一族没有粗体时（宋体、黑体）模拟（裁决 18） | `plan._user_faces`、`FaceSet.cjk_variant` |
| 14 | 用不上的脸 | 用到才入场：不读这个对象没用到的兄弟，缺了也不报。用到的缺失时记 `source_font_unavailable`，退到同族常规脸模拟；常规脸也用不了才回批准脸 | `plan._user_faces` |
| 15 | 预览 | 字体栈按片段算，判据和回退顺序同导出，只取要画的那张脸的字节。同族没有的粗斜交给浏览器合成，导出同样模拟 | `lib/sourceFontPreview.ts`、`canvas/TextView.tsx` |
| 16 | 搜索框 | 新控件 `SearchSelect`：图内文字和标注的字体行共用，收起样子同 `Select`；搜中英文名，不分大小写、不看空格，↑↓ 回车。不往 Radix Select 里塞输入框：条目悬停抢焦点，字打不下去。收起时不渲染选项，本机字体几百项时属性页更快 | `components/ui/SearchSelect.tsx` |

| 17 | 没选本机中文字体时的粗体 | 批准集合加 Noto Sans SC Bold、Noto Serif SC Bold（维护者 2026-11-05：「加入 Noto Sans SC Bold 和 Noto Serif SC Bold」），`cjk_face(family, bold)` 随对象与片段取。见 ADR 0060 修订。预览：系统字体栈靠不住（macOS 的 Songti SC 在 Chromium、WebKit 里都没有粗体），文字有中文且要粗体时经本机服务加载批准的 Noto 粗体，`unicode-range` 限定只画中日韩字符；没有中文不取字节；浏览器版退回系统栈。| `fonts.cjk_record`、`typography.faces_for`、`lib/sourceFontPreview.ts`、`TextView.tsx` |

| 18 | 没有粗 / 斜体的字体（修订 0164 裁决 10、本 ADR 不变量 4 和裁决 7） | 维护者 2026-11-05：「像Word一样，模拟加粗，并进这个PR，还有斜体也一样」。按钮不再置灰，对象记下要的粗斜，字形不变，写入器模拟：粗体 = 同色描边，宽度取 Skia 合成粗体的表（9 pt 及以下 1/24 字号，36 pt 及以上 1/32）；斜体 = 错切 0.25。两个数都跟浏览器一致，画布才对得上导出。英文、中文、内置中文斜体同一条路。文本层不变（PDF 文字渲染模式 2，SVG 可编辑文字写 `font-weight` / `font-style` 由打开它的软件合成，SVG 轮廓与 EPS 走描边 + 错切）。挑脸先要粗细对、再要斜体对，不用更粗或更斜的兄弟顶常规 | `ir.synth_bold_width`、`typography.SynthFace`、`plan._user_faces`、`pdfwriter` / `svgwriter` / `epswriter` |
| 19 | 复制图内文字 | 有 `face_src` 时对象的粗斜取那张脸自己的：字体没有粗体时图里画的是常规脸，复制过来不再模拟一遍 | `lib/figureTextCopy.ts` |

设置里的样式档案字体下拉仍是 `Select`：它在模态对话框里，选项也不走这张字体行。

验证：`tests/test_font_catalog.py`（行内粗体同族脸、无同族模拟、同族缺失报问题、未用到不读、中文对象粗体与行内粗体、内置中文斜体、PDF 与 SVG 写入）；`web/src/lib/sourceFontPreview.test.ts`；`web/src/components/ui/searchSelect.test.tsx`；`tests/golden/source_font_vectors.json` 加 `siblings` 向量。
