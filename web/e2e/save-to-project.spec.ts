/**
 * ⌘S = 给这一版起名字（ADR 0211 第二期 PR 3，取代 ADR 0096 的「⌘S 存进项目」）的真浏览器全程：
 *
 * 排版始终自动存进项目（`tavottofile/<名>.tavotto/layout.json`，用画布名起的目录）；⌘S 先把还没写的编辑冲刷写完、再弹就地命名小框，
 * 填名字 → 时间线里出现命名节点。判据量的是**磁盘上那个文件**与时间线里的节点，不是界面说了什么。
 * 不带任何环境变量：写入端现在默认开。
 */
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, test, waitForDocumentReady } from './fixtures'

async function addText(page: Page, text: string, x: number, y: number) {
  await page.getByRole('button', { name: '文字' }).click()
  await page.locator('[data-canvas-stage]').click({ position: { x, y } })
  await page.keyboard.type(text)
  await page.keyboard.press('Escape')
  await expect(page.locator('[data-canvas-stage]').getByText(text)).toBeVisible()
}

/** 项目里排版目录（`tavottofile/*.tavotto`） */
function layoutDirs(project: string): string[] {
  const store = path.join(project, 'tavottofile')
  return existsSync(store) ? readdirSync(store).filter((n) => n.endsWith('.tavotto')) : []
}

/** 项目文件里全部画布的文字内容（按画布、对象顺序）；没有目录 / 文件时是空 */
function textsOnDisk(project: string): string[] {
  const dirs = layoutDirs(project)
  if (dirs.length !== 1) return []
  const file = path.join(project, 'tavottofile', dirs[0], 'layout.json')
  if (!existsSync(file)) return []
  const doc = JSON.parse(readFileSync(file, 'utf-8')) as {
    canvases: { objects: { type: string; text?: string }[] }[]
  }
  return doc.canvases.flatMap((c) => c.objects.filter((o) => o.type === 'text').map((o) => o.text ?? ''))
}

async function nameVersion(page: Page, name: string) {
  await page.keyboard.press('ControlOrMeta+s')
  const quick = page.locator('[data-timeline-quick-name-input]')
  await expect(quick).toBeFocused()
  await quick.fill(name)
  await quick.press('Enter')
  await expect(quick).toHaveCount(0)
}

test('⌘S 给这一版起名字：先冲刷写进项目、再命名；时间线里有命名节点；刷新后从项目里打开一致', { tag: '@feature:file.name-version' }, async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  await waitForDocumentReady(page)

  await addText(page, '第一版', 380, 220)

  // ⌘S：弹命名小框（不是「存进项目」对话框），按下那一刻没写的编辑已经冲刷进项目的 layout.json
  await page.keyboard.press('ControlOrMeta+s')
  await expect(page.getByRole('dialog', { name: '存进项目' })).toHaveCount(0)
  const quick = page.locator('[data-timeline-quick-name-input]')
  await expect(quick).toBeFocused()
  expect(layoutDirs(a.figures)).toHaveLength(1)
  await expect.poll(() => textsOnDisk(a.figures)).toEqual(['第一版'])
  await quick.fill('投稿前')
  await quick.press('Enter')
  await expect(quick).toHaveCount(0)

  // 时间线「命名」里有它
  await page.locator('[data-inspector-tab="timeline"]').click()
  const drawer = page.locator('[data-timeline-panel]')
  await expect(drawer.locator('[data-timeline-node][data-timeline-named] [data-timeline-name]')).toHaveText('投稿前')
  await page.locator('[data-inspector-tab="properties"]').click()

  // 再改一处、再 ⌘S：项目里是最新的
  await addText(page, '第二版', 380, 300)
  await page.keyboard.press('ControlOrMeta+s')
  await expect(page.locator('[data-timeline-quick-name-input]')).toBeFocused()
  await expect.poll(() => textsOnDisk(a.figures).sort()).toEqual(['第一版', '第二版'])
  await page.keyboard.press('Escape')
  expect(layoutDirs(a.figures)).toHaveLength(1)
  const name = layoutDirs(a.figures)[0].replace(/\.tavotto$/, '')
  const onDisk = textsOnDisk(a.figures).sort()

  // 刷新后从「项目里的排版」打开：内容与磁盘上那份一致；顶栏指着同一个文件，没有「未存进项目」
  await page.reload()
  await waitForDocumentReady(page)
  await page.getByRole('button', { name: /^(文档|排版)：/ }).click()
  await page.getByRole('menuitem', { name: /^打开(文档|排版)/ }).click()
  const openDialog = page.getByRole('dialog')
  await openDialog.getByRole('button', { name: new RegExp(name) }).click()
  await expect(openDialog).toBeHidden()
  const stage = page.locator('[data-canvas-stage]')
  for (const t of onDisk) await expect(stage.getByText(t)).toBeVisible()
  const label = page.locator('[data-save-destination]')
  await expect(label).toHaveAttribute('data-save-destination', 'project')
  await expect(label).toHaveAttribute('title', new RegExp(`tavottofile/${name}\\.json`))
  await expect(page.getByText('未存进项目')).toHaveCount(0)
})

