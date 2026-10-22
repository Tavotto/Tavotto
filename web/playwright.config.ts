import { defineConfig, devices } from '@playwright/test'

/**
 * 「黄金路径」端到端：用真实浏览器操作真实界面，打的是**打包后的应用**。
 *
 * 为什么值得单独一套：这类问题（首次启动、中文路径、缺 Python、文件占用、
 * 端口冲突）在 jsdom 里一个都复现不了，而它们恰恰是「只在别人电脑上发生」
 * 的那一类。失败时留完整 trace，定位这种问题快得多。
 *
 * 服务端由 e2e/fixtures.ts 按用例各自拉起——每个场景要的环境不一样
 * （空的用户目录 / 中文路径 / 没有 Python / 端口被占），共用一个 webServer
 * 反而测不出东西。
 */
export default defineConfig({
  testDir: './e2e',
  // Agent 专用长时间性能基准由 playwright.perf.config.ts 启动，常规黄金路径不运行它。
  testIgnore: ['perf-agent.spec.ts'],
  // 每个用例都要起一次后端（含冷启动的 matplotlib 会话），给足时间
  timeout: 180_000,
  expect: { timeout: 15_000 },
  fullyParallel: false, // 端口与临时目录都是真实资源，串行更好排查
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [
    ...(process.env.CI
      ? ([['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]] as const)
      : ([['list']] as const)),
    // 功能登记表（ADR 0097）：posix-e2e 设了它就多写一份 JSON 报告，下一步
    // `scripts/ci/feature_registry.py verify-run` 据此判「登记的用例真的执行并通过」
    ...(process.env.TAVOTTO_E2E_JSON
      ? ([['json', { outputFile: process.env.TAVOTTO_E2E_JSON }]] as const)
      : []),
  ],
  use: {
    // 失败时留下完整操作轨迹：这类问题往往看一遍 trace 就明白了
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
    screenshot: 'only-on-failure',
    locale: 'zh-CN',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
      // error-recovery-en / preparation-card-en 自带 en-US locale、由 chromium-en 跑：
      // 基础 project 再跑一遍就是同一份内容双倍的串行启动与渲染等待
      testIgnore: ['error-recovery-en.spec.ts', 'preparation-card-en.spec.ts', 'perf-agent.spec.ts'],
    },
    // WebKit 是 macOS 桌面壳（WKWebView）与 Safari 用户的引擎（审计 P1-03）：
    // 只跑黄金路径与可访问性——全量跑三遍只是把门禁拉长三倍，而剩下那些
    // spec 测的是与引擎无关的后端行为。zh-CN：spec 里的可达名是中文。
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
      // twin-axes-pick 进这条腿的理由与上面同源：⌥ 点击轮换（issue #216）唯一
      // 没被量到的维度就是「另一个引擎里 altKey 到不到得了命中层」，那只有
      // 换引擎跑才回答得了。这一腿在 merge_group / full-ci 上真会执行
      // （ci.yml 的 windows-exe-smoke 第 2 片：`playwright install chromium webkit` +
      // `pnpm e2e --project=webkit --project=chromium-en`；第 1 片只跑 chromium。CI03c 起
      // 按 project 分两台机器，project 集合与 ci.yml 的 matrix 由
      // tests/test_merge_queue_workflows.py::TestPlaywrightShards 对拍，加 / 删 / 改名
      // 一个 project 就要回去改那张 matrix）。
      testMatch: [
        'golden-paths.spec.ts',
        'a11y.spec.ts',
        'keyboard-golden-path.spec.ts',
        'twin-axes-pick.spec.ts',
        // Tree pointer selection precedes default focus; verify the WebKit sequence and touch taps.
        'tree-selection-routing.spec.ts',
        // Translated panels must not trap transient spine text behind sibling artwork.
        'spine-feedback.spec.ts',
        // 性能探针（ADR 0075）量的就是用户那台 WKWebView：rAF / MessageChannel 的
        // 时序与合成 PointerEvent 在 WebKit 里成不成立，只有这一腿答得了
        'perf-probe.spec.ts',
        // 画布页签条的纵向溢出：只有 WebKit 把那 1px 画成一根常驻的竖滚动条（用户截图），
        // chromium 量得到数、画不出条——用户那台引擎必须亲自量一遍
        'canvas-tabs-scroll.spec.ts',
        // 排版时间线（ADR 0101）：节点缩略图在浏览器里用 canvas 合成——WKWebView 编不出
        // webp（退回 png）、画 SVG 会不会 taint 画布，只有这个引擎答得了
        'layout-timeline.spec.ts',
        // 时间线行的真实双击（前导 click 序列）与显式钮：WebKit 的事件序列要亲自量
        'timeline-row-actions.spec.ts',
        // 顶栏窄宽度下两两不重叠：量的是字宽，WebKit 的字体度量与 chromium 不同
        'topbar-narrow.spec.ts',
        // 树行键位与分栏手柄（UI 2.0 PR7）：Esc 回滚 / 双击复位靠 pointer capture 与 WebKit 的事件序列，
        // 选中加粗的字宽锁量的是 WKWebView 自己的字体度量
        'tree-list-refine.spec.ts',
        // 三步原生新手教程（ADR 0136）：卡片 / SVG 图例的指针拖动与 pointer capture、外部文件拖放的拒收
        // 都是引擎相关的事件序列，用户的 macOS 桌面壳就是这个引擎
        'tutorial.spec.ts',
        // 主页最近项目卡超长路径：旧 WebKit（Safari 17）里 button 的 flex-col 不拉伸子项致卡片溢出，只有这个引擎量得出
        'home-recent-card-layout.spec.ts',
      ],
    },
    // 英文 locale（审计 P1-02/P1-03）：a11y spec 是语言无关写法；
    // 英文的完整流程覆盖在 i18n.spec.ts（两种语言各走一遍）。
    {
      name: 'chromium-en',
      use: { ...devices['Desktop Chrome'], locale: 'en-US' },
      // inspector-overflow：英文标签更长，撑破的多半是它——两种语言各量一遍
      testMatch: [
        // 助手草稿与提交可靠性（ADR 0132）：状态说明是中英两套文案，定位走 data-*，两种语言各走一遍
        'ai-draft.spec.ts',
        // 运行中会话的恢复（PR4b）：占位与锁的文案是中英两套，定位走 data-*
        'ai-restore.spec.ts',
        'a11y.spec.ts',
        // 拖页面边（渐进式画布改版 裁决 5）：标注是中英两套文案（宽 / W），定位走 data-*，两种语言各走一遍
        'page-edge-resize.spec.ts',
        // 说明条 + 不推挤（UI 2.0 PR10）：构建不一致芯片不位移、刷新失败 Notice 顺着滑出；英文文案更长，两种语言各量
        'notice-no-push.spec.ts',
        'error-recovery-en.spec.ts',
        // 树与列表精修（UI 2.0 PR7）：英文标签更长（截断 / 字宽锁最容易出事），选择器全是稳定 data-*，两种语言各量一遍
        'tree-list-refine.spec.ts',
        // 准备引导卡（T13b）：英文文案同样一句话、一个主按钮，可达名与布局在英文下照样成立
        'preparation-card-en.spec.ts',
        // 改图助手输入框：英文胶囊 / 菜单说明更长，量弹层盒子在两种语言下都不被裁
        'ai-composer.spec.ts',
        'inspector-overflow.spec.ts',
        // 导出检查的计数芯片与逐条清单（2026-10-16 精修 03）：语言无关写法，英文标签更长，两种语言各量一遍
        'export-checks.spec.ts',
        // 同步修改 → 标准写回窗口（ADR 0037 末节）：语言无关写法，两种语言各走一遍
        'sync-overrides.spec.ts',
        // 导出对象头的「示意」标注与透明背景棋盘格（PR3）：英文标注更长，撑破对象头的话先在这里红
        'export-preview.spec.ts',
        // 素材卡 / 属性栏底部：英文文案更长，量盒子位置两种语言各一遍
        'inspector-assets.spec.ts',
        // 新手黄金路径（渐进式编辑 P06）：只认 data-* 锚点与落盘产物，不认显示文案——两种语言各走一遍
        'newcomer-golden-path.spec.ts',
      ],
    },
  ],
})
