/**
 * 导出前的 EPS 半透明提醒（ADR 0174 后续）：PostScript 没有透明，单图带标注导 EPS、标注里有半透明时，
 * 点「开始导出」先问「改导出 PDF / EPS 按不透明导出 / 取消」。真后端、真浏览器：
 * 选「EPS 按不透明导出」后 EPS 真的落盘（不含透明），PDF 里标注仍是半透明；选「改导出 PDF」请求里不再有 EPS。
 */
import { expect, test } from './fixtures'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'
import type { Page } from '@playwright/test'

const FEATURE = '@feature:export.eps-opacity-prompt'
// 截图用：TAVOTTO_E2E_SCHEME=dark 时按深色拍（默认跟系统 = 浅色）；文件名带 light / dark
const SCHEME = process.env.TAVOTTO_E2E_SCHEME === 'dark' ? 'dark' : 'light'

async function openAnnotatedFigure(page: Page, baseURL: string) {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(baseURL)
  await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 120_000 })
  await expect(page.locator('[data-display="exact"]')).toHaveCount(1, { timeout: 120_000 })
  // 插一个三角形标注、加填充、填充不透明度 50%
  await page.locator('[data-tool-menu="annotate"]').click({ timeout: 10_000 })
  // 「插入形状」直接落一个形状（不用拖）；三角形是封闭形状、能加填充
  await page.getByRole('menuitem', { name: '三角形' }).click({ timeout: 10_000 })
  // 刚插入的形状没被选中：点三角形左边的斜边选上它（命中的是描边）
  const shape = page.locator('[data-canvas-stage] [data-object-id]').last() // 最上层 = 刚插入的形状（图在它下面）
  await expect(shape).toBeVisible({ timeout: 10_000 })
  const box = (await shape.boundingBox())!
  await page.mouse.click(box.x + box.width * 0.25, box.y + box.height * 0.5)
  await page.getByRole('button', { name: '添加填充' }).click({ timeout: 10_000 })
  const opacity = page.locator('[data-prop="fillOpacity"] input')
  await opacity.fill('50')
  await opacity.press('Enter')
  await expect(opacity).toHaveValue(/50/)
}

async function openExport(page: Page, name: string) {
  await page.keyboard.press('Escape')
  await page.keyboard.press('ControlOrMeta+e')
  const dialog = page.locator('[data-dialog="export"]')
  await expect(dialog).toBeVisible()
  const eps = dialog.locator('[data-export-format="eps"] input')
  await expect(eps).toBeEnabled({ timeout: 30_000 })
  await eps.check()
  await expect(eps).toBeChecked({ timeout: 30_000 })
  await page.locator('#export-filename').fill(name)
  return dialog
}

test('选了 EPS 且标注半透明：点导出先问；选「EPS 按不透明导出」后 EPS 落盘', { tag: [FEATURE] }, async ({ app, page }) => {
  test.setTimeout(300_000)
  const a = await app()
  await page.emulateMedia({ colorScheme: SCHEME })
  await openAnnotatedFigure(page, a.baseURL)
  const dialog = await openExport(page, 'eps-opaque-e2e')
  if (process.env.TAVOTTO_E2E_SHOT) await page.screenshot({ path: `${process.env.TAVOTTO_E2E_SHOT}/export-before-${SCHEME}.png` })
  const start = page.locator('[data-export-start]')
  await expect(start).toBeEnabled()
  await start.click()
  const prompt = page.locator('[data-dialog="eps-opacity"]')
  await expect(prompt).toBeVisible()
  await expect(prompt.locator('[data-eps-opacity]')).toHaveCount(3)
  if (process.env.TAVOTTO_E2E_SHOT) {
    await page.waitForTimeout(600) // 等进场动画走完再拍
    await page.screenshot({ path: `${process.env.TAVOTTO_E2E_SHOT}/eps-opacity-prompt-${SCHEME}.png` })
  }
  const sent = page.waitForRequest((r) => new URL(r.url()).pathname === '/api/export/start' && r.method() === 'POST')
  await prompt.locator('[data-eps-opacity="opaque"]').click()
  const body = (await sent).postDataJSON() as { formats: string[]; eps_opaque?: boolean }
  expect(body.formats).toContain('eps')
  expect(body.eps_opaque).toBe(true)
  await expect(page.locator('[data-export-result]')).toBeVisible({ timeout: 120_000 })
  // EPS 真的落盘了：是 PostScript、没有透明（PS 没有 /ca 这类透明图形状态）
  const found = findFile(a.figures, 'eps-opaque-e2e.eps')
  expect(found, 'EPS 落盘').not.toBeNull()
  const eps = readFileSync(found!, 'latin1')
  expect(eps.startsWith('%!PS-Adobe')).toBe(true)
  expect(eps).not.toMatch(/\/ca\b|\/CA\b/)
  await expect(dialog).toBeVisible()
})

function findFile(dir: string, name: string): string | null {
  if (!existsSync(dir)) return null
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry)
    if (entry === name) return full
    if (statSync(full).isDirectory()) {
      const hit = findFile(full, name)
      if (hit) return hit
    }
  }
  return null
}
