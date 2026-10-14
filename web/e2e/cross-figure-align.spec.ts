import type { Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

/**
 * 图内编辑跨图对齐同类元素的浏览器腿（ADR 0128 §二）：**真 PanelView、真指针、真 matplotlib**。
 *
 * 画布上有两张宽度不同的图（图例落在各自图里不同的地方）。进第一张图的图内编辑，把图例往第二张图
 * 的图例左边拖到**差几个像素**的地方：松手后第一张图的图例左边必须与第二张图的图例左边在页面上
 * 对齐（误差 < 0.1 mm）——吸上了，且写进去的仍是一条 loc_frac（权威成图之后量）。
 * 对照：同样的拖法按住 Ctrl 不吸，落点就是光标位移，停在差几个像素的地方。
 *
 * 独立尺：屏幕像素。两张图在同一块画布上、同一个视图倍率，页面 mm 误差 = 屏幕像素误差 / (像素每毫米)；
 * 像素每毫米取第二张图的画布对象框宽 / 它的页面宽（脚本里写死的 110 mm，原尺寸置入）。
 */
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    try {
      const key = 'tavotto.ui'
      const saved = JSON.parse(localStorage.getItem(key) || '{}')
      localStorage.setItem(
        key,
        JSON.stringify({ ...saved, prefsVersion: 2, snapEnabled: true, snapToObjects: true }),
      )
    } catch {
      /* 存储不可用时照常跑 */
    }
  })
})

const SCRIPT = (stem: string, widthCm: number) =>
  [
    'import matplotlib',
    'matplotlib.use("Agg")',
    'import matplotlib.pyplot as plt',
    'import numpy as np',
    '',
    '',
    'def main():',
    '    t = np.linspace(0, 10, 100)',
    `    fig, ax = plt.subplots(figsize=(${widthCm} / 2.54, 6 / 2.54))`,
    '    ax.plot(t, np.sin(t), label="sin")',
    '    ax.plot(t, np.cos(t), label="cos")',
    '    ax.legend(loc="upper right")',
    `    fig.savefig("${stem}.pdf")`,
    '',
  ].join('\n')

function library(): string {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-cross-')), 'figures')
  mkdirSync(dir)
  for (const [stem, w] of [
    ['Fig_cross_a', 8],
    ['Fig_cross_b', 11],
  ] as const) {
    writeFileSync(path.join(dir, `${stem.toLowerCase()}.py`), SCRIPT(stem, w), 'utf-8')
  }
  writeFileSync(
    path.join(dir, 'tavotto_registry.json'),
    JSON.stringify({
      scripts: {
        'fig_cross_a.py': { entry: 'main', cost: 'light', stems: ['Fig_cross_a'] },
        'fig_cross_b.py': { entry: 'main', cost: 'light', stems: ['Fig_cross_b'] },
      },
    }),
    'utf-8',
  )
  const fallback = process.platform === 'win32' ? 'python' : 'python3'
  const py = process.env.TAVOTTO_WORKER_PYTHON || fallback
  for (const stem of ['fig_cross_a', 'fig_cross_b'])
    execFileSync(py, ['-c', `import ${stem}; ${stem}.main()`], { cwd: dir, timeout: 120_000 })
  return dir
}

type Box = { x: number; y: number; w: number; h: number }

/** 图内编辑宿主里的元素框（屏幕 px） */
const editBox = (page: Page, gid: string): Promise<Box | null> =>
  page.evaluate((id) => {
    const hosts = document.querySelectorAll('[data-element-svg]')
    if (hosts.length !== 1) throw new Error(`图内编辑宿主应恰有一个，实际 ${hosts.length}`)
    const n = hosts[0].querySelector(`[id="${id}"]`)
    if (!n) return null
    const r = (n as SVGGraphicsElement).getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  }, gid)

async function settleAfter(page: Page, action: () => Promise<void>) {
  const rendered = page.waitForResponse((r) => r.url().includes('/api/engine/render'), {
    timeout: 60_000,
  })
  await action()
  await rendered
  await page.waitForTimeout(600)
}

async function addCard(page: Page, card: string, count: number) {
  // 加完第一张图左栏会换成属性：素材卡要重新点开「素材」才在
  const cardEl = page.locator(`[data-card="${card}"]`)
  if (!(await cardEl.isVisible())) await page.locator('[data-rail="assets"]').click()
  await cardEl.focus({ timeout: 30_000 })
  await page.keyboard.press('Shift+Enter')
  await expect(page.locator('[data-object-id]')).toHaveCount(count, { timeout: 30_000 })
  await page.waitForTimeout(1200)
}

const LEGEND = 'axes_0.legend'

