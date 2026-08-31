# SESSION_HANDOFF — 跨 Session 交接

> **整段重写，不加行。** 半新半旧的状态块会继承「刚被动过」的可信度，
> 而下一个 Session 没有办法分辨哪几行是这次更新的。

---

## 最近一次：Session 12（2026-08-31）

### 目标

把导出**底层与界面**同时重做：原图与画布收敛成**同一个 `ExportRequest` 的
两个 scope**，作业获得原子落盘 / 取消 / 部分失败可见 / 明确的覆盖策略，
导出面板按用户做决定的顺序重排并删掉六类噪音。

本阶段**不做**属性系统改造（Prompt 13）、**不做**科学文本与字体回退
（Prompt 14）、**不动** MCP 那条导出入口（另一个 bundle、另一份载荷）。

### 开始前实测到的四件事（不是假设）

1. **「按原图导出」这条路根本不存在。** ADR 0028 定义了 `OriginalOutputSpec`，
   Prompt 09 让快速编辑跑起来了，但导出那一端只有画布合成——用户在快速编辑里
   改完一张图点导出，拿到的是**那张图在画布上的落位**。
2. **「导出什么」有四份构造**：`ExportDialog.tsx` / `app.api_export()` /
   codex-plugin bridge / `/api/package`，默认值并不一样。
3. **导出目录里的文件全都带 `_MMDD_HHMMSS`**：撞名问题解决了，代价是用户
   永远拿不到自己起的那个名字。
4. **合成是直写目标路径的**：`canvas.save_pdf(out_dir / name)` 没有临时文件，
   中途失败留下半个 PDF；`proof` 也是 `write_text` 直写。

### 实际完成

**1. `engine/exportreq.py` —— 「这次导出要什么」只有这一份定义。**

```text
scope ∈ {original, canvas}   formats   filename   ppi: int|None
background   overwrite ∈ {ask, replace, rename}   validation{policy, acknowledged}
include_style_check_report   document_id / document_revision
canvas{page_w_mm, page_h_mm, objects[]} | original{figure_id, overrides[], w/h/px, ignored[]}
```

`OriginalSource` 上**没有** x/y/w/h、页面尺寸、crop（T-59）。缺省值只有这一处；
旧契约（`stem` / `items[]`+`texts[]`）由 `normalize()` 抬成同一个作业，
`legacy_naming=True` 让它继续拿到带时间戳的名字。

**2. `engine/exportjob.py` —— 一次导出的生命周期只有这一份实现。**
`prepare / validate / run / cancel / progress`；临时目录 → 全部产出完成 →
`atomicio.publish_file()` 逐个原子 replace；`partial` 是独立一档；
取消清临时目录；`sweep_stale_tmp_dirs()` 扫掉上一次进程被 kill 留下的。

**3. `atomicio.publish_file(tmp, dest)`** —— 「字节从来没经过我们的手」那条
路上的同一套纪律（fsync 文件 → replace → fsync 目录 → 失败清 tmp）。

**4. `pdfbackend` 新增两个原图产出 + 透明背景。**
`original_pdf()`（矢量整页 `insert_pdf` 搬运，不重画）/ `original_png()`
（位图**保源像素网格**）/ `compose(w, h, transparent)`。

**5. 五个端点、一个服务。** `POST /api/export`（同步，旧契约照旧）、
`/api/export/start`（后台作业 + SSE `export.progress`）、`/state`、`/cancel`、
`/validate`。后台线程经 `app.bound_project(ctx)` 钉在起它的那个项目上（T-63）。

**6. 前端三个新模块。** `lib/exportName.ts`（文件名规则，**严格同源对**）、
`lib/exportRequest.ts`（载荷构造 + scope 默认值 + 原图可用性 + 快照指纹）、
`store/exportStore.ts`（作业编排：SSE + 轮询、晚到快照挡掉、失败保留设置）。

**7. 导出面板整屏重做。** 文件名 → 输出范围 → 格式 → 分辨率（仅位图）→
规范 → 检查 → 高级选项。删除清单逐项见下；「打包项目」**搬到 TopBar 文档菜单**
与「导入项目包」并排（移走不等于砍掉）。

**8. 「留档」→「样式检查报告」**，进高级选项，格式升 v3（+ 版本 / 时间 /
产物事实 / scope / ignored）。文件名 `<基名>_style-check.json`；旧路径仍写
`<stem>_<时间戳>_proof.json`，`kind` 不变。

### 关键 API（Prompt 13 直接用）

```python
# src/tavotto/engine/exportreq.py
normalize(spec) -> ExportRequest        # 缺省值唯一出处
check_filename(name) -> str | None      # 八条闭集原因；顺序是判据的一部分
strip_output_extension / output_name / dedupe_name
SCOPES / FORMATS / OVERWRITE_POLICIES / BACKGROUNDS / VALIDATION_POLICIES
PPI_MIN / PPI_MAX / PPI_DEFAULT / FILENAME_MAX / ERROR_CODES

# src/tavotto/engine/exportjob.py
prepare(spec, export_dir) -> ExportJob   validate(job) -> dict
run(job, produce, *, publish=None, report=None) -> dict    run_async(...)
cancel(job_id) / progress(job_id) / sweep_stale_tmp_dirs(dir) / ERROR_CODES
Produced / Output / Cancelled / STATUS_*

# src/tavotto/app.py
bound_project(ctx)                       # 后台线程钉项目（**必须**）
_export_produce(job, tmp_dir)            # canvas / original 的分歧只在这里
```

```ts
// web/src/lib/exportName.ts   —— 与 exportreq.py 严格同源
checkFilename / stripOutputExtension / outputName / outputNames / dedupeCheck / FILENAME_MAX

// web/src/lib/exportRequest.ts  —— 载荷构造**唯一一处**
buildExportRequest(input): { request, names, revision }
defaultScope(mode) / originalAvailability(figureId) / filenameProblem(raw, formats)
snapshotRevision(request) / hasRaster(formats) / PPI_MIN / PPI_MAX / PPI_DEFAULT

// web/src/store/exportStore.ts
prepareExport(input)          // 不发网络，输入框每敲一个字都能调
validateExportRequest(input)  // 重名 / 目录写不写得了
runExport(input) / cancelCurrentExport() / applyExportJob(job) / resetExportState()
liveRevision(input)           // 用**此刻的文档**重算指纹
useExportStore                // job / running / startError / lastInput / editedDuringExport
```

### 迁移

**没有磁盘格式改动**，除了两处**新增**：
* 样式检查报告 v3（新路径的新文件名；旧路径的 `_proof.json` 一个字节没动，
  `kind` 仍是 `tavotto-proof`）；
* 新路径的导出文件名不再带时间戳（旧路径带）。

`tests/golden/filename_vectors.json` 是新增的跨语言向量；生成器
`scripts/gen_filename_vectors.py`（`--write` 重生成，无参校对）。

### 修改的文件

```text
新增  src/tavotto/engine/exportreq.py          ExportRequest / 文件名规则 / 覆盖策略
新增  src/tavotto/engine/exportjob.py          作业生命周期 / 原子发布 / 取消 / 部分失败
新增  scripts/gen_filename_vectors.py          跨语言向量的生成与校对
新增  tests/golden/filename_vectors.json       37 check + 10 strip + 2 name + 5 dedupe
新增  tests/test_export_request.py             （22 条）
新增  tests/test_export_pipeline.py            （29 条）
新增  web/src/lib/exportName.ts                文件名规则（TS 侧同源）
新增  web/src/lib/exportName.golden.test.ts    （62 条）
新增  web/src/lib/exportRequest.ts             载荷构造唯一一处
新增  web/src/lib/exportRequest.test.ts        （10 条）
新增  web/src/store/exportStore.ts             作业编排（SSE + 轮询）
新增  web/src/store/exportStore.test.ts        （7 条）
新增  docs/adr/0031-unified-export-pipeline.md
改动  src/tavotto/engine/atomicio.py           +publish_file()
改动  src/tavotto/pdfbackend/pymupdf_backend.py +original_pdf/original_png/透明背景
改动  src/tavotto/pdfbackend/__init__.py       边界契约 +2
改动  src/tavotto/app.py                       导出端点整段重写 + bound_project()
改动  web/src/lib/api.ts                       ExportRequest/ExportJob/ExportOutput +4 端点
                                               +'export.progress' 事件
改动  web/src/hooks/useServerEvents.ts         +export.progress → applyExportJob
改动  web/src/components/ExportDialog.tsx      **整屏重做**（956 → 970 行）。界面项少了
                                               六类，行数没少是因为结果区 / 进度 / 冲突条 /
                                               范围说明各拆成了具名子组件
改动  web/src/components/TopBar.tsx            +「导出项目包」（从导出面板搬来）
改动  web/src/components/ExportDialog.test.tsx 重写（17 条）
改动  web/src/i18n/locales/*/dialogs.json      export.* 删 21 组 / 加 27 组
改动  web/src/i18n/locales/*/errors.json       +27 条后端 code 文案
改动  web/src/i18n/locales/*/workspace.json    +topbar.exportPackage / status.packaged*
改动  web/e2e/asset-library.spec.ts            预检摘要的措辞变了
改动  web/e2e/keyboard-golden-path.spec.ts     **修掉一处空门禁**（见下）
改动  tests/test_error_codes.py                扫描面 +2 模块 +3 正则 + ERROR_CODES 注册表
改动  tests/test_telemetry_invariants.py       埋点挪进 _export_telemetry，门禁跟着改扫描面
改动  AGENTS.md / src/tavotto/AGENTS.md / web/AGENTS.md
改动  docs/implementation/product-ux-reliability/*
重建  codex-plugin/mcp/widget/canvas.html      指纹 27fad295d1c942bb（评审回合后）
重建  web/dist-playground/                     指纹 32a6a5f66f78265c（不进 git）
```

### 界面上删掉的（§五 逐项）

预设整行 / 重复的期刊宽 / profile 的 id 与版本号 / 页面·栏位·字号·DPI·矢量·
位图的大方格 / 「合成走 PyMuPDF」「Codex 插件的 tavotto_export」那句说明 /
行内的内部对象标签 / 三点菜单里的「打包项目」/「留档」这个标签 /
`_时间戳` 后缀 / 导出摘要那一行（页面·N 面板·N 文字·N 标注）/
弹窗里可展开的第二套问题清单。

### 这一轮踩到的坑

**1. 变异反证抓出三条空判据，三种成因。**
① 「透明背景」量的是 `pix.alpha == 1`（有没有 alpha 通道），而变异改的是
"底下有没有铺白" —— **主语对了，维度错了**；② 「后台线程不绑定项目」在只开
一个项目的用例里量不出来 —— **判据看不见那个维度**；③ 「原图尺寸用 spec
还是用落位」在夹具里两个数字相等（都是 80×60）—— **夹具让判据恒真**。
三条都补了判据，第二轮 23/23 全红。

**2. `editedDuringExport` 第一版是恒等成立的。** 它拿 `lastInput.doc` 重算
指纹再跟自己比 —— 那份是导出开始时冻住的引用，**比出来永远相等**，而空的
diff 与"没变化"长得一模一样。改成现取 `useDocumentStore.getState().doc`。

**3. 一处 e2e 空门禁是新界面制造的。** `keyboard-golden-path` 用
`dialog.getByText(/\.pdf/)` 判"导出完成"，而新界面在文件名下方摆了一行
**文件名预览**（`Fig 1.pdf`）——那条判据在按导出**之前**就成立。改成断言
结果区的「已保存到」+ 一个真正指向 `/exports/` 的链接。

**4. 错误码门禁的前提变了。** 它靠正则扫源码找 `"code": "..."`，而
`exportreq._one_of()` 收的 code 是**变量**、`exportjob` 是 `job.error_code =`
的赋值——两个模块整体对它隐形。处置是**升级枚举面**（两个模块各导出一个
`ERROR_CODES` 元组，门禁直接读它）而不是给新代码开白名单。

**5. `str.strip()` 与 `String.trim()` 认的空白字符集不一样。** U+FEFF 只有 JS
认，`\x1c`–`\x1f` 只有 Python 认。文件名的首尾空白判定要是各用各的内建函数，
两侧对 `"﻿Fig"` 会给出不同答案。写死一份共用集合，向量里专门留了几条用例。

**6. `dot_only` 排在 `trailing_dot` 后面就永远够不着。** `.` 与 `..` 都以点
结尾，第一版里那条规则是死的。变异反证顺手抓到（把它删掉不红）。

### 评审回合 3（PR #214）：六条全改

Codex 报了 3 P1 + 3 P2，**全部成立**。逐条处置见 `TEST_MATRIX.md`；这里只记
三件会影响后面阶段的：

**1. `pdfbackend` 里不许有密度常量。** 第一版写死 96 dpi 把位图装进 PDF，
而 `engine/originalspec.ASSUMED_DPI` 是 PNG 600 / 其余 300 —— 更糟的是那行
上面挂着一句注释声称两者"是同一个假设"。**注释是断言**（T-66）。现在
`original_pdf(src, out, page_pt)` 收页面尺寸，密度只从唯一权威来。

**2. 「能不能做」的判据要去问真正会执行的那条路的前提**（T-65）。用
`spec.stale` 判原图能不能导是错的——它答的是"这份规格是不是上一次已知的"。
真正的前提在 `_resolve_panel_source()` 的第一步 `safe_resolve()`。

**3. 报告是产物，不是附属品。** 覆盖策略、去重、冲突检测对它一视同仁。

### 尚存限制

1. **`codex-plugin` 那条导出入口没并进来**（`bridge.py` 自己的 `_write_proof`
   仍写 `_proof.json`）。它是另一个进程、另一份载荷、另一条分发路径，
   并进来要连 widget 一起改，本轮刻意没动。
