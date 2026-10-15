# X03 · 原 E02：原图与画布 SVG

2026-10-15 实现范围。保留 RC-113/114：只有可兑现的 IR / 来源能导出，
`ImportedPage` 不意味着任意 PDF 都能转换为 SVG。原 worker SVG 直出已有 U08 基础，
这次接上统一导出入口、同源 Figure 产物和画布写入器。

## 1. 实现与来源边界

- `scope=original` 与 `scope=canvas` 共用已有导出请求、逐格式结果和原子发布。
  SVG 是矢量格式，不因选择 SVG 而要求 PPI；原有位图素材仍按其实际像素嵌入。
- 画布新增文字、箭头、形状复用同一份 `RenderPlan`。`rendercore/svgwriter.py`
  写 `Path` / `Group` / clip / `Image` / `ShapedText`，不重做画布布局。
- Figure 面板必须有**同一已接纳 Figure 状态**的全质量 PDF/SVG 对：同一 owner-thread
  命令、一次 overrides 应用、同一页面框与执行回执；PDF draw 确定布局后，SVG 不再次
  运行 tight / constrained layout，结束时还原 Figure 状态和临时字体设置。
  `svg.fonttype="path"` 在导出上下文中显式设定，不能受脚本全局设置影响。
- 自动捕获和导出用 `require_built`，只读已经运行并接纳的热 Figure 或经过核验的冻结对。
  worker 冷了、会话过期、配对缺失都不能借导出重新执行脚本。动态 draw 回调、自定义 draw
  或不能证明配对稳定的格式化器不授予 SVG 配对资格；自动捕获在触发 draw 前拒绝。
- 静态 PDF 无 override 时，只有经接纳确认、绑定到**当前精确原件字节 hash** 的同源 SVG 才可独立授权。
  旁边有同名脚本 / SVG、存在预览或历史导出都不是证据。没有已验证表示时，SVG 逐格式
  失败并指出对应素材，PDF 等其他成功项仍交付；不丢面板、不用旧代替、不自动转位图。

导出与执行约束的权威细则见 [export-pipeline.md](../../../rules/backend/export-pipeline.md)。

## 2. 写入与安全合同

- 根元素 `width` / `height` 用物理 pt，`viewBox` 与页面同尺寸。IR 保持左下原点、y 向上，
  SVG 只在页面外层转换到顶原点；导入 SVG / 图片的源坐标单独归一化，再经已有
  `placement.place()` 落位，保留 crop、旋转、翻转和非零源 viewBox。
- 画图顺序就是 IR 列表顺序。clip 在组的局部空间生效；组 opacity 留在组上，不能逐个
  乘进子对象。图片内嵌 PNG / JPEG，TIFF 经已有解码与 PNG 编码路径，不改变像素网格。
- 文字读取已经排好的 gid / advance / offset / rise 和批准字体，以 fontTools 字形轮廓
  落笔，不再按字符串排一次。输出如实报告“文字转轮廓”，不声称可检索文字、字体嵌入或
  Office 可编辑文本框；缺字沿用同一张脸的 `.notdef` 并记账，不换系统字体。
- 每个导入实例单独加 ID 前缀，同步改写 `href` / `xlink:href` / `url(#id)`；样式表
  选择器限制在该实例内部，重复面板不能串用资源或污染其他对象。
- `svgcheck.py` 在导入和封口检查时共用严格、有预算的 XML / CSS 校验：拒绝 DTD、entities、
  脚本、事件、外部引用、远程字体、危险 CSS、重复 / 悬空 / 循环资源引用；内嵌图片也有预算。
  唯一例外是在可信 worker 生成边界、计算 hash **之前**移除精确匹配的 Matplotlib 标准
  惰性 DOCTYPE；任意来源的 DTD 不做清洗后放行。最终产物仍必须通过严格校验。
- 写入器再次核对 canonical 源与 SVG companion 各自的 hash。有限检查器只报告封口字节
  能证实的事实；轮廓文字的检索 / 字体嵌入结论不能被生产者声明伪装成 verified。

精确坐标、安全边界与事实口径见 [rendercore.md](../../../rules/backend/rendercore.md)。

## 3. 项目持久化与旧项目

文档面板保存明确的 `figureSnapshot`，闭集为
`{version, sourceId, patchHash, receiptId, snapshotId}`；导出请求通过 `figure_snapshot` 传同一引用。
保存时先冻结精确源 / patch 变体，再把引用与正文单次原子写入。冻结失败就清除本次待保存正文的旧引用，
返回来源 warning；不阻止布局保存、已成功的运行或正常 PDF 导出，也不触发脚本执行。
前端明确新运行成功、patch 或 source 改变时清引用，纯几何编辑保留；晚到的保存响应不能恢复已失效引用。

