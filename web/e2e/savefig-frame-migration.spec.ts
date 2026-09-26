import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import type { Page, Response } from '@playwright/test'
import { expect, test } from './fixtures'

/**
 * ADR 0098 §三（用户 2026-09-26 选 C）真浏览器 + 真 matplotlib 走一遍：
 *
 * 1. 打开一份**升级前**存下的排版（面板没有 `figureFrame` 记号，带一处图内修改）：迁移补上
 *    `figure.frame = "figsize"`，引擎照旧按 figsize 出图，面板在排版上的外框一个字节不动；
 * 2. 属性里点「改用原图的图幅」：图内的标题在屏幕上不动，外框换成脚本保存时的图幅；
 * 3. 撤销：外框与标题都回到切换之前。
 *
 * 「标题在屏幕上的位置」用两把彼此独立的尺子合起来量：面板在屏幕上的矩形取 DOM，
 * 标题在图里的位置取**引擎**这一版渲染回来的 manifest（bbox 分数）——不经过前端的换算函数。
 */

const LEGACY_DOC_ID = 'legacy-frame-e2e'
const EDIT = { gid: 'axes_0.title', prop: 'text', value: 'Reaction kinetics (legacy)' }

function legacyProject() {
  return {
    schema: 3,
    project: { id: 'legacy-frame', name: 'Legacy layout' },
    canvases: [
      {
        id: 'c1',
        name: 'Figure 1',
        page: { w: 180, h: 100 },
        objects: [
          {
            id: 'p1',
            type: 'panel',
            fileId: 'Fig1_kinetics.pdf',
            fileKind: 'pdf',
            script: 'fig1_kinetics.py',
            nativeW: 80,
            nativeH: 57.6,
            x: 20,
            y: 15,
            w: 80,
            h: 57.6,
            overrides: [EDIT],
          },
        ],
        guides: [],
      },
    ],
    activeCanvasId: 'c1',
    createdAt: 1756800000000,
    updatedAt: 1756800000000,
  }
}

interface Manifest {
  size_mm: [number, number]
  elements: { gid: string; bbox?: [number, number, number, number] }[]
  frame?: { active: boolean; savefig_mm: [number, number, number, number] }
}

/** 收集引擎渲染回来的 manifest，按「带没带 figsize 那条」分两份（最后一次为准） */
function watchRenders(page: Page) {
  const seen: { legacy?: Manifest; adopted?: Manifest } = {}
  page.on('response', async (r: Response) => {
    if (!r.url().includes('/api/engine/render') || r.request().method() !== 'POST') return
    try {
      const req = r.request().postDataJSON() as { patches?: { prop: string }[] }
      const body = (await r.json()) as { manifest?: Manifest }
      if (!body.manifest) return
      const legacy = (req.patches ?? []).some((p) => p.prop === 'frame')
      seen[legacy ? 'legacy' : 'adopted'] = body.manifest
    } catch {
      /* 非 JSON / 请求体读不到：不是这里要的 */
    }
  })
  return seen
}

async function panelRect(page: Page) {
  const box = await page.locator('[data-object-id="p1"]').boundingBox()
  expect(box).not.toBeNull()
  return box!
}

function titleOnScreen(rect: { x: number; y: number; width: number; height: number }, man: Manifest) {
  const el = man.elements.find((e) => e.gid === 'axes_0.title')
  expect(el?.bbox).toBeTruthy()
  const [x, y, w, h] = el!.bbox!
  return { x: rect.x + (x + w / 2) * rect.width, y: rect.y + (y + h / 2) * rect.height }
}

