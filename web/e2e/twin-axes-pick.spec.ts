import { expect, test } from './fixtures'
import { copyFileSync, mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

/**
 * 真浏览器里跑一遍「⌥ 点击在重叠候选之间轮换」（issue #216）。
 *
 * 为什么值得单独一条 e2e：jsdom 那套用例喂的是手写 manifest、命中层的
 * `getBoundingClientRect` 是桩出来的，验的是结构与算术。**只有真浏览器 + 真
 * matplotlib** 才能回答：引擎真的把 twinx 的孪生轴当成一个独立 axes 发过来了
 * 吗？它的 bbox 真的与宿主一模一样吗？⌥（macOS 上的 Option）真的能带着
 * `altKey` 走到命中层、而不是被浏览器或系统吃掉？那条说明「换到了谁」的 toast
 * 真的看得见吗？
 *
 * 判据自带自检：第一下 ⌥ 点击报的 `第 1/N` 就说明这个点上确实压着 N 个候选，
 * 不用另写一句「这儿是空白」的断言。
 */

/** 造一个含 twinx 的图库：左轴一条线、右轴一条线，两条都画在上半部分。 */
function writeTwinProject(): string {
  const dir = path.join(mkdtempSync(path.join(os.tmpdir(), 'tavotto-twin-')), 'figures')
  mkdirSync(dir, { recursive: true })
  const script = [
    'import matplotlib',
    'matplotlib.use("Agg")',
    'import matplotlib.pyplot as plt',
    'from pathlib import Path',
    '',
    '',
    'def main():',
    '    fig, ax = plt.subplots(figsize=(4, 3))',
    '    ax.plot([0, 1, 2, 3], [1, 3, 2, 4], color="tab:blue")',
    '    ax.set_xlabel("time / s")',
    '    ax.set_ylabel("left")',
    '    # 两条曲线都钉在上半部分：绘图区下半块留成空白，那儿只剩两个 axes 容器',
    '    ax.set_ylim(-6, 5)',
    '    ax2 = ax.twinx()',
    '    ax2.plot([0, 1, 2, 3], [40, 10, 30, 20], color="tab:red")',
    '    ax2.set_ylabel("right")',
    '    ax2.set_ylim(-60, 50)',
    '    fig.tight_layout()',
    '    fig.savefig(Path(__file__).with_name("Fig_twin.pdf"))',
    '    plt.close(fig)',
    '',
    '',
    'if __name__ == "__main__":',
    '    main()',
    '',
  ].join('\n')
  writeFileSync(path.join(dir, 'fig_twin.py'), script, 'utf-8')
  writeFileSync(
    path.join(dir, 'tavotto_registry.json'),
    JSON.stringify({
      version: 1,
      scripts: { 'fig_twin.py': { entry: 'main', cost: 'light', stems: ['Fig_twin'] } },
    }),
    'utf-8',
  )
  // 素材库里要有一张产物，双击才进得去快速编辑。这里拿现成的样例 PDF 改个名
  // 顶上（与 fixtures.writeRuntimeNamedProject 同一招）：**画布上的图与命中用的
  // manifest 都来自引擎当场跑这个脚本**，占位 PDF 只负责让那张卡片出现，所以
  // 不需要在跑测试的机器上另装一份 matplotlib 去烤它。
  copyFileSync(
    path.join(import.meta.dirname, '..', '..', 'examples', 'figures', 'Fig1_kinetics.pdf'),
    path.join(dir, 'Fig_twin.pdf'),
  )
  return dir
}

test('twinx：⌥ 点击在宿主与孪生轴之间轮换，并说出换到了谁', async ({ app, page }) => {
  const a = await app({ figures: writeTwinProject() })
  await page.goto(a.baseURL)

  await page.getByText('Fig_twin.pdf').dblclick({ timeout: 30_000 })
  const svgWrap = page.locator('[data-element-svg]').first()
  await expect(svgWrap.locator('svg')).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(1500)

  // 绘图区容器在屏幕上的矩形：直接问 SVG 里那个 `<g id="axes_0">`
  const box = await page.evaluate(() => {
    const g = document.querySelector('[data-element-svg] svg [id="axes_0"]')
    if (!g) return null
    const r = (g as SVGGElement).getBoundingClientRect()
    return { x: r.x, y: r.y, w: r.width, h: r.height }
  })
  expect(box, 'SVG 里应当有 axes_0 这个组').not.toBeNull()

  /** 绘图区下半块中间：曲线都在上半部分，这儿只剩两个重叠的 axes 容器 */
  const at = { x: box!.x + box!.w * 0.5, y: box!.y + box!.h * 0.8 }

  /** 属性页此刻挂在哪个元素上（属性行的 data-gid，见 ElementInspector） */
  const shownGid = () =>
    page.evaluate(() => document.querySelector('[data-gid]')?.getAttribute('data-gid') ?? null)
  /** toast 的无障碍播报（常驻在 DOM 里的 aria-live 区） */
  const announced = () =>
    page.evaluate(() => document.querySelector('[role="status"]')?.textContent?.trim() ?? '')

  // 1) 普通点击：选中先登记的宿主，没有轮换 toast
  await page.mouse.click(at.x, at.y)
  await page.waitForTimeout(400)
  const host = await shownGid()
  expect(host, '空白处点击应当选中一个 axes 容器').toMatch(/^axes_\d+$/)
  // 不能断言这条播报是空的：渲染完成那句 toast 还挂着，播报区里本来就有话
  expect(await announced(), '普通点击不该说出轮换那句话').not.toContain('重叠元素')

  // 2) ⌥ 点击：每按一下往后走一格。第一下报的 `第 i/N` 里的 N 就是「这一点上
  //    压着 N 个候选」，用它当循环上界——不用另写一句「这儿是空白」的断言。
  const CYCLE = /第 (\d+)\/(\d+) 个重叠元素/
  await page.keyboard.down('Alt')
  await page.mouse.click(at.x, at.y)
  await page.waitForTimeout(400)
  const first = await announced()
  expect(first, '⌥ 点击应当播报换到了谁').toMatch(CYCLE)
  const total = Number(CYCLE.exec(first)![2])
  expect(total, 'twinx 之后这一点上至少压着宿主与孪生轴两个容器').toBeGreaterThanOrEqual(2)

  // 一路轮换到孪生轴：它是唯一带「右轴」的那个（措辞出处 engine/manifest.py）
  let sawTwin = first.includes('右轴') ? first : ''
  for (let i = 1; i < total && !sawTwin; i++) {
    await page.mouse.click(at.x, at.y)
    await page.waitForTimeout(400)
    const text = await announced()
    if (text.includes('右轴')) sawTwin = text
  }
  const lastSeen = await announced()
  await page.keyboard.up('Alt')
  expect(sawTwin, `轮换应当能走到孪生轴，实际播报：${lastSeen}`).toContain('右轴')

  const twin = await shownGid()
  expect(twin, '孪生轴也是一个 axes 容器').toMatch(/^axes_\d+$/)
  expect(twin, '属性页应当真的挂到了另一个 axes 上，不只是 toast 说说').not.toBe(host)

  // 3) 反方向：轮换是一次性的，普通点击仍然回到宿主
  await page.mouse.click(at.x, at.y)
  await page.waitForTimeout(400)
  expect(await shownGid()).toBe(host)
})
