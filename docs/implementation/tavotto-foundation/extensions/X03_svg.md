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
- 静态 PDF 只有经接纳确认、绑定到该原件字节 hash 的同源 SVG 才可使用。
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

全质量配对产物由 `engine/svgstore.py` 保存到项目的 `tavottofile/figure_snapshots/`，
是项目资产，不是预览缓存，也不是依赖原机器数据目录的临时结果。复制完整项目目录时
一同携带；读取重新核对来源、patch、输入改指、当前执行代、回执与两份字节身份。
新一次明确运行会推进当前代标记，旧 patch 变体不能冒充新一代 Figure。

已有项目资产不按 TTL 到期，不为给新资产腾空间而自动驱逐；超出受控预算拒绝新增。
保存布局时尽力冻结可用的精确编辑变体，缺表示不阻止用户保存布局，也不触发脚本执行。
项目包打开仍只做检视，不能借打开包自动安装素材。
持久化权威细则见 [layout-versions-and-documents.md](../../../rules/backend/layout-versions-and-documents.md)。

旧项目只有布局 / PDF / 预览、没有已验证配对时，打开或点击导出不能凭空重建 SVG。
用户须明确重新运行并通过 Figure 接纳与配对检查；在此之前该来源的 SVG 保持不可用。

## 4. 验证与未覆盖范围

先运行 Ruff 和字体准备，再执行：

```sh
.venv/bin/python -m pytest tests/test_rendercore_svgwriter.py tests/test_rendercore_svginspect.py tests/test_worker_svg_pair.py tests/test_svgstore.py tests/test_export_svg.py
.venv/bin/python -m pytest tests/test_rendercore_ir.py tests/test_rendercore_sources.py tests/test_rendercore_writer.py tests/test_rendercore_placement.py tests/test_rendercore_model.py tests/test_retirement_scan.py
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

`original` 且仅请求 SVG 时，已有、已接纳的热 Figure 直接调用一次原生 SVG 导出。
这一档不需要跨 PDF/SVG 后端配对，因此自定义 formatter / Artist 的正常 Matplotlib
单次导出仍可用；同样不允许重新执行脚本，不把单份 SVG 写成已验证 PDF 配对。
未编辑的静态 PDF 不因存在同名脚本或热 Figure 获得资格，仍须已有的 hash 绑定。
动态回调图的自动快照 / 跨格式或整页配对可能明确拒绝，保存响应及界面提示相关素材；
容量满或项目只读时同样报告提示，不把文档保存失败或假称快照已保存。
