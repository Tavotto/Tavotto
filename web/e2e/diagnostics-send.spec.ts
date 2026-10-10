import AxeBuilder from '@axe-core/playwright'
import { mkdtempSync, writeFileSync } from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
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
    await expect(dialog.locator('[data-diag-send-confirm]')).toBeEnabled({ timeout: 60_000 })
    // 详情默认收起；展开才看得到将发送的内容类别
    await expect(dialog.locator('[data-diag-kind="environment"]')).toBeHidden()
    await dialog.locator('[data-diag-send-details] summary').click()
    await expect(dialog.locator('[data-diag-kind="environment"]')).toBeVisible()
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
    await expect(page.locator('[data-dialog="diagnostics-send"] [data-diag-send-confirm]')).toBeEnabled({
      timeout: 60_000,
    })
    expect(connections).toBe(0)

    // 写说明、选类型，点发送：这才出现第一条连接，失败有人话与重试
    const d2 = page.locator('[data-dialog="diagnostics-send"]')
    await d2.locator('[data-diag-send-details] summary').click()
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

const SHOTS = process.env.TAVOTTO_E2E_SHOTS

test('故障卡 → 打开 → 发送：脚本运行失败卡里入口在折叠详情（主按钮仍是「再试一次」）；打开只备包，点发送才连', async ({
  app,
  page,
}) => {
  test.setTimeout(240_000)
  let connections = 0
  const sink = net.createServer((sock) => {
    connections += 1
    sock.destroy()
  })
  await new Promise<void>((r) => sink.listen(0, '127.0.0.1', r))
  const port = (sink.address() as net.AddressInfo).port
  const project = mkdtempSync(path.join(os.tmpdir(), 'tavotto-diag-card-'))
  writeFileSync(
    path.join(project, 'plot.py'),
    'import matplotlib\nmatplotlib.use("Agg")\nimport matplotlib.pyplot as plt\n' +
      'fig, ax = plt.subplots()\nax.plot([1, 2, 3])\nraise RuntimeError("boom")\nfig.savefig("out.png")\n',
    'utf-8',
  )
  try {
    const a = await app({
      figures: project,
      env: {
        TAVOTTO_DIAG_UPLOAD: '1',
        TAVOTTO_DIAG_DEV_LOOPBACK: '1',
        TAVOTTO_DIAG_ENDPOINT: `https://127.0.0.1:${port}`,
      },
    })
    await page.setViewportSize({ width: 1400, height: 900 })
    await page.goto(a.baseURL)
    const card = page.locator('[data-prep-card]')
    await expect(card).toHaveAttribute('data-prep-state', 'discover', { timeout: 60_000 })
    await card.locator('[data-prep-primary]').click()
    await expect(card).toHaveAttribute('data-prep-state', 'ready', { timeout: 120_000 })
    await card.locator('[data-prep-primary]').click() // 运行
    await expect(card).toHaveAttribute('data-prep-state', /failed|error/, { timeout: 120_000 })

    // 主按钮仍是修复类（再试一次），发送反馈住在折叠详情里
    const primaryBtn = card.locator('[data-prep-primary]')
    await expect(primaryBtn).toHaveCount(1)
    await expect(primaryBtn).not.toHaveAttribute('data-send-report', /.*/)
    const entry = card.locator('[data-prep-details] [data-send-report]')
    await card.locator('[data-prep-details-toggle]').click()
    await expect(entry).toBeVisible()
    expect(connections).toBe(0)

    await entry.click()
    const dialog = page.locator('[data-dialog="diagnostics-send"]')
    await expect(dialog).toBeVisible()
    await expect(dialog.locator('[data-diag-send-confirm]')).toBeEnabled({ timeout: 60_000 })
    // 轻确认框：默认只有一句话，详情收起
    await expect(dialog.locator('[data-diag-send-sentence]')).toBeVisible()
    await expect(dialog.locator('[data-diag-send-details]')).not.toHaveAttribute('open', '')
    expect(connections).toBe(0)
    if (SHOTS) {
      await page.screenshot({ path: `${SHOTS}/send-dialog-collapsed.png` })
      await dialog.locator('[data-diag-send-details] summary').click()
      await page.waitForTimeout(600)
      await page.screenshot({ path: `${SHOTS}/send-dialog-expanded.png` })
      await dialog.locator('[data-diag-send-details] summary').click()
    }
    await page.waitForTimeout(600) // 等折叠与对话框重新居中落定再量

    // 按钮不跳位：发送前后「发送」按钮的位置不变
    const before = await dialog.locator('[data-diag-send-confirm]').boundingBox()
    await dialog.locator('[data-diag-send-confirm]').click()
    await expect(dialog.locator('[data-diag-send-failure]')).toBeVisible({ timeout: 60_000 })
    expect(connections).toBeGreaterThan(0)
    const after = await dialog.locator('[data-diag-send-confirm]').boundingBox()
    expect(after!.x).toBeCloseTo(before!.x, 0)
    expect(after!.y).toBeCloseTo(before!.y, 0)
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/send-dialog-failed.png` })
  } finally {
    sink.close()
  }
})
