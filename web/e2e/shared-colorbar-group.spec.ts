import { expect, test } from './fixtures'
import { copyFileSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'

/**
 * 真浏览器 + 真 matplotlib 跑一遍共享色条的组（ADR 0102）。
 *
 * jsdom 那几份用例喂的是手写 manifest；这里回答只有真链路才回答得了的：引擎真的从
 * `fig.colorbar(im, ax=[b, c])` 推出了组吗？树上真的是「整张图 → 组 → B、C、色条」吗？
 * 选中组后在画布上拖 B，matplotlib 重排出来的 B、C、色条真的一起走了、A 没动吗？
 * 撤销真的回去了吗？单拖 B 时色条真的不跟了吗？存盘重开后组和位移都还在吗？
 *
 * 位移的尺子是**权威渲染回来之后** SVG 里各 `<g id="axes_N">` 的屏幕矩形（不是预览期的
 * transform），所以量到的是 matplotlib 真画出来的落点。
 */

/** `titleB`：给 B 加个标题（成员子图里的东西，拖它起手的用例用；别的用例不带，几何不变） */
function writeSharedProject({ titleB = false } = {}): string {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), 'tavotto-cbar-group-')), 'figures')
  mkdirSync(dir, { recursive: true })
  const script = [
    'import matplotlib',
    'matplotlib.use("Agg")',
    'import matplotlib.pyplot as plt',
    'import numpy as np',
    'from pathlib import Path',
    '',
    '',
    'def main():',
    '    z = np.arange(64).reshape(8, 8)',
    '    fig, (a, b, c) = plt.subplots(1, 3, figsize=(9, 3))',
    '    a.plot([0, 1], [0, 1], label="A")',
    '    a.legend()',
    '    im = b.imshow(z, cmap="viridis")',
    ...(titleB ? ['    b.set_title("B")'] : []),
    '    c.imshow(z.T, cmap="viridis", norm=im.norm)',
    '    fig.colorbar(im, ax=[b, c])',
    '    fig.savefig(Path(__file__).with_name("Fig_shared.pdf"))',
    '    plt.close(fig)',
    '',
    '',
    'if __name__ == "__main__":',
    '    main()',
    '',
  ].join('\n')
  writeFileSync(path.join(dir, 'fig_shared.py'), script, 'utf-8')
  writeFileSync(
    path.join(dir, 'tavotto_registry.json'),
    JSON.stringify({
      version: 1,
      scripts: { 'fig_shared.py': { entry: 'main', cost: 'light', stems: ['Fig_shared'] } },
    }),
    'utf-8',
  )
  // 占位图只负责让素材卡片出现；画布上的图与 manifest 都来自引擎当场跑脚本
  copyFileSync(
    path.join(import.meta.dirname, '..', '..', 'examples', 'figures', 'Fig1_kinetics.pdf'),
    path.join(dir, 'Fig_shared.pdf'),
  )
  return dir
}

const GROUP = 'group:axes_3'

/**
 * 权威图里各子图组的屏幕矩形，外加 `rel`：相对子图 A（组外、始终不动）的横向偏移。
 * 右栏开合、左栏抽屉收起都会让整张画布在屏幕上挪位置——位移一律按 `rel` 比。
 */
const boxes = async (page: Page) => {
  const abs = await page.evaluate(() => {
    const out: Record<string, { x: number; y: number; w: number; h: number }> = {}
    for (const id of ['axes_0', 'axes_1', 'axes_2', 'axes_3']) {
      const g = document.querySelector(`[data-element-svg] svg [id="${id}"]`) as SVGGElement | null
      if (!g) continue
      const r = g.getBoundingClientRect()
      out[id] = { x: r.x, y: r.y, w: r.width, h: r.height }
    }
    return out
  })
  const rel = Object.fromEntries(Object.entries(abs).map(([k, b]) => [k, b.x - abs.axes_0.x]))
  // 以 A 的宽度为单位：重开后画布缩放倍率会变（像素位移跟着缩放），这把尺子不变
  const unit = Object.fromEntries(Object.entries(rel).map(([k, v]) => [k, v / abs.axes_0.w]))
  return { abs, rel, unit }
}

