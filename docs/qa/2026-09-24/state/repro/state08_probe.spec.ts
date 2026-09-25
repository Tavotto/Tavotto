/**
 * STATE-08 探针：提交 → 权威 → 撤销/重做 → 自动保存 → 整个后端进程（含 worker）退出 → 同端口、
 * 同数据目录重开，看文档是怎么回来的、界面停在哪。
 * 跑法：docs/qa/2026-09-24/state/repro/run.sh e2e-repro state08_probe.spec.ts
 */
import { execSync } from 'node:child_process'
import { cpSync, mkdtempSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, freePort, test } from './fixtures'

const REPO = path.resolve(import.meta.dirname, '..', '..')
const locate = (page: Page, id: string) =>
  page.evaluate((gid) => {
    const n = document.querySelector(`[data-element-svg] [id="${gid}"]`) as SVGGraphicsElement | null
    const svg = document.querySelector('[data-element-svg] svg') as SVGGraphicsElement | null
    if (!n || !svg) return null
    const r = n.getBoundingClientRect()
    const s = svg.getBoundingClientRect()
    return { x: r.x + r.width / 2, y: r.y + r.height / 2, rx: (r.x + r.width / 2 - s.x) / s.width, ry: (r.y + r.height / 2 - s.y) / s.height, sw: s.width }
  }, id)

test('probe STATE-08', async ({ app, page }) => {
  test.setTimeout(480_000)
  const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-qa-state08-'))
  const figures = path.join(root, 'figures')
  cpSync(path.join(REPO, 'examples', 'figures'), figures, { recursive: true })
  const env = { TAVOTTO_DATA_DIR: path.join(root, 'data'), TAVOTTO_CONFIG_DIR: path.join(root, 'config') }
  const port = await freePort()
  const renders: string[] = []
  const saves: { url: string; body: string }[] = []
  page.on('request', (r) => {
    if (r.url().includes('/api/engine/render')) renders.push(r.postData() ?? '')
    if (r.url().includes('/api/autosave/') && r.method() === 'PUT') saves.push({ url: r.url(), body: r.postData() ?? '' })
  })
  const a1 = await app({ figures, port, env })
  await page.goto(a1.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(2500)
  const p0 = await locate(page, 'axes_0.title')
  await page.mouse.move(p0!.x, p0!.y)
  await page.mouse.down()
  for (let i = 1; i <= 20; i++) await page.mouse.move(p0!.x + i * 2.5, p0!.y + i)
  await page.mouse.up()
  await page.waitForTimeout(2500)
  const p1 = await locate(page, 'axes_0.title')
  await page.keyboard.press('Control+z')
  await page.waitForTimeout(1500)
  const pUndo = await locate(page, 'axes_0.title')
  await page.keyboard.press('Control+Shift+z')
  await page.waitForTimeout(2500)
  const pRedo = await locate(page, 'axes_0.title')
  const lastSave = saves.at(-1)
  const beforeUrl = page.url()
  const ls = await page.evaluate(() => Object.keys(localStorage))
  // 整个后端退出（worker 是它的子进程）
  await fetch(`${a1.baseURL}/api/shutdown`, { method: 'POST' }).catch(() => {})
  for (let i = 0; i < 40 && a1.proc.exitCode === null; i++) await new Promise((r) => setTimeout(r, 250))
  const exit1 = a1.proc.exitCode
  // 端口真的放出来了吗：谁还在 LISTEN
  let holders = ''
  for (let i = 0; i < 40; i++) {
    try {
      holders = execSync(`lsof -nP -iTCP:${port} -sTCP:LISTEN || true`).toString()
    } catch {
      holders = ''
    }
    if (!holders.trim()) break
    await new Promise((r) => setTimeout(r, 500))
  }
  console.log(JSON.stringify({ probe: 'state08-port', exit1, port, holdersAfter20s: holders }))
  // 产品的 port_is_free() 用不带 SO_REUSEADDR 的 bind 判空闲：上一个实例留下的 TIME_WAIT
  // 会让它顺延到下一个端口（换了源 → localStorage 里的「上次文档」就找不到了）。
  // 这里用同一种 bind 等到端口真的可用，量出要等多久。
  const t0 = Date.now()
  for (;;) {
    try {
      execSync(`python3 -c "import socket;s=socket.socket();s.bind(('127.0.0.1',${port}))"`, { stdio: 'ignore' })
      break
    } catch {
      if (Date.now() - t0 > 150_000) break
      await new Promise((r) => setTimeout(r, 1000))
    }
  }
  console.log(JSON.stringify({ probe: 'state08-timewait', waitedMs: Date.now() - t0 }))
  const a2 = await app({ figures, port, env })
  renders.length = 0
  await page.goto(a2.baseURL)
  await page.waitForTimeout(8000)
  const svgCount = await page.locator('[data-element-svg] svg').count()
  const pReopen = svgCount ? await locate(page, 'axes_0.title') : null
  const docId = lastSave ? decodeURIComponent(lastSave.url.split('/api/autosave/')[1].split('?')[0]) : null
  const reread = docId ? await (await page.request.get(`${a2.baseURL}/api/autosave/${encodeURIComponent(docId)}`)).text() : null
  console.log(JSON.stringify({ probe: 'state08', p0, p1, pUndo, pRedo, exit1, beforeUrl, afterUrl: page.url(), ls, svgCount, pReopen, docId, lastSaveBody: lastSave?.body.slice(0, 600), reread: reread?.slice(0, 600), rendersAfterReopen: renders.map((r) => r.slice(0, 300)) }))
})
