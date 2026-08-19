# Tavotto v2 — 前端工作台

论文 Figure 排版 + 参数化图表编辑的浏览器工作台。Vite + React 19 + TypeScript +
Tailwind v4 + Radix + Zustand/Immer。

## 启动

后端（Flask，端口 5089）需先跑起来：

```bash
cd .. && ./run.sh          # 或 python app.py
```

前端：

```bash
pnpm install
pnpm dev                   # http://localhost:5173
```

`/api` 与 `/exports` 由 vite dev server 代理到 `http://127.0.0.1:5089`（见
`vite.config.ts`）。

```bash
pnpm build                 # tsc -b && vite build → dist/
pnpm exec tsc -b           # 只做类型检查
pnpm lint                  # oxlint
```

## 目录

```
src/
  canvas/        画布：视口、世界层、各对象视图、屏幕坐标覆盖层、标尺、交互
    interactions.ts   所有指针拖动逻辑（移动/缩放/端点/框选/平移/绘制/参考线/裁剪/图内拖动）
    PanelView.tsx     面板显示：PNG ↔ 引擎 SVG 切换、裁剪、图内命中层、渲染状态角标
  components/    App Shell：顶栏、左侧栏（素材/图层）、Inspector（检查器 | AI）、状态栏、对话框
    inspector/   ElementInspector 由 manifest 动态生成表单，前端不硬编码 matplotlib 属性
    ai/          AiPanel 流式对话 + DiffView（unified diff，可放大到对话框）
    ui/          自绘的 shadcn 风格基元（Button/Input/Select/Dialog/Menu/…）
  store/         Zustand：document(唯一进 undo)/selection/viewport/asset/ui/interaction/render/ai
  hooks/         useEngineSync（引擎渲染的唯一驱动点）、useServerEvents（SSE）、useKeyboard
  lib/           单位换算、几何（吸附/对齐/分布）、API、v1 布局迁移
  types/         文档模型（schema 2）
```

## 关键约定

- **单位一律 mm**，`objects` 数组顺序即 z 序（末尾在最上）。
- **世界坐标**：1mm = `BASE_PX_PER_MM`(96dpi) 世界像素，`scale(zoom)` 是唯一世界变换；
  选择框 / 手柄 / 参考线画在屏幕坐标的 `OverlaySvg` 上，任何缩放下线宽恒为 1px。
- **撤销**：只有 `documentStore` 进历史，用 Immer `produceWithPatches`；拖动类操作
  pointerdown 开事务、pointerup 合并成一条（上限 200 条）。文字自适应高度走
  `silent()` 不进历史。
- **字体分离**：UI 用系统字体，画布内文字对象用 `--font-doc`
  (`"Times New Roman", "Songti SC", serif`)。
- **面板取图**：矢量走 `/api/render?w=<bucket>` 分档 PNG（档位只升不降），位图走
  `/api/file`。裁剪用「放大的 img + overflow hidden」实现，不改源文件。
- **布局兼容**：`/api/layouts` 读到 v1 结构时由 `lib/migrate.ts` 转成 schema 2；
  保存一律写 schema 2。

## 图内元素编辑（Phase 4）

双击 ⚡ 面板（或 Inspector 里「编辑图内元素」）进入。进入后面板从 PNG 换成引擎
出的内联 SVG，所见即所得；退出时若该面板有 override 则继续用 SVG 显示。

- **命中测试**只用 `manifest.bbox`（面积小者优先、axes 降权、0.4% 容差），
  不依赖 SVG 内部结构，换 matplotlib 版本也不会失效。
- **表单**完全由 `manifest.elements[].editable` 驱动（text/number/color/bool/enum/
  pair/rect 七种类型），前端只维护一张 prop → 中文名的映射表。
- **override 存在 PanelObject 上**，因此天然进 undo history。
  `hooks/useEngineSync.ts` 是唯一的渲染驱动点：只要「文档里的 overrides」与
  「已渲染的 patches」不一致就重渲染 —— 编辑、撤销/重做、AI 改脚本、文件变更
  全部走同一条路径，各处不需要自己触发渲染。
- **合流**：文字/数值防抖 300ms，颜色/开关/枚举/拖动结束立即；渲染中再来请求
  只保留最后一次（`renderStore` 的 busy/queued）。
- **拖动**先直接平移 SVG 里对应的 `<g id="gid">` 做乐观预览，松手才写 override。
- 失败保留旧 SVG 并展示可折叠 traceback；`panel.file_changed` 把相关面板标 stale。

## AI 改脚本（Phase 5）

右栏 AI 标签页。选中 ⚡ 面板（和可选的图内元素）后用自然语言描述改动，
`POST /api/ai/run` 拉起 codex / claude 直接改产出该图的 matplotlib 脚本，
输出经 SSE `ai.delta` 逐行流式回显；结束后如脚本有变化则显示 unified diff
（可放大到对话框）与「回滚此次修改」。

改动落盘后 watcher 会作废渲染会话，前端据此重建图表；回滚同理。
