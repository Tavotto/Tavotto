import { test, expect, type Page } from '@playwright/test'
import { createServer, type Server } from 'node:http'
import { existsSync, readFileSync } from 'node:fs'
import type { AddressInfo } from 'node:net'
import path from 'node:path'

/**
 * 浏览器 playground 的端到端证明（ADR 0007 的 Definition of Done）：
 *
 *     普通 .py → File API → Dedicated Worker → **真 Pyodide**（CDN 拉真包）
 *     → 真 matplotlib Figure → 真 FigState/manifest → 真 CanvasStage
 *     → 语义选中 → 真 override → Pyodide 重渲染 → undo → 原样
 *
 * 这套用例**不 mock Worker**，Pyodide 与 matplotlib 从钉死的 CDN 版本真实
 * 下载执行——它慢（冷缓存分钟级）、要联网，是有意的：这是 Phase II 存在
 * 与否的证明，放在专门的 e2e 里跑，不摊给每个小测试。
 *
 * 附带两条安全性质：
 *   * 哨兵测试：上传的源码内容**不出现在任何一个网络请求**里（隐私承诺
 *     「Tavotto 不上传你的代码」的机器可验证形式）；
 *   * 死循环：脚本阶段的硬超时把 Worker 杀掉并给出诚实的错误与出口。
 *
 * 前置：python scripts/build_browser_playground.py（产物在 web/dist-playground）。
 */

const DIST = path.resolve(import.meta.dirname, '..', 'dist-playground')

//: 冷缓存 + CDN 下载 + WASM 实例化，给足；见 §69——只放宽这一个 spec
test.describe.configure({ mode: 'serial' })
test.setTimeout(420_000)

let server: Server
let origin = ''

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.zip': 'application/zip',
  '.json': 'application/json; charset=utf-8',
}

test.beforeAll(async () => {
  test.skip(!existsSync(path.join(DIST, 'index.html')),
    '先跑 python scripts/build_browser_playground.py 生成 dist-playground')
  server = createServer((req, res) => {
    const pathname = new URL(req.url ?? '/', 'http://x').pathname
    const rel = pathname === '/' ? '/index.html' : pathname
    const file = path.join(DIST, rel)
    if (!file.startsWith(DIST) || !existsSync(file)) {
      res.writeHead(404).end('not found')
      return
    }
    res.writeHead(200, { 'Content-Type': MIME[path.extname(file)] ?? 'application/octet-stream' })
    res.end(readFileSync(file))
  })
  await new Promise<void>((ok) => server.listen(0, '127.0.0.1', ok))
  origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
})

test.afterAll(async () => {
  await new Promise<void>((ok) => server?.close(() => ok()))
})

/** 记录整页所有网络请求（URL + POST body），供哨兵断言。 */
function recordRequests(page: Page): { url: string; body: string }[] {
  const seen: { url: string; body: string }[] = []
  page.on('request', (req) => {
    seen.push({ url: req.url(), body: req.postData() ?? '' })
  })
  return seen
}

/** 等编辑器就位：权威 SVG 挂进画布。 */
async function waitForEditor(page: Page) {
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({
    timeout: 360_000,
  })
}

/** SVG 里一个 gid 元素的屏幕位置（图内元素命中层就铺在它上面）。 */
async function gidBox(page: Page, gid: string) {
  return page.evaluate((id) => {
    const n = document.querySelector(`[data-element-svg] svg [id="${id}"]`)
    if (!n) return null
    const r = (n as SVGGraphicsElement).getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  }, gid)
}