/**
 * 元素树在左栏抽屉里：画布上一动手抽屉可能收起，要用时再打开。
 *
 * **只在关着时点轨道**，开没开看轨道自己的 `aria-expanded`（与 store 同步，没有动画滞后）：
 * 进图内编辑时左栏若开着会顺手切到元素树（`actions.enterElementEdit`），这时再点一下轨道是
 * **收起**。收起有动画，树还在 DOM 里多留一会儿，本机快、后面几步常常抢在它卸载前做完，
 * CI 慢就卡在找树行上（#691 full-ci 两条腿同一处超时）。不看树可不可见来判断，也是同一个
 * 理由：收起动画中它仍「可见」。
 */
async function openTree(page: Page) {
  const rail = page.locator('[data-rail="elements"]')
  if ((await rail.getAttribute('aria-expanded')) !== 'true') await rail.click()
  await expect(rail).toHaveAttribute('aria-expanded', 'true')
  await expect(page.locator('[role="tree"]')).toBeVisible()
}

/**
 * 选中树上的一行：聚焦 + Enter（树行的键盘入口，与点击同一条选中通路）。左栏抽屉开合动画
 * 期间画布层会短暂压在抽屉上，指针点击会被它截走——键盘不受遮挡影响。
 */
async function pickRow(page: Page, gid: string) {
  const r = page.locator(`[role="tree"] [data-el="${gid}"]`)
  await r.focus()
  await page.keyboard.press('Enter')
}

/** 做一件事，并等画布换成那之后的**精确**图（撤销 / 拖动松手都会触发一次权威渲染） */
async function settleAfter(page: Page, act: () => Promise<void>) {
  const key = () => page.locator('[data-display-key]').first().getAttribute('data-display-key')
  const before = await key()
  await act()
  await expect
    .poll(
      async () => {
        const el = page.locator('[data-display-key]').first()
        const k = await el.getAttribute('data-display-key')
        const kind = await el.getAttribute('data-display')
        return k !== before && kind === 'exact' ? 'switched' : `${kind}:${k}`
      },
      { timeout: 60_000, message: '画布没有换成这一步之后的精确图' },
    )
    .toBe('switched')
  await page.waitForTimeout(300)
}

async function dragBy(page: Page, from: { x: number; y: number }, dx: number) {
  await page.mouse.move(from.x, from.y)
  await page.mouse.down()
  for (let i = 1; i <= 8; i++) await page.mouse.move(from.x + (dx * i) / 8, from.y)
  await page.mouse.up()
}

const center = (b: { x: number; y: number; w: number; h: number }) => ({ x: b.x + b.w / 2, y: b.y + b.h / 2 })