2. **PPI 的重采样只在原图位图源上有开关**（`native_grid`），界面没有暴露
   「按另一个像素网格导出位图」这个选项——默认永远保源网格。
3. **`/api/package` 仍是同步的**，没有进作业模型（它不出图，没有部分失败）。
4. **进度只有阶段与步数**，没有百分比：合成那一步的耗时占大头而它不可分。
5. **透明背景对 PDF 是"不画白底"**，不是 PDF 的透明组；位图源装进 PDF 时
   `vector: false`，界面没有单独说这一句。
6. **e2e 只跑了四条 spec 的 chromium project**（a11y / asset-library /
   keyboard-golden-path / i18n，27 passed；评审回合之后复跑仍 27 passed）。
   webkit / chromium-en 与其余 spec 本轮没跑。
7. **源文件不在素材清单里时不能按原图导出**，哪怕它有脚本能重新画
   （`safe_resolve()` 排在查注册表之前）。界面已经如实说出来，但**能力本身
   是缺的**——改它要动画布导出共用的那条路。
8. **「按另一个像素网格导出位图」这个能力不存在**（评审回合 3 删掉了那条
   没有调用点、又用着错误常量的分支）。
9. 04–11 的其余遗留原样开着。

### 工作树状态

- worktree：`/Volumes/Projects/Tavotto/.claude/worktrees/product-ux-v2`
- 分支：`feat/product-ux-reliability-11-12`（从 `origin/main` 的 `dd7c5b5`
  开出）→ **PR #214**（11 与 12 一起，四个提交 + 一个评审回合）
- author 用 `88193520+erwanjun@users.noreply.github.com`（与 `main` 上每一个
  提交一致）。本机 `~/.gitconfig` 是别的邮箱，提交时用
  `git -c user.email=… commit`，**别改共享的 `.git/config`**

---

## 下一阶段入口（Prompt 13：统一属性系统、文字控件、标注字体）

**从这里开始读**：`UX_CONTRACTS.md` 的「6. 输出一致性合同」（本轮整段重写）
与「4. Style / Spec / Validation / Export 分层」、`ARCHITECTURE.md` 的 §5.3
（本轮整段重写）与 §6/§6b、`docs/adr/0031-unified-export-pipeline.md`。

**Session 12 留给它的可复用入口**：

| 东西 | 位置 | 性质 |
| --- | --- | --- |
| 这次导出要什么 | `lib/exportRequest.buildExportRequest(input)` | **唯一构造**。13–16 改属性之后不需要动它——属性改的是 `doc.objects`，载荷从那里现取 |
| 文件名合不合法 | `lib/exportName.checkFilename()` | 八条闭集原因；与 Python 侧同源，改一边必须改另一边 + 重生成向量 |
| 起 / 取消 / 跟进度 | `store/exportStore.ts` | 作业活在 store，不活在对话框 |
| 这张图有多大 | `lib/originalSpec.getOriginalOutputSpec(figureId)` | Session 09 留的（12 的 `scope=original` 就是它的消费端） |
| 按哪套规范 | `lib/specBinding.resolveDocumentSpec(binding, catalog)` | Session 10 留的 |
| 检查结果摘要 | `store/validationStore.getValidationSummary(scope, extra?)` | Session 11 留的，**不要再跑第二遍求值器** |

**属性/文本改动怎么自动进同一条导出管线**（Prompt 13–16 的关注点）：

```text
用户改一个属性 → documentStore.commit → doc.objects 变
    ↓（导出时现取，没有第二份缓存）
buildExportRequest({ doc })
    ↓ scope=canvas  → canvas.objects[]  → pdfbackend.compose().place()
    ↓ scope=original→ original.overrides[] → worker.export() 全质量重渲染
```

**属性系统只要改 `doc.objects` 与 `panel.overrides`，导出这一端一行都不用动。**
`toExportObjects()` 是画布对象 → 载荷的唯一投影（顺序即 z 序、隐藏对象不发），
新属性加在那里一处；原图那一端连投影都没有——它直接把 `overrides` 交给引擎。
文字与字体（14）同理：图内文字走 override → worker，画布文字走
`pdfbackend._draw_text`（几何与 `TextView` 严格同源）。

**绝不要做的事**（07 的六条、08 的三条、09 的四条、10 的五条、11 的五条原样
成立，12 再加五条）：

24. **不许在组件里拼导出载荷。** 构造只有 `buildExportRequest()` 一处；
    要新字段就加进 `ExportRequest`（两侧同时），不要在第二个 API 上抄一遍。
25. **不许往 `OriginalSource` 上加布局字段**（T-59）。x/y/w/h、页面尺寸、
    crop 一个都不进那个类型——那是「原图导出不套用画布缩放」唯一的结构性保证。
26. **不许把「不适用」压成一个默认值**（T-60）。`ppi: null` 与 `ppi: 600`
    是两个答案；同族的还有 `dpi_source: unknown`、`ready: false`。
27. **不许把部分失败报成全部成功，也不许因为一项失败就丢掉另一项**（T-62）。
28. **不许让后台线程走「落到默认项目」那条兜底**（T-63）。起线程之前
    `bound_project(ctx)`；它不会报错，只会成功地导出另一个图库的图。

**必须保留的不变式**（改动前先确认还成立）：

1. `loadSeq` / `derivedSeq` 把「载入」「用户编辑」「派生同步」分成三档。
2. `dirty` 同时盯 `doc` 与 `canvases`；收到 409 后基线**故意不推进**。
3. 落盘一律走 `engine/atomicio`（ADR 0023）——**导出产物也是**
   （`publish_file()`）；保存状态只经 `setSaveState()` / `setDocNotice()` 改。
4. **刷新的编排只有 `refresh_project_index()` 一份**（ADR 0025）；**发现只有
   `project_watch` 一份**（ADR 0026）；**前端的消费只有 `liveSync` 一份**；
   **能力事实只有 `readiness` 一份**（ADR 0027）；**原图规格的决策只有
   `lib/originalSpec.ts` 一份**（ADR 0028）；**「按哪套规范检查」只有
   `lib/specBinding.ts` 一份**（ADR 0029）；**「这份项目有什么问题」只有
   `lib/validation.ts` + `store/validationStore.ts` 一条链**（ADR 0030）；
   **「这次导出要什么」只有 `ExportRequest` 一个结构、「怎么落盘」只有
   `engine/exportjob.py` 一份**（ADR 0031）。
5. **无差异 = 零事件、零写盘、零 worker 失效、零缓存失效**（后端）；
   **无差异 = 零 `set()`、零 dirty、零提示**（前端）。**检查本身零写入**。
6. 「哪些文件算素材」只有 `iter_assets()` 一处；「谁认领了这个 stem」只有
   `discover.claims_of()` 一处；「状态说成什么话」只有 `lib/readinessText.ts`
   一处；「文档里有没有这张图」只有 `findFigurePanel()` 一处；「profile 叫
   什么」只有 `lib/profileText.ts` 一处；**「文件名合不合法」只有
   `check_filename()` / `checkFilename()` 这一对**。
7. **就绪度与检查都不执行用户脚本、不 probe、不写盘**；**导出会执行**
   （有 override 的图要重渲染），但**只在用户明确点导出之后**。
8. **派生数据刷新不得把文档标脏，也不得进普通撤销历史。** 导出设置
   （格式 / PPI / 报告开关 / 最近的规范）是**本机 UI 偏好**，不进文档、
   不进 undo；选规范是文档修改。
9. **素材不在清单里 ≠ 脚本关系失效**；**≠ 这张图没有规格**；**查不了 ≠ 没问题**；
   **原图不可用 ≠ 悄悄改成画布导出**。
10. `reason` 是闭集：定位失败的原因、**文件名不合法的原因**（八条）、
    **覆盖策略**（三条）、**作业状态**（七档）都不接受自由文本。
11. **「没测量 / 不适用」不许压扁**：`conflicts` 的 `null`、`registry_valid`
    的 `null`、`capability` 的 `undefined`、`dpi` 的 `null` 与 `dpi_source`
    的四档、`follow` 的"没选过"、检查的 `ready: false`、**导出的 `ppi: null`**。

---

## 历史：Session 11（2026-08-31）

### 目标

把散在导出弹窗、设置与局部组件里的样式检查收敛成**一个统一 Validation 服务**，
并建立默认可见的左侧「问题」面板。核心不是美化警告列表，而是**保证每一条问题
都能定位到真实文档、工作流、对象和属性字段**。

本阶段**不做导出面板与输出门禁**（Prompt 12）、**不做属性系统改造**
（Prompt 13）、**不把就绪度并进问题清单**（那是另一类事实，见 T-56）。

### 开始前实测到的四件事（不是假设）

1. **「这张图有没有问题」只有一条路能问：打开导出对话框。** `ExportDialog`
   自己调 `runPreflight()`，展开之后每行末尾挂着 `axes_0.lines_1`，点一下调
   `revealObjects(ids)`。
2. **`PreflightIssue` 没有画布维度**（R-12）。第二张画布上的问题**根本不会被
   列出来**（对话框只查激活画布），列出来了也跳不过去。
3. **聚合项没法定位到"是谁"。** 一条 `font-too-small` 底下挂三个 gid，文案说
   的是最糟那个的数字；点「定位」把三个对象一起选中，属性页显示多选摘要。
4. **导出对话框里有第二套判据。** 「导出 DPI」那一格写着 `bad={dpi < minDpi}`
   ——一个直接写在组件里的比较；而 MCP 那条入口
   （`bridge.export_raster_issues()`）判的又是第三份。

### 实际完成

**1. `web/src/lib/validation.ts` —— 求值与导航分开。**

`lib/preflight.ts` 保持它的角色（规则求值器，跨语言 golden vectors 对齐），
新的一层回答「谁没过、点一下去哪」：

```ts
ValidationIssue {
  issueId        // = fingerprint = ruleCode｜canvasId｜objectId｜gid｜propertyPath
  ruleCode severity context   // context: 'document' | 'export'
  objectRef      // { documentId, canvasId, objectId, gid }   ← canvasId 是新补的那一维
  subject        // 界面拿它说人话（elementLabel / elementRole / objectName）
  propertyPath   // 'fontsize' / 'sizePt' / 'page.w' / 'export.dpi'
  message technicalDetails fixKind
}
```

**2. 聚合项摊成逐条命中**（T-52）。`Sink` 额外记一份 `PreflightOccurrence`
（objectId / gid / prop / **它自己那次**的 message 与 detail），去重的尺子与
聚合项完全一样。**不进跨语言合同**——golden vectors 比的仍是聚合投影，
Python 侧一个字节没改；看护用例盯着两者一致。

**3. `store/validationStore.ts` —— 编排。** 防抖 250ms + 代次（还在飞的那一轮
回来时丢掉）、按画布增量（沿用 = **同一个对象引用**）、**失败不清空**、
**不改文档**。`startValidation()` 在 `App.tsx` 装配一次，是唯一驱动点。

**4. `lib/issueFocus.ts` —— 跨模块唯一的一个 focus 动作。**
切画布 → 切工作流模式 → 选中 → 视口 → 短暂高亮 → Inspector → 属性字段；
失败回**闭集原因**（`canvas_missing` / `object_deleted` / `not_editable` /
`document_not_loaded`），绝不静默不动。属性字段的落点是 `data-prop`。

**5. `lib/issueFix.ts`（纯计算）+ `store/issueFixActions.ts`（落地）。**
`safe_auto` 三条门槛见 T-55；落地经 `documentStore.commit`，一个修复一个事务、
一批一个批事务、⌘Z 一次撤回；**批量只在当前画布**。

**6. 左侧「问题」面板 + 常驻轨道入口 + 角标。** 等级 chip 筛选、行内「定位 /
修复」、技术详情默认收起、空态与「这一次没查成」是两句不同的话、方向键漫游。
**普通界面一个 gid 都不出现**（措辞唯一实现 `lib/validationText.ts`）。

**7. 导出对话框只消费摘要。** 不再跑第二遍求值器；打开时**当场同步跑一遍**
（那 250ms 防抖窗口里不能说「检查通过」）；proof 留档用 `rawIssuesFor()` 的
聚合投影（格式一个字节没动）；「导出 DPI」那一格的判断交给统一服务。

### 关键 API（Prompt 12 直接用）

```ts
// web/src/lib/validation.ts
ValidationIssue / ObjectRef / IssueSubject / FixKind / IssueContext
validateCanvas(input, documentId, assets, render): CanvasResult   // { issues, raw }
validateProject(input): CanvasResult[]
exportContextRaw(ctx, profile): PreflightIssue[]      // 与 MCP 严格同源
exportContextIssues(ctx, profile, ref): ValidationIssue[]
summaryFor(issues, { canvasId?, extra?, ready, failed }): ValidationSummary
fingerprintOf(ruleCode, ref, propertyPath) / ruleEntry(code) / knownRuleCodes()
filterIssues(issues, filter) / mergeExportIssues(a, b)

// web/src/store/validationStore.ts
startValidation(): () => void        // App 装配一次；唯一驱动点
runValidation(only?: Set<canvasId>)  // 同步跑一遍（导出对话框打开时用）
schedule(canvasId?) / cancelScheduled() / resetValidation()
getValidationSummary(scope, extra?) / listIssues(filter?) / rawIssuesFor(canvasId)
useValidationStore   // results / issues / ready / failed / running / lastDurationMs

// web/src/lib/issueFocus.ts
focusObject(ref, propertyPath?): FocusOutcome
focusIssue(issue): FocusOutcome
openProblems(filter?: { severities?: Severity[] })
focusFailureMessage(reason)

// web/src/lib/issueFix.ts（纯）/ web/src/store/issueFixActions.ts（落地）
planFix(issue, profile, doc, choice?) / fixOptions(issue, profile)
applyIssueFix(issue, profile, choice?) / applyIssueFixes(issues, profile)

// web/src/lib/validationText.ts
SEVERITY_ICON / severityLabel / issueTitle / issueValues / subjectName
technicalDetailLines / issueAriaLabel / issueDetailText
```