/** 两条用例同一套步骤，只差拖动时按没按 Ctrl（用例要各自是字面量，功能登记的扫描器才认得） */
async function dragLegendToOther(
  { app, page }: { app: (o?: { figures?: string }) => Promise<{ baseURL: string }>; page: Page },
  pressCtrl: boolean,
) {
  const a = await app({ figures: library() })
  await page.setViewportSize({ width: 1600, height: 1000 })
  await page.goto(a.baseURL)
  await addCard(page, 'Fig_cross_a.pdf', 1)
  await addCard(page, 'Fig_cross_b.pdf', 2)

  const objs = page.locator('[data-object-id]')
  // 两张图置入时叠在页面中央：把第二张（b，在上层）往下拖开 400 px，两张图只在竖直方向错开一点，
  // 水平方向仍重叠——图例左边在页面上差一大截，正好拿来对齐
  const b0 = (await objs.nth(1).boundingBox())!
  await page.mouse.move(b0.x + b0.width / 2, b0.y + b0.height / 2)
  await page.mouse.down()
  for (let i = 1; i <= 8; i++) await page.mouse.move(b0.x + b0.width / 2, b0.y + b0.height / 2 + (400 * i) / 8)
  await page.mouse.up()
  await page.waitForTimeout(400)
  await page.keyboard.press('Escape')
  // 别的图在排版画布上是位图，图例的位置取引擎渲染 b 时给的 manifest（分数坐标）
  const manifests: Record<string, { x: number; y: number; w: number; h: number }> = {}
  page.on('response', async (r) => {
    if (!r.url().includes('/api/engine/render')) return
    try {
      const j = (await r.json()) as { manifest?: { stem: string; elements: { gid: string; bbox: number[] }[] } }
      const el = j.manifest?.elements.find((e) => e.gid === LEGEND)
      if (j.manifest && el)
        manifests[j.manifest.stem] = { x: el.bbox[0], y: el.bbox[1], w: el.bbox[2], h: el.bbox[3] }
    } catch {
      /* 非 JSON 响应：忽略 */
    }
  })
  const enter = async (i: number) => {
    const box = (await objs.nth(i).boundingBox())!
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2)
    await page.keyboard.press('Enter')
    await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 60_000 })
    await page.waitForTimeout(2000)
  }
  const objB = (await objs.nth(1).boundingBox())!
  // 直接进 a 的图内编辑、不预热 b：进编辑时引擎要把视口里看得见的 b 也渲染出来（权威 manifest，
  // `crossFigureNeighbours`）。等待条件 = 引擎对 b 的渲染响应已回来、画面也换上了（再等一拍入库）
  await enter(0)
  await expect.poll(() => manifests['Fig_cross_b'], { timeout: 60_000, message: 'b 应被补渲染' }).toBeTruthy()
  await page.waitForTimeout(1500)
  const lb = manifests['Fig_cross_b']
  const theirsLeft = objB.x + lb.x * objB.width

  const mine0 = (await editBox(page, LEGEND))!
  expect(mine0, '编辑中的图应有图例').not.toBeNull()

  // 把我的图例左边拖到差 3 px 的地方（再往右 3 px，容差 6 px 内），其余维度不动
  const dx = theirsLeft - mine0.x + 3
  const sx = mine0.x + mine0.w * 0.5
  const sy = mine0.y + mine0.h * 0.5
  expect(Math.abs(dx), '起点已经几乎对齐，用例没有意义').toBeGreaterThan(20)

  if (pressCtrl) await page.keyboard.down('Control')
  await page.mouse.move(sx, sy)
  await page.mouse.down()
  for (let i = 1; i <= 10; i++) await page.mouse.move(sx + (dx * i) / 10, sy)
  await page.waitForTimeout(150)
  await settleAfter(page, () => page.mouse.up())
  if (pressCtrl) await page.keyboard.up('Control')

  const mine1 = (await editBox(page, LEGEND))!
  // 页面 mm 误差 = px 误差 / (px 每 mm)；对象 b 宽 110 mm（脚本 11 cm、原尺寸置入）
  const pxPerMm = objB.width / 110
  const errPx = mine1.x - theirsLeft
  const errMm = errPx / pxPerMm
  if (pressCtrl) {
    expect(mine1.x - mine0.x, 'Ctrl：落点就是光标位移').toBeCloseTo(dx, 0)
    expect(Math.abs(errPx), 'Ctrl：不吸，停在差几个像素的地方').toBeGreaterThan(2)
  } else {
    expect(Math.abs(errMm), `吸上后两图例左边的页面误差（mm），px 误差=${errPx.toFixed(2)}`).toBeLessThan(0.1)
  }
}

test(
  '图内编辑拖图例靠近另一张图的图例：左边与对方图例左边对齐（页面 mm 误差 < 0.1）',
  { tag: '@feature:figure.cross-figure-align' },
  async ({ app, page }) => dragLegendToOther({ app, page }, false),
)

test('图内编辑拖图例靠近另一张图的图例：按住 Ctrl 不吸，落点就是光标位移', async ({ app, page }) =>
  dragLegendToOther({ app, page }, true))
