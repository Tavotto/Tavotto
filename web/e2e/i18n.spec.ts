import { copyFileSync, mkdirSync, readdirSync, statSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

const REPO = path.resolve(import.meta.dirname, '..', '..')

/**
 * 中英文各走一遍：启动 → 项目选择 → 工作台 → 设置 → 导出对话框 → 基本编辑。
 *
 * 为什么非要真浏览器：
 *   ① **溢出只有布局引擎知道**。jsdom 的 offsetWidth / scrollWidth 恒为 0，
 *      `src/i18n/overflow.test.tsx` 只能守住「英文字数别失控」和「该截断的地方
 *      挂了 truncate」；「Export 这个词在 1024px 窗口下把缩放控件挤出去了吗」
 *      只能在这儿量。
 *   ② 语言选择要跨一次**真实刷新**才算数——localStorage 写没写对、
 *      `initI18n` 在挂 React 之前有没有读到，刷新一次全暴露。
 *
 * 语言怎么设：`page.addInitScript` 在**任何页面脚本之前**写好偏好，模拟
 * 「用户上次选了英文」。不能用 context 的 locale：那只改 navigator.language，
 * 测不到「手动选择压过系统语言」这条。
 */

/** 每档语言在几个关键位置该出现的词。全部取自 web/src/i18n/locales/。 */
const LOCALES = [
  {
    tag: 'zh-CN',
    picker: '选择项目',
    create: '新建项目',
    pathLabel: '项目路径',
    open: '打开',
    export: '导出',
    settings: '设置',
    language: '界面语言',
    emptyCanvas: '画布是空的',
    text: '文字',
    undo: '撤销',
    /** 另一门语言的标志词，用来证明「没混着显示」 */
    foreign: 'Choose a project',
  },
  {
    tag: 'en-US',
    picker: 'Choose a project',
    create: 'New project',
    pathLabel: 'Project path',
    open: 'Open',
    export: 'Export',
    settings: 'Settings',
    language: 'Language',
    emptyCanvas: 'The canvas is empty',
    text: 'Text',
    undo: 'Undo',
    foreign: '选择项目',
  },
] as const

const LOCALE_KEY = 'tavotto.locale'

for (const L of LOCALES) {
  test.describe(`界面语言 ${L.tag}`, () => {
    test.beforeEach(async ({ page }) => {
      await page.addInitScript(
        ([key, tag]) => window.localStorage.setItem(key, tag),
        [LOCALE_KEY, L.tag] as const,
      )
    })

    test('项目选择器', async ({ app, page }) => {
      const a = await app({ noProject: true })
      await page.goto(a.baseURL)

      await expect(page.getByRole('main', { name: L.picker })).toBeVisible()
      await expect(page.getByRole('button', { name: L.create })).toBeVisible()
      await expect(page.getByLabel(L.pathLabel)).toBeVisible()
      // <html lang> 跟着走：读屏器与浏览器的断词都看它
      await expect(page.locator('html')).toHaveAttribute('lang', L.tag)
    })

    test('工作台 → 设置 → 导出对话框', async ({ app, page }) => {
      const a = await app()
      await page.goto(a.baseURL)

      // 顶栏的主动作
      const exportBtn = page.getByRole('button', { name: L.export, exact: true })
      await expect(exportBtn).toBeVisible({ timeout: 30_000 })

      // 空画布的空状态
      await expect(page.getByText(L.emptyCanvas)).toBeVisible()

      // 设置：语言项就在「通用」里
      await page.getByRole('button', { name: L.settings, exact: true }).first().click()
      // 帮助气泡也是 role=dialog，按名字取设置那一个
      const dialog = page.getByRole('dialog', { name: L.settings })
      await expect(dialog).toBeVisible()
      await expect(dialog.getByText(L.language)).toBeVisible()
      // 语言选择器是 ui/Select（Radix）：选项在 portal 里，要先点开才存在。
      // 语言自称永远用目标语言写，两档都在，不跟着界面语言翻译
      await dialog.getByRole('combobox', { name: L.language }).click()
      await expect(page.getByRole('option', { name: '简体中文' })).toHaveCount(1)
      await expect(page.getByRole('option', { name: 'English' })).toHaveCount(1)
      await page.keyboard.press('Escape') // 关下拉
      await expect(page.getByRole('option', { name: 'English' })).toHaveCount(0)
      await page.keyboard.press('Escape') // 关设置
      await expect(dialog).toBeHidden()

      // 导出对话框
      await exportBtn.click()
      await expect(page.getByRole('dialog')).toBeVisible()
      await expect(page.getByRole('dialog').getByText(L.export).first()).toBeVisible()
      await page.keyboard.press('Escape')
    })

    test('基本编辑：放一个面板 + 一段文字，撤销按钮跟着有内容', async ({ app, page }) => {
      const a = await app()
      await page.goto(a.baseURL)

      await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
      await expect(page.getByText(L.emptyCanvas)).toHaveCount(0)

      // 撤销按钮的无障碍名带上「撤销什么」——那句话来自历史标签，
      // 而历史标签存的是描述符，**不是**当时那门语言的字符串
      const undo = page.getByRole('button', { name: new RegExp(L.undo) })
      await expect(undo).toBeEnabled()
      await expect(undo).toHaveAttribute('aria-label', new RegExp(L.undo))
    })

    test('刷新之后仍是这门语言（手动选择压过系统语言）', async ({ app, page }) => {
      const a = await app()
      await page.goto(a.baseURL)
      await expect(page.getByRole('button', { name: L.export, exact: true })).toBeVisible({
        timeout: 30_000,
      })

      await page.reload()
      await expect(page.getByRole('button', { name: L.export, exact: true })).toBeVisible({
        timeout: 30_000,
      })
      await expect(page.locator('html')).toHaveAttribute('lang', L.tag)
      expect(await page.evaluate((k) => localStorage.getItem(k), LOCALE_KEY)).toBe(L.tag)
    })

    test('不混语言：这一屏里不出现另一门语言的标志词', async ({ app, page }) => {
      const a = await app()
      await page.goto(a.baseURL)
      await expect(page.getByRole('button', { name: L.export, exact: true })).toBeVisible({
        timeout: 30_000,
      })
      await expect(page.getByText(L.foreign)).toHaveCount(0)
    })
  })
}

test.describe('英文界面的排版', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(
      ([key]) => window.localStorage.setItem(key, 'en-US'),
      [LOCALE_KEY] as const,
    )
  })

  /**
   * 断点下限（1024×680，见 CLAUDE.md 的视觉纪律）是最容易被英文撑坏的一档：
   * 再窄左右栏就该互斥了，这个宽度下三栏还都在。
   */
  test('1024px 下顶栏 / 右栏 / 标签条都不横向溢出', async ({ app, page }) => {
    await page.setViewportSize({ width: 1024, height: 700 })
    const a = await app()
    await page.goto(a.baseURL)
    await expect(page.getByRole('button', { name: 'Export', exact: true })).toBeVisible({
      timeout: 30_000,
    })
    await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
    // 抽屉是宽度动画，动画途中量到的是中间帧——等它停下来再量，
    // 否则这条用例会按机器快慢随机红（问过一次 getAnimations 就够）
    await page.waitForFunction(
      () => [...document.querySelectorAll('aside')].every((a) => a.getAnimations().length === 0),
      undefined,
      { timeout: 10_000 },
    )

    // 页面自身绝不横向滚动
    const bodyOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(bodyOverflow, 'body 出现了横向滚动').toBeLessThanOrEqual(0)

    // 具体容器：真的量 scrollWidth 与 clientWidth。容差 1px 是**有出处的**：
    // 抽屉的内容层固定成停靠宽度（收起动画期间内容整体滑出而不是被挤扁），
    // 而抽屉自己带 1px 边框，于是内容比内容盒宽正好 1px，被 overflow-hidden
    // 裁掉的是那 1px 内边距。真正的溢出（英文那次是 17px）一样拦得住。
    const overflowing = await page.evaluate(() => {
      const sel = ['header', 'aside', '[role="tablist"]', '[role="toolbar"]']
      const out: string[] = []
      for (const s of sel) {
        for (const el of document.querySelectorAll<HTMLElement>(s)) {
          if (el.scrollWidth - el.clientWidth > 1) {
            out.push(`${s} → scrollWidth=${el.scrollWidth} clientWidth=${el.clientWidth}`)
          }
        }
      }
      return out
    })
    expect(overflowing).toEqual([])
  })

  /**
   * 用户自己起的名字不受任何字数预算保护——项目目录叫什么是他的事。
   * 顶栏在英文下本来就更满，再来一个长项目名是最容易出事的组合。
   */
  test('超长项目名不会把顶栏撑开', async ({ app, page }) => {
    const longName = 'a_project_folder_name_nobody_would_ever_shorten_for_you_2026'
    const dir = path.join(os.tmpdir(), `tavotto-e2e-long-${process.pid}`, longName)
    mkdirSync(dir, { recursive: true })
    for (const f of readdirSync(path.join(REPO, 'examples', 'figures'))) {
      const src = path.join(REPO, 'examples', 'figures', f)
      if (statSync(src).isFile()) copyFileSync(src, path.join(dir, f))
    }

    await page.setViewportSize({ width: 1024, height: 700 })
    const a = await app({ noProject: true })
    await page.goto(a.baseURL)
    await page.getByLabel('Project path').fill(dir)
    await page.getByRole('button', { name: 'Open', exact: true }).click()

    await expect(page.getByRole('button', { name: 'Export', exact: true })).toBeVisible({
      timeout: 30_000,
    })
    // 顶栏里确实出现了这个长名字（不然这条用例只是在空转）
    await expect(page.getByRole('button', { name: new RegExp(longName) })).toBeVisible()

    const bodyOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    )
    expect(bodyOverflow, 'body 出现了横向滚动').toBeLessThanOrEqual(0)

    const header = await page.evaluate(() => {
      const h = document.querySelector('header')!
      return h.scrollWidth - h.clientWidth
    })
    expect(header, '顶栏被长项目名撑开了').toBeLessThanOrEqual(1)
  })
})