### 迁移

**没有磁盘格式改动。** 文档 schema、proof report v2、profile 清单一个字节没动。
`PreflightIssue` 多了一个 `occurrences` 字段（TS 侧，运行时内存），golden
vectors 的投影不含它。

### 修改的文件

```text
新增  web/src/lib/validation.ts               Issue 模型 / 规则目录 / 指纹 / 摘要
新增  web/src/lib/validation.test.ts          （21 条）
新增  web/src/lib/validationText.ts           「问题怎么说」唯一实现
新增  web/src/lib/validationText.test.ts      （13 条）
新增  web/src/lib/issueFocus.ts               跨模块唯一的 focus 动作
新增  web/src/lib/issueFocus.test.ts          （14 条）
新增  web/src/lib/issueFix.ts                 修复计划（纯计算）
新增  web/src/lib/issueFix.test.ts            （12 条）
新增  web/src/store/validationStore.ts        编排（防抖 / 代次 / 增量 / 失败不清空）
新增  web/src/store/validationStore.test.ts   （12 条）
新增  web/src/store/issueFixActions.ts        修复的落地（走 commit）
新增  web/src/components/left/ProblemPanel.tsx        左侧「问题」抽屉
新增  web/src/components/left/problemPanel.test.tsx   （15 条）
新增  docs/adr/0030-validation-and-problem-navigation.md
改动  web/src/lib/preflight.ts                Sink 记逐条命中 + 19 处 add() 带上 prop
改动  web/src/store/uiStore.ts                +'problems' tab / issueHighlight / problemFilter
改动  web/src/store/profileStore.ts           catalog() 抽出纯函数 toCatalog()
改动  web/src/components/left/LeftRail.tsx    +「问题」常驻入口 + 角标
改动  web/src/components/left/LeftPanel.tsx   +problems 分派 + 标题计数
改动  web/src/components/ExportDialog.tsx     消费摘要；删掉组件里的 dpi 判据、
                                              删掉行内 gid、定位改走 focusIssue
改动  web/src/canvas/OverlaySvg.tsx           定位后的短暂高亮（加粗虚线外框）
改动  web/src/components/inspector/ElementInspector.tsx  FieldBlock +data-prop/data-gid
改动  web/src/components/inspector/TextSection.tsx       字号行 +data-prop
改动  web/src/App.tsx                         +startValidation()
改动  web/src/i18n/locales/*                  +errors:problems.*（含 32 条规则短标题）
                                              +workspace:rail.problems / history.fixIssue*
                                              +dialogs:export.openProblems / preflightFailed*
改动  web/src/i18n/overflow.test.tsx          +9 条英文字数预算
改动  tests/test_preflight.py                 +1 条跨语言同源
改动  tests/test_profile_store.py             字号字面量看护 +4 个消费点
改动  tests/test_i18n_dead_keys.py            匹配器认识复数后缀 + 自检 +2
改动  AGENTS.md / web/AGENTS.md               同源对 + 统一检查那一节
改动  docs/implementation/product-ux-reliability/*   本轨道交接
重建  codex-plugin/mcp/widget/canvas.html     指纹 1ac44a5f373b11b8
重建  web/dist-playground/                    指纹 712cf96d09cfac61（不进 git）
```

### 测试命令与真实结果

```sh
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest
cd web && pnpm test && pnpm build && pnpm i18n:check && pnpm lint
NODE_OPTIONS=--no-experimental-webstorage npx vitest run src/lib/validation.test.ts
python scripts/build_mcp_widget.py && python scripts/build_browser_playground.py
ruff check . && ruff format --check .
```

后端全量 **exit 0 —— 3370 passed / 34 skipped / 0 failed**（本轮只加了 1 条
后端用例；与 Session 10 的 3271 之间的差额来自后来合进 `main` 的 PR）；
前端 **147 files / 1805 passed**，`build` / `i18n:check` / `lint` 三条 exit 0。
完整表格见 `STATUS.md`。**变异反证 44 条全部被打红**（第一轮 38/44，
六条存活的成因与处置见 `TEST_MATRIX.md`）。

**e2e 本轮真跑了两批**（不是 `--list`）：`a11y.spec.ts` 8 passed（新增「问题
面板」一条）、`asset-library` + `keyboard-golden-path` 7 passed（这三条 spec
断言导出对话框里的预检块，本轮重写过它）。跑法见 `STATUS.md` 末节。

### 这一轮踩到的坑

**1. 反证脚本自己的判据是空的。** 第一版拿 `vitest ... | tail -3` 的文本找
`failed`，而 vitest 的统计行**不在最后三行里**——44 条全部显示「存活」。
判据没有进控制流（用文本而不是退出码）时，它会把一整套好用例报成坏用例。
改成看退出码 + **先跑一遍基线自检**（没有变异时必须绿）。

**2. `useProfileStore((s) => s.catalog())` 会把界面转到报错。** `catalog()`
每次调用都新建一个数组，拿它当 zustand 选择器的返回值 = 每一帧都"变了" =
`Maximum update depth exceeded`。处置是把 `catalog()` 的实现抽成纯函数
`toCatalog(specs)`，组件订阅 `specs` 再 `useMemo`。

**3. 埋点读的是渲染闭包里的旧值。** 「打开导出对话框记一次预检计数」的 effect
里用了上一次渲染算出来的 `summary`，而 `runValidation()` 是同一轮 effect 里
刚跑的——埋点稳定报 0。处置是在 effect 里**现取**。

**4. 又一次「两道守卫说同一件事」。** 「画布还在不在」查了两遍，把前一句改成
恒真没有任何用例会红。合并成一处（T-57）——本轨道第三次撞见这个形状。

**5. `git stash push -- src` 在 `web/` 目录下会把整轮改动收走。** 为了数一个
lint 基线跑的，当场 `git stash pop` 全部取回。**别为了取个基线动工作树**。

**6. a11y 那条真跑起来当场红了一次，而且红得对。** 问题面板里「技术详情」的
`<summary>` 用了 `text-ink-faint`（2.54:1，axe serious）——`ink-faint` 按 UI
纪律只给装饰与禁用态，而 summary 是个真控件、上面是要读的字。单测里的
「有 aria-label / 可键盘到达」一条都没红：**结构性断言看不见对比度**。

**7. 性能预算的第一版量到的是一张画布。** `addCanvas` 建的是**空**画布，
只在激活画布上摆对象的话「12 画布 × 8 面板 × 60 元素」这个负载是假的
（2.66ms 显得很好看）。每张都装满之后是 22ms，预算定 300ms。同一轮还差点
再踩一次：`vi.useFakeTimers()` 默认接管 `performance.now`，那样 `spent` 恒为 0，
预算判据什么都量不到——用例里先 `expect(spent).toBeGreaterThan(0)` 证明尺子是活的。

### 尚存限制

1. **不渲染的面板会成批报「无法核验」**（见 `STATUS.md` 的遗留表）。
2. **批量修复不跨画布**（撤销栈按画布换入换出）。
3. **问题面板没有虚拟滚动**，真实上限没量过。
4. **`user_choice` 目前只有页宽一条规则**。
5. **MCP 内嵌画布保留自己的等级图标表**（另一个 bundle、消费的是另一种载荷）。
6. **e2e 只跑了三条 spec**（a11y + asset-library + keyboard-golden-path，
   chromium project）。webkit / chromium-en 两个 project 与其余 spec 本轮没跑。
7. 04–10 的其余遗留原样开着。

### 工作树状态

- worktree：`/Volumes/Projects/Tavotto/.claude/worktrees/product-ux-v2`
- 分支：`verify-main`（从 `origin/main` 的 `dd7c5b5` 开出，**尚未推送**）
- author 用 `88193520+erwanjun@users.noreply.github.com`（与 `main` 上每一个
  提交一致）。本机 `~/.gitconfig` 是别的邮箱，提交时用
  `git -c user.email=… commit`，**别改共享的 `.git/config`**

---

### Session 11 当时留给 12 的入口（已被 12 消费，保留备查）

**从这里开始读**：`UX_CONTRACTS.md` 的「5. 问题定位合同」（本轮整段重写）与
「6 / 6b 输出一致性 / 原图规格」、`ARCHITECTURE.md` 的 §5.3 与 §6b、
`docs/adr/0030-validation-and-problem-navigation.md`。

**Session 11 留给它的可复用入口**：

| 东西 | 位置 | 性质 |
| --- | --- | --- |
| 检查结果摘要 | `store/validationStore.getValidationSummary(scope, extra?)` | **唯一来源**，带 `ready` / `failed`。**不要再跑第二遍求值器** |
| proof 留档要的聚合投影 | `store/validationStore.rawIssuesFor(canvasId)` | 同一次求值的另一份投影；proof report v2 格式一个字节没动 |
| 导出上下文规则 | `lib/validation.exportContextRaw / exportContextIssues` | 与 MCP 的 `bridge.export_raster_issues()` **严格同源**。新的导出上下文规则加在这里，**不要加进组件** |
| 把用户交回问题面板 | `lib/issueFocus.openProblems(filter?)` | 导出弹窗里不再列第二套清单 |
| 跳到那个对象 | `lib/issueFocus.focusIssue(issue)` | 切画布 / 切模式 / 选中 / 聚焦字段一处实现 |
| 问题怎么说 | `lib/validationText.ts` | 短标题 / 当前值→要求 / 人话主语 / 等级图标表 |
| 这张图有多大 | `lib/originalSpec.getOriginalOutputSpec(figureId)` | Session 09 留的 |
| 按哪套规范 | `lib/specBinding.resolveDocumentSpec(binding, catalog)` | Session 10 留的 |

**绝不要做的事**（07 的六条、08 的三条、09 的四条、10 的五条原样成立，11 再加五条）：

19. **不许在导出面板（或任何组件）里现算「这个值合不合规范」。** 阈值一个字
    都不进组件；要判就加一条规则进 `exportContextRaw()`，它会自动进摘要、
    进问题面板、进 proof。
20. **不许把 `total === 0` 当成「检查通过」**（T-54）。`ready` / `failed` 与
    计数一起来，压扁它等于宣布一句假的好消息。
21. **不许在普通界面显示 gid / 对象 id / 文件路径。** 精确名词只在「技术详情」
    里，措辞只有 `lib/validationText.ts` 一处。
22. **不许另造第二个 focus 动作。** 定位只有 `lib/issueFocus.focusObject()`；
    要新的落点就往那八步里加，别在组件里拼一遍。
23. **不许把 `safe_auto` 理解成"写个合理的值"**（T-55）。修完必须真的能过，
    而且要按面板缩放反算回脚本坐标系。

**必须保留的不变式**（改动前先确认还成立）：

1. `loadSeq` / `derivedSeq` 把「载入」「用户编辑」「派生同步」分成三档。
2. `dirty` 同时盯 `doc` 与 `canvases`；收到 409 后基线**故意不推进**。
3. 落盘一律走 `engine/atomicio`（ADR 0023）；保存状态只经 `setSaveState()` /
   `setDocNotice()` 改（ADR 0024）。
4. **刷新的编排只有 `refresh_project_index()` 一份**（ADR 0025）；**发现只有
   `project_watch` 一份**（ADR 0026）；**前端的消费只有 `liveSync` 一份**；
   **能力事实只有 `readiness` 一份**（ADR 0027）；**原图规格的决策只有
   `lib/originalSpec.ts` 一份**（ADR 0028）；**「按哪套规范检查」只有
   `lib/specBinding.ts` 一份**（ADR 0029）；**「这份项目有什么问题」只有
   `lib/validation.ts` + `store/validationStore.ts` 一条链，定位只有
   `lib/issueFocus.ts` 一处，措辞只有 `lib/validationText.ts` 一处**（ADR 0030）。
5. **无差异 = 零事件、零写盘、零 worker 失效、零缓存失效**（后端）；
   **无差异 = 零 `set()`、零 dirty、零提示**（前端）。**检查本身零写入**。
6. 「哪些文件算素材」只有 `iter_assets()` 一处；脚本遍历只有
   `discover.iter_all_scripts()` / `iter_scripts()` 两个视图；「谁认领了这个
   stem」只有 `discover.claims_of()` 一处；「状态说成什么话」只有
   `lib/readinessText.ts` 一处；「文档里有没有这张图」只有 `findFigurePanel()`
   一处；「profile 叫什么」只有 `lib/profileText.ts` 一处。
7. **就绪度不执行用户脚本、不 probe、不写盘、不改注册表、不发 SSE**；
   **检查同样不执行、不写盘、不发后端**。
8. **派生数据刷新不得把文档标脏（对用户而言），也不得进普通撤销历史。**
   侧栏折叠、横幅关闭、聚焦目标、工作区模式、**问题面板的筛选与定位高亮**
   同样不进文档、不进 undo。**但选规范 / 同步快照 / 切换跟随 / 应用样式 /
   点「修复」都是文档修改**——它们进 undo、进 dirty，这是有意的区别。
9. **素材不在清单里 ≠ 脚本关系失效**（T-28）；**也 ≠ 这张图没有规格**；
   **全局规范被删了 ≠ 这个项目没有规范**；**查不了 ≠ 没问题**（T-54）。
10. `reason` 是闭集，表外的值归成 `manual`；客户端字符串不透传。
    **定位失败的原因同样是闭集**，不接受自由文本。
