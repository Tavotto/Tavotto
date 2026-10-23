import AxeBuilder from '@axe-core/playwright'
import net from 'node:net'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { expect, test } from './fixtures'
import { horizontalOffenders } from './overflow'

/**
 * 「联系作者」（ADR 0145）——真浏览器 + 真引擎，量 jsdom 与模拟服务端量不到的几件事：
 *
 *   1. **硬开关**：`TAVOTTO_NO_CONTACT=1`（fixtures 的默认）的引擎上，主页没有入口，本机端点只回 `{enabled:false}`；
 *   2. **打开后，点发送之前零连接**：引擎指向一个只数 TCP 连接的回环端口；打开窗口、写字、勾选、关窗再开，连接数始终是 0；
 *      点「发送」才出现第一条连接。对端不是 TLS 服务，所以发送以网络类错误失败——正是要验的「失败有人话 + 草稿还在 + 只有复制」；
 *   3. 入口在真布局里的位置：主页右上角、编辑器顶栏撤销重做与导出之间；窗口窄于 900 只剩图标；窗口内不横向溢出、axe 无 critical / serious。
 *
 * 服务端契约对账（选路、换线路、附件、令牌落盘、回信轮询）由 `tests/test_contact_send.py` 钉。
 */
const SHOTS = process.env.TAVOTTO_E2E_SHOTS

test('硬开关：主页没有入口，本机端点只回 enabled:false', async ({ app, page }) => {
  const a = await app({ noProject: true })
  await page.goto(a.baseURL)
  await expect(page.locator('[data-home-variant]')).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('[data-contact-button]')).toHaveCount(0)
  const cap = await page.request.get(`${a.baseURL}/api/contact`)
  expect(await cap.json()).toEqual({ enabled: false })
  expect((await page.request.get(`${a.baseURL}/api/contact/messages`)).status()).toBe(404)
})

test('主页：入口在右上角；点发送前零连接；失败有人话、草稿还在、只有复制（没有邮件链接）', { tag: '@feature:contact.maker' }, async ({ app, page }) => {
  test.setTimeout(240_000)
  let connections = 0
  const sink = net.createServer((sock) => {
    connections += 1
    sock.destroy()
  })
  await new Promise<void>((r) => sink.listen(0, '127.0.0.1', r))
  const port = (sink.address() as net.AddressInfo).port
  try {
    const a = await app({
      noProject: true,
      env: {
        TAVOTTO_NO_CONTACT: '0', // 摘掉 fixtures 的硬关；目的地只能是下面的回环端点（误配不回退生产）
        TAVOTTO_DIAG_DEV_LOOPBACK: '1',
        TAVOTTO_CONTACT_ENDPOINT: `https://127.0.0.1:${port}`,
      },
    })
    await page.setViewportSize({ width: 1280, height: 860 })
    await page.goto(a.baseURL)

    // 主页：右上角有入口（和左上角的应用菜单位置对称：在页面的右半边、靠上）
    const home = page.locator('[data-contact-button="home"]')
    await expect(home).toBeVisible({ timeout: 30_000 })
    const box = (await home.boundingBox())!
    const vw = page.viewportSize()!.width
    expect(vw - (box.x + box.width)).toBeLessThanOrEqual(24) // 窗口右上角，不是内容列的右缘
    expect(box.y).toBeLessThan(24)
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'home.png') })

    await home.click()
    const dialog = page.locator('[data-dialog="contact"]')
    await expect(dialog).toBeVisible()
    await expect(dialog.locator('[data-contact-recipient]')).toHaveCount(0)
    await dialog.locator('[data-contact-subject]').fill('e2e')
    await dialog.locator('[data-contact-text]').fill('e2e: nothing private here')
    await dialog.locator('[data-contact-email]').fill('someone@example.org')
    await dialog.locator('[data-contact-file-input]').setInputFiles({
      name: 'shot.png',
      mimeType: 'image/png',
      buffer: Buffer.from('89504e470d0a1a0a00000000', 'hex'),
    })
    await expect(dialog.locator('[data-contact-file="shot.png"]')).toBeVisible()
    expect(connections).toBe(0)
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'dialog-edit.png') })

    // 真布局：窗口内不横向溢出，axe 无 critical / serious
    expect(await horizontalOffenders(page, '[data-dialog="contact"]')).toEqual([])
    const axe = await new AxeBuilder({ page }).include('[data-dialog="contact"]').analyze()
    const bad = axe.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious')
    expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([])

    // 关窗再开：草稿还在，连接仍然是 0
    await dialog.locator('[data-contact-dismiss]').click()
    await expect(dialog).toHaveCount(0)
    await home.click()
    await expect(dialog.locator('[data-contact-text]')).toHaveValue('e2e: nothing private here')
    expect(connections).toBe(0)

    // 点发送：这才出现第一条连接；失败有人话，草稿在，只有复制
    await dialog.locator('[data-contact-send]').click()
    await expect(dialog.locator('[data-contact-status="failed"]')).toBeVisible({ timeout: 60_000 })
    expect(connections).toBeGreaterThan(0)
    await expect(dialog.locator('[data-contact-text]')).toHaveValue('e2e: nothing private here')
    await expect(dialog.locator('[data-contact-fallback] button')).toHaveCount(2)
    expect(await page.locator('a[href^="mailto" i]').count()).toBe(0)
    expect((await page.content()).toLowerCase()).not.toContain('mailto')
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'dialog-failed.png') })
    await dialog.locator('[data-contact-dismiss]').click()

  } finally {
    sink.close()
  }
})