test('升级前的排版：原样打开 → 切到原图图幅内容不动 → 撤销还原', async ({ app, page }) => {
  const a = await app()
  const autosave = path.join(a.dataDir, 'layouts', '_autosave')
  mkdirSync(autosave, { recursive: true })
  writeFileSync(path.join(autosave, `${LEGACY_DOC_ID}.json`), JSON.stringify(legacyProject()))
  await page.addInitScript((id: string) => {
    try {
      localStorage.setItem('tavotto.currentDoc', id)
    } catch {
      /* 读不到就是读不到：用例会在下面红 */
    }
  }, LEGACY_DOC_ID)
  const renders = watchRenders(page)
  await page.goto(a.baseURL)

  const obj = page.locator('[data-object-id="p1"]')
  await expect(obj).toBeVisible({ timeout: 60_000 })

  // 1. 原样：引擎按 figsize 画回来，外框就是存下的那个
  await expect.poll(() => renders.legacy?.size_mm, { timeout: 180_000 }).toEqual([80, 57.6])
  expect(renders.legacy!.frame?.active).toBe(false)
  const opened = await panelRect(page)
  expect(opened.width / opened.height).toBeCloseTo(80 / 57.6, 2)
  await expect(obj.getByText('按 figsize 显示')).toBeVisible({ timeout: 30_000 })

  // 2. 切换（选中之后右栏展开、视口可能挪动：切换前的矩形在这之后量）
  await obj.click()
  const note = page.locator('[data-frame-note]')
  await expect(note).toBeVisible({ timeout: 30_000 })
  await page.waitForTimeout(500)
  const before = await panelRect(page)
  expect(before.width / before.height).toBeCloseTo(80 / 57.6, 2)
  const titleBefore = titleOnScreen(before, renders.legacy!)
  await note.locator('[data-frame-adopt]').click()
  await expect.poll(() => renders.adopted?.frame?.active, { timeout: 180_000 }).toBe(true)
  const [, , fw, fh] = renders.legacy!.frame!.savefig_mm
  expect(renders.adopted!.size_mm[0]).toBeCloseTo(fw, 1)
  await expect.poll(async () => (await panelRect(page)).width / (await panelRect(page)).height).toBeCloseTo(fw / fh, 2)
  const after = await panelRect(page)
  expect(Math.abs(after.width - before.width)).toBeGreaterThan(2)
  const titleAfter = titleOnScreen(after, renders.adopted!)
  expect(Math.abs(titleAfter.x - titleBefore.x)).toBeLessThan(1.5)
  expect(Math.abs(titleAfter.y - titleBefore.y)).toBeLessThan(1.5)
  await expect(note).toHaveCount(0)

  // 3. 撤销：外框回到切换之前
  await page.getByRole('button', { name: '撤销' }).click()
  await expect.poll(async () => (await panelRect(page)).width, { timeout: 30_000 }).toBeCloseTo(before.width, 0)
  const back = await panelRect(page)
  expect(Math.abs(back.x - before.x)).toBeLessThan(1)
  expect(Math.abs(back.y - before.y)).toBeLessThan(1)

  // 自动保存里的面板：figsize 那条回来了，几何与升级前逐字节相同
  await page.reload()
  await expect(obj).toBeVisible({ timeout: 60_000 })
  const saved = JSON.parse(readFileSync(path.join(autosave, `${LEGACY_DOC_ID}.json`), 'utf-8'))
  const p = saved.canvases[0].objects[0]
  expect([p.x, p.y, p.w, p.h, p.nativeW, p.nativeH]).toEqual([20, 15, 80, 57.6, 80, 57.6])
  expect(p.overrides).toEqual([EDIT, { gid: 'figure', prop: 'frame', value: 'figsize' }])
})

/**
 * 教程 Fig1_kinetics（`paper_style.save` → `savefig(bbox_inches="tight", pad_inches=0.02)`）：
 * 快速编辑里 X 轴标题整条都在图里、点它下半截选中的就是它。修之前这条标题的包围盒纵向
 * 0.980–1.030，下半截落在图幅外被 SVG 切掉、点下去落在舞台上（ADR 0086 的命中排查表）。
 */
test('快速编辑：tight 存盘的 Fig1 的 X 轴标题完整、下半截点得中', async ({ app, page }) => {
  const a = await app()
  await page.goto(a.baseURL)
  await page.getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
  const svg = page.locator('[data-element-svg] svg').first()
  await expect(svg).toBeVisible({ timeout: 120_000 })
  await page.waitForTimeout(1500)
  const geo = await page.evaluate(() => {
    const root = document.querySelector('[data-element-svg] svg')
    const label = root?.querySelector('[id="axes_0.xlabel"]')
    if (!root || !label) return null
    const r = root.getBoundingClientRect()
    const l = label.getBoundingClientRect()
    return { svg: { top: r.top, bottom: r.bottom }, label: { x: l.x, y: l.y, w: l.width, h: l.height } }
  })
  expect(geo, 'SVG 里应当有 X 轴标题').not.toBeNull()
  expect(geo!.label.y + geo!.label.h).toBeLessThanOrEqual(geo!.svg.bottom + 0.5)
  // 下半截（离下沿 1/4 高处）：修之前这一带在图幅外
  await page.mouse.click(geo!.label.x + geo!.label.w / 2, geo!.label.y + geo!.label.h * 0.75)
  await expect
    .poll(() => page.evaluate(() => document.querySelector('[data-gid]')?.getAttribute('data-gid') ?? null))
    .toBe('axes_0.xlabel')
})
