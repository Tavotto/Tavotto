import AxeBuilder from '@axe-core/playwright'
import net from 'node:net'
import { expect, test } from './fixtures'
import { horizontalOffenders } from './overflow'
import type { Page } from '@playwright/test'

/**
 * 「发送问题反馈」（ADR 0118）——真浏览器 + 真引擎，量 jsdom 与模拟服务端量不到的两件事：
 *
 *   1. **默认关闭**：没有开关环境变量的引擎上，「帮助与诊断」里一个入口都没有，本机端点也不存在；
 *   2. **打开后，确认之前零连接**：引擎指向一个只数 TCP 连接的回环端口；打开对话框（备包）、查看内容、
 *      关窗、再打开，连接数始终是 0；点「发送」才出现第一条连接。服务端是否被「说服」做别的事由
 *      `tests/test_diag_send.py` 钉，这里只证明真实界面的触发点。
 *
 * 对端不是 TLS 服务（只数连接就关），所以发送会以网络/证书类错误失败——正是要验的「失败有人话 + 有保存退路」。
 */

async function openDiagnostics(page: Page, baseURL: string) {
  await page.goto(baseURL)
  await page.locator('[data-rail="settings"]').click()
  await page.locator('[data-section="diagnostics"]').click()
  await expect(page.locator('[data-diagnostics-bundle]')).toBeVisible({ timeout: 30_000 })
}

test('默认关闭：没有入口，本机端点也不存在', async ({ app, page }) => {
  const a = await app()
  await openDiagnostics(page, a.baseURL)
  await expect(page.locator('[data-diagnostics-send]')).toHaveCount(0)
  const cap = await page.request.get(`${a.baseURL}/api/diagnostics/send`)
  expect(await cap.json()).toEqual({ enabled: false })
  const prep = await page.request.post(`${a.baseURL}/api/diagnostics/send/prepare`, { data: {} })
  expect(prep.status()).toBe(404)
  // 本地导出照旧可用
  const bundle = await page.request.get(`${a.baseURL}/api/diagnostics/bundle`)
  expect(bundle.status()).toBe(200)
})

test('打开后：确认之前零连接，点发送才连；失败说人话并留保存退路', async ({ app, page }) => {
  test.setTimeout(180_000)
  let connections = 0
  const sink = net.createServer((sock) => {
    connections += 1
    sock.destroy()
  })
  await new Promise<void>((r) => sink.listen(0, '127.0.0.1', r))
  const port = (sink.address() as net.AddressInfo).port
  try {
    const a = await app({
      env: {
        TAVOTTO_DIAG_UPLOAD: '1',
        TAVOTTO_DIAG_DEV_LOOPBACK: '1',
        TAVOTTO_DIAG_ENDPOINT: `https://127.0.0.1:${port}`,
      },
    })
    await openDiagnostics(page, a.baseURL)
    await expect(page.locator('[data-diagnostics-send]')).toBeVisible()

    // 打开 = 备包（本机）：看得到内容类别与大小，一个连接都没有
    await page.locator('[data-diagnostics-send-open]').click()
    const dialog = page.locator('[data-dialog="diagnostics-send"]')
    await expect(dialog).toBeVisible()
    await expect(dialog.locator('[data-diag-kind="environment"]')).toBeVisible({ timeout: 60_000 })
    await expect(dialog.locator('[data-diag-send-confirm]')).toBeEnabled()
    expect(connections).toBe(0)

    // 真布局：窗口内不横向溢出，axe 无 critical / serious
    expect(await horizontalOffenders(page, '[data-dialog="diagnostics-send"]')).toEqual([])
    const axe = await new AxeBuilder({ page }).include('[data-dialog="diagnostics-send"]').analyze()
    const bad = axe.violations.filter((v) => v.impact === 'critical' || v.impact === 'serious')
    expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)).toEqual([])

    // 关窗再开：还是零
    await dialog.locator('[data-diag-send-dismiss]').click()
    await expect(dialog).toHaveCount(0)
    await page.locator('[data-diagnostics-send-open]').click()
    await expect(page.locator('[data-dialog="diagnostics-send"] [data-diag-kind="environment"]')).toBeVisible({
      timeout: 60_000,
    })
    expect(connections).toBe(0)

    // 写说明、选类型，点发送：这才出现第一条连接，失败有人话与重试
    const d2 = page.locator('[data-dialog="diagnostics-send"]')
    await d2.locator('[data-diag-send-note]').fill('e2e: nothing private here')
    await d2.locator('[data-diag-send-confirm]').click()
    await expect(d2.locator('[data-diag-send-failure]')).toBeVisible({ timeout: 60_000 })
    expect(connections).toBeGreaterThan(0)
    await expect(d2.locator('[data-diag-send-confirm]')).toBeVisible() // 「重试」
    await expect(d2.locator('[data-diag-send-save]')).toBeEnabled() // 保存到本地的退路

    // 关窗之后引擎不再有这个会话：不会自己继续
    const seen = connections
    await d2.locator('[data-diag-send-dismiss]').click()
    await page.waitForTimeout(1500)
    expect(connections).toBe(seen)
  } finally {
    sink.close()
  }
})
