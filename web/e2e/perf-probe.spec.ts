import { readdirSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { expect, test } from './fixtures'

/**
 * 性能探针（ADR 0075）：**真浏览器里**走一遍用户会走的整条路。
 *
 * jsdom 没有真实的帧：rAF 间隔、「渲染结束后的第一个任务」（MessageChannel）
 * 在那里都量不到，单测只能验记账逻辑。这条用例验的是 jsdom 验不了的三件事：
 *
 *   1. 帧行的「主线程渲染」列在真浏览器里**真的有值**（量渲染的那一招生效了）；
 *   2. 自动测试按两轮（m1 / m3）跑完，拖动期间与之后**一次后端渲染都不发**、
 *      被拖元素的 transform 原样复原——它跑在用户的真实文档上，不许留下改动；
 *   3. 「完成并保存」交出的文件是 `tavotto-perf-probe/1`、只有数字（不含文件名），
 *      并且经后端写进了数据目录（桌面壳里浏览器式下载会被取消）。
 */
test('性能探针：录制真实拖动 + 自动测试 + 保存报告', async ({ app, page }) => {
  test.setTimeout(180_000)
  const a = await app()
  const renders: string[] = []
  page.on('request', (req) => {
    if (req.url().includes('/api/engine/render')) renders.push(req.url())
  })

  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  const svgWrap = page.locator('[data-element-svg]').first()
  await expect(svgWrap.locator('svg')).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(1500)

  // 设置 → 诊断 → 性能分析 → 开始：设置关掉、探针面板挂出来
  await page.locator('[data-rail="settings"]').click()
  await page.locator('[data-section="diagnostics"]').click()
  await page.locator('[data-perf-probe-start]').click()
  const hud = page.locator('[data-perf-probe]')
  await expect(hud).toHaveAttribute('data-phase', 'recording')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  // 空闲基线：探针在第一次拖动之前采这台机器空转时的帧
  await page.waitForTimeout(1200)

  const target = await page.evaluate(() => {
    const svg = document.querySelector('[data-element-svg] svg')
    for (const id of ['axes_0.title', 'axes_0.xlabel', 'axes_0.ylabel']) {
      const n = svg?.querySelector(`[id="${id}"]`)
      if (!n) continue
      const r = (n as SVGGraphicsElement).getBoundingClientRect()
      if (r.width > 2 && r.height > 2) {
        return { id, x: r.x + r.width / 2, y: r.y + r.height / 2, transform: n.getAttribute('transform') }
      }
    }
    return null
  })
  expect(target, '图里应当至少有一个可拖的文字元素').not.toBeNull()

  // 自动测试：选点 → 两轮合成拖动 → 回到录制态
  renders.length = 0
  await hud.locator('[data-perf-action="runTest"]').click()
  await expect(hud).toHaveAttribute('data-phase', 'picking')
  await page.mouse.click(target!.x, target!.y)
  await expect(hud).toHaveAttribute('data-phase', 'running', { timeout: 5_000 })
  await expect(hud).toHaveAttribute('data-phase', 'recording', { timeout: 30_000 })
  await page.waitForTimeout(800)
  expect(renders, `自动测试不许惊动后端，实际 ${renders.length} 次`).toHaveLength(0)
  const after = await page.evaluate(
    (id) => document.querySelector(`[data-element-svg] [id="${id}"]`)?.getAttribute('transform') ?? null,
    target!.id,
  )
  expect(after, '自动测试结束后被拖元素的 transform 必须复原').toBe(target!.transform)

  // 保存：浏览器模式另给一份下载
  const dl = page.waitForEvent('download')
  await hud.locator('[data-perf-action="finish"]').click()
  const file = await (await dl).path()
  await expect(hud).toHaveAttribute('data-phase', 'done')
  // 同一份也经后端写进了数据目录：桌面壳里 <a download> 会被取消，那才是用户拿得到的那份
  const saved = readdirSync(path.join(a.dataDir, 'perf-reports'))
  expect(saved).toHaveLength(1)
  expect(readFileSync(path.join(a.dataDir, 'perf-reports', saved[0]), 'utf8')).toBe(
    readFileSync(file!, 'utf8'),
  )
  const raw = readFileSync(file!, 'utf8')
  const report = JSON.parse(raw)
  expect(report.schema).toBe('tavotto-perf-probe/1')
  expect(raw).not.toContain('Fig1_kinetics')

  const synth = report.segments.filter((s: { source: string }) => s.source === 'synthetic')
  const labels = synth.map((s: { label: string }) => s.label)
  expect(labels).toEqual(expect.arrayContaining(['m1', 'm3']))
  for (const s of synth) {
    expect(s.kind).toBe('element')
    expect(s.frames.length, `${s.label} 应当跑满约 3 秒的帧`).toBeGreaterThan(60)
    // 主线程渲染列：真浏览器里 MessageChannel 在渲染之后到达，绝大多数帧有值
    const withRender = s.frames.filter((f: (number | null)[]) => typeof f[1] === 'number').length
    expect(withRender / s.frames.length).toBeGreaterThan(0.8)
    expect(s.spans['raf.preview_write']?.count ?? 0).toBeGreaterThan(0)
  }
  const m3 = synth.find((s: { label: string }) => s.label === 'm3')
  const mpf = m3.moves / m3.frames.length
  expect(mpf, 'm3 每帧应当约有 3 个 pointermove').toBeGreaterThan(2)
  console.log(`[e2e 性能探针] 报告 ${raw.length} 字节 · 片段 ${report.segments.length} · m3 每帧 ${mpf.toFixed(1)} 个 move`)
  if (process.env.TAVOTTO_PERF_REPORT_OUT) {
    const { writeFileSync } = await import('node:fs')
    writeFileSync(process.env.TAVOTTO_PERF_REPORT_OUT, raw)
  }
})
