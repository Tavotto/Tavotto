import { defineConfig, devices } from '@playwright/test'

/**
 * Agent 的独立性能 runner。与标准 e2e 的必需 project 名单隔离，
 * 避免每次 PR 都跑长时间性能测试，也不打乱 CI 固定 shard。
 */
export default defineConfig({
  testDir: './e2e',
  testMatch: 'perf-agent.spec.ts',
  timeout: 360_000,
  expect: { timeout: 20_000 },
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    locale: 'zh-CN',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'webkit', use: { ...devices['Desktop Safari'] } },
  ],
})
