// QA 2026-09-24 REL-01：临时配置——把几何关键流（fake-realtime / 路径选中 / 右键多选对齐）
// 也放到 WebKit 上跑。基础配置的 webkit project 刻意不含它们；这里只为 QA 取证，不进仓库配置。
// 用法：cp docs/qa/2026-09-24/rel/repro/playwright.qa-webkit.config.ts web/ &&
//       cd web && npx playwright test --config=playwright.qa-webkit.config.ts
//       （跑完删掉 web/playwright.qa-webkit.config.ts）
import base from './playwright.config'
import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  ...base,
  projects: [
    {
      name: 'webkit-qa-geometry',
      use: { ...devices['Desktop Safari'], locale: 'zh-CN' },
      testMatch: ['fake-realtime.spec.ts', 'element-path-selection.spec.ts', 'quick-menu.spec.ts'],
    },
  ],
})
