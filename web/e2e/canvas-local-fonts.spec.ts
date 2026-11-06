/**
 * 画布文字选本机字体（ADR 0169）：字体下拉里除内置三类外列出这台机器能嵌入的字体，选中后导出请求带上
 * 这张脸的身份；按加粗换成同族的粗体文件。DejaVu 随 matplotlib 而来，每台机器都有。
 */
import { expect, test } from './fixtures'
import path from 'node:path'
import type { Locator } from '@playwright/test'

test(
  '标注选本机字体：下拉里有 DejaVu Serif，选中后导出带身份，加粗换成同族粗体文件',
  { tag: ['@feature:canvas.text-local-fonts'] },
  async ({ app, page }) => {
    test.setTimeout(240_000)
    const a = await app()
    await page.setViewportSize({ width: 1500, height: 950 })
    const catalogReady = page.waitForResponse(async (r) =>
      new URL(r.url()).pathname === '/api/source-fonts/catalog' && r.ok() && (await r.json()).state === 'ready',
      { timeout: 60_000 },
    )
    await page.goto(a.baseURL)
    await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
    await expect(page.locator('[data-canvas-toolbar]')).toHaveCount(1)
    await page.keyboard.press('ControlOrMeta+k')
    const addText = page.locator('[data-cmd-id="add-text"]')
    await expect(addText).toBeVisible()
    await addText.click()
    await expect(addText).toHaveCount(0)
    await page.keyboard.press('Escape')
    await page.locator('[data-canvas-stage] [data-object-id] [data-text-body]').click()
    const catalog: { families: { name: string; cjk: boolean }[] } = await (await catalogReady).json()
    expect(catalog.families.some((f) => f.name === 'DejaVu Serif')).toBe(true)
    const latin = page.locator('[data-inspector-panel] [data-prop="fontFamily"] [data-select-trigger]')
    await expect(latin).toBeVisible({ timeout: 30_000 })
    const openMenu = async (trigger: Locator) => {
      await trigger.click()
      await expect(trigger).toHaveAttribute('data-state', 'open')
      const id = await trigger.getAttribute('aria-controls')
      expect(id).toBeTruthy()
      return page.locator(`[data-popover][id="${id}"][data-state="open"]`)
    }
    const shot = async (name: string) => {
      const dir = process.env.TAVOTTO_SHOT_DIR
      if (!dir) return
      await page.screenshot({ path: path.join(dir, `${name}.png`), animations: 'disabled' })
    }
    const menu = await openMenu(latin)
    await expect(menu.locator('[data-select-option="DejaVu Serif"]')).toBeVisible()
    await shot('menu')
    // 搜索框：打一段名字就筛出来，回车选中
    const search = menu.locator('[data-select-search]')
    await expect(search).toBeFocused()
    await search.fill('dejavu ser')
    // 目录可能同时包含 DejaVu Serif Display；筛选结果取真实目录中的匹配族。
    const matching = catalog.families.filter((f) => f.name.toLowerCase().replace(/\s+/g, '').includes('dejavuser'))
    await expect(menu.locator('[data-select-option]')).toHaveCount(matching.length)
    await expect(menu.locator('[data-select-option="DejaVu Serif"]')).toBeVisible()
    await shot('search')
    await search.press('ArrowDown')
    await search.press('ArrowUp')
    await search.press('Enter')
    await expect(latin).toContainText('DejaVu Serif')

    // 以 ready 的真实目录决定中文格是否存在；控件漏渲染不能静默跳过。
    const cjk = page.locator('[data-inspector-panel] [data-font-fallback-cell] [data-select-trigger]')
    const cjkFamily = catalog.families.find((f) => f.cjk)
    if (cjkFamily) {
      await expect(cjk).toBeVisible()
      const cjkMenu = await openMenu(cjk)
      const chosen = cjkMenu.locator(`[data-select-option=${JSON.stringify(cjkFamily.name)}]`)
      await chosen.click()
      await expect(cjk).toHaveAttribute('data-state', 'closed')
    } else {
      await expect(cjk).toHaveCount(0)
    }
    await shot('picked')

    const bold = page.locator('[data-inspector-panel] [data-prop="bold"] button')
    await bold.click()
    await expect(bold).toHaveAttribute('aria-pressed', 'true')

    await page.keyboard.press('ControlOrMeta+e')
    const start = page.locator('[data-export-start]')
    await expect(start).toBeEnabled()
    const request = page.waitForRequest((r) => new URL(r.url()).pathname === '/api/export/start' && r.method() === 'POST')
    await start.click()
    const faces: { family: string; bold?: boolean; cjk?: { family: string } }[] = []
    JSON.stringify((await request).postDataJSON(), (key, value) => {
      if (key === 'source_font') faces.push(value)
      return value
    })
    expect(faces.length).toBeGreaterThan(0)
    expect(faces[0]).toMatchObject({ family: 'DejaVu Serif', bold: true })
    if (cjkFamily) expect(faces[0].cjk?.family).toBe(cjkFamily.name)
  },
)
