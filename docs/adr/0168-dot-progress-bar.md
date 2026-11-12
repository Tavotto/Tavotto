# ADR 0168：点阵进度条（疏密表现填充，按物理像素画）

日期：2026-11-03 · 状态：**Accepted**（用户 2026-11-03 在截图页上逐轮拍板「2 号好」，给 1.0 豁免，「分两个 PR 做」）
相关：`docs/rules/frontend/ui-visual-discipline.md`（加载）；`docs/ux/DESIGN_CONSTITUTION.md` 第七节「加载」；
`docs/1.0-release-readiness.md` §1.8。

## 动机

用户给了一份 ▓░ 字符进度条（`CharProgress.tsx`），要把全应用的进度条（`ui/ProgressBar`，原来是铅笔排线）换成「点阵格子」。
看图过程中依次排除了：

1. **字形 ▓░**：每台机器落到的字体不同（这台 Mac 没装 SF Mono，写了也落到别的字体），12px 时糊成一片灰；Windows 上又是
   Consolas / 微软雅黑各画各的。
2. **「变黑」表现填充**（已完成用实心底挖孔，或换深色）：用户要的是**点一样大、同一个颜色，已完成只是点更密**。
3. **CSS 遮罩画点阵**：瓦片按 CSS 像素铺，Windows 125% / 175% 下一个点占 1.25 / 1.75 个物理像素，被抹成深浅不一的灰
   （真缩放 Chromium 截图量出 41%–79% 的着色像素是半灰）；两段间距不同，底边还不齐（用户指出「底端不对齐」）。

## 决定

### 图样（用户选的「2 号」）

- 未完成 = 每 4×4 个点径一个点，已完成 = 每 2×2 个点径一个点，横竖对齐排；点一样大、墨色一样（`ink-2`）。
- 条高 12px；纵向固定两格（稀点 3 行、密点 5 行），各屏幕行数一致。
- 不确定态：一段密点（整条的 1/8，至少一格）来回扫，80ms 一格；确定态变化时 18ms 一格补齐 / 退回。
  减少动态效果时都不动（不确定态那段停在正中）。百分比文字照旧由调用方写。

### 按物理像素画

- `ui/ProgressBar` 是一张 canvas，位图尺寸取 ResizeObserver 的 `devicePixelContentBoxSize`（Chromium / WebView2 有）；
  不支持的内核（WebKit）按 CSS 尺寸 × dpr 估，并听 `resolution` 媒体查询，窗口换屏时重量。
- 点 = 最接近实际倍率的整数个物理像素（100% / 125% 是 1，150%–200% 是 2）；间距 = 点 × 4 / 点 × 2——与 Mac 2× 上
  「点 2、间距 8 / 4」同一个比例，疏密与墨量在各档缩放下一致。
- 两段共用一张网格：列 / 行周期取两种间距的最小公倍数，点阵只铺「点 + 整数个周期」那一块并居中，四条边都落在点上，
  两段顶行、底行同高；分界吸附到周期上。
- 几何唯一出处 `web/src/lib/dotProgress.ts`（纯函数，`dotProgress.test.ts` 看护：整像素、四边对齐、比例、行数）；
  组件只量尺寸、跑动画、照着画。墨色读 canvas 自己计算后的 `color`，换主题（`useEffectiveTheme`）当场重画。

### 与 Mac 的差别

125% 下点是 1 个物理像素（Mac 是 2 个），屏幕上看起来略小；150% / 175% 与 Mac 同为 2 个。人眼看到的是物理尺寸：
常见 125% 屏幕（15.6 寸 1080p、24 寸 2K）一个像素约 0.18–0.21 mm，与 Retina 上 2 个像素（约 0.2 mm）接近。
要按屏幕真实 ppi 精确适配，见下面「第二步」。

## 第二步：按屏幕 ppi 选点径（2026-11-03，同一豁免）

按缩放倍数取整，在「中等 ppi 却开了 150% / 175%」的屏幕上点偏大（27 寸 4K 163 ppi、150% → 2 个物理像素 ≈ 0.31 mm）。
用户要求「根据不同的屏幕进行不同的适配」。

- 壳读主窗口所在屏幕的物理 ppi（`src-tauri/src/screen_ppi.rs`）：
  - Windows：`MonitorFromWindow` + `GetDpiForMonitor(MDT_RAW_DPI)`——显示器 EDID 报的真实 ppi，不是缩放后的有效 dpi。
  - macOS：`NSWindow.screen` 的 `NSScreenNumber` → `CGDisplayScreenSize` 毫米宽；像素宽取 Tauri 报的当前显示器物理
    像素宽（后备缓冲像素，正是 canvas 画的那套）。
  - 其他平台、读不到、读数不在 50–1000 之间：`None`。
- **只在主线程读**（窗口建好时、`WindowEvent::Moved` / `ScaleFactorChanged`），存进去重底账 `PpiCache`，真变了
  （差 ≥ 0.5 ppi，或有 / 无变了）才发 `tavotto:screen-ppi`（载荷 `{ppi}`）；命令 `screen_ppi` 只读底账、只给主窗口
  （`only_main_window` + 只在 `capabilities/main.json`）。远程实例窗口不给，退回按倍率取整。
- 前端 `lib/desktop.ts` 的 `getScreenPpi` / `onScreenPpiChange`（与壳同一合理范围）→ `lib/screenPpi.ts` 模块级一份
  （先听再读，回读不覆盖已到的事件值）→ `dotForPpi`：round(0.2 mm × ppi ÷ 25.4)，夹在 1–4 → `dotLayout` 的
  `dotOverride`。间距比例（×4 / ×2）不变。
- 0.2 mm 取自 Mac Retina 上「2 个物理像素」（14 寸 MacBook Pro 254 ppi）。常见屏幕：Retina 2、15.6 寸 1080p / 24 寸 2K
  （125%）1、27 寸 4K（150% / 175%）1（按倍率原是 2）。
- 看护：`screen_ppi.rs` 单测（合理范围、毫米换算、去重）；`tests/test_desktop_screen_ppi.py`（事件名与载荷、合理范围
  两侧同源、三处登记且只给主窗口、命令不在命令线程读屏、换屏 / 启动时重读）；`screenPpi.test.tsx`、`desktop.test.ts`。

## 不做

- 不用字体字形；不加字体文件。
- 不用颜色深浅表现进度。
- 画布冷启动那条 2px 扫条（PanelView 内，叠在图上）不是这个组件，不改。

## 1.0 纪律

纯前端视觉表现，不加端点、不改协议、不动文档与写回。用户 2026-11-03 给豁免（登记在 `docs/1.0-release-readiness.md` §1.8，
连同第二步的壳命令）。第二步新增的壳命令只读一个显示器参数，不碰文件、不联网。