test(
  '共享色条成组：树、整组平移、撤销重做、钻进成员、单拖不带色条、重开还在',
  { tag: '@feature:figure.shared-colorbar-group' },
  async ({ app, page }) => {
  // 关吸附：位移要是一个确定的数
  await page.addInitScript(() => {
    for (const key of Object.keys(localStorage)) {
      if (!key.includes('ui')) continue
      try {
        const saved = JSON.parse(localStorage.getItem(key) || '{}')
        localStorage.setItem(key, JSON.stringify({ ...saved, snapEnabled: false }))
      } catch {
        /* 不是 JSON 的键不管 */
      }
    }
  })
  const a = await app({ figures: writeSharedProject() })
  await page.goto(a.baseURL)
  await page.getByText('Fig_shared.pdf').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('[data-display="exact"]').first()).toBeVisible({ timeout: 60_000 })

  // 1) 元素树：整张图 → 组 → B、C、色条轴
  await openTree(page)
  const groupRow = page.locator(`[role="tree"] [data-el="${GROUP}"]`)
  await expect(groupRow).toBeVisible({ timeout: 30_000 })
  await expect(groupRow).toContainText('共享色条组')
  const indent = (gid: string) =>
    page
      .locator(`[role="tree"] [data-el="${gid}"]`)
      .evaluate((n) => parseFloat((n as HTMLElement).style.paddingLeft))
  const gIndent = await indent(GROUP)
  for (const m of ['axes_1', 'axes_2', 'axes_3']) {
    expect(await indent(m), `${m} 应当是组的直接子节点`).toBeGreaterThan(gIndent)
  }
  expect(await indent('axes_0'), '子图 A 不在组里').toBe(gIndent)

  // 2) 选中组 → 拖 B = 整组平移（选中之后右栏打开、画布挪位，量要在那之后）
  await pickRow(page, GROUP)
  await expect(page.locator('[data-group-page]')).toBeVisible()
  await page.waitForTimeout(500)
  const b0 = await boxes(page)
  expect(Object.keys(b0.abs).sort()).toEqual(['axes_0', 'axes_1', 'axes_2', 'axes_3'])
  // 往左拖：组右边贴着图幅，往右会被钳住
  await settleAfter(page, () => dragBy(page, center(b0.abs.axes_1), -60))
  const b1 = await boxes(page)
  for (const g of ['axes_1', 'axes_2', 'axes_3']) {
    expect(b0.rel[g] - b1.rel[g], `${g} 应当跟着整组左移`).toBeGreaterThan(40)
  }
  // 相对布局不变：C 与色条相对 B 的偏移与之前一致
  expect(Math.abs(b1.rel.axes_2 - b1.rel.axes_1 - (b0.rel.axes_2 - b0.rel.axes_1))).toBeLessThan(2)
  expect(Math.abs(b1.rel.axes_3 - b1.rel.axes_1 - (b0.rel.axes_3 - b0.rel.axes_1))).toBeLessThan(2)

  // 3) 撤销回原位，重做再过去
  await settleAfter(page, () => page.keyboard.press('ControlOrMeta+z'))
  const b2 = await boxes(page)
  expect(Math.abs(b2.rel.axes_3 - b0.rel.axes_3), '撤销后色条回原位').toBeLessThan(1)
  await settleAfter(page, () => page.keyboard.press('Shift+ControlOrMeta+z'))
  const b3 = await boxes(page)
  expect(Math.abs(b3.rel.axes_3 - b1.rel.axes_3), '重做后色条再过去').toBeLessThan(1)

  // 4) 选中组时只点不拖 = 钻进去选中点到的那个成员（B 的图像：点子图内部与平时一样
  //    选中点下去的那个元素）。C 可能被右栏盖着，点 B 左边五分之一处
  await openTree(page)
  await pickRow(page, GROUP)
  await expect(page.locator('[data-group-page]')).toBeVisible()
  const b3s = await boxes(page)
  const bb = b3s.abs.axes_1
  await page.mouse.click(bb.x + bb.w * 0.2, bb.y + bb.h / 2)
  await expect(page.locator('[data-group-page]')).toHaveCount(0)
  await openTree(page)
  const picked = page.locator('[role="tree"] [aria-selected="true"]')
  await expect(picked).toHaveCount(1)
  expect(await picked.getAttribute('data-el'), '选中的应当是 B 里点到的元素').toMatch(/^axes_1(\.|$)/)

  // 5) 单独选中 B 拖动：C 与共享色条都不跟
  await pickRow(page, 'axes_1')
  await page.waitForTimeout(500)
  const b3b = await boxes(page)
  await settleAfter(page, () => dragBy(page, center(b3b.abs.axes_1), 40))
  const b4 = await boxes(page)
  expect(b4.rel.axes_1 - b3b.rel.axes_1, 'B 自己右移了').toBeGreaterThan(25)
  expect(Math.abs(b4.rel.axes_2 - b3b.rel.axes_2), 'C 不动').toBeLessThan(1)
  expect(Math.abs(b4.rel.axes_3 - b3b.rel.axes_3), '共享色条不跟着 B 走').toBeLessThan(1)
  // 重新布局之后色条仍在组里，没被挂到 B 名下
  await openTree(page)
  await expect(groupRow).toBeVisible()
  expect(await indent('axes_3')).toBeGreaterThan(await indent(GROUP))

  // 6) 存盘重开：组与位移都还在（位移是成员各自的 position override）
  await page.keyboard.press('ControlOrMeta+s')
  await page.waitForTimeout(1500)
  await page.reload()
  await expect(page.locator('[data-display="exact"]').first()).toBeVisible({ timeout: 90_000 })
  await page.waitForTimeout(1000)
  // 重开后没有选中的图：用元素树空态自己的入口选中它，再点组的行（点树行会进图内编辑）
  const pickFigure = page.getByRole('button', { name: '选中一张可编辑的图' })
  const tree = page.locator('[role="tree"]')
  if (!(await pickFigure.isVisible()) && !(await tree.isVisible())) {
    await page.locator('[data-rail="elements"]').click()
  }
  if (await pickFigure.isVisible()) await pickFigure.click()
  await expect(tree).toBeVisible({ timeout: 30_000 })
  await pickRow(page, GROUP)
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('[data-group-page]')).toBeVisible()
  await page.waitForTimeout(800)
  const b5 = await boxes(page)
  expect(Math.abs(b5.unit.axes_3 - b4.unit.axes_3), '重开后色条仍在整组平移之后的位置').toBeLessThan(0.01)
  expect(Math.abs(b5.unit.axes_1 - b4.unit.axes_1), '重开后 B 仍在单拖之后的位置').toBeLessThan(0.01)
  // 反证这把尺子量得到位移：重开后的落点与最初（没挪过）明显不同
  expect(Math.abs(b5.unit.axes_3 - b0.unit.axes_3), '重开后的色条应当不在最初的位置').toBeGreaterThan(0.1)
  },
)