test('画布文字仍在编辑时按 ⌘S，先提交文字、冲刷写进项目，再弹命名小框', { tag: '@feature:file.name-version' }, async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  await waitForDocumentReady(page)
  await page.locator('[data-tool="text"]').click()
  await page.locator('[data-canvas-stage]').click({ position: { x: 380, y: 220 } })
  await expect(page.locator('[data-rich-text="inline"]')).toBeFocused()
  await page.keyboard.type('编辑中保存')
  await page.keyboard.press('ControlOrMeta+s')
  await expect(page.locator('[data-timeline-quick-name-input]')).toBeFocused()
  await expect.poll(() => textsOnDisk(a.figures)).toEqual(['编辑中保存'])
  await page.keyboard.press('Escape')

  await page.locator('[data-tool="text"]').click()
  await page.locator('[data-canvas-stage]').click({ position: { x: 380, y: 300 } })
  await expect(page.locator('[data-rich-text="inline"]')).toBeFocused()
  await page.keyboard.type('再次保存')
  await page.keyboard.press('ControlOrMeta+s')
  await expect.poll(() => textsOnDisk(a.figures).sort()).toEqual(['再次保存', '编辑中保存'].sort())
})

test('文字工具提示关闭时 Escape 提交，再 ⌘S 命名：项目里是第二版', { tag: '@feature:file.name-version' }, async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  await waitForDocumentReady(page)
  await addText(page, '第一版', 380, 220)
  await nameVersion(page, '第一版快照')
  await expect.poll(() => textsOnDisk(a.figures)).toEqual(['第一版'])
  // 拉长旧实现的退场动画，把捕获 Escape 的窗口变成稳定的反证。
  await page.addStyleTag({ content: '[data-tooltip][data-state="closed"] { animation-duration: 5s !important }' })
  const textTool = page.locator('[data-tool="text"]')
  await textTool.hover()
  await expect(page.locator('[data-tooltip][data-state="delayed-open"], [data-tooltip][data-state="instant-open"]')).toBeVisible()
  await textTool.click()
  await page.locator('[data-canvas-stage]').click({ position: { x: 380, y: 300 } })
  await expect(page.locator('[data-rich-text="inline"]')).toBeFocused()
  await page.keyboard.type('第二版')
  await page.keyboard.press('Escape')
  // 保存自身也会收尾编辑器，所以先记录 Escape 后、保存前的状态。
  const editorsAfterEscape = await page.locator('[data-rich-text="inline"]').count()
  const textVisibleAfterEscape = await page.locator('[data-canvas-stage]').getByText('第二版').isVisible()
  await page.keyboard.press('ControlOrMeta+s')
  await expect.poll(() => textsOnDisk(a.figures).sort()).toEqual(['第一版', '第二版'])
  expect(editorsAfterEscape).toBe(0)
  expect(textVisibleAfterEscape).toBe(true)
})