test('黄金路径：示例脚本 → 真 Pyodide → 语义拖动标题 → 重渲染 → 撤销还原', async ({ page }) => {
  const requests = recordRequests(page)
  await page.goto(`${origin}/?lang=zh`)

  // 空状态是 Tavotto 的样子：拖放区 + 示例 + 隐私说明
  await expect(page.getByText('拖入一个 Matplotlib 脚本')).toBeVisible()
  await expect(page.getByText(/不会把它上传到服务器/)).toBeVisible()

  // 选内置示例（真 Python 源码，经真 Pyodide 执行）
  await page.getByRole('button', { name: /折线图/ }).click()
  // 真话进度：阶段列表出现（不是一个空转的 spinner）
  await expect(page.getByText('加载 Python 运行时')).toBeVisible()

  await waitForEditor(page)

  // 源码证明：文件名 + 未改动（头部那颗 chip 的可访问名就是两者拼起来的）
  const sourceChip = page.getByRole('button', { name: /kinetics\.py · 未改动/ })
  await expect(sourceChip).toBeVisible()
  await expect(page.getByRole('button', { name: /0 条修改/ })).toBeVisible()

  // 语义元素真的在：标题 gid 落在权威 SVG 里
  const before = await gidBox(page, 'axes_0.title')
  expect(before, '权威 SVG 里应有 axes_0.title').not.toBeNull()

  // 用鼠标把标题拖走：语义选中 + 真 override（pos_frac）
  const cx = before!.x + before!.w / 2
  const cy = before!.y + before!.h / 2
  await page.mouse.move(cx, cy)
  await page.mouse.down()
  for (let i = 1; i <= 12; i++) await page.mouse.move(cx + i * 3, cy + i * 2)
  await page.mouse.up()

  // override 进了账本，Pyodide 重渲染回来的 SVG 里标题真的挪了
  await expect(page.getByRole('button', { name: /1 条修改/ })).toBeVisible({ timeout: 60_000 })
  await expect
    .poll(async () => (await gidBox(page, 'axes_0.title'))?.x ?? 0, { timeout: 60_000 })
    .toBeGreaterThan(before!.x + 10)

  // 技术视图展开的是**真实的 patch 表示**
  await page.getByRole('button', { name: /1 条修改/ }).click()
  await expect(page.getByText(/"prop":\s*"pos_frac"/)).toBeVisible()

  // 撤销 = 空 patch 列表全量重放 = 回到原样
  await page.getByRole('button', { name: '撤销' }).click()
  await expect(page.getByRole('button', { name: /0 条修改/ })).toBeVisible()
  await expect
    .poll(async () => (await gidBox(page, 'axes_0.title'))?.x ?? 0, { timeout: 60_000 })
    .toBeLessThan(before!.x + 5)

  // 源码仍然未改动；只读源码面板能打开
  await expect(sourceChip).toBeVisible()
  await sourceChip.click()
  await expect(page.getByRole('dialog').getByText('Reaction kinetics')).toBeVisible()
  await page.keyboard.press('Escape')

  // 整个流程只碰了两类地址：本页静态资源 + 钉死的 Pyodide CDN
  const outside = requests.filter(
    (r) => !r.url.startsWith(origin) && !r.url.startsWith('https://cdn.jsdelivr.net/pyodide/'),
  )
  expect(outside, `不该有别的外呼:\n${outside.map((r) => r.url).join('\n')}`).toEqual([])
})

test('哨兵：上传的源码内容不出现在任何网络请求里', async ({ page }) => {
  const SENTINEL = 'TAVOTTO_SENTINEL_7f3c9a1b'
  const src = [
    `# ${SENTINEL}`,
    'import matplotlib.pyplot as plt',
    `fig, ax = plt.subplots(figsize=(2.6, 2))`,
    `ax.plot([0, 1, 2], [1, 0, 2])`,
    `ax.set_title("${SENTINEL}")`,
    'fig.savefig("sentinel.pdf")',
    '',
  ].join('\n')

  const requests = recordRequests(page)
  await page.goto(`${origin}/?lang=zh`)
  await page.locator('input[type=file]').setInputFiles({
    name: 'sentinel_case.py',
    mimeType: 'text/x-python',
    buffer: Buffer.from(src, 'utf-8'),
  })
  await waitForEditor(page)
  // 图真的按源码画出来了：标题元素在权威 SVG 里（matplotlib 的 SVG 把文字
  // 序列化成字形路径，不是 <text>——所以按 gid 断言，不按文字内容）
  await expect
    .poll(async () => (await gidBox(page, 'axes_0.title')) != null, { timeout: 60_000 })
    .toBe(true)

  const leaked = requests.filter(
    (r) => r.url.includes(SENTINEL) || r.body.includes(SENTINEL),
  )
  expect(leaked, '源码内容绝不能出现在任何请求里').toEqual([])
  // 也不落进任何持久化存储（会话只活在内存里；localStorage 只有语言偏好）
  const stored = await page.evaluate(() => JSON.stringify(Object.entries(localStorage)))
  expect(stored).not.toContain(SENTINEL)
  // 顺带：除静态资源与 CDN 之外零外呼
  const outside = requests.filter(
    (r) => !r.url.startsWith(origin) && !r.url.startsWith('https://cdn.jsdelivr.net/pyodide/'),
  )
  expect(outside).toEqual([])
})

test('不支持的依赖：在下载科学栈之前拒绝，并给桌面版出口', async ({ page }) => {
  const requests = recordRequests(page)
  await page.goto(`${origin}/?lang=zh`)
  await page.locator('input[type=file]').setInputFiles({
    name: 'needs_rdkit.py',
    mimeType: 'text/x-python',
    buffer: Buffer.from('import rdkit\nimport matplotlib.pyplot as plt\n', 'utf-8'),
  })
  await expect(page.getByText(/浏览器 playground 里没有 rdkit/)).toBeVisible({
    timeout: 360_000,
  })
  await expect(page.getByRole('link', { name: /下载 Tavotto/ }).first()).toBeVisible()
  // 分类发生在包下载之前：matplotlib 的包一个都没拉
  const pkgFetches = requests.filter((r) => /matplotlib.*\.whl/.test(r.url))
  expect(pkgFetches).toEqual([])
})

test('死循环：脚本阶段硬超时，Worker 被杀，错误诚实', async ({ page }) => {
  await page.goto(`${origin}/?lang=zh`)
  await page.locator('input[type=file]').setInputFiles({
    name: 'spin.py',
    mimeType: 'text/x-python',
    buffer: Buffer.from('while True:\n    pass\n', 'utf-8'),
  })
  await expect(page.getByText(/超过了浏览器 playground 的时限/)).toBeVisible({
    timeout: 400_000,
  })
  await expect(page.getByRole('button', { name: '换一个脚本' })).toBeVisible()
})
