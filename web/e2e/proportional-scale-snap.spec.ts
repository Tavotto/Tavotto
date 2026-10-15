import type { Page } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test } from './fixtures'

/**
 * 等比缩放吸附（ADR 0128 §三）：**真 PanelView、真指针、真 matplotlib**。
 *
 * 两张同尺寸（80 × 50 mm）的图，各有一个用 `fig.add_axes` 钉死位置的子图：A 的子图右边在图宽 65% 处，B 的在 75% 处。
 * 拖 A 的东南角柄（面板默认锁比例）让 A 的子图右边靠近 B 的子图右边：
 * 松手后两条子图右边对齐，A 的宽高比不变。对照组按住 ⌘ 拖到同一个位置：不吸，差距还在。
 *
 * 子图位置不靠渲染结果量：`add_axes` 的分数坐标就是引擎 manifest 里的 position，
 * 屏幕上的右边 = 面板左缘 + 分数 × 面板屏幕宽（面板缩放是显示缩放，分数坐标不变）。
 */
const A_RIGHT = 0.65
const B_RIGHT = 0.75

function scaleLibrary(): string {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-scale-')), 'figures')
  mkdirSync(dir)
  const fig = (stem: string, left: number, width: number) =>
    [
      `def ${stem.toLowerCase()}():`,
      '    fig = plt.figure(figsize=(8 / 2.54, 5 / 2.54))',
      `    ax = fig.add_axes([${left}, 0.35, ${width}, 0.5])`,
      '    ax.plot([0, 1], [0, 1])',
      `    fig.savefig("${stem}.pdf")`,
      '',
      '',
    ].join('\n')
  writeFileSync(
    path.join(dir, 'fig_scale.py'),
    [
      'import matplotlib',
      'matplotlib.use("Agg")',
      'import matplotlib.pyplot as plt',
      '',
      '',
      fig('Fig_a', 0.10, A_RIGHT - 0.1),
      fig('Fig_b', 0.2, B_RIGHT - 0.2),
      'def main():',
      '    fig_a()',
      '    fig_b()',
      '',
    ].join('\n'),
    'utf-8',
  )
  writeFileSync(
    path.join(dir, 'tavotto_registry.json'),
    JSON.stringify({ scripts: { 'fig_scale.py': { entry: 'main', cost: 'light', stems: ['Fig_a', 'Fig_b'] } } }),
    'utf-8',
  )
  const fallback = process.platform === 'win32' ? 'python' : 'python3'
  const py = process.env.TAVOTTO_WORKER_PYTHON || fallback
  execFileSync(py, ['-c', 'import fig_scale; fig_scale.main()'], { cwd: dir, timeout: 120_000 })
  return dir
}

type Box = { x: number; y: number; w: number; h: number }

const panels = async (page: Page): Promise<Box[]> => {
  const n = await page.locator('[data-object-id]').count()
  const out: Box[] = []
  for (let i = 0; i < n; i++) {
    const b = (await page.locator('[data-object-id]').nth(i).boundingBox())!
    out.push({ x: b.x, y: b.y, w: b.width, h: b.height })
  }
  return out
}

async function addBoth(page: Page, baseURL: string) {
  await page.goto(baseURL)
  for (const [i, file] of ['Fig_a.pdf', 'Fig_b.pdf'].entries()) {
    // 添加后左栏收回去了：素材卡不在时先点回「素材」
    const card = page.locator(`[data-card="${file}"]`)
    if (i > 0 && !(await card.count())) await page.locator('[data-rail="assets"]').click()
    await card.focus({ timeout: 30_000 })
    await page.keyboard.press('Shift+Enter')
    await expect(page.locator('[data-object-id]')).toHaveCount(i + 1, { timeout: 30_000 })
    await page.waitForTimeout(1500)
  }
  // 两张图落在同一处：把后加的 B 拖到左下，A 的上半部分与东南角柄都露出来。
  // A 缩到 0.58 倍时它的 y 向锚点（25 / 39.5 / 54 与子图 29 / 37 / 44 mm）离页面线 50 与 B 的线（48.8 / 56 / 69 …）都在 4 mm 以上，
  // 只有 x 向那一对在容差（约 1.2 mm）里——否则「最近的一对」可能是 y 向的，结果不是这条用例要看的
  const [, b] = await panels(page)
  const cx = b.x + b.w / 2
  const cy = b.y + b.h / 2
  await page.mouse.move(cx, cy)
  await page.mouse.down()
  for (let i = 1; i <= 6; i++) await page.mouse.move(cx - (150 * i) / 6, cy + (120 * i) / 6)
  await page.mouse.up()
  await page.waitForTimeout(500)
  const [a2, b2] = await panels(page)
  expect(b2.x, 'B 被拖开了').toBeLessThan(a2.x - 100)

  // 吸附只取权威 manifest（ADR 0017）：面板得被引擎渲染过。各进一次图内编辑再退出，两张图的 manifest 就都在了
  for (const at of [
    [a2.x + a2.w * 0.8, a2.y + a2.h * 0.1],
    [b2.x + b2.w * 0.1, b2.y + b2.h * 0.8],
  ]) {
    await page.mouse.click(at[0], at[1])
    await page.keyboard.press('Enter')
    await expect(page.locator('[data-element-svg] > svg')).toBeVisible({ timeout: 60_000 })
    await page.waitForTimeout(1500)
    await page.keyboard.press('Escape')
    await expect(page.locator('[data-element-svg]')).toHaveCount(0)
  }
}

