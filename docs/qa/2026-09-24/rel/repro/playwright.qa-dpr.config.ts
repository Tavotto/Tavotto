// QA 2026-09-24 REL-04：临时配置——几何/键盘/长标签关键流在 DPR 2（HiDPI）与英文长标签下各跑一遍。
// 基础配置没有 DPR≠1 的 project；这里只为 QA 取证，不进仓库配置。
// 注意：浏览器 DPR 模拟 ≠ 真实系统缩放 / 跨显示器拖动（spec §2 STATE-07），后者只能真机腿。
// 用法：cp docs/qa/2026-09-24/rel/repro/playwright.qa-dpr.config.ts web/ &&
//       cd web && npx playwright test --config=playwright.qa-dpr.config.ts
import base from './playwright.config'
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  ...base,
  projects: [
    {
      name: 'chromium-dpr2',
      use: { ...devices['Desktop Chrome HiDPI'], locale: 'zh-CN' },
      testMatch: [
        'fake-realtime.spec.ts',
        'twin-axes-pick.spec.ts',
        'keyboard-golden-path.spec.ts',
        'element-path-selection.spec.ts',
      ],
    },
    {
      name: 'webkit-dpr2-en',
      use: { ...devices['Desktop Safari'], deviceScaleFactor: 2, locale: 'en-US' },
      testMatch: ['inspector-overflow.spec.ts'],
    },
    {
      name: 'chromium-en-longlabels',
      use: { ...devices['Desktop Chrome'], locale: 'en-US' },
      testMatch: ['inspector-overflow.spec.ts', 'error-recovery-en.spec.ts'],
    },
  ],
})
