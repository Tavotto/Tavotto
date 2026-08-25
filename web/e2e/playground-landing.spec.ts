import { test, expect, type Page } from '@playwright/test'
import { createServer, type Server } from 'node:http'
import { existsSync, readFileSync } from 'node:fs'
import type { AddressInfo } from 'node:net'
import path from 'node:path'

/**
 * Playground 案例库首屏的布局与交互 e2e（§29.5 / §29.11 / §29.12）：
 * 响应式各视口、真鼠标拖放、reduced-motion 完整功能。
 *
 * 与 playground.spec.ts 分开：这里的用例**不等 Pyodide 跑完**（drop 后只断言
 * 进入真实加载态就收工），秒级完成，适合每次 PR 都跑。真执行链路归
 * playground.spec.ts 的黄金路径。
 *
 * 前置：python scripts/build_browser_playground.py（产物在 web/dist-playground）。
 */

const DIST = path.resolve(import.meta.dirname, '..', 'dist-playground')

let server: Server
let origin = ''

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.zip': 'application/zip',
  '.json': 'application/json; charset=utf-8',
  '.webp': 'image/webp',
}

test.beforeAll(async () => {
  test.skip(!existsSync(path.join(DIST, 'index.html')),
    '先跑 python scripts/build_browser_playground.py 生成 dist-playground')
  server = createServer((req, res) => {
    const pathname = new URL(req.url ?? '/', 'http://x').pathname
    const file = path.join(DIST, pathname === '/' ? '/index.html' : pathname)
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

const noHorizontalOverflow = async (page: Page) => {
  const overflow = await page.evaluate(() => ({
    doc: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    body: document.body.scrollWidth - document.body.clientWidth,
  }))
  expect(overflow.doc, '页面不许横向溢出').toBeLessThanOrEqual(1)
}

// ---------------------------------------------------------------- 响应式

const VIEWPORTS: [string, number, number][] = [
  ['1920x1080', 1920, 1080],
  ['1440x900', 1440, 900],
  ['1366x768', 1366, 768],
  ['1024x768', 1024, 768],
  ['768x1024', 768, 1024],
  ['390x844', 390, 844],
]

for (const [tag, width, height] of VIEWPORTS) {
  test(`响应式 ${tag}：卡片不溢出、入口可达、上传不抢主视觉`, async ({ page }) => {
    await page.setViewportSize({ width, height })
    await page.goto(`${origin}/?lang=zh`)

    // header 不溢出
    const header = page.locator('header').first()
    await expect(header).toBeVisible()
    await noHorizontalOverflow(page)

    // 三张案例卡都在文档里，且宽度装得进视口
    const cards = page.locator('[data-example-card]')
    await expect(cards).toHaveCount(3)
    for (let i = 0; i < 3; i++) {
      const box = await cards.nth(i).boundingBox()
      expect(box, `卡片 ${i} 应有布局`).not.toBeNull()
      expect(box!.width).toBeLessThanOrEqual(width)
      expect(box!.x).toBeGreaterThanOrEqual(0)
    }

    // 首屏视觉主角是案例库：主标题与第一张卡片在首屏内
    await expect(page.getByText('挑一张图，亲手改一次。')).toBeInViewport()
    await expect(cards.first()).toBeInViewport()

    if (width >= 640) {
      // 中央试验台存在（≥sm）
      await expect(page.getByText('把案例拖到这里')).toBeVisible()
    } else {
      // 手机不要求拖放：试验台退化为「选择一个案例开始」，点击是完整路径
      await expect(page.getByText('把案例拖到这里')).toBeHidden()
      await expect(page.getByText('选择一个案例开始')).toBeVisible()
    }

    // 「开始体验」按钮可达且触点尺寸不塌
    const start = cards.first().getByRole('button', { name: /开始体验|Start editing/ })
    await start.scrollIntoViewIfNeeded()
    const btnBox = await start.boundingBox()
    expect(btnBox!.height).toBeGreaterThanOrEqual(24)

    // 上传入口存在但不抢主视觉：在案例库之后（DOM 顺序即视觉顺序）
    const upload = page.getByText('已有一个独立脚本？')
    const uploadBox = await upload.boundingBox()
    const firstCardBox = await cards.first().boundingBox()
    expect(uploadBox!.y).toBeGreaterThan(firstCardBox!.y)
  })
}

test('英文 locale 无裸 key、无溢出', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 })
  await page.goto(`${origin}/?lang=en`)
  await expect(page.getByText('Pick a figure. Change it yourself.')).toBeVisible()
  await expect(page.getByText('Already have a standalone script?')).toBeVisible()
  // 裸 i18n key 的特征是 "playground.xxx" 直接出现在界面上
  const text = await page.locator('body').innerText()
  expect(text).not.toMatch(/playground\.[a-zA-Z]/)
  await noHorizontalOverflow(page)
})

// ---------------------------------------------------------------- 真鼠标拖放

test('拖放：卡片拖进试验台 → 高亮说出案例名 → 松开进入真实加载（只启动一次）', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`${origin}/?lang=zh`)

  const card = page.locator('[data-example-card="kinetics"]')
  const stage = page.locator('[data-stage-state]')
  const cardBox = (await card.boundingBox())!
  const stageBox = (await stage.boundingBox())!

  // 从卡片封面处拖起（避开按钮）
  await page.mouse.move(cardBox.x + cardBox.width / 2, cardBox.y + 40)
  await page.mouse.down()
  await page.mouse.move(cardBox.x + cardBox.width / 2 + 60, cardBox.y + 80, { steps: 4 })
  await expect(card).toHaveAttribute('data-dragging', 'true')
  await expect(stage).toHaveAttribute('data-stage-state', 'ready')

  // 进台：高亮 + 明确说出即将运行的案例名
  await page.mouse.move(stageBox.x + stageBox.width / 2, stageBox.y + stageBox.height / 2, {
    steps: 8,
  })
  await expect(stage).toHaveAttribute('data-stage-state', 'active')
  await expect(page.getByText('松开，运行「反应动力学」')).toBeVisible()

  // 松开 → 进入真实加载（阶段列表；不是假装已经进编辑器）
  await page.mouse.up()
  await expect(page.getByText('正在准备「反应动力学」')).toBeVisible()
  await expect(page.getByText('加载 Python 运行时')).toBeVisible()
})

