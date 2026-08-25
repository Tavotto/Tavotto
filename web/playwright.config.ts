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
  // 每个用例都要起一次后端（含冷启动的 matplotlib 会话），给足时间
  timeout: 180_000,
  expect: { timeout: 15_000 },
  fullyParallel: false, // 端口与临时目录都是真实资源，串行更好排查
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]]
    : [['list']],
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
      // error-recovery-en 自带 en-US locale、由 chromium-en 跑：
      // 基础 project 再跑一遍就是同一份内容双倍的串行启动与渲染等待
      testIgnore: ['error-recovery-en.spec.ts'],
    },
    // WebKit 是 macOS 桌面壳（WKWebView）与 Safari 用户的引擎（审计 P1-03）：
    // 只跑黄金路径与可访问性——全量跑三遍只是把门禁拉长三倍，而剩下那些
    // spec 测的是与引擎无关的后端行为。zh-CN：spec 里的可达名是中文。
    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
      testMatch: ['golden-paths.spec.ts', 'a11y.spec.ts'],
    },
    // 英文 locale（审计 P1-02/P1-03）：a11y spec 是语言无关写法；
    // 英文的完整流程覆盖在 i18n.spec.ts（两种语言各走一遍）。
    {
      name: 'chromium-en',
      use: { ...devices['Desktop Chrome'], locale: 'en-US' },
      testMatch: ['a11y.spec.ts', 'error-recovery-en.spec.ts'],
    },
  ],
})