11. **「没测量」不许压扁**：`conflicts` 的 `null`、`registry_valid` 的 `null`、
    `capability` 的 `undefined`、`dpi` 的 `null` 与 `dpi_source` 的四档、
    `follow` 的"没选过"、**检查的 `ready: false`**。

---

## 历史：Session 10（2026-08-30）

### 目标

把混在设置、规范文件、导出组件和硬编码里的配置拆成三个稳定层，并让**规范
进项目时带着它自己那份规则**：

```text
Style   图实际长什么样      Spec   图必须满足什么      Export  文件如何生成
```

本阶段**不做统一检查引擎与问题面板**（Prompt 11）、**不做导出面板**
（Prompt 12），也**不把设置整合进稳定 Shell**（Prompt 19）。

### 开始前实测到的三件事（不是假设）

1. **Spec 只有内置两条，用户加不了。** `profiles/publication.json` 是两侧
   求值器共读的唯一权威，但没有任何"用户自建一份"的路；文档里只存
   `{id, journal}`，注释写着「规范升级后旧文档自动跟新规则走」。
2. **Style 已经在数据目录，但和用户的画布混在一个文件夹里**
   （`<data_dir>/layouts/_styles.json`）——正因为如此才需要一张
   `RESERVED_DOCUMENT_FILENAMES` 表把它从「打开画布」里挡掉。没有 schema
   版本、没有 revision、不能导入导出。
3. **最小字号有三个数**：`min_effective_font_size_pt: 8.5`、
   `absolute_min_font_size_pt: 8.0`、`legend_policy.min_font_size_pt: 8.5`，
   外加两个求值器里各自写死的兜底 `_num_or(..., 8.5)` / `8.0`（TS 侧则**根本
   没有兜底**：缺键时比较对象是 `NaN`，`x < NaN` 恒假 = **静默放行**）。

### 实际完成

**1. `src/tavotto/engine/profilestore.py` —— 全局清单的唯一服务。**

```text
<data_dir>/profiles/styles.json   用户自建样式（schema 1，每条带 revision）
<data_dir>/profiles/specs.json    用户自建规范
<data_dir>/profiles/backup/       坏文件与迁移前的原件（**不删**）
```

内置**不落盘**：规范来自 canonical JSON，样式**从默认规范派生**（规范说正文
9 pt / 拉丁 Times New Roman / 线宽第一档，样式照它生成——改规范时样式跟着变，
两者从此不可能互相矛盾）。原子写、乐观并发（`expected_revision` 对不上回 409
带磁盘现值）、损坏回退内置且坏文件挪进 `backup/`、比本构建**新**的清单原样
不动、导入一律建新的一条（id 重新分配）。

**2. 项目里存的是绑定 + 规则全文快照**（T-46，ADR 0029）。
`CanvasData.profile` 新增三个**可选**字段 `snapshot` / `snapshotVersion` /
`follow`，磁盘 schema 一个字节没升版。默认**「项目结果稳定」优先于「规范升级
自动生效」**：全局那份后来变了，旧项目的结论一个字不变，界面提示「有新版可
同步」，由用户点一下（那一步进文档历史）。

**「有没有新版」的判据是内容不等，不是版本号**（T-47）。版本号是人写的，
两个方向都会错：版本号没动而规则改了 → 该提示的没提示；版本号跳了而规则没改
→ 点进去什么都没变。看护用例两条对称。

**3. 最小字号统一为 8 pt**（T-48）。删掉的是那条 8.5 pt 的严格下限——规范
文件自己的 `source` 里写着它是「本项目补充（原文示例里有 8 pt 图例/刻度，
这里从严）」，**比它想守护的规范更严**。**8 pt 那条边的语义一个字没动**
（`eff <= floor`，正好 8.0 仍然不算过，ADR 0006 的「必须大于 8pt」原样有效）。
两条检查仍然是两条：`free-form-v1`（6.0/5.0）与期刊覆盖照样各自出场。
两个求值器里的兜底收成一个 `profiles.FALLBACK_MIN_FONT_SIZE_PT`（TS 侧同名，
**登记进根 `AGENTS.md` 的严格同源对表**）。

**4. 界面：设置里新增「样式与规范」分区**（`components/settings/
ProfilesSettings.tsx`）。列表 + 新建 / 复制 / 重命名 / 删除 / 恢复默认值 /
导入 / 导出；Style 与 Spec **不在同一张表单里混改**（切换后字段整组换掉）；
两个明确出口：「应用样式到当前图」（交给样式对话框，那里才看得见影响范围与
冲突）与「本项目用这套规范」+「跟随更新」开关。

**默认视图不出现 `lab-publication-v1 · v1.0.0`**：内置的名字跟界面语言走
（「默认规范」/「默认样式」），用户起的名字不翻译；id 与版本只在那一行的
`title` 里。措辞的唯一实现是 `lib/profileText.ts`（与 `readinessText.ts`
同一条纪律）。

**5. 旧位置一次性迁移并腾空。** `layouts/_styles.json` 首次访问时迁进 store，
原件**逐字节**备份进 `backup/`，然后删掉旧文件——与 `config._migrate_ai_agents()`
同一条纪律：两份权威并存的话，下次读哪份全看读取顺序。备份写不出来就整个不迁。
没能映射的字段进 `data.extra` 并记结构化 warning（`unmapped_field:<键名>`）。

### 关键 API（后面几个 Prompt 直接用）

```python
# src/tavotto/engine/profilestore.py
KIND_STYLE / KIND_SPEC
list_profiles(kind) / get_profile(kind, id) / require_profile(kind, id)
create_profile(kind, data, display_name, *, derived_from="")
duplicate_profile(kind, id, name=None)
update_profile(kind, id, patch, expected_revision)   # 抛 RevisionConflict
delete_profile(kind, id) / reset_profile(kind, id)
export_profile(kind, id) / import_profile(payload, *, kind=None)
migrate_legacy_styles() -> {"migrated","skipped","warnings","backup"}
resolve_spec(profile_id=None, journal=None)   # ← 「任意 id → 规范」唯一入口

# src/tavotto/engine/profiles.py（新增的公开面）
FALLBACK_MIN_FONT_SIZE_PT      # 缺键兜底；严格同源对，求值器不许再写字面量
validate_spec(profile, pid=None)
merge_journal(base, journal)
```

```ts
// web/src/lib/specBinding.ts   ← Prompt 11 的检查、12 的导出都从这里取现值
resolveDocumentSpec(binding, catalog): ResolvedSpec
  // { profile, source: 'snapshot'|'global'|'builtin',
  //   updateAvailable, globalMissing, globalVersion, snapshotVersion }
bindingFor(entry, { journal?, follow? }): SpecBinding   // 快照**只在这里生成**
sameRules(a, b)          // 「有没有新版」的判据
builtinCatalog()         // 没有后端时也拿得到（演练场 / MCP 内嵌画布）

// web/src/store/profileStore.ts   清单的唯一持有者（组件里不许有 fetch）
useProfileStore  // styles / specs / loaded / error / conflict
load() / list(kind) / get(kind,id) / catalog()
create / duplicate / rename / save / remove / restoreDefaults / exportOne / importOne

// web/src/lib/profileText.ts     profile 在界面上叫什么，只有这一处
profileName(record) / profileTechnicalDetail(record) / profileWarningText(w)
```

HTTP：`GET|POST /api/profiles/<kind>`、`POST …/<id>/duplicate`、
`PATCH|DELETE …/<id>`、`POST …/<id>/reset`、`GET …/<id>/export`、
`POST …/import`。**`/api/styles` 三个端点已删除**（前端是唯一消费方）。

### 迁移

* **磁盘文档格式没升版。** `DocumentProfile` 的三个新字段全部可选：老文档
  没有它们 = 从没绑过快照 = 按 id 取全局现值（这正是 Prompt 10 要的
  「未显式保存的旧默认迁到 8 pt」）。
* **`layouts/_styles.json` → `<data_dir>/profiles/styles.json`**：首次访问
  `/api/profiles/*` 时迁移，幂等，原件备份，旧位置腾空。
  `RESERVED_DOCUMENT_FILENAMES` 里的 `_styles.json` **保留**——老装机上那份
  文件可能还在，而「画布列表 = 对目录 glob("*.json")」。
* **显式存下的 8.5 仍然生效**：期刊覆盖 `journal.min_effective_font_size_pt`
  照常合并（有看护用例）。

### 修改的文件

```text
新增  src/tavotto/engine/profilestore.py        全局清单唯一服务
新增  tests/test_profile_store.py               （33 个函数 / 37 条）
新增  web/src/lib/specBinding.ts                「按哪套规范检查」的唯一判据
新增  web/src/lib/specBinding.test.ts           （18 条）
新增  web/src/lib/profileText.ts                profile 的措辞唯一实现
新增  web/src/store/profileStore.ts             清单的前端持有者
新增  web/src/store/profileStore.test.ts        （6 条）
新增  web/src/store/styleAndSpec.test.ts        （6 条）
新增  web/src/components/settings/ProfilesSettings.tsx
新增  web/src/components/settings/profilesSettings.test.tsx  （11 条）
新增  docs/adr/0029-style-spec-profiles.md
改动  src/tavotto/profiles/publication.json     8.5→8.0 两处；version 1.0.0→1.1.0
改动  src/tavotto/engine/profiles.py            +FALLBACK_MIN_FONT_SIZE_PT /
                                                validate_spec / merge_journal；
                                                absolute_min 进 _REQUIRED
改动  src/tavotto/engine/preflight.py           四处字面量 → 那一个常量
改动  src/tavotto/app.py                        /api/styles → /api/profiles/*
改动  src/tavotto/engine/documents.py           _styles.json 的注释改成"旧位置"
改动  codex-plugin/mcp/tavotto_mcp/bridge.py    走 profilestore.resolve_spec
改动  codex-plugin/mcp/server.py                _BRIDGE_IMPORT +profilestore
改动  web/src/components/ExportDialog.tsx       规范一次解析 + 同步提示，
                                                去掉 id·版本那一格
改动  web/src/components/StyleDialog.tsx        清单改走 profileStore
改动  web/src/components/SettingsDialog.tsx     +profiles 分区
改动  web/src/lib/api.ts                        /api/styles → profiles CRUD
改动  web/src/lib/profile.ts                    +FALLBACK / mergeJournalInto
改动  web/src/lib/preflight.ts                  两处 ×2 用兜底常量
改动  web/src/lib/stylePresets.ts               StyleProfileData / 草稿互转 /
                                                角色白名单扩到 manifest 真有的
                                                prop（weight/style/linestyle/
                                                marker/markersize）/ background
改动  web/src/store/actions.ts                  应用样式一并写画布背景
改动  web/src/types/document.ts                 DocumentProfile +3 个可选字段
改动  web/src/i18n/locales/*                    +profiles.* / export.profile* /
                                                16 条 backend 错误码；删掉
                                                死掉的 export.profileStamp
改动  tests/test_preflight.py                   两条改判据 + 一条换样例
改动  tests/test_error_codes.py                 +16 个 code、扫描范围 +profilestore
改动  tests/test_document_persistence.py        fixture 改指 TAVOTTO_DATA_DIR；
                                                两条改成量"老装机残留文件"
改动  tests/golden/preflight_vectors.json       重新生成（8.2 不再报、图例 8pt 放行）
改动  AGENTS.md / src/tavotto/AGENTS.md / web/AGENTS.md   同源对 + 三层边界
改动  docs/adr/0006-…                           8.5 那条标注为已删除（指向 0029）
改动  README.md / README.zh-CN.md / codex-plugin/README.md /
      codex-plugin/skills/tavotto-figure/references/*.md   8.5 的口径全部更新
重建  codex-plugin/mcp/widget/canvas.html       指纹 2e72e0094357a576
重建  web/dist-playground/                      指纹 22b775a453e77970（不进 git）
```

### 测试命令与真实结果

```sh
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest tests/test_profile_store.py
cd web && pnpm test && pnpm build && pnpm i18n:check && pnpm lint
# 单跑一个前端用例文件要自己补环境变量（package.json 的 test 脚本里有）
NODE_OPTIONS=--no-experimental-webstorage npx vitest run src/lib/specBinding.test.ts
# 改了 web/src 之后两个受管产物都要重建（重建之后一个字都别再动）
python scripts/build_mcp_widget.py && python scripts/build_browser_playground.py
ruff check . && ruff format --check .
```

后端全量 **exit 0 —— 3271 passed / 34 skipped / 0 failed**（收集 3305 条 =
Session 09 的 3251 + 54，逐项对得上：`test_profile_store.py` 37 +
`test_preflight.py` 新增 1 + `test_error_codes.py` 那条按 code 参数化的用例
随 16 个新错误码一起 +16）；前端 **138 files / 1659 passed**，
`build` / `i18n:check` / `lint` 三条 exit 0。完整表格见 `STATUS.md`。

> `pytest -q` 在**没有失败**的那一遍里，这台机器上仍然把结尾那行计数吞掉
> （与 Session 09 同一现象）。上面两个数是从进度流里逐字符数出来的
> （`.` 3271、`s` 34、`F`/`E` 0），不是估的。

**变异反证 36 条全部被打红**，
第一轮 0 条存活——但有两条判据在反证之前就先改掉了，因为它们**恒等成立**
（内置样式派生、错误文案按语言渲染），成因与处置见 `TEST_MATRIX.md`。

### 这一轮踩到的坑

**1. 一个 200 但没有 `profiles` 的响应会把内置规范一起抹掉。** 前端用例里
`fetch` 打的桩对任何 URL 都回 `{figures_dir, panels: []}`，于是
`fetchProfiles()` "成功"返回 `undefined`，`specs` 被写成 `undefined`，
八条既有用例一起红在 `specRecords.map`。**这不是测试的问题，是实现的问题**：
代理、离线页、别的服务占了端口都会产生这种响应，而界面上看起来只是"这台
机器上没有规范"。处置是在 `load()` 里加形状判据——**形状不对 = 没拿到，
不是拿到了空的**。

