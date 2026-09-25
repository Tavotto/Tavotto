// QA 2026-09-24 REL-04：隔离 webkit-dpr2-en 上 inspector-overflow 的红——是 WebKit、DPR 还是语言？
// 用法同 playwright.qa-dpr.config.ts（拷到 web/ 下跑，跑完删）。
import base from './playwright.config'
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  ...base,
  projects: [
    {
      name: 'webkit-dpr1-en',
      use: { ...devices['Desktop Safari'], deviceScaleFactor: 1, locale: 'en-US' },
      testMatch: ['inspector-overflow.spec.ts'],
    },
    {
      name: 'webkit-dpr1-zh',
      use: { ...devices['Desktop Safari'], deviceScaleFactor: 1, locale: 'zh-CN' },
      testMatch: ['inspector-overflow.spec.ts'],
    },
    {
      name: 'chromium-dpr2-en',
      use: { ...devices['Desktop Chrome HiDPI'], locale: 'en-US' },
      testMatch: ['inspector-overflow.spec.ts'],
    },
  ],
})
