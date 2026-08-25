// Playground 截图工具：把 dist-playground 静态起起来，按视口 × 语言 × 状态
// 逐张截图，供 docs/ux/PLAYGROUND_V2.md 的前后对照用。
//
//   node scripts/capture-playground.mjs <输出目录> [--states idle,loading,editor]
//
// 默认只截 idle（快、不联网）；loading 会点一下主 CTA 立即截；editor 会等
// 真 Pyodide 跑完（联网、分钟级），只在 1440×900 zh 一档截。
import { createServer } from 'node:http'
import { readFileSync, existsSync, mkdirSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'

const DIST = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'dist-playground')
const outDir = path.resolve(process.argv[2] ?? 'playground-shots')
const states = (process.argv.find((a) => a.startsWith('--states='))?.split('=')[1] ?? 'idle').split(',')
mkdirSync(outDir, { recursive: true })

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.zip': 'application/zip',
  '.json': 'application/json; charset=utf-8',
  '.webp': 'image/webp',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
}

const server = createServer((req, res) => {
  const pathname = new URL(req.url ?? '/', 'http://x').pathname
  const file = path.join(DIST, pathname === '/' ? '/index.html' : pathname)
  if (!file.startsWith(DIST) || !existsSync(file)) return void res.writeHead(404).end()
  res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] ?? 'application/octet-stream' })
  res.end(readFileSync(file))
})
await new Promise((ok) => server.listen(0, '127.0.0.1', ok))
const origin = `http://127.0.0.1:${server.address().port}`

const VIEWPORTS = [
  ['1440x900', 1440, 900],
  ['1920x1080', 1920, 1080],
  ['1366x768', 1366, 768],
  ['1024x768', 1024, 768],
  ['768x1024', 768, 1024],
  ['390x844', 390, 844],
]

const browser = await chromium.launch()

async function shot(name, { width, height, lang = 'zh', reducedMotion = false, run }) {
  const ctx = await browser.newContext({
    viewport: { width, height },
    reducedMotion: reducedMotion ? 'reduce' : 'no-preference',
    deviceScaleFactor: 2,
  })
  const page = await ctx.newPage()
  await page.goto(`${origin}/?lang=${lang}`)
  await page.waitForLoadState('networkidle').catch(() => {})
  if (run) await run(page)
  await page.screenshot({ path: path.join(outDir, `${name}.png`) })
  await ctx.close()
  console.log(name)
}

if (states.includes('idle')) {
  for (const [tag, w, h] of VIEWPORTS) await shot(`idle-zh-${tag}`, { width: w, height: h })
  await shot('idle-en-1440x900', { width: 1440, height: 900, lang: 'en' })
  await shot('idle-zh-1440x900-reduced-motion', { width: 1440, height: 900, reducedMotion: true })
}

if (states.includes('loading')) {
  await shot('loading-zh-1440x900', {
    width: 1440,
    height: 900,
    run: async (page) => {
      const cta = page
        .getByRole('button', { name: /直接试一个示例|开始体验/ })
        .first()
      await cta.click()
      await page.getByText(/加载 Python 运行时|正在准备/).waitFor({ timeout: 15_000 })
      await page.waitForTimeout(600)
    },
  })
}

if (states.includes('editor')) {
  await shot('editor-zh-1440x900', {
    width: 1440,
    height: 900,
    run: async (page) => {
      const cta = page
        .getByRole('button', { name: /直接试一个示例|开始体验/ })
        .first()
      await cta.click()
      await page
        .locator('[data-element-svg] svg')
        .first()
        .waitFor({ timeout: 360_000 })
      await page.waitForTimeout(1200)
    },
  })
}

await browser.close()
server.close()