**2. 两条判据恒等成立，反证之前就得改。**
`el["text"]["fontsize"] == spec["default_font_size_pt"]` 的两侧取自同一份
文件，把"派生"换成写死的 `9.0` 也照样绿；`errorOf` 那条在 zh-CN 下透传原文
与按 code 翻给出同一句话。处置分别是**换一份改过数字的规范**（`TAVOTTO_
PROFILES_FILE`）和**切到 en-US 量**。同一个形状本轮出现两次。

**3. 变异跑完要看 `git status`。** 「清单写回包目录」那条被打红了，但它写出来
的 `src/tavotto/profiles/styles.json` 留在了工作树里——变异被杀死不等于它没有
副作用。

**4. `profile_too_many` 撞上了 i18next 的复数后缀。** `_many` 是 i18next 的
plural 形态之一，于是 `pnpm i18n:check` 把它当成 `profile_too` 的一个变体，
报"缺 `_other`、多出 `_many`"。改名 `profile_limit_reached`。**错误码的名字
不能以复数后缀结尾**。

**5. `npx vitest` 仍然会漏 `NODE_OPTIONS=--no-experimental-webstorage`**
（连续第四轮）。

### 尚存限制

1. **规范编辑只覆盖数值字段。** 设置里能改的是字号下限 / 栏宽 / DPI 这一类
   数字（`SPEC_FIELDS` 表）。severity 表、字体白名单、配色策略、坐标轴策略
   **改不了**——它们要的是各自的控件，而这一屏的定位是「最小编辑入口」
   （Prompt 19 会把设置整合进稳定 Shell，那时再谈）。改不了的字段**原样保留**，
   复制 / 导入 / 恢复默认值都不会丢。
2. **`follow: true` 不会把快照写回文档。** 它的语义是"解析时优先看全局现值"，
   所以打开项目**不写盘**。代价：关掉跟随时用的是**上一次绑定那一刻**的快照，
   不是"跟随期间见过的最后一份"。
3. **每张画布各存一份完整快照**（约 4 KB）。多画布项目会重复几份，没有做
   去重——去重要引入一层间接，而那正是"改一处影响另一处"的来源。
4. **README 里两张预检截图仍然是旧规范拍的**（alt 文本如实描述图里的
   「低于 8.5 pt」）。改 alt 文本会让它不再描述那张图；重拍要跑真实应用，
   本轮没做。记在 `STATUS.md` 的遗留表。
5. **导出面板仍然只按画布合成**（Session 09 的限制 1 原样成立），Prompt 12 接。
6. **e2e 本轮没跑**（与 08 的 axe、09 的 spec 改动同一条限制）。本轮**没有改
   任何 e2e spec**：`mcp-canvas.spec.ts` 里那条 `lab-publication-v1 v1.0.0`
   断言量的是 widget 渲染 mock 会话里的字段，与真实 profile 版本无关。
7. 04–09 的其余遗留原样开着（R-05 五处手写原子写、R-07 autosave 位置、
   `/api/layouts/<name>` 无 schema 校验、axe 那两条从没真跑过、接入中心无
   虚拟滚动）。

### 工作树状态

- worktree：`/Volumes/Projects/Tavotto/.claude/worktrees/product-ux-v2`
- 分支：`feat/product-ux-reliability-v2`，从 `origin/main` 的 `ef9ac02` 开出
- **PR #201 带的仍然是 Session 01–04；05–10 的提交还没有推**——节奏由用户定
  （一推就触发一轮 Codex 评审）
- author 用 `88193520+erwanjun@users.noreply.github.com`（与 `main` 上每一个
  提交一致）。本机 `~/.gitconfig` 是别的邮箱，提交时用
  `git -c user.email=… commit`，**别改共享的 `.git/config`**

---

## 历史：Session 09（2026-08-29）

### 目标

把「打开一张图 → 改 → 按原图规格导出」做成**默认路径**，并给「按原图导出」
一个说得出口的定义。

本阶段**不做导出面板、不做输出门禁**（Prompt 12），**不动 Style/Spec 分层**
（Prompt 10），也**不在前端重新判一次「这张图能不能编辑」**（那是 07/08 的
事实模型）。

### 开始前实测到的两件事（不是假设）

1. **图内编辑确实必须先把面板放进画布。** `uiStore.elementPanelId` 存的是
   **画布对象 id**；`usePruneSelection` 在对象离开 `doc.objects` 时清掉它。
   没有对象就没有图内编辑——这不是 UI 层的限制，是 overrides 挂在
   `CanvasObject` 上的直接后果。
2. **导出尺寸没有出处。** `/api/export` 只按 `page_w_mm/page_h_mm` + 每个对象
   的 x/y/w/h 合成，没有"原图尺寸"这条路；而 `/api/panels` 的
   `native_*_mm` 在位图那一档是**猜**的：`ppi = 600 if png else 300`，
   依据只有旁边那句注释。

### 实际完成

**1. `web/src/store/workspace.ts` —— 两条工作流的唯一出口。**

```text
mode: 'fast_edit' | 'layout'      activePanelId: string | null
openFastEdit(figureId)      打开一张图 → 快速编辑工作区
addFigureToLayout(figureId) 已在文档里就聚焦，不重复创建
returnToLayout()            回排版
focusLayoutPanel(panelId)   切画布 + 选中 + 滚进视野（11 / 12 复用）
findFigurePanel(figureId)   「文档里有没有这张图」的唯一判据
```

**关键决定（T-43）：不建第二个容器。** 一张图在文档里只有一个面板对象，
快速编辑是它的**另一种看法**。三个要求因此不需要各写一份实现：

* 「添加到画布不复制失联对象」——根本没有复制这一步；
* 「进图内编辑再返回位置尺寸不变」——快速编辑**一个字都不写 x/y/w/h**；
* 「重复添加不叠对象」——找得到就聚焦。

**代价如实记着**：打开一张图会真的在当前画布上放一个面板（一次可撤销的文档
修改）。用户从没想过画布，但画布里多了一个对象——那个对象就是他接下来编辑的
载体，让它凭空存在于"文档之外"才是幻觉。

**2. 快速编辑这一屏**（`CanvasStage` + 新的 `canvas/FastEditBar.tsx`）：
不铺纸面、不画网格/参考线/标尺、只画那一个对象，取景框（fit / 双击适应）
换成那张图的包围盒；画布标签行与顶栏的标注工具整组收起（它们画的是画布对象，
这一屏上看不见）；浮动条给三样东西——图名、原图规格、两个出口
（添加到画布 / 画布排版）。没有源脚本时多一条说明，词取自
`lib/readinessText.ts`，动作是就绪度中心的 `focusPanel()`。

**3. 打开的入口全部换成快速编辑**：素材卡双击 / Enter（主动作从「加入画布」
换成「打开」）、运行时图卡片（跑过的那一档）、`tavotto open <stem>` 的交接。
交接不再自己拼「有就选中、没有就 addPanel」——它调 `findFigurePanel` +
`openFastEdit`，只把结果翻译成 `selected` / `placed`。

**4. `web/src/lib/originalSpec.ts` —— 原图规格的唯一服务**（ADR 0028）。
规格不确定时**界面必须说出来**（浮动条上一个短标记 + 一句 `title`）：
`assumed`（位图没写密度）/ `stale`（源文件不在了，这是上次已知的）/
`fallback`（一个来源都没有，显示「尺寸未知」而**不是**那个占位数）。
优先级 ① 渲染回来的 manifest `size_mm` → ② 文档里的 `nativeW/nativeH`
→ ③ `/api/panels` 的 `original_spec` → ④ 明确 fallback。①在②之前是因为
**图幅不是派生字段**；②在③之前是因为**源文件消失之后它还在**。
画布上的缩放 / 裁剪 / 旋转 / 翻转 / 透明度只进 `spec.ignored`。

**5. `src/tavotto/engine/originalspec.py` —— 事实层。** 位图密度**先量后猜**：
纯标准库解析 PNG `pHYs`、JPEG JFIF 密度、以及 JFIF 只给长宽比时 Exif 的
`XResolution`/`YResolution`；读不到才落回 `ASSUMED_DPI`（**取值与改造前逐位
相同**）并报 `dpi_source: "assumed"`。`/api/panels` 的 `native_*_mm` 改成这份
spec 的**投影**，不是第二次计算。

**别改回 MuPDF 的 `Pixmap.xres`**：实测（PyMuPDF 1.28.2）它对「没有 pHYs」
与「写着 96 dpi」一律回 `96`——两个不同的答案被压成同一个值，而"不知道"正是
这里最需要说出来的那一档。`test_ninety_six_dpi_is_metadata_not_the_absence_of_it`
就钉着这条。

### 关键 API（后面几个 Prompt 直接用）

```ts
// web/src/store/workspace.ts
useWorkspaceStore              // mode / activePanelId
openFastEdit(figureId)         // 'editing' | 'layout_only' | 'missing'
addFigureToLayout(figureId)    // 'added' | 'focused' | 'missing'
returnToLayout()
focusLayoutPanel(panelId)      // boolean —— 11 的问题定位、12 的导出报告直接调
findFigurePanel(figureId)      // { panel, canvasId } | null
restoreWorkspace(documentId, objects)   // 恢复前先验对象还在不在

// web/src/lib/originalSpec.ts   ← Prompt 12 的导出面板从这里取尺寸
getOriginalOutputSpec(figureId): OriginalOutputSpec | null   // 不认识 → null
resolveOriginalSpec(inputs)    // 纯函数核心，判据都打在它上面
ignoredTransforms(panel)       // 画布上设了、原图导出不套用的那几项
FALLBACK_MM                    // 占位尺寸；走到它的 spec 必带 fallback: true
```

```python
# src/tavotto/engine/originalspec.py
asset_spec(path, kind, probe) -> dict     # 文件自己说了什么
raster_dpi(path) -> (x, y) | None         # 只回文件写下的密度，没写就 None
ASSUMED_DPI / ASSUMED_DPI_DEFAULT         # 明确 fallback（值与改造前相同）
```

`/api/panels` 每项新增 `original_spec`；`PanelObject` 新增可选 `pxH`；
`pdfbackend.probe_asset()` 的 raster 结果新增 `alpha`。

### 迁移

**没有迁移，磁盘格式一个字节没升版。** 两处新增都是**可选**字段：
`PanelObject.pxH`（老文档没有它 = 那一维未知，**不许补默认值**）、
`PanelInfo.original_spec`（老后端不发 = 那个后端没有这份事实，解析退到文档里
那份）。新增的本机存储是 `localStorage['tavotto.workspace.<documentId>']`，
读不回来就当"上次在画布排版"。

**一处行为变化要留意**：写了物理密度、而那个密度不等于我们旧假定的位图
（例如 72 dpi 的照片、300 dpi 的 PNG），`native_*_mm` 会变成按文件说的算。
改造前那些值本来就是错的——但用户**已经摆在版上的面板一个都不动**
（`nativeW/nativeH` 存在文档里，`panelSourceSync` 明确不碰图幅）。

### 修改的文件

```text
新增  src/tavotto/engine/originalspec.py         原图规格事实层（+16 条用例）
新增  tests/test_original_spec.py               （含两侧 dpi_source 闭集的同源看护）
新增  web/src/store/workspace.ts                 两条工作流（+19 条用例）
新增  web/src/store/workspace.test.ts
新增  web/src/lib/originalSpec.ts                原图规格唯一服务（+25 条用例）
新增  web/src/lib/originalSpec.test.ts
新增  web/src/canvas/FastEditBar.tsx             快速编辑浮动条
新增  web/src/canvas/fastEditStage.test.tsx      这一屏的可见差别（5 条）
新增  docs/adr/0028-original-output-spec.md
改动  src/tavotto/app.py                         scan_panels 走 originalspec
改动  src/tavotto/pdfbackend/pymupdf_backend.py  raster probe +alpha
改动  web/src/canvas/CanvasStage.tsx             快速编辑取景 / 只画一个对象
改动  web/src/canvas/PageSheet.tsx               +data-page-sheet 测试落点
改动  web/src/components/TopBar.tsx              模式标签 + 标注工具收进 MarkTools
改动  web/src/components/left/AssetBrowser.tsx   主动作「加入画布」→「打开」
改动  web/src/lib/openRequest.ts                 交接落到快速编辑
改动  web/src/App.tsx                            挂持久化订阅 + 快速编辑藏画布标签
改动  web/src/hooks/usePruneSelection.ts         对象消失也退出快速编辑
改动  web/src/store/projectStore.ts              换项目清工作区
改动  web/src/store/workspace.ts                 returnToLayout 的焦点救援
改动  web/src/store/actions.ts / lib/clipboard.ts / lib/migrate.ts /
      store/panelSourceSync.ts                   pxH 与 pxW 成对
改动  web/src/lib/api.ts                         +AssetOriginalSpec / original_spec
改动  web/src/types/document.ts                  +PanelObject.pxH
改动  web/src/i18n/locales/*                     +assets.open* / +fastEdit.*，
                                                 删掉死掉的 assets.addAria
改动  web/src/i18n/overflow.test.tsx             +6 条字数预算
改动  web/src/components/left/AssetBrowser.runtime.test.tsx  主动作改名跟着改
改动  web/e2e/*.spec.ts（8 个）                 双击卡片已经进图内编辑态，
                                              「编辑图内元素」那一步整批删掉；
                                              golden-paths 用标注工具前先回排版
                                              （**本轮没真跑过**，见尚存限制）
改动  AGENTS.md                                  +一行严格同源对（dpi_source 闭集）
改动  src/tavotto/AGENTS.md / web/AGENTS.md      长期规则的家
重建  codex-plugin/mcp/widget/canvas.html        指纹 e8a2c128a5200354
重建  web/dist-playground/                       指纹 73719cc4290353e6（不进 git）
```