test('编辑器顶栏：入口在撤销重做与导出之间；窄于 900 只剩图标', async ({ app, page }) => {
  test.setTimeout(240_000)
  const a = await app({
    env: {
      TAVOTTO_NO_CONTACT: '0',
      TAVOTTO_DIAG_DEV_LOOPBACK: '1',
      TAVOTTO_CONTACT_ENDPOINT: 'https://127.0.0.1:9', // 不会被连：这个用例不点发送
    },
  })
  await page.setViewportSize({ width: 1280, height: 860 })
  await page.goto(a.baseURL)
    // 编辑器顶栏：打开项目后，入口在撤销重做与导出之间；窄于 900 只剩图标
  const top = page.locator('[data-topbar] [data-contact-button="topbar"]')
  await expect(top).toBeVisible({ timeout: 60_000 })
  const exportBox = (await page.locator('[data-onboarding-anchor="export"]').boundingBox())!
  const topBox = (await top.boundingBox())!
  expect(topBox.x + topBox.width).toBeLessThanOrEqual(exportBox.x)
  await expect(top).toContainText('联系作者')
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'topbar.png') })
  await page.setViewportSize({ width: 800, height: 700 })
  const narrow = (await top.boundingBox())!
  expect(narrow.width).toBeLessThan(48) // 只剩图标
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'topbar-narrow.png') })
})

test('窄窗口：从已打开项目回到主页，「返回当前项目」与「联系作者」不相交、都点得到', async ({ app, page }) => {
  test.setTimeout(240_000)
  const a = await app({
    env: {
      TAVOTTO_NO_CONTACT: '0',
      TAVOTTO_DIAG_DEV_LOOPBACK: '1',
      TAVOTTO_CONTACT_ENDPOINT: 'https://127.0.0.1:9', // 不会被连：这个用例不点发送
    },
  })
  await page.setViewportSize({ width: 720, height: 700 })
  await page.goto(a.baseURL)
  await page.locator('[data-topbar]').waitFor({ timeout: 60_000 })
  await page.locator('[data-home-button]').first().click()
  const back = page.locator('[data-home-back-current]')
  const contact = page.locator('[data-contact-button="home"]')
  await expect(back).toBeVisible({ timeout: 30_000 })
  await expect(contact).toBeVisible()
  const b = (await back.boundingBox())!
  const c = (await contact.boundingBox())!
  const disjoint = b.x + b.width <= c.x || c.x + c.width <= b.x || b.y + b.height <= c.y || c.y + c.height <= b.y
  expect(disjoint).toBe(true)
  for (const [loc, box] of [[back, b], [contact, c]] as const) {
    const hit = await loc.evaluate((el, p) => el.contains(document.elementFromPoint(p.x, p.y)), {
      x: box.x + box.width / 2,
      y: box.y + box.height / 2,
    })
    expect(hit).toBe(true)
  }
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'home-narrow.png') })
})

