/**
 * ⌘S 保存到项目（ADR 0096）的真浏览器全程：
 *
 * 项目里新建排版 → ⌘S 弹「存进项目」命名 → 改一处 → ⌘S 直接写回 → 读磁盘上的
 * `tavottofile/<名>.json` 验证内容更新 → 刷新后从「项目里的排版」打开，内容一致。
 *
 * 判据量的是**磁盘上那个文件**（不是界面说了什么）：jsdom 里的用例钉住了每一步的
 * 分支，这里钉的是整条链真的把字节写进了用户的项目文件夹。
 */
import { readFileSync, existsSync } from 'node:fs'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, test } from './fixtures'

const NAME = '我的排版'

async function addText(page: Page, text: string, x: number, y: number) {
  await page.getByRole('button', { name: '文字' }).click()
  await page.locator('[data-canvas-stage]').click({ position: { x, y } })
  await page.keyboard.type(text)
  await page.keyboard.press('Escape')
  await expect(page.locator('[data-canvas-stage]').getByText(text)).toBeVisible()
}

/** 项目文件里全部画布的文字内容（按画布、对象顺序） */
function textsOnDisk(file: string): string[] {
  const doc = JSON.parse(readFileSync(file, 'utf-8')) as {
    canvases: { objects: { type: string; text?: string }[] }[]
  }
  return doc.canvases.flatMap((c) => c.objects.filter((o) => o.type === 'text').map((o) => o.text ?? ''))
}

test('⌘S 存进项目 → 之后 ⌘S 写回同一个文件 → 刷新后从项目里打开一致', async ({ app, page }) => {
  const a = await app()
  const file = path.join(a.figures, 'tavottofile', `${NAME}.json`)
  await page.goto(a.baseURL)
  await expect(page.locator('[data-canvas-stage]')).toBeVisible({ timeout: 30_000 })

  await addText(page, '第一版', 380, 220)

  // 第一次 ⌘S：这份排版还只在本机 → 「存进项目」命名框，预填排版名（不是画布名 Figure 1）
  await page.keyboard.press('ControlOrMeta+s')
  const dialog = page.getByRole('dialog', { name: '存进项目' })
  await expect(dialog).toBeVisible()
  const nameInput = dialog.locator('#layout-save-name')
  await expect(nameInput).not.toHaveValue(/^Figure \d+$/)
  await nameInput.fill(NAME)
  await dialog.getByRole('button', { name: '存进项目' }).click()
  await expect(dialog).toBeHidden()
  await expect.poll(() => existsSync(file)).toBe(true)
  expect(textsOnDisk(file)).toEqual(['第一版'])
  const label = page.locator('[data-save-destination]')
  await expect(label).toHaveAttribute('data-save-destination', 'project')
  await expect(label).toHaveAttribute('title', new RegExp(`tavottofile/${NAME}\\.json`))

  // 改一处：自动保存只写本机，项目里那份不动，圆点亮
  await addText(page, '第二版', 380, 300)
  await expect(page.locator('[data-project-file-dirty]')).toBeVisible()
  await page.waitForTimeout(1500) // 过了自动保存的防抖窗口
  expect(textsOnDisk(file)).toEqual(['第一版'])

  // 第二次 ⌘S：不再问名字，直接写回同一个文件
  await page.keyboard.press('ControlOrMeta+s')
  await expect.poll(() => textsOnDisk(file).sort()).toEqual(['第一版', '第二版'])
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.locator('[data-project-file-dirty]')).toHaveCount(0)
  const onDisk = textsOnDisk(file).sort()

  // 刷新后从「项目里的排版」打开：内容与磁盘上那份一致
  await page.reload()
  await expect(page.locator('[data-canvas-stage]')).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: /^(文档|排版)：/ }).click()
  await page.getByRole('menuitem', { name: /^打开(文档|排版)/ }).click()
  const openDialog = page.getByRole('dialog')
  await openDialog.getByRole('button', { name: new RegExp(NAME) }).click()
  await expect(openDialog).toBeHidden()
  const stage = page.locator('[data-canvas-stage]')
  for (const t of onDisk) await expect(stage.getByText(t)).toBeVisible()
  // 打开的这一份绑定了项目文件：顶栏说「已保存到项目」，指着同一个文件
  await expect(label).toHaveAttribute('data-save-destination', 'project')
  await expect(label).toHaveAttribute('title', new RegExp(`tavottofile/${NAME}\\.json`))
})