### 测试命令与真实结果

```sh
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest tests/test_original_spec.py
cd web && pnpm test && pnpm build && pnpm i18n:check && pnpm lint
# 单跑一个前端用例文件要自己补环境变量（package.json 的 test 脚本里有）
NODE_OPTIONS=--no-experimental-webstorage npx vitest run src/store/workspace.test.ts
# 改了 web/src 之后两个受管产物都要重建
python scripts/build_mcp_widget.py && python scripts/build_browser_playground.py
ruff check . && ruff format --check .
```

后端全量 **exit 0 —— 3217 passed / 34 skipped / 0 failed**
（Session 08 的 3200 + 本轮新增的 17 条 = 3217，数字对得上）；
前端 **134 files / 1618 passed**，`build` / `i18n:check` / `lint` 三条 exit 0。
完整表格见 `STATUS.md` 的「Session 09 之后」。

> `pytest -q` 在**没有失败**的那一遍里，这台机器上把结尾那行计数吞掉了
> （有失败时照常打印）。上面这两个数是从进度流里逐字符数出来的
> （`.` 3217、`s` 34、`F` 0），不是估的——**「看不到计数」不等于「没跑」，
> 但也不等于可以直接写一个数上去**。

**变异反证 26 条全部被打红**；第一轮活下来 2 条，成因与处置见
`TEST_MATRIX.md`。两条都不是"判据写错了"，而是**判据没被执行到它该看的那个
点上**：一条是 T-36 的形状（两条判据说同一件事），合并之后露出了一个从来没被
量过的维度（"上次停在画布排版"）；另一条是三条界面用例里没有一条让素材从清单
里消失过，于是「上次已知」那个标记在界面上从来没被量到。

### 这一轮踩到的坑

**1. `Pixmap.xres` 看不见"没写"这一维。** 第一版打算直接用 MuPDF 报的
`xres`——实测之后发现没有 `pHYs` 的 PNG 和写着 96 dpi 的 PNG 它一律回 96。
**尺子量不了那一维时，判据是恒等成立的**：两条用例会给出同一个答案，而它们
问的是两件不同的事。处置是自己按格式解析（纯标准库）。

**2. pHYs 是每米整数像素**，300 dpi 落盘再读回来是 299.9994。第一版用例
`assert dpi == 300.0` 当场红——红的不是实现，是**编码损失**。量化误差上界
`0.0254/2 = 0.0127`，所以"离最近整数不到 0.02 就还原"是有根据的，不是
四舍五入的方便。

**3. 一条用例自己把 JFIF 段写坏了。** 测试里改 JFIF 密度时把 units 写到了
版本字节上（`JFIF\0` 之后是 2 字节版本再是 units），于是"读不到密度"这条绿得
毫无意义，"读得到"那条红。**自己捏的输入形状会产生假红**——先确认构造是对的
再怀疑实现。

**4. 前端 mock 回 `undefined` 把崩溃甩到被测代码外面。**
`AssetBrowser.runtime.test.tsx` 把 `@/store/actions` 整个打了桩，
`addRuntimePanel` 回 `undefined`；主动作改成"打开"之后，工作区要拿它的 `id`
——报错栈指向 `workspace.ts`，看起来像产品坏了。处置是让桩回一个真的对象，
不是给产品代码加一句 `?.`（那句话没有任何用例能打红它）。

**5. `npx vitest` 直接跑仍然会漏 `NODE_OPTIONS=--no-experimental-webstorage`。**
连续第三轮踩到，记在这里。

### 尚存限制

1. **导出还没有"按原图"这条路。** 本轮只给规格与合同，`/api/export` 仍然只
   会按画布合成——**在快速编辑里点顶栏「导出」，出来的仍然是整张画布**。
   Prompt 12 接（它要做的第一件事就是让导出面板读 `getOriginalOutputSpec()`
   并给出「按原图 / 按画布」两条路）。这一条是本轮已知的**表里不一**：
   工作区说的是一张图，导出给的是一张版。
2. **快速编辑里画不了画布标注。** 标注工具整组收起了——它们画的是画布对象，
   这一屏上看不见。图内标注（override）不受影响。真需要"在图上加个箭头"的
   用户，路径是"添加到画布 → 排版模式"。
3. **打开一张图会在当前画布上放一个面板**（见上面的代价）。多画布项目里它落在
   **激活画布**上，不由用户挑。
4. **`original_spec` 只覆盖 `/api/panels` 的素材。** runtime 素材（ADR 0013）
   走描述符里的 `size_mm`，没有像素网格与密度——它没有磁盘原件，那两维本来
   就不存在。
5. **`FALLBACK_MM` 是个占位常数**（80 × 60 mm），与 Prompt 10 的规范层没有
   耦合。真到了要按规范给默认尺寸的那一步，那是 10 的事。
6. **窄屏下快速编辑浮动条没有实测过**。它是一条 flex 行，jsdom 量不出溢出；
   英文字数预算已经进了 `overflow.test.tsx`，但真实断行要等 e2e（issue #30 的
   POSIX 腿仍然缺）。
7. **e2e 这一层本轮没有真跑过**（与 Session 08 的 axe 同一条限制：Playwright
   要真实后端与浏览器，本机沙箱里起不来）。改动是**必需的**——「打开」的语义
   变了，8 个 spec 里「双击卡片 → 再点一次『编辑图内元素』」的那一步现在会
   点到一个不存在的按钮。改法一致（删掉那一步；`golden-paths` 用标注工具前
   先点「画布排版」回去），`playwright test --list` 收得到全部 110 条，
   但**收得到 ≠ 跑得过**。23 之前必须真跑一次，这一条与 axe 那条一起记在
   `STATUS.md` 的遗留表里。
8. 04–08 的其余遗留原样开着（R-05 五处手写原子写、R-07 autosave 位置、
   `/api/layouts/<name>` 无 schema 校验、axe 那两条从没真跑过）。

### 工作树状态

- worktree：`/Volumes/Projects/Tavotto/.claude/worktrees/product-ux-v2`
- 分支：`feat/product-ux-reliability-v2`，从 `origin/main` 的 `ef9ac02` 开出
- **PR #201** 带的仍然是 Session 01–04；**05–09 的提交还没有推**——节奏由用户
  定（一推就触发一轮 Codex 评审）
- author 用 `88193520+erwanjun@users.noreply.github.com`（与 `main` 上每一个
  提交一致）。本机 `~/.gitconfig` 是别的邮箱，提交时用
  `git -c user.email=… commit`，**别改共享的 `.git/config`**

---

## 历史：Session 08（2026-08-29）

### 目标

把 Session 07 算好的那份事实**变成普通科研用户看得懂的产品体验**，并顺手把
左侧工作区外壳整理成稳定的常驻结构。

本阶段**不改 watcher、不增强解析器、不实现多选栏与 onboarding**，也**不在
前端重新判一次状态**。

### 实际完成

**1. `web/src/store/projectReadinessStore.ts` —— 就绪度的前端唯一持有者。**
职责只有三样：把报告取回来、记住「用户已经看过哪一版」、记住「接入中心此刻
聚焦在哪张图」。并发治理逐条照抄 `assetStore` 的纪律（请求序号挡旧响应、
发请求那一刻的 `pj` 挡串项目、同批合并、`force` 另起一次、失败保留上一次成功
那份）。**fingerprint 没变时连报告对象的引用都不换**——换了引用，订阅它的每个
组件都会白重渲染一轮。

**2. 顶部摘要横幅 `ProjectReadinessBanner`**，与 `UpdateBanner` /
`DocumentBanner` 同形（同高度、同挂载点、不阻塞画布、不自动弹框）：

```text
已找到 18 张图：8 张可编辑，5 张待连接，5 张仅排版。   [查看接入状态] [关闭]
```

关闭按 **项目 id + 报告 fingerprint** 记在本机（`tavotto.readinessDismissed`，
只留最近 20 个项目，坏 blob 安全恢复，**不记项目绝对路径**）。事实一变就再说
一次——它不是「永久别再提」。

**3. `RegistryDialog` 重构成「项目接入状态」**（文件名与导出名保留，T-38）。
信息架构从「一份脚本清单」翻成「一张图一行」：

```text
总计 18 · 可编辑 8 · 待连接 5 · 仅排版 5          [重新扫描]
需要处理（5）
  Fig3.pdf                                        [有冲突]
  不止一个脚本说自己生成这张图，需要你指定用哪一个。
  [用 old_version.py] [用 z_newer.py]  ▸ 技术详情
可编辑（8） / 仅排版（5）
▸ 全部脚本（12）        ← 高级段：每个 .py + 试运行 + 手工填图名
```

每个状态的下一步、以及**绝不做的事**：

| 状态 | 动作 | 绝不 |
| --- | --- | --- |
| `editable` | 添加到画布 / 重新试运行 / **技术详情里改绑**（候选不含它现在连着的那个） | — |
| `auto_linkable` | 自动连接（= 重新扫描） | — |
| `needs_probe` | 试运行并连接（点之前先说「Tavotto 将运行这个脚本」） | 不自动跑 |
| `conflict` | 候选**逐个列出**，点哪个写哪个 | **不猜**，一个都不预选 |
| `source_missing` | 重新扫描 / 选择新脚本 | 不说成"文件损坏" |
| `layout_only` | 选择源脚本 / 继续当普通素材 | **不画成错误** |

聚焦（`focusPanel(id)`）：打开 → 滚到那一行 → 焦点落上去 → 短暂静态高亮 →
**当场清掉聚焦标记**。关闭后的焦点归位**不在这里**——`ui/Dialog` 已经做了
（`onOpenAutoFocus` 记、`onCloseAutoFocus` 还，带节点被换掉的兜底）；再记一份
就是同一条保证有两个实现，删掉任意一个都不会有用例红（T-36 的形状）。

**4. 素材卡与画布上的四个出口**，全部读同一份 `PanelInfo.capability`：

* 卡片左下角一个**非交互** `<span>` 角标（`editable` 不加，那里已经有 `{}`）；
  状态进 `aria-label`；完整解释在 `title` 与说明条里；
* 选中卡片后，**listbox 外面**一条说明条（文件名 · 状态 / 一句原因 /
  「查看接入状态」按钮）——`role="option"` 里不许再嵌可 Tab 的控件；
* 画布单选没有编辑入口的图时，ContextBar 上多一个「为什么不能编辑？」；
* 属性栏 panel 段顶部一条非阻塞说明。

**5. 常驻左侧工作区外壳。** 轨道与抽屉的骨架早就在（默认展开、可折叠、可钉住、
可拖宽、三档断点），本轮做了三件事：轨道底部加**项目接入状态**入口、在
`ITEMS` 旁标注 Prompt 11 的「问题」入口位置（**不放占位按钮**），以及——

**修掉一个真实缺陷（T-40）**：`persist()` 原来照抄当前状态，而**响应式让位也
写在同一个 `leftOpen` 上**。把窗口拖窄一次（左栏自动让位），之后任何一次
persist 都会把 `leftOpen: false` 当成偏好写进本机；回到大屏、重启之后常驻左栏
再也回不来，**而用户从没关过它**。现在偏好单独记一份，只有用户自己的动作与
产品规则写它，响应式让位一律不写。

**6. 后端一处很小的改动**：就绪度报告的每个 panel 多一个 `stem`（T-37）。
关联动作写进去的键是 stem 不是那张图，而 `sub/Fig.v2.pdf` 的 stem 是 `Fig.v2`
——前端自己切就是第二份判据。`CAPABILITY_FIELDS` 一个字没改。

### 关键 API（后面几个 Prompt 直接用）

```ts
// web/src/store/projectReadinessStore.ts
useProjectReadinessStore   // report / loading / error / focusId / dismissed
  .load({ force })         // 合并；force 另起一次
  .focusPanel(fileId)      // 打开「项目接入状态」并滚到这张图  ← 17/18 复用这个
  .openCenter() / .closeCenter() / .dismissBanner() / .clear()
bannerReport(state)        // 横幅该不该出现（纯函数，五个条件）

// web/src/lib/readinessText.ts   ← 状态、句子与「待连接」的唯一一份实现
statusLabel(status)        // 可编辑 / 待连接 / 需试运行 / 有冲突 / 源脚本丢失 / 仅排版
reasonText(capability)     // 按 reason_code 查，**不按 status**
PENDING_STATUSES           // 「待连接」是哪几个状态（集合，不是显示顺序）
pendingCount(summary)      // 由上面那个集合现算——横幅与接入中心同一个加法

// web/src/lib/api.ts
ReadinessPanel.stem        // 新增：关联动作的键
ReadinessSummary           // 从内联类型抽出来的具名类型
```

**开关仍然是 `uiStore.registryOpen`**（T-38）——就绪度 store 里没有同义布尔值。

### 迁移

**没有迁移，磁盘格式一个字节没动。** 唯一新增的本机存储是
`localStorage['tavotto.readinessDismissed']`（项目 id → fingerprint），
读不回来就当"谁都没关过"。`tavotto.ui` 的键集没变——`leftOpen`/`rightOpen`
写进去的值从「当前状态」改成了「用户的偏好」，老 blob 原样能读。

### 修改的文件

