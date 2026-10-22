import AxeBuilder from '@axe-core/playwright'
import net from 'node:net'
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
    await expect(dialog.locator('[data-contact-recipient]')).toHaveText('feedback@tavotto.com')
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
