import { defineConfig, devices } from '@playwright/test'

/**
 * Agent 的独立性能 runner。与标准 e2e 的必需 project 名单隔离，
 * 避免每次 PR 都跑长时间性能测试，也不打乱 CI 固定 shard。
 */
export default defineConfig({
  testDir: './e2e',
  // exec-trace-perf.spec.ts：执行记录 Waterfall 的大 Trace 性能预算（ADR 0151 PR3），本机 / Agent 按需跑
  testMatch: ['perf-agent.spec.ts', 'exec-trace-perf.spec.ts'],
  // Playwright 启动时会删除整个 outputDir。
  // 性能原始报告和 pending/failed summary 放在 web/test-results/perf-agent；
  // 这里必须独立，避免在长时间子进程执行中间抹掉 Agent 可读状态。
  outputDir: './test-results-perf-agent-playwright',
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