```text
新增  web/src/store/projectReadinessStore.ts    就绪度前端持有者（+ 22 条用例）
新增  web/src/lib/readinessText.ts              状态标签 / 一句话原因（唯一一份）
新增  web/src/components/ProjectReadinessBanner.tsx  顶部摘要（+ 9 条用例）
新增  web/src/canvas/drawerViewportResize.test.tsx   抽屉开合 → 画布视口（5 条）
新增  web/src/canvas/panelReadinessEntry.test.tsx    「为什么不能编辑？」（7 条）
新增  web/src/components/RegistryDialog.test.tsx     接入中心（25 条）
新增  web/src/components/inspector/panelCapabilityNote.test.tsx（5 条）
新增  web/src/components/left/AssetBrowser.readiness.test.tsx（11 条）
改写  web/src/components/RegistryDialog.tsx     脚本清单 → 一张图一行
改动  web/src/components/left/AssetBrowser.tsx  角标 + listbox 外的说明条
改动  web/src/components/left/LeftRail.tsx      项目接入状态入口 + 11 的位置注记
改动  web/src/components/inspector/PanelSection.tsx  非阻塞说明
改动  web/src/canvas/ContextBar.tsx             「为什么不能编辑？」
改动  web/src/store/uiStore.ts                  偏好与实际开合分开（T-40）
改动  web/src/store/liveSync.ts                 就绪度刷新挂在统一入口（T-39）
改动  web/src/store/projectStore.ts             换项目清就绪度 + 重取
改动  web/src/App.tsx                           挂横幅 + 启动取一次
改动  web/src/lib/api.ts                        +ReadinessPanel.stem、+ReadinessSummary、
                                              writeRegistryEntry 的 entry 改成可省
改动  web/AGENTS.md                           +「接入状态与左侧外壳」一节（长期规则的家）
改动  web/src/i18n/locales/*                    +readiness.*，删掉 33 个死掉的 registry.*
改动  web/src/i18n/overflow.test.tsx            +9 条字数预算
改动  web/src/store/uiStore.test.ts             +9 条左栏外壳用例
改动  web/e2e/golden-paths.spec.ts              跟着改名（菜单项 / 对话框名 / 按钮）
改动  web/e2e/a11y.spec.ts                    +2 条 axe 用例（**本轮没真跑过**，见尚存限制）
改动  web/src/i18n/locales/*/errors.json      三条指向「脚本注册表」的后端错误文案跟着改
改动  src/tavotto/engine/readiness.py           panels 多一个 stem
改动  tests/test_project_readiness.py           +2 条、shape 用例的键集 +1
重建  codex-plugin/mcp/widget/canvas.html       改了 web/src 就要重建（指纹 ebea0b57749239f2）
重建  web/dist-playground/                      同上（指纹 4dd2877615f06445，不进 git）
```

### 测试命令与真实结果

```sh
# 后端全量（worktree 里必须带 PYTHONPATH，否则 import 到主工作区）
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest
# 只跑本阶段动过的那份
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest tests/test_project_readiness.py
# 前端（先 cd web）
pnpm test && pnpm build && pnpm i18n:check && pnpm lint
# 单跑一个前端用例文件时**必须自己补环境变量**（package.json 的 test 脚本里有）
NODE_OPTIONS=--no-experimental-webstorage npx vitest run src/store/projectReadinessStore.test.ts
# 改了 web/src 之后两个受管产物都要重建
python scripts/build_mcp_widget.py && python scripts/build_browser_playground.py
```

后端全量 **exit 0 —— 3200 passed / 34 skipped / 2 deselected**，10 分 27 秒
（Session 07 的 3199 + 本轮新增的 1 条 = 3200，数字对得上）。
前端 **131 files / 1557 passed**，`build` / `i18n:check` / `lint` 三条 exit 0。

**Session 06 那条偶发红本轮又是绿的**
（`tests/native/test_run_cli_integration.py::test_ctrl_c_reaches_the_script_and_leaves_no_orphan`）。
**三次绿仍不构成"它被修好了"**：`tavotto run` 那条线本轮一个字节没改。
它仍留在 `STATUS.md` 的遗留表里。
**变异反证 33 条全部被打红**（第一轮活下来 5 条，四种成因与处置见 `TEST_MATRIX.md`——
其中一条查出来是**杀不死的冗余**，处置是删掉那句防御，不是造输入去覆盖它）。

### 这一轮踩到的坑

**1. 一条测试**捏了一个**后端给不出来的输入形状**（两个不同项目、同一个
fingerprint），于是红的不是缺陷、是幻觉。`project_id` 就在被哈希的那份 body
里，两个项目不可能撞指纹；换项目那条路又必然先 `clear()`。**处置是改测试，
不是给代码加一句 `project_id` 比较**——那句话没有任何用例能打红它，正是
T-36 说的「冗余的保证杀不死」。

**2. 三条变异第一轮活了下来**，没有一条是"判据写错了"，全都是**判据没被执行
到自己该看的那个点上**：一条的 fixture 让断言恒真（`mockResolvedValue(report())`
只求值一次，两次响应是同一个对象）；一条缺一维（没有任何用例选中过一张
`capability` 缺席的卡片）；一条是 fixture 里两个出处给了同一个值，于是屏蔽掉
第一个出处，第二个照样返回它。第三条是 T-36 的形状长在了 fixture 里。

**3. 一条判据的尺子看不见它要量的那一维**：「改绑候选里不含当前脚本」原本
打在 Radix Select 的触发器文本上，而选项住在弹层里、触发器上只有 placeholder
——无论实现怎么改它都恒真。处置是把算选项那段抽成纯函数
（`sourceOptions()`），判据直接打在它上面。

**4. `npx vitest` 直接跑会漏掉 `NODE_OPTIONS=--no-experimental-webstorage`**，
表现是 `localStorage` 是 `undefined`、报错看起来像被测代码坏了。这条在
STATUS.md 里记过一次，本轮又踩了一次——单跑文件时记得带上。

### 尚存限制

1. **runtime figure 素材（ADR 0013）在接入状态里一个字不说。** 它们不在
   `/api/panels` 的 id 空间里，拿不到 `capability`；四个出口都以「拿得到
   capability」为前提，所以自然沉默。runtime 卡片有它自己那套角标。
2. **「重新扫描」只有项目级一个入口**（对话框顶部）。Prompt 08 的原文把它
   列进了 `editable` 与 `source_missing` 两个状态的行内动作；`source_missing`
   那一行给了，`editable` 没给——18 行里每行都挂一个项目级动作是噪音。
3. **冲突那一行只给两个声称者，不给「从全部脚本里挑一个」的下拉。** 正确答案
   是第三个脚本时，出路在高级段的「全部脚本 → 手工填图名」。这么排是因为
   两个候选按钮就是绝大多数情况下的答案，再摆一个下拉会把"选哪个"这件事
   稀释掉。真遇到用户抱怨再加。
4. **接入中心没有虚拟滚动**：报告里有多少张图就渲染多少行（每行一个
   `<details>` + 若干按钮）。**本轮没有实测过大项目**——用例里最多 6 行，
   真实上限不知道。几百张图的项目要不要分页或虚拟化，等有人拿真项目量过
   再定。
5. **横幅关闭记录不随项目走**（存本机 `localStorage`，按项目 id 索引）。
   换一台电脑要重新关一次——刻意的：它是 UI 会话偏好，不该写进用户项目。
6. **axe 那一层本轮没有真跑过。** `e2e/a11y.spec.ts` 新增了两条（接入状态
   对话框的 axe + focus trap + Escape 归位；素材卡角标的 nested-interactive），
   `playwright test --list` 收得到它们，但 Playwright 要真实后端与浏览器，
   本机沙箱里起不来。单测只做了**结构性**断言（`role="option"` 内零 `<button>`、
   零可 Tab 控件、方向键导航不回归）——那不等于 axe 跑过。**23 之前必须真跑
   一次**，这一条记在 `STATUS.md` 的遗留表里。
7. 04/05/06/07 的其余遗留原样开着（R-05 五处手写原子写、R-07 autosave 位置、
   `/api/layouts/<name>` 无 schema 校验、项目打开仍走自己的静态草稿逻辑、
   「编辑历史」入口位置）。

### 工作树状态

- worktree：`/Volumes/Projects/Tavotto/.claude/worktrees/product-ux-v2`
- 分支：`feat/product-ux-reliability-v2`，从 `origin/main` 的 `ef9ac02` 开出
- **PR #201** 已开，带的是 Session 01–04。**05 / 06 / 07 / 08 的提交还没有推**
  ——用户定的节奏是「每个 Session 一个独立提交，攒够几个再一次推」，
  推上去会立刻触发一轮 Codex 评审，所以由用户决定什么时候推
- author 用 `88193520+erwanjun@users.noreply.github.com`（与 `main` 上每一个
  提交一致）。本机 `~/.gitconfig` 是 `1259959884@qq.com`，两者不一致会让
  cla-check 在同一个仓库里数出两个贡献者；提交时用
  `git -c user.email=… commit`，**别改共享的 `.git/config`**
  （linked worktree 默认共享它，一条命令污染所有会话）
- `web/node_modules` 已在 worktree 内真装

---

## 历史：Session 07（2026-08-29）

### 目标

把「这张图能不能进图内编辑」变成**一句可以直接显示的事实**，主语固定为
`/api/panels` 的那一个素材。

本阶段**只做后端事实模型与 API**——不做界面（08）、不增强解析器、不跑用户
脚本、不 probe。前端只加了类型与一个 fetch 函数，一个组件都没动。

### 为什么需要它（真实的起始状态，不是 prompt 假设的那个）

「这张图能不能编辑」在 07 之前由三处各答一次，而**三处的主语都不一样**：

| 出处 | 主语 | 它能回答的 |
| --- | --- | --- |
| `/api/panels` 给不给 `script` | **素材** | 注册表映射了没有 |
| `/api/registry` 的 `candidates` | **stem** | 静态扫描认领了没有 |
| `probe.script_inventory()` 的 `reason` | **脚本** | 这个 .py 处于什么状态 |

同一张图于是在素材面板里「不可编辑」、在注册表对话框里「有候选脚本」、在
脚本清单里「可试运行」——三句话都对，合起来却没有一句回答了用户的问题。
决策写在 `DECISIONS.md` 的 T-31。

### 实际完成

**1. 新模块 `src/tavotto/engine/readiness.py`（纯标准库，Flask 父进程 import）。**
六个互斥状态 + 稳定 reason code，判定表如下（分支从上往下，每张图只落一个）：

| # | 条件 | status | reason_code |
| ---: | --- | --- | --- |
| 1 | 注册表映射了这个 stem，脚本文件**在** | `editable` | `registered_source` |
| 2 | 注册表映射了，脚本文件**不在** | `source_missing` | `registered_script_missing` |
| 3 | 这一轮静态扫描**没跑成** | `layout_only` | `source_scan_unavailable` |
| 4 | **多个**脚本认领同一个 stem | `conflict` | `multiple_source_candidates` |
| 5 | **恰好一个**脚本认领 | `auto_linkable` | 见下 |
| 6 | 项目里有产图但输出名要跑才知道的脚本 | `needs_probe` | `runtime_output_unknown` |
| 7 | 其余 | `layout_only` | `no_source_candidate` |

第 5 行的 reason 说的是**卡在哪一步**，优先级从「刷多少次都没用」往「下一次
刷新就好了」排：

```text
registry_invalid  >  project_read_only  >  registry_write_failed  >  static_unique_candidate
```

**注册表优先于静态报告**（第 1 行在第 4 行之上，T-34）：注册表文件就是人工
裁决的落处（`src/tavotto/AGENTS.md`：「归属有歧义的 stem，裁决结果记在各图库
自己的注册表文件里，**勿改**」）。静态冲突照旧出现在项目级 `conflicts` 里，
带上 `resolved_by`。

**2. `GET /api/project/readiness`。** 下面这份是**真的跑出来的**（一个含
`sub/fig_a.py`→`FigA`、两个脚本抢 `Dup`、一个动态输出名脚本的项目；只有
`generated_at` 换成了固定值，其余逐字照抄，fingerprint 也是真的）：

```json
{
  "project_id": "3f9c1a2b7d04",
  "fingerprint": "70b70db1f41d093425be7c0349362c76",
  "generated_at": 1756468800.42,
  "summary": {
    "total": 3, "editable": 1, "auto_linkable": 0, "needs_probe": 1,
    "conflict": 1, "source_missing": 0, "layout_only": 0
  },
  "panels": [
    { "id": "Dup.pdf", "status": "conflict",
      "reason_code": "multiple_source_candidates", "script": null,
      "candidates": ["old_version.py", "z_newer.py"],
      "can_probe": true, "can_manual_link": true,
      "details": { "candidate_scope": "panel" } },
    { "id": "FigA.pdf", "status": "editable",
      "reason_code": "registered_source", "script": "sub/fig_a.py",
      "candidates": [], "can_probe": false, "can_manual_link": true,
      "details": { "entry": "main", "cost": "light" } },
    { "id": "Mystery.pdf", "status": "needs_probe",
      "reason_code": "runtime_output_unknown", "script": null,
      "candidates": ["dyn.py"], "can_probe": true, "can_manual_link": true,
      "details": { "candidate_scope": "project" } }
  ],
  "conflicts": [
    { "stem": "Dup", "candidates": ["old_version.py", "z_newer.py"],
      "resolved_by": null }
  ],
  "project": { "writable": true, "registry_valid": true,
               "scan_ok": true, "can_rescan": true },
  "issues": []
}
```

注意 `Dup.pdf` 那一条：`z_newer.py` 的名字更像"新版本"，`old_version.py` 的
名字更像"旧的"——**机器一个都不选**（`tests/…::test_two_scripts_claiming_one_stem_is_a_conflict_and_is_never_auto_resolved`
连 mtime 更新的那一个也不许赢）。

三个字段的取值是**三档不是两档**，08 不要把它们压扁：

