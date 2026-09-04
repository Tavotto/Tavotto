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
    '    # U 形曲线：bbox 中心落在杯口里、不在曲线自己身上 —— 键盘入口的探针',
    '    xs = [i * 3 / 40 for i in range(41)]',
    '    ax.plot(xs, [4 * (x - 1.5) ** 2 / 2.25 - 1 for x in xs], color="tab:green", label="U")',
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

  // 4) ⌥ 双击：只轮换，**不弹快速改字**。两个 pointerdown 各换一次选中，双击
  //    再弹一个内容输入框的话，用户要的是「换一个」，拿到的是一次没要的编辑。
  await page.keyboard.down('Alt')
  await page.mouse.dblclick(at.x, at.y)
  await page.waitForTimeout(500)
  await page.keyboard.up('Alt')
  expect(
    await page.locator('[role="dialog"]').count(),
    '⌥ 双击不该弹出快速编辑弹层',
  ).toBe(0)
  expect(await shownGid(), '⌥ 双击仍然只是换选中').toMatch(/^axes_\d+$/)
})

test('键盘轮换：bbox 中心不在曲线身上时也走得回来', async ({ app, page }) => {
  const a = await app({ figures: writeTwinProject() })
  await page.goto(a.baseURL)
  await page.getByText('Fig_twin.pdf').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-element-svg] svg').first()).toBeVisible({ timeout: 60_000 })
  await page.waitForTimeout(1500)

  /**
   * 在**真的** matplotlib 输出里挑出那条 U 形曲线：对每条曲线量「bbox 中心到
   * 路径的最近距离」，取最大的那条 —— 那正好就是「中心不在自己身上」的定义，
   * 不靠猜 gid 的序号。顺带返回一个**确实在线上**的点用来选中它。
   */
  const probe = await page.evaluate(() => {
    const svg = document.querySelector('[data-element-svg] svg')
    if (!svg) return null
    let best: { id: string; on: { x: number; y: number }; gap: number } | null = null
    for (const g of svg.querySelectorAll('[id*=".lines_"]')) {
      const path = g.querySelector('path') as SVGPathElement | null
      if (!path?.getTotalLength) continue
      const len = path.getTotalLength()
      const ctm = path.getScreenCTM()
      if (!ctm || len < 40) continue
      const at = (f: number) => {
        const q = path.getPointAtLength(len * f)
        return { x: q.x * ctm.a + q.y * ctm.c + ctm.e, y: q.x * ctm.b + q.y * ctm.d + ctm.f }
      }
      const pts = Array.from({ length: 201 }, (_, i) => at(i / 200))
      const r = path.getBoundingClientRect()
      const c = { x: r.x + r.width / 2, y: r.y + r.height / 2 }
      const gap = Math.min(...pts.map((q) => Math.hypot(q.x - c.x, q.y - c.y)))
      if (!best || gap > best.gap) best = { id: g.id, on: at(0.5), gap }
    }
    return best
  })
  expect(probe, '图里应当有曲线').not.toBeNull()
  expect(
    probe!.gap,
    'U 形曲线的 bbox 中心应当离曲线足够远（这条断言垮了说明夹具没画出 U 形）',
  ).toBeGreaterThan(20)

  await page.mouse.click(probe!.on.x, probe!.on.y)
  await page.waitForTimeout(400)
  const shownGid = () =>
    page.evaluate(() => document.querySelector('[data-gid]')?.getAttribute('data-gid') ?? null)
  expect(await shownGid(), '点在曲线上应当选中它').toBe(probe!.id)

  /**
   * ⌘K → 「在重叠的图内元素之间轮换」：键盘那条路，跑一次。
   *
   * 输入框**必须**按命令面板自己的可达名取。`getByRole('textbox').first()` 会
   * 抓到属性页里的某个数值框 —— 那样这条用例不但测不到轮换，还会往图上写一个
   * 属性（实测第一版就是这么静默跑偏的）。
   */
  const palette = page.getByRole('listbox', { name: '命令' })
  const search = page.getByRole('textbox', { name: '搜索命令' })
  const runCommand = async () => {
    // 焦点可能停在属性页某个输入框里，那时 useKeyboard 会把 ⌘K 让给原生编辑
    // （`inEditableTarget`）—— 面板根本不开。先摘掉焦点，别让这条用例偶发红。
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur())
    await page.keyboard.press('ControlOrMeta+k')
    await expect(search).toBeVisible()
    await search.fill('重叠')
    // 按可达名点那一条，不按「筛出来几条」计数：计数会把「命令暂时不可用」
    // 与「筛错了」混成同一种红，而且它对渲染时序敏感。
    await palette.getByRole('option', { name: /重叠/ }).click()
    await expect(search).toBeHidden()
    await page.waitForTimeout(400)
  }

  // 一路走出去再走回来：中途换到的都不是它，最后一步必须回到它自己
  const seen: (string | null)[] = []
  for (let i = 0; i < 6 && seen.at(-1) !== probe!.id; i++) {
    await runCommand()
    seen.push(await shownGid())
  }
  expect(seen.length, '不该一步就「回到」自己（那说明根本没轮换）').toBeGreaterThan(1)
  expect(seen.at(-1), `轮换应当走得回起点，实际走过：${seen.join(' → ')}`).toBe(probe!.id)
})