test('我的来信：收到 HTML 回信显示成白色信纸（沙盒 iframe、无 allow-scripts、附图本机内联），上面是自己写的原文', { tag: '@feature:contact.maker' }, async ({ app, page }) => {
  test.setTimeout(240_000)
  const a = await app({
    noProject: true,
    env: {
      TAVOTTO_NO_CONTACT: '0',
      TAVOTTO_DIAG_DEV_LOOPBACK: '1',
      TAVOTTO_CONTACT_ENDPOINT: 'https://127.0.0.1:9', // 不会被连：回信与附图是预先落在本机数据目录里的
    },
  })
  // 本机收件记录与附图（引擎每次请求都重读文件）：模拟“已经收到过一封 HTML 回信、附图已下载”
  const mid = 'TVM-ABCD-EFGH-JKMN-PQRS'
  const html = readFileSync(path.resolve(import.meta.dirname, '..', '..', 'tests', 'fixtures', 'contact', 'reply_sanitized.html'), 'utf8')
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64')
  const assets = [0, 1, 2, 3].map((n) => ({ n, content_type: 'image/png', size: png.length }))
  writeFileSync(
    path.join(a.dataDir, 'contact_messages.json'),
    JSON.stringify({
      version: 1,
      messages: [
        {
          message_id: mid,
          reply_token: 'a'.repeat(64),
          route: 'cn',
          sent_at: Date.now() / 1000 - 3600,
          delivered: true,
          state: 'replied',
          subject: '导出报错',
          body: '导出 PDF 时提示字体缺失。\n第二行\n第三行\n第四行\n第五行',
          attachment_names: ['shot.png'],
          replies: [{ id: 'r_AAAA', created_at: '2026-10-22T08:00:00Z', body: '纯文字版', html, assets }],
          read: [],
        },
      ],
    }),
  )
  const dir = path.join(a.dataDir, 'contact_assets', mid, 'r_AAAA')
  mkdirSync(dir, { recursive: true })
  for (const n of [0, 1, 2, 3]) writeFileSync(path.join(dir, String(n)), png)

  await page.setViewportSize({ width: 1280, height: 900 })
  await page.goto(a.baseURL)
  await page.locator('[data-contact-button="home"]').click({ timeout: 30_000 })
  const dialog = page.locator('[data-dialog="contact"]')
  // 有未读回信时入口直接打开「我的来信」

  // 自己写的原文在上面：主题、折叠的正文（5 行 > 4 行）、附件名
  await expect(dialog.locator('[data-contact-letter-subject]')).toHaveText('导出报错')
  await expect(dialog.locator('[data-contact-letter-toggle]')).toBeVisible()
  await expect(dialog.locator('[data-contact-letter-files]')).toContainText('shot.png')

  // 信纸：白底、沙盒里没有 allow-scripts、文档自带 CSP、附图是内联的 data: URI
  const iframe = dialog.locator('[data-contact-reply-paper] iframe')
  await expect(iframe).toBeVisible({ timeout: 30_000 })
  const sandbox = (await iframe.getAttribute('sandbox')) ?? ''
  expect(sandbox.split(' ')).not.toContain('allow-scripts')
  const srcdoc = (await iframe.getAttribute('srcdoc')) ?? ''
  expect(srcdoc).toContain("default-src 'none'")
  expect(srcdoc).not.toContain('asset:')
  expect(srcdoc.match(/data:image\/png;base64,/g)?.length).toBe(4)
  const inner = page.frameLocator('[data-contact-reply-paper] iframe')
  await expect(inner.locator('table').first()).toBeVisible()
  expect(await dialog.locator('[data-contact-reply-paper]').evaluate((el) => getComputedStyle(el).backgroundColor)).toBe('rgb(255, 255, 255)')
  // 自适应高度：不是初始的 120px，也没有内部滚动条
  expect((await iframe.boundingBox())!.height).toBeGreaterThan(150)
  // 回信文档本身没有发出任何请求（CSP 禁网络；图是 data:）
  expect(await horizontalOffenders(page, '[data-dialog="contact"]')).toEqual([])
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, 'letters-html-reply.png') })

  // 深色模式下信纸仍是白的
  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'))
  expect(await dialog.locator('[data-contact-reply-paper]').evaluate((el) => getComputedStyle(el).backgroundColor)).toBe('rgb(255, 255, 255)')
  if (SHOTS) {
    await page.waitForTimeout(400) // 按钮颜色有过渡动画：切主题后立刻截会截到半途
    await page.screenshot({ path: path.join(SHOTS, 'letters-html-reply-dark.png') })
  }
})