* `conflicts`：`null` = 这一轮没跑静态扫描；`[]` = 扫过了、没有冲突；
* `project.registry_valid`：`null` = 项目里根本没有注册表文件（还没起草过）；
  `false` = 有、但读不回来；
* `details.candidate_scope`：`"panel"` = 这张图的候选；`"project"` = 项目里
  这几个脚本的产物静态解不出来，跑一个才知道是不是它。

**3. `/api/panels` 每项多一个 `capability`**（六个字段，`CAPABILITY_FIELDS`）。
它是**同一次 `compute()` 的投影**，`/api/panels` 不自己再判一遍——两处各算
一遍的话，「素材面板说可编辑、就绪度面板说要试运行」只是时间问题。

**老字段一个没动。** `script` 的语义仍然是「注册表声明了映射」，`editable`
时照旧有值；`auto_linkable` / `conflict` 有候选，但候选**不塞进 `script`**
（塞了的话旧前端会当场给它画上 ⚡）。`source_missing` 仍带 `script` ——那是
注册表里真实记着的那一条，不是伪造；要分辨「脚本还在」与「指着的文件没了」
就看 `capability.status`。

**4. fingerprint = 报告自身的内容哈希**（T-32）：规范化 JSON（**键排序**）
的 SHA-256 前 32 位，输入是 body 去掉 `generated_at` 与 `fingerprint`。
于是要求里那四条自动成立，不用逐条去防——时间戳不在 body 里所以进不来；
素材 / 脚本的 mtime 没有进报告所以变了它不动；绝对路径本来就一个都不在；
键序由 `sort_keys` 排掉。

**5. 项目级缓存**（挂在 `RefreshState.readiness`，随项目消亡）：两层，键都是
**输入的内容签名**——贵的那层是 `discover.discover()`（逐脚本 `ast.parse`），
外层是整份报告。**扫描失败的那一份不进缓存**（缓存一次失败等于让一次瞬时
错误把就绪度永久钉死）。**进出都深拷贝**（缓存里那份是唯一权威）。

**6. 三处很小的既有代码改动**（都在同一条链路上，不是顺手重构）：

* `discover.claims_of()` —— 从 `discover()` 里抽出的纯函数（「stem 被谁认领」
  的唯一判据）。`discover()` 的输出**一字未变**，它现在是这个函数的第一个
  消费者，就绪度是第二个；
* `RefreshState.registry_write_failed` —— 静态合并**写**注册表失败时置位、
  成功时清零。对外的 `scan_failed` code **没改**（老 `/api/registry/scan` 的
  契约），区分只留在状态里给就绪度用；
* `RefreshState.readiness` —— 缓存槽位。刷新在**确认事实真的动了之后**把它
  清成 `None`（`project_refresh` 不 import `readiness`，否则依赖成环）。
  这是签名之外的**第二道**判据：签名盖不住「同尺寸 + 同一个 mtime_ns 刻度里
  的就地改写」，而那正是刷新自己写注册表时的形状。

### 关键 API（Prompt 08 直接用）

```python
# src/tavotto/engine/readiness.py
compute(ctx) -> dict            # 报告本体（**不含** generated_at）；ctx 只要 path/id/registry
capability_map(ctx) -> dict     # 素材 id → capability 子集（/api/panels 用的就是它）
invalidate(ctx) -> None         # 丢掉缓存（用例与非刷新路径用）
fingerprint(body) -> str        # 报告 → 内容哈希
STATUSES, REASONS_BY_STATUS, CAPABILITY_FIELDS   # 枚举与判定表的机器可读版本
```

```ts
// web/src/lib/api.ts
fetchReadiness(): Promise<ReadinessReport>
PanelInfo.capability?: PanelCapability
type ReadinessStatus   // 六个状态的闭集
type ReadinessReason   // 十个 reason code 的闭集
```

### 迁移

**没有迁移，磁盘格式一个字节没动。** 就绪度不写盘。唯一的接口变化是
`/api/panels` 每项**多**了一个可选 `capability`——旧前端忽略未知字段。

### 修改的文件

```text
新增  src/tavotto/engine/readiness.py         事实模型（纯诊断，不执行、不写盘）
新增  tests/test_project_readiness.py         53 条
改动  src/tavotto/engine/discover.py          抽出 claims_of()（discover() 输出不变）
改动  src/tavotto/engine/project_refresh.py   +registry_write_failed、+readiness 缓存槽、
                                              _static_merge 记账、有差异才失效缓存
改动  src/tavotto/app.py                      +GET /api/project/readiness；
                                              scan_panels 挂 capability（同源投影）
改动  web/src/lib/api.ts                      +六状态/十 reason 的类型、+fetchReadiness、
                                              PanelInfo.capability
重建  codex-plugin/mcp/widget/canvas.html     改了 web/src 就要重建（指纹 47aee0ca4eee6e47）
```

### 测试命令与真实结果

```sh
# 后端全量（worktree 里必须带 PYTHONPATH，否则 import 到主工作区）
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest
# 只跑本阶段
PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python -m pytest tests/test_project_readiness.py
# 格式（与 CI 那一格逐字相同）
ruff check . && ruff format --check .
# 前端（先 cd web）
pnpm test && pnpm build && pnpm i18n:check && pnpm lint
# 改了 web/src 之后
python scripts/build_mcp_widget.py
```

后端全量 **exit 0 —— 3199 passed / 34 skipped / 2 deselected**，9 分 57 秒
（Session 06 的 3145 passed + 53 新增 + 1 = 3199，数字对得上）。
前端 **124 files / 1456 passed**，`build` / `i18n:check` / `lint` 三条 exit 0。
**变异反证 35 条全部被打红**（第一轮活下来 7 条，两种成因与处置见 `TEST_MATRIX.md`）。

**Session 06 那条红本轮两次全量都绿**
（`tests/native/test_run_cli_integration.py::test_ctrl_c_reaches_the_script_and_leaves_no_orphan`）。
本轮 `tavotto run` 那条线一个字节没改，所以两次绿**不构成"它被修好了"**——
它是偶发的，仍留在 `STATUS.md` 的遗留表里。

### 这一轮踩到的坑

**七条变异第一轮活了下来**，两种成因，都值得下一个 Session 记住：

1. **同一条保证有两个实现，谁也杀不死谁（2 条）。** 排序做了两遍
   （素材清单一次、报告 panels 一次），删掉任意一处都还有另一处兜着。
   这不是"多一层保险"，是**判据量不到自己**。处置：删掉冗余的那一处
   （T-36），顺序的契约只留一份。
2. **用例只跑了「方便的那个时刻」（5 条）。** 只读项目、非法注册表、内存
   注册表、深拷贝出口、结构校验——五条的形状完全一样：用例把状态**摆好之后
   才第一次读**，于是缓存里根本没有旧值可以过期，"缓存键含这一维"就量不到。
   处置：先热一遍缓存，再改条件，再读第二遍。

变异脚本带 `PYTHONDONTWRITEBYTECODE=1`，还原走**备份文件**而不是
`git checkout --`（工作树里有未提交的新文件）。

### 尚存限制

1. **就绪度只覆盖磁盘素材**（`/api/panels` 的 id 空间）。runtime figure 素材
   （ADR 0013，`runtime:` 前缀）不在报告里——它们按定义就有脚本，且 id 空间
   不同，混进来会破坏「id 与 `PanelInfo.id` 逐字相同」这条。
2. **`needs_probe` 的候选是项目级的**：静态解不出那些脚本的产物，所以说不出
   「这张图来自其中哪一个」。项目里有一个动态脚本，所有没有专属候选的图都会
   变成 `needs_probe`——`details.candidate_scope: "project"` 就是为了让 08 能
   如实措辞（「跑一个就知道了」，而不是「这张图来自其中之一」）。
3. **`/api/panels` 的 `capability` 可能缺席**：就绪度扫描与素材遍历之间新出现
   的素材这一轮没有它。`undefined` 的意思是「这一轮还不知道」，**不是**
   `layout_only`——08 不要给它补默认值。
4. **签名的分辨率与 watcher 同级**（`(size, mtime_ns)`）：「同尺寸 + 同一个
   mtime_ns 刻度里的就地改写」两边都发现不了。刻意不在就绪度这一侧单独收紧
   ——收紧一侧只会让两个模块对「变了没有」给出不同答案。刷新那一侧的显式
   失效是第二道判据。
5. **项目打开仍走自己的静态草稿逻辑**，没并进统一服务（为了不扫两遍）。
6. 04/05/06 的其余遗留（R-05 五处手写原子写、R-07 autosave 位置、
   `/api/layouts/<name>` 无 schema 校验、没有 SSE 事件名的同源门禁、
   「编辑历史」入口位置）原样开着。

### 工作树状态

- worktree：`/Volumes/Projects/Tavotto/.claude/worktrees/product-ux-v2`
- 分支：`feat/product-ux-reliability-v2`，从 `origin/main` 的 `ef9ac02` 开出
- **PR #201** 已开，带的是 Session 01–04。**05 / 06 / 07 的提交还没有推**
  ——用户定的节奏是「每个 Session 一个独立提交，攒够几个再一次推」，
  推上去会立刻触发一轮 Codex 评审，所以由用户决定什么时候推
- author 用 `88193520+erwanjun@users.noreply.github.com`（与 `main` 上每一个
  提交一致）。本机 `~/.gitconfig` 是 `1259959884@qq.com`，两者不一致会让
  cla-check 在同一个仓库里数出两个贡献者；提交时用
  `git -c user.email=… commit`，**别改共享的 `.git/config`**
  （linked worktree 默认共享它，一条命令污染所有会话）
- `web/node_modules` 已在 worktree 内真装

---

## 下一阶段入口（Prompt 08：Readiness 前端与常驻左栏）

**从这里开始读**：`src/tavotto/engine/readiness.py` 的模块文档（判定表与三档
取值都在里面）、`web/src/lib/api.ts` 的 `ReadinessStatus` / `ReadinessReason` /
`ReadinessReport`、`DECISIONS.md` 的 T-31~T-36。

**Session 07 留给它的**：一份**已经算好**的事实面。

| 东西 | 位置 | 08 可以直接依赖的性质 |
| --- | --- | --- |
| 每张图的能力 | `PanelInfo.capability`（`/api/panels` 每项都带） | 与整份就绪度**同一次计算**，不会互相矛盾 |
| 整份报告 | `GET /api/project/readiness` → `fetchReadiness()` | 带 summary、conflicts、项目级 issues |
| 「变了没有」 | `fingerprint` | 同一份事实下不变；`generated_at` 与无关文件的 mtime 都进不来 |
| 状态与文案的对应 | `REASONS_BY_STATUS`（后端）/ `ReadinessReason`（前端类型） | 闭集，且有用例钉住「不许冒出没备案的组合」 |
| 动作能力 | `can_probe` / `can_manual_link` / `can_rescan` | 只说"界面可以提供"，执行仍归既有端点 |

**绝不要做的事**：

1. **不许在前端重新猜状态。** 按 `script` 有没有值自己判一遍，就是把改造前
   那三个互相矛盾的答案又请回来一个。能力事实只有 `capability.status` /
   `reason_code` 一个出处。
2. **不许另起同义状态。** 六个就是六个；界面上要分得更细的话，回后端加
   reason code（并在 `REASONS_BY_STATUS` 里备案），不要在组件里再分一层。
3. **不许把三档压成两档**（`conflicts` 的 `null`、`registry_valid` 的 `null`、
   `capability` 的 `undefined`）。「没测量」不是「测量结果是零」，把它补成
   默认值，用户会一直等一个永远不来的提示。
4. **不许把 reason code 翻译成的句子存进文档或 history**（存 message key +
   结构化参数——`HistoryEntry.label` 的既有约定）。
5. **不许让就绪度界面去执行动作。** 试运行走 `/api/registry/probe`（用户显式
   触发、可取消、有进度），手工关联走 `PUT /api/registry`，重扫走
   `POST /api/project/refresh` → `refreshProjectNow()`（素材面板的刷新按钮
   已经在用这一条）。
6. **不许在 UI 上暴露实现术语**（stem / registry / AST / manifest）——这正是
   reason code 存在的理由：后端给枚举，前端给人话。

**必须保留的不变式**（改动前先确认还成立）：

1. `loadSeq` / `derivedSeq` 把「载入」「用户编辑」「派生同步」分成三档。
2. `dirty` 同时盯 `doc` 与 `canvases`；收到 409 后基线**故意不推进**。
3. 落盘一律走 `engine/atomicio`（ADR 0023）；保存状态只经 `setSaveState()` /
   `setDocNotice()` 改（ADR 0024）。
4. **刷新的编排只有 `refresh_project_index()` 一份**（ADR 0025）；**发现只有
   `project_watch` 一份**（ADR 0026）；**前端的消费只有 `liveSync` 一份**；
   **能力事实只有 `readiness` 一份**（本轮新增）。
5. **无差异 = 零事件、零写盘、零 worker 失效、零缓存失效**（后端）；
   **无差异 = 零 `set()`、零 dirty、零提示**（前端）。
6. 「哪些文件算素材」只有 `iter_assets()` 一处判据；脚本遍历只有
   `discover.iter_all_scripts()` / `iter_scripts()` 两个视图；「谁认领了这个
   stem」只有 `discover.claims_of()` 一处。
7. **就绪度不执行用户脚本、不 probe、不写盘、不改注册表、不发 SSE**
   （磁盘 CANARY + 桩两层证据钉着）。
8. **派生数据刷新不得把文档标脏（对用户而言），也不得进普通撤销历史。**
9. **素材不在清单里 ≠ 脚本关系失效**（T-28）。
10. `reason` 是闭集，表外的值归成 `manual`；客户端字符串不透传。