test(
  '选中组后从成员子图里的标题起手拖 = 整组平移（不是只挪标题）',
  { tag: '@feature:figure.shared-colorbar-group' },
  async ({ app, page }) => {
    await page.addInitScript(() => {
      for (const key of Object.keys(localStorage)) {
        if (!key.includes('ui')) continue
        try {
          const saved = JSON.parse(localStorage.getItem(key) || '{}')
          localStorage.setItem(key, JSON.stringify({ ...saved, snapEnabled: false }))
        } catch {
          /* 不是 JSON 的键不管 */
        }
      }
    })
    const a = await app({ figures: writeSharedProject({ titleB: true }) })
    await page.goto(a.baseURL)
    await page.getByText('Fig_shared.pdf').dblclick({ timeout: 30_000 })
    await expect(page.locator('[data-display="exact"]').first()).toBeVisible({ timeout: 60_000 })
    await openTree(page)
    await expect(page.locator(`[role="tree"] [data-el="${GROUP}"]`)).toBeVisible({ timeout: 30_000 })

    await pickRow(page, GROUP)
    await expect(page.locator('[data-group-page]')).toBeVisible()
    await page.waitForTimeout(500)
    const b0 = await boxes(page)
    const title = await page
      .locator('[data-element-svg] svg [id="axes_1.title"]')
      .first()
      .evaluate((n) => {
        const r = (n as SVGGElement).getBoundingClientRect()
        return { x: r.x, y: r.y, w: r.width, h: r.height }
      })
    expect(title.w, 'B 的标题画出来了').toBeGreaterThan(0)
    await settleAfter(page, () => dragBy(page, center(title), -60))
    const b1 = await boxes(page)
    for (const g of ['axes_1', 'axes_2', 'axes_3']) {
      expect(b0.rel[g] - b1.rel[g], `${g} 应当跟着整组左移`).toBeGreaterThan(40)
    }
    // 选区仍是组（没被换成标题）
    await expect(page.locator('[data-group-page]')).toBeVisible()
  },
)