test('拖放取消：拖出台面松开不启动，卡片回原位', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(`${origin}/?lang=zh`)

  const card = page.locator('[data-example-card="kinetics"]')
  const cardBox = (await card.boundingBox())!

  await page.mouse.move(cardBox.x + cardBox.width / 2, cardBox.y + 40)
  await page.mouse.down()
  await page.mouse.move(cardBox.x + cardBox.width / 2 + 80, cardBox.y + 120, { steps: 4 })
  await expect(card).toHaveAttribute('data-dragging', 'true')
  // 在台面外松开：不启动
  await page.mouse.up()
  await expect(card).not.toHaveAttribute('data-dragging', 'true')
  await expect(page.getByText('正在准备')).toHaveCount(0)
  await expect(page.getByText('挑一张图，亲手改一次。')).toBeVisible()
})

// ---------------------------------------------------------------- reduced motion

test.describe('prefers-reduced-motion', () => {
  test.use({ contextOptions: { reducedMotion: 'reduce' } })

  test('功能零删减：Code Sheet 直接展开、拖放靠边框与文字表达、正常进入加载', async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(`${origin}/?lang=zh`)

    // Code Sheet 直接打开（无翻转）且完整可用
    const card = page.locator('[data-example-card="kinetics"]')
    await card.getByRole('button', { name: '查看代码' }).click()
    const sheet = page.getByRole('dialog')
    await expect(sheet.getByText('kinetics.py').first()).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(sheet).toBeHidden()

    // 拖动：卡片**不位移不缩放**（无 inline transform），但拖动状态照样明确
    const cardBox = (await card.boundingBox())!
    const stage = page.locator('[data-stage-state]')
    const stageBox = (await stage.boundingBox())!
    await page.mouse.move(cardBox.x + cardBox.width / 2, cardBox.y + 40)
    await page.mouse.down()
    await page.mouse.move(stageBox.x + stageBox.width / 2, stageBox.y + stageBox.height / 2, {
      steps: 8,
    })
    await expect(card).toHaveAttribute('data-dragging', 'true')
    const transform = await card.evaluate((el) => el.style.transform)
    expect(transform, 'reduced-motion 下卡片不做位移/缩放').toBe('')
    await expect(stage).toHaveAttribute('data-stage-state', 'active')
    await expect(page.getByText('松开，运行「反应动力学」')).toBeVisible()

    // 功能完整：drop 照样进入真实加载
    await page.mouse.up()
    await expect(page.getByText('正在准备「反应动力学」')).toBeVisible()
  })
})
