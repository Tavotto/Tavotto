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
 *   1. **硬开关**：`TAVOTTO_NO_DIAG_UPLOAD=1`（fixtures 的默认）的引擎上，「帮助与诊断」里一个入口都没有，本机端点也不存在；
 *      发行版默认开启（ADR 0118 修订），所以 e2e 默认硬关、绝不连生产；
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

test('硬开关：没有入口，本机端点也不存在', async ({ app, page }) => {
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
        TAVOTTO_NO_DIAG_UPLOAD: '0', // 摘掉 fixtures 的硬关；目的地只能是下面的回环端点（误配不回退生产）
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
    await dialog.locator('[data-diag-send-details] > summary').click()
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
    await d2.locator('[data-diag-send-details] > summary').click()
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
        TAVOTTO_NO_DIAG_UPLOAD: '0', // 摘掉 fixtures 的硬关；目的地只能是下面的回环端点（误配不回退生产）
        TAVOTTO_DIAG_DEV_LOOPBACK: '1',
        TAVOTTO_DIAG_ENDPOINT: `https://127.0.0.1:${port}`,
      },
    })
    await page.setViewportSize({ width: 1400, height: 900 })
    await page.goto(a.baseURL)
    const card = page.locator('[data-prep-card]')
    await expect(card).toHaveAttribute('data-prep-state', 'discover', { timeout: 60_000 })
    await card.locator('[data-prep-primary]').click() // 打开并运行：点一次就检查并认领（V2 PR4）
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
    // 说明框在前（2026-10-19）：副标题那一句 + 输入框常显，附件内容收起
    await expect(dialog.locator('[data-diag-send-note]')).toBeVisible()
    await expect(dialog.locator('[data-diag-send-sentence]')).toBeVisible()
    await expect(dialog.locator('[data-diag-send-details]')).not.toHaveAttribute('open', '')
    expect(connections).toBe(0)
    // 收起态紧凑：标题 + 副标题一句 + 说明（标签 + 框 + 提示行）+ 问题类型 + 一行附件 + 按钮行
    const compact = await dialog.boundingBox()
    expect(compact!.height).toBeLessThan(760) // 1400×900；2026-10-21 额外内容三框移出折叠区后上限放宽（此前 500，实测约 466）
    if (SHOTS) {
      await page.screenshot({ path: `${SHOTS}/send-dialog-collapsed.png` })
      await dialog.locator('[data-diag-send-details] > summary').click()
      await page.waitForTimeout(600)
      await page.screenshot({ path: `${SHOTS}/send-dialog-expanded.png` })
      await dialog.locator('[data-diag-send-details] > summary').click()
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

test('意外错误 → 右下角反馈卡：贴窗口右下角、一颗主按钮；点了只打开确认框（零连接），× 关掉', async ({ app, page }) => {
  test.setTimeout(120_000)
  let connections = 0
  const sink = net.createServer((sock) => {
    connections += 1
    sock.destroy()
  })
  await new Promise<void>((r) => sink.listen(0, '127.0.0.1', r))
  const port = (sink.address() as net.AddressInfo).port
  const project = mkdtempSync(path.join(os.tmpdir(), 'tavotto-diag-corner-'))
  try {
    const a = await app({
      figures: project,
      env: {
        TAVOTTO_NO_DIAG_UPLOAD: '0',
        TAVOTTO_DIAG_DEV_LOOPBACK: '1',
        TAVOTTO_DIAG_ENDPOINT: `https://127.0.0.1:${port}`,
      },
    })
    await page.setViewportSize({ width: 1400, height: 900 })
    await page.goto(a.baseURL)
    await expect(page.locator('[data-work-panel]')).toBeVisible({ timeout: 60_000 })
    // 一条未捕获的内建错误（不是业务错误类、不是 ApiError）：走 window error → 意外错误入口
    await page.evaluate(() => {
      setTimeout(() => {
        throw new TypeError('corner card e2e')
      }, 0)
    })
    const card = page.locator('[data-unexpected-error-card]')
    await expect(card).toBeVisible({ timeout: 10_000 })
    await page.waitForTimeout(400) // 等入场动画落定再量
    const box = (await card.boundingBox())!
    expect(1400 - (box.x + box.width), '贴右边').toBeLessThan(32)
    expect(900 - (box.y + box.height), '贴底边').toBeLessThan(32)
    await expect(card.locator('[data-variant="primary"]')).toHaveCount(1)
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/feedback-corner-card.png` })

    // 任何对话框都盖在它上面（层级在遮罩之下）：打开设置，反馈卡左上角点到的不是反馈卡
    await page.locator('[data-rail="settings"]').click()
    await expect(page.locator('[data-section="diagnostics"]')).toBeVisible()
    const onTop = await page.evaluate(() => {
      const el = document.querySelector('[data-unexpected-error-card]')!
      const r = el.getBoundingClientRect()
      const hit = document.elementFromPoint(r.left + 8, r.top + 8)
      return !!hit && el.contains(hit)
    })
    expect(onTop, '对话框开着时反馈卡不在最上层').toBe(false)
    await page.keyboard.press('Escape')
    await expect(page.locator('[data-section="diagnostics"]')).toBeHidden()

    await card.locator('[data-feedback-corner-send]').click()
    await expect(card).toHaveCount(0)
    const dialog = page.locator('[data-dialog="diagnostics-send"]')
    await expect(dialog).toBeVisible()
    await expect(dialog.locator('[data-diag-send-confirm]')).toBeEnabled({ timeout: 60_000 })
    expect(connections).toBe(0)
  } finally {
    sink.close()
  }
})

test('右下角反馈卡不盖住准备引导卡：属性栏收起、窗口小，两张卡不相交且反馈卡整张在窗口里', async ({ app, page }) => {
  test.setTimeout(180_000)
  const project = mkdtempSync(path.join(os.tmpdir(), 'tavotto-diag-prep-'))
  writeFileSync(
    path.join(project, 'plot.py'),
    'import matplotlib\nmatplotlib.use("Agg")\nimport matplotlib.pyplot as plt\n' +
      'fig, ax = plt.subplots()\nax.plot([1, 2, 3])\nraise RuntimeError("boom")\nfig.savefig("out.png")\n',
    'utf-8',
  )
  const a = await app({
    figures: project,
    env: {
      TAVOTTO_NO_DIAG_UPLOAD: '0',
      TAVOTTO_DIAG_DEV_LOOPBACK: '1',
      TAVOTTO_DIAG_ENDPOINT: 'https://127.0.0.1:9', // 不会被连：本条不点发送
    },
  })
  // 一次加载（准备卡关掉后重载不会再自动出现），之后只改窗口大小：避让要跟着窗口重算
  await page.setViewportSize({ width: 1024, height: 768 })
  await page.goto(a.baseURL)
  const prep = page.locator('[data-prep-card]')
  await expect(prep).toHaveAttribute('data-prep-state', 'discover', { timeout: 60_000 })
  const close = page.locator('[data-inspector-close]')
  if (await close.isVisible()) await close.click()
  await page.evaluate(() => {
    setTimeout(() => {
      throw new TypeError('prep overlap e2e')
    }, 0)
  })
  const card = page.locator('[data-unexpected-error-card]')
  await expect(card).toBeVisible({ timeout: 10_000 })
  for (const [w, h] of [
    [1024, 768],
    [1024, 560],
  ] as const) {
    await page.setViewportSize({ width: w, height: h })
    await page.waitForTimeout(500) // 入场动画与避让落定
    const c = (await card.boundingBox())!
    const p = (await prep.boundingBox())!
    const intersects = c.x < p.x + p.width && p.x < c.x + c.width && c.y < p.y + p.height && p.y < c.y + c.height
    expect(intersects, `${w}×${h}：反馈卡 ${JSON.stringify(c)} 与准备卡 ${JSON.stringify(p)} 相交`).toBe(false)
    expect(c.y, '整张在窗口里（上）').toBeGreaterThanOrEqual(0)
    expect(c.x, '整张在窗口里（左）').toBeGreaterThanOrEqual(0)
    if (SHOTS) await page.screenshot({ path: `${SHOTS}/feedback-corner-vs-prep-${w}x${h}.png` })
  }
})

test('属性栏变窄、准备卡被画布带着右移时，反馈卡跟着避让（只有布局变化、没有 DOM 增删）', async ({ app, page }) => {
  test.setTimeout(180_000)
  const project = mkdtempSync(path.join(os.tmpdir(), 'tavotto-diag-prep-drag-'))
  writeFileSync(
    path.join(project, 'plot.py'),
    'import matplotlib\nmatplotlib.use("Agg")\nimport matplotlib.pyplot as plt\n' +
      'fig, ax = plt.subplots()\nax.plot([1, 2, 3])\nfig.savefig("out.png")\n',
    'utf-8',
  )
  const a = await app({
    figures: project,
    env: { TAVOTTO_NO_DIAG_UPLOAD: '0', TAVOTTO_DIAG_DEV_LOOPBACK: '1', TAVOTTO_DIAG_ENDPOINT: 'https://127.0.0.1:9' },
  })
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto(a.baseURL)
  const prep = page.locator('[data-prep-card]')
  await expect(prep).toHaveAttribute('data-prep-state', 'discover', { timeout: 60_000 })
  // 只改属性栏的宽度（style 属性变化，没有 DOM 增删）——拖动把手的每一帧就是这种变化；
  // 用键盘 Home / End 换档会让属性栏里的内容重排出增删，做不成「只有布局变化」的对照
  const setInspectorWidth = (px: number) =>
    page.evaluate((w) => {
      // 外层宽度由抽屉动效（drawerMotion）写在 style 上，内层再写一份：两层都改
      const aside = document.querySelector<HTMLElement>('[data-inspector-panel]')
      const inner = aside?.querySelector<HTMLElement>(':scope > div')
      if (!aside || !inner) throw new Error('找不到属性栏')
      aside.style.width = `${w}px`
      inner.style.width = `${w}px`
    }, px)
  await setInspectorWidth(520) // 属性栏很宽：准备卡在左边，不与反馈卡相交
  await page.waitForTimeout(400)
  await page.evaluate(() => {
    setTimeout(() => {
      throw new TypeError('prep drag e2e')
    }, 0)
  })
  const card = page.locator('[data-unexpected-error-card]')
  await expect(card).toBeVisible({ timeout: 10_000 })
  const overlap = async () => {
    const c = (await card.boundingBox())!
    const p = (await prep.boundingBox())!
    return c.x < p.x + p.width && p.x < c.x + c.width && c.y < p.y + p.height && p.y < c.y + c.height
  }
  await page.waitForTimeout(400)
  expect(await overlap(), '属性栏最宽时不相交').toBe(false)
  const before = (await prep.boundingBox())!.x
  await setInspectorWidth(240) // 属性栏变窄：画布变宽，准备卡右移
  await page.waitForTimeout(600)
  expect((await prep.boundingBox())!.x, '准备卡确实被带着右移了（否则本条是空对照）').toBeGreaterThan(before + 20)
  expect(await overlap(), '拖窄后仍不相交').toBe(false)
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/feedback-corner-after-inspector-drag.png` })
})