/** 拖 A 的东南角柄，使 A 的子图右边落在 B 的子图右边右侧 `offPx` 像素处（容差内）；返回两条子图右边的屏幕差 */
async function dragCorner(page: Page, mods: { meta?: boolean }, offPx: number) {
  const [a, b] = await panels(page)
  await page.mouse.click(a.x + a.w * 0.8, a.y + a.h * 0.1) // A 上沿一带：B 够不到
  const handle = (await page.locator('[data-handle="se"]').boundingBox())!
  const hx = handle.x + handle.width / 2
  const hy = handle.y + handle.height / 2
  const bRight = b.x + B_RIGHT * b.w
  const newW = (bRight + offPx - a.x) / A_RIGHT
  await page.mouse.move(hx, hy)
  await page.mouse.down()
  if (mods.meta) await page.keyboard.down('Meta')
  const dx = newW - a.w
  // 沿对角线拖：等比缩放取 max(宽比, 高比)，只拖一个轴缩小时会被另一个轴顶住
  const dy = (dx * a.h) / a.w
  for (let i = 1; i <= 8; i++) await page.mouse.move(hx + (dx * i) / 8, hy + (dy * i) / 8)
  // 松手前的吸附反馈（ADR 0128 §四）：只框对方（B 的子图虚线框）+ 自身加粗边；不再画自身虚线框（2026-10-15 手感裁决）
  const feedback = {
    selfBox: await page.locator('[data-snap-self]').count(),
    selfEdge: await page.locator('[data-snap-self-edge]').count(),
    target: await page.locator('[data-snap-target]').count(),
  }
  await page.mouse.up()
  if (mods.meta) await page.keyboard.up('Meta')
  await page.waitForTimeout(500)
  const [a2, b2] = await panels(page)
  return { before: a, a: a2, b: b2, feedback, diffPx: a2.x + A_RIGHT * a2.w - (b2.x + B_RIGHT * b2.w) }
}

test(
  '功能：锁比例角柄缩放时，面板的子图边吸到别的图的子图边，松手后对齐、宽高比不变',
  { tag: ['@feature:canvas.proportional-scale-snap'] },
  async ({ app, page }) => {
    const a = await app({ figures: scaleLibrary() })
    await addBoth(page, a.baseURL)

    const snapped = await dragCorner(page, {}, 1.5)
    console.log(
      `[scale-snap e2e] 吸附后差距 ${snapped.diffPx.toFixed(4)} px；宽高比 ${(snapped.a.w / snapped.a.h).toFixed(5)} ← ${(snapped.before.w / snapped.before.h).toFixed(5)}`,
    )
    // 0.1 mm 在屏幕上约 0.5 px（133%）；吸上的话差距只剩浮点误差
    expect(Math.abs(snapped.diffPx), '松手后两条子图右边应对齐').toBeLessThan(0.1)
    expect(snapped.a.w / snapped.a.h).toBeCloseTo(snapped.before.w / snapped.before.h, 3)
    expect(snapped.a.w, 'A 确实被缩小了').toBeLessThan(snapped.before.w - 50)
    expect(snapped.feedback, '吸上时只有对方（B 的子图）目标框 + 自身加粗边，没有自身虚线框').toEqual({ selfBox: 0, selfEdge: 1, target: 1 })

    // 对照：⌘ 临时不吸——同样的手势停在手指的位置，差距还在（证明上面的对齐不是巧合）
    const free = await dragCorner(page, { meta: true }, 1.5)
    console.log(`[scale-snap e2e] 对照（⌘）差距 ${free.diffPx.toFixed(3)} px`)
    expect(Math.abs(free.diffPx), '⌘ 不吸：差距应约等于手指停的 1.5 px（跳变上限 0.5×容差：子图右边在锚点比 1.54 倍放大，3 px 会被拒）').toBeGreaterThan(0.75)
    expect(free.feedback, '⌘ 不吸：没有反馈').toEqual({ selfBox: 0, selfEdge: 0, target: 0 })
  },
)