当前回执未知时，磁盘 current marker 与预览 metadata 不能作为执行证据。冷态文档有明确引用，
可以读取其保存版本，**即使只请求 PDF 也从该引用取同一版本**；当前进程已知新回执时，旧引用必须拒绝，
不能因新捕获失败而回退。静态无 override 的精确字节 hash 配对是独立授权路径，同样服从已知回执核对。
旧项目无引用时，执行源需要当前进程已经观察到的回执，或由用户明确再运行并通过接纳；不能猜造引用。

只有显式 `storage_root` 才将 `engine/svgstore.py` 的配对保存为项目
`tavottofile/figure_snapshots/` 资产，缺省数据目录存储仍有 TTL。项目身份可搬迁，复制完整项目目录时
一同携带；不可变 archive 的内容寻址包含完整来源 identity、回执身份与 generation、PDF/SVG 两份 hash。
新运行 `activate` 可以撤销旧 current lookup，但不会删历史 archive。项目资产无 TTL、不按容量驱逐；
预算 256 条是 current 与 archive 元数据合计，首次变体通常占两条，总字节预算 512 MiB、相同 blob 去重。
容量满只拒新增，保存过的 archive 与字节不动；保留历史版本不等于授权它冒充当前执行。
项目包打开仍只检视，不能借打开包自动安装素材。
持久化权威细则见 [layout-versions-and-documents.md](../../../rules/backend/layout-versions-and-documents.md)。

native 回执另以私有 `session_id` + 执行 / 屏障 epoch 区分执行实例，不改 wire generation，
也不泄漏 argv。同一 barrier 内稳定，成功 continue / released 和新 barrier 推进 epoch；
继续后直接结束也撤销旧 barrier 权威。实例与公开身份的区别见
[preparation-and-receipts.md](../../../rules/backend/preparation-and-receipts.md)。

## 4. 验证与未覆盖范围

先运行 Ruff 和字体准备，再执行：

```sh
.venv/bin/python -m pytest tests/test_rendercore_svgwriter.py tests/test_rendercore_svginspect.py tests/test_worker_svg_pair.py tests/test_svgstore.py tests/test_export_svg.py
.venv/bin/python -m pytest tests/test_rendercore_ir.py tests/test_rendercore_sources.py tests/test_rendercore_writer.py tests/test_rendercore_placement.py tests/test_rendercore_model.py tests/test_retirement_scan.py
.venv/bin/python -m pytest tests/test_export_request.py tests/test_execution_receipt.py tests/test_run_argv_e2e.py tests/test_foundation_facade_ledger.py
cd web && pnpm exec vitest run src/store/figureSnapshot.test.ts src/store/figureSnapshotInvalidation.test.ts src/store/projectSave.test.ts src/lib/autosave/diskWriter.test.ts
```

写入器测试有 Chromium 与 Inkscape 两条独立渲染路径，手算检查裁剪 / 旋转 / 翻转、
透明重叠与实例隔离，并与 canonical PDF 同几何对拍，不要求跨 renderer 抗锯齿逐像素相等。
缺包 / 缺字体 / 缺独立渲染器必须记为 not-run；`-k 'not chromium'` 只能说明其余测试通过。
本次开发环境已实际跑通 Inkscape 对拍；Chromium 因运行环境禁止进程 socket 未完成，
不能宣称浏览器资格全部通过。尚未做真实 Windows / macOS Word、PowerPoint、Excel
插入、保存重开和转 PDF 验证，不能据文件合法就承诺 Office 全特性兼容。

不包含画布 EPS、PDF/X、通用 PDF 逆向编辑、完整 PDF→SVG parser，或用户未授权的局部
栅格降级。这一扩展独立资格，不将未完成的兼容性验证计为核心已通过。

### 单格式原图与配对的区别

`original` 且仅请求 SVG、未携带明确快照引用时，已有、已接纳的热 Figure 直接调用一次原生 SVG 导出。
这一档不需要跨 PDF/SVG 后端配对，因此自定义 formatter / Artist 的正常 Matplotlib
单次导出仍可用；同样不允许重新执行脚本，不把单份 SVG 写成已验证 PDF 配对。
未编辑的静态 PDF 不因存在同名脚本或热 Figure 获得资格，仍须已有的 hash 绑定。
动态回调图的自动快照 / 跨格式或整页配对可能明确拒绝，保存响应及界面提示相关素材；
容量满或项目只读时同样报告提示，不把文档保存失败或假称快照已保存。
