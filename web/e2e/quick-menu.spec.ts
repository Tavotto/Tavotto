import { expect, test } from './fixtures'

/**
 * 画布对象的右键菜单（Prompt 18）——只放 jsdom 量不到的那几件事：
 *
 *   * 子菜单开着时按 Esc，事件不能冒到全局快捷键（否则选区被清空）。这条在 jsdom 里
 *     **恒绿**：真浏览器在事件监听器之间有微任务检查点，Radix 在 document 捕获层关掉
 *     菜单之后 React 已把节点卸掉，冒泡层的 onKeyDown 再也跑不到；jsdom 没有这个
 *     检查点，同一份代码删掉捕获层的守卫照样绿（Session 18 变异 M8 / M9）。
 *   * 越界翻转：贴着画布区右下角右键，菜单翻到上方、子菜单翻到左边（jsdom 没有布局）。
 *   * 「重新构建」真的重跑脚本：作废热会话 → 冷构建 → 成功 toast。
 */
test('右键菜单：Esc 不清空选区 / 越界翻转 / 重新构建真跑脚本 / 多选对齐', async ({ app, page }) => {
  test.setTimeout(240_000)
  const a = await app()
  await page.setViewportSize({ width: 1400, height: 900 })
  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  await expect(page.getByText('画布是空的')).toHaveCount(0)
  await page.getByRole('button', { name: '画布排版' }).first().click()

  const panel = page.locator('[data-object-id]').first()
  await expect(panel).toBeVisible()
  const menu = page.locator('[role="menu"][data-quick-menu]')

  // ---- 面板菜单 + 子菜单上的 Esc ----
  await panel.click({ button: 'right' })
  await expect(menu).toHaveAttribute('data-quick-menu', 'panel')
  await expect(page.locator('[data-context-bar]')).toHaveCount(0)
  await menu.locator('[data-quick-item="z-order"]').hover()
  await expect(page.locator('[data-quick-item="z-top"]')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(menu).toHaveCount(0)
  // 选区还在：单选浮动栏回来、属性页仍是这张图
  await expect(page.locator('[data-context-bar]')).toBeVisible()
  await expect(page.getByRole('button', { name: '编辑图内元素' }).first()).toBeVisible()

  // ---- 重新构建：真的重跑脚本 ----
  await panel.click({ button: 'right' })
  await menu.locator('[data-quick-item="rebuild"]').click()
  await expect(menu).toHaveCount(0)
  await expect(page.getByText('已按源脚本重新构建').first()).toBeVisible({ timeout: 90_000 })

  // ---- 键盘：↓ 走项、→ 进子菜单、首字母不切工具 ----
  await panel.click({ button: 'right' })
  await expect(menu).toBeVisible()
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('ArrowDown')
  await page.waitForTimeout(50) // Radix 把「聚焦下一项」放在 setTimeout(0)
  expect(await page.evaluate(() => (document.activeElement as HTMLElement)?.dataset.quickItem)).toBe(
    'rebuild',
  )
  await page.keyboard.press('r')
  await expect(menu).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(menu).toHaveCount(0)
  await expect(page.getByRole('button', { name: '矩形', pressed: true })).toHaveCount(0)

  // ---- 越界翻转：把面板拖到画布区右下角（右栏从 ≈1040 起、画布下沿 ≈895）----
  const box = (await panel.boundingBox())!
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(960, 830, { steps: 12 })
  await page.mouse.up()
  await page.waitForTimeout(300)
  const box2 = (await panel.boundingBox())!
  const rx = Math.min(box2.x + box2.width - 6, 1030)
  const ry = Math.min(box2.y + box2.height - 6, 885)
  await page.mouse.click(rx, ry, { button: 'right' })
  await expect(menu).toBeVisible()
  const mb = (await menu.boundingBox())!
  expect(mb.x + mb.width).toBeLessThanOrEqual(1400 - 8)
  expect(mb.y + mb.height).toBeLessThanOrEqual(ry + 1) // 下方放不下 → 翻到光标上方
  await menu.locator('[data-quick-item="z-order"]').hover()
  const sub = page.locator('[data-quick-item="z-top"]')
  await expect(sub).toBeVisible()
  const sb = (await sub.boundingBox())!
  expect(sb.x + sb.width).toBeLessThanOrEqual(mb.x + 1) // 右边放不下 → 翻到左边
  await page.keyboard.press('Escape')
  await expect(menu).toHaveCount(0)

  // ---- 多选：⌘A / Ctrl+A → 右键 → 对齐子菜单 → 左对齐 ----
  for (const [x, y, s] of [
    [300, 200, 'alpha'],
    [520, 320, 'beta'],
  ] as const) {
    await page.getByRole('button', { name: '文字' }).click()
    await page.locator('[data-canvas-stage]').click({ position: { x, y } })
    await page.keyboard.type(s)
    await page.keyboard.press('Escape')
  }
  await page.keyboard.press('Escape')
  await page.keyboard.press('ControlOrMeta+a')
  await expect(page.locator('[data-multi-selection-context-bar]')).toBeVisible()
  await page.locator('[data-object-id]').nth(1).click({ button: 'right' })
  await expect(menu).toHaveAttribute('data-quick-menu', 'multi')
  await expect(page.locator('[data-multi-selection-context-bar]')).toHaveCount(0)
  await menu.locator('[data-quick-item="arrange"]').hover()
  await expect(page.locator('[data-quick-arrange-ref]')).toHaveAttribute('data-quick-arrange-ref', 'selection')
  await page.locator('[data-quick-item="align-left"]').click()
  await expect(menu).toHaveCount(0)
  await expect(page.locator('[data-multi-selection-context-bar]')).toBeVisible()
  const xs = await page.locator('[data-object-id]').evaluateAll((els) =>
    els.map((e) => Math.round((e as HTMLElement).getBoundingClientRect().x)),
  )
  expect(new Set(xs).size).toBe(1)
})
