import type { Page } from '@playwright/test'
import { cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, realpathSync, renameSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { addSecondFigure, expect, freePort, test, waitForDocumentReady } from './fixtures'

/**
 * ADR 0211 第一期：项目挪走 / 复制之后，从项目**自己的**时间线找回排版；空白排版不再往项目里留空 `.vx`。
 *
 * 2026-11-25 实测（改动前 origin/main）：整夹挪走、没按 ⌘S，新路径打开是空白排版；每加载一次新路径，
 * 项目的 `tavottofile/versions/` 里多一个只有空 `open` 节点的 `.vx` 目录。
 *
 * 真后端、真页面、真文件系统：项目夹、数据目录、配置目录都在本用例自己的临时目录里（`startApp` 的 `stop()`
 * 只删它自己的 workdir），「挪走」= 真 `rename`，「复制」= 真 `cpSync`。端口固定，第二个实例与第一个同 origin
 * ——同一个浏览器 context 就是「本机存储完好」；新开 context 就是「换了一份空 localStorage」（换端口的桌面同一处境）。
 *
 * 时间线节点的时序用 `__TAVOTTO_TIMELINE_TIMING__` 注入（不改产品默认值）：停顿 300 ms 就拍第一个节点、两个节点
 * 至少隔 10 分钟——于是第一个节点只含第一张图，之后加的第二张图**只在本机槽位里**。重开后画布上是两张图，
 * 就证明用的是本机那份（含节点之后的修改），而不是节点。
 */

const TIMING = { debounceMs: 300, minGapMs: 600_000 }
const ok = (r: { status(): number }) => r.status() >= 200 && r.status() < 300

const vxDirs = (project: string): string[] => {
  const dir = path.join(project, 'tavottofile', 'versions')
  return existsSync(dir) ? readdirSync(dir).filter((n) => n.endsWith('.vx')).sort() : []
}

const owners = (dataDir: string): Record<string, string | null> => {
  const p = path.join(dataDir, 'state', 'layout-sessions.json')
  return existsSync(p) ? ((JSON.parse(readFileSync(p, 'utf8')) as { owners?: Record<string, string | null> }).owners ?? {}) : {}
}

/** 后端的项目键是归一化的绝对路径（大小写不敏感的卷上小写）：两边都取真实路径再比 */
const samePath = (key: string | null | undefined, dir: string): boolean => {
  if (!key) return false
  const real = (p: string) => {
    try {
      return realpathSync.native(p).toLowerCase()
    } catch {
      return p.toLowerCase()
    }
  }
  return real(key) === real(dir)
}

const currentDoc = (page: Page) => page.evaluate(() => localStorage.getItem('tavotto.currentDoc'))

async function openAndWait(page: Page, baseURL: string) {
  await page.goto(baseURL)
  await waitForDocumentReady(page)
}

test('整夹挪走 / 复制之后从项目自己的时间线找回排版，加载多次不留空 .vx', async ({ app, browser }) => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-projhist-'))
  try {
    const projA = path.join(root, 'projA')
    const projB = path.join(root, 'projB')
    const projC = path.join(root, 'projC')
    cpSync(path.join(import.meta.dirname, '..', '..', 'examples', 'figures'), projA, { recursive: true })
    const env = { TAVOTTO_DATA_DIR: path.join(root, 'data'), TAVOTTO_CONFIG_DIR: path.join(root, 'config') }
    mkdirSync(env.TAVOTTO_DATA_DIR, { recursive: true })
    const port = await freePort()

    // ---- 1. 旧路径里排一版：第一张图进时间线节点，第二张图只在本机槽位里 ----
    const ctx = await browser.newContext()
    await ctx.addInitScript((t) => {
      ;(window as unknown as { __TAVOTTO_TIMELINE_TIMING__: unknown }).__TAVOTTO_TIMELINE_TIMING__ = t
    }, TIMING)
    const a1 = await app({ figures: projA, env, port })
    const page = await ctx.newPage()
    await openAndWait(page, a1.baseURL)
    const node = page.waitForResponse(
      (r) => r.request().method() === 'POST' && /\/api\/versions\/[^/]+$/.test(new URL(r.url()).pathname) && ok(r),
      { timeout: 60_000 },
    )
    await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
    await expect(page.locator('[data-object-id]')).toHaveCount(1)
    const nodeRes = await node
    expect(((await nodeRes.json()) as { version?: unknown }).version, '第一个节点应当真的写进了时间线').toBeTruthy()
    const docId = decodeURIComponent(new URL(nodeRes.url()).pathname.split('/').pop()!)
    expect(docId).toMatch(/^d_/)
    const saved = page.waitForResponse(
      (r) => r.request().method() === 'PUT' && r.url().includes(`/api/autosave/${docId}`) && ok(r),
      { timeout: 30_000 },
    )
    await addSecondFigure(page)
    await expect(page.locator('[data-object-id]')).toHaveCount(2)
    await saved
    expect(vxDirs(projA)).toEqual([`${docId}.vx`])
    await page.close()
    await a1.stop()

    // ---- 2. 整夹挪走、没按 ⌘S：同一个浏览器 context（本机存储完好）打开新路径 ----
    renameSync(projA, projB)
    const a2 = await app({ figures: projB, env, port })
    const tab = await ctx.newPage()
    await openAndWait(tab, a2.baseURL)
    await expect(tab.locator('[data-object-id]'), '新路径打开就是上次的排版，节点之后的修改也在').toHaveCount(2, {
      timeout: 30_000,
    })
    expect(await currentDoc(tab)).toBe(docId)
    // 归属转到了新路径：之后按 last 正常恢复，不再靠项目历史
    await expect.poll(() => samePath(owners(env.TAVOTTO_DATA_DIR)[docId!], projB)).toBe(true)

    // ---- 3. 连续加载 3 次：项目里不新增 .vx，排版一直在 ----
    const before = vxDirs(projB)
    for (let i = 0; i < 3; i++) {
      await openAndWait(tab, a2.baseURL)
      await expect(tab.locator('[data-object-id]')).toHaveCount(2, { timeout: 30_000 })
    }
    expect(vxDirs(projB)).toEqual(before)
    await tab.close()

    // ---- 4. 换一份空的 localStorage（新 context）：同样找回 ----
    const fresh = await browser.newContext()
    expect((await fresh.storageState()).origins).toEqual([])
    const tabF = await fresh.newPage()
    await openAndWait(tabF, a2.baseURL)
    await expect(tabF.locator('[data-object-id]')).toHaveCount(2, { timeout: 30_000 })
    await fresh.close()
    await a2.stop()

    // ---- 5. 复制（旧路径还在）：复制品用项目内节点恢复出新 docId 的一份，两边互不影响 ----
    cpSync(projB, projC, { recursive: true })
    const ownerBefore = owners(env.TAVOTTO_DATA_DIR)[docId!]
    const blank = await browser.newContext()
    await blank.addInitScript((t) => {
      ;(window as unknown as { __TAVOTTO_TIMELINE_TIMING__: unknown }).__TAVOTTO_TIMELINE_TIMING__ = t
    }, TIMING)
    const a3 = await app({ figures: projC, env, port })
    const tabC = await blank.newPage()
    await openAndWait(tabC, a3.baseURL)
    // 复制品里没有可用的本机槽位（槽位归原项目，原项目还在）：从最近一个非空节点恢复。上面几次打开
    // 已经给原项目打过有内容的「打开」节点（两张图），所以复制品里也是两张
    await expect(tabC.locator('[data-object-id]')).toHaveCount(2, { timeout: 30_000 })
    const copyId = await currentDoc(tabC)
    expect(copyId).toBeTruthy()
    expect(copyId).not.toBe(docId)
    await expect.poll(() => owners(env.TAVOTTO_DATA_DIR)[copyId!]).toBeTruthy()
    // 原项目的归属一个字节不动
    expect(owners(env.TAVOTTO_DATA_DIR)[docId!]).toBe(ownerBefore)
    // 在复制品里再加一张：只进复制品
    const copySaved = tabC.waitForResponse(
      (r) => r.request().method() === 'PUT' && r.url().includes(`/api/autosave/${copyId}`) && ok(r),
      { timeout: 30_000 },
    )
    await addSecondFigure(tabC, 'Fig2_yield.pdf')
    await expect(tabC.locator('[data-object-id]')).toHaveCount(3)
    await copySaved
    // 复制品重开 3 次（同一个 context）+ 换空存储再开一次：一直是同一份（按 last 恢复），不会每次另开一个新 docId、
    // 也不会在复制品里多出 .vx
    const copyVx = vxDirs(projC)
    expect(copyVx).toContain(`${copyId}.vx`)
    for (let i = 0; i < 3; i++) {
      await openAndWait(tabC, a3.baseURL)
      await expect(tabC.locator('[data-object-id]')).toHaveCount(3, { timeout: 30_000 })
      expect(await currentDoc(tabC)).toBe(copyId)
    }
    await blank.close()
    const blank2 = await browser.newContext()
    const tabC2 = await blank2.newPage()
    await openAndWait(tabC2, a3.baseURL)
    await expect(tabC2.locator('[data-object-id]')).toHaveCount(3, { timeout: 30_000 })
    expect(await currentDoc(tabC2)).toBe(copyId)
    await blank2.close()
    expect(vxDirs(projC)).toEqual(copyVx)
    await a3.stop()

    // 回到原项目：还是它自己的两张图（不是复制品里的），docId 不变
    const a4 = await app({ figures: projB, env, port })
    const back = await browser.newContext()
    const tabB = await back.newPage()
    await openAndWait(tabB, a4.baseURL)
    await expect(tabB.locator('[data-object-id]')).toHaveCount(2, { timeout: 30_000 })
    expect(await currentDoc(tabB)).toBe(docId)
    const figures = await tabB.evaluate(async (id) => {
      const doc = (await (await fetch(`/api/autosave/${id}`)).json()) as {
        canvases: { objects: { type: string; fileId?: string }[] }[]
      }
      return doc.canvases.flatMap((c) => c.objects).filter((o) => o.type === 'panel').map((o) => o.fileId).sort()
    }, docId)
    expect(figures).toEqual(['Fig1_kinetics.pdf', 'Fig2_correlation.pdf'])
    await back.close()
    await ctx.close()
  } finally {
    rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
  }
})

test('空白项目连续加载 3 次、回主页再回来一次：项目里不出现只有空「打开」「离开」节点的 .vx', async ({ app, page }) => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-blank-'))
  try {
    const proj = path.join(root, 'proj')
    cpSync(path.join(import.meta.dirname, '..', '..', 'examples', 'figures'), proj, { recursive: true })
    const a = await app({ figures: proj })
    const versionPosts: string[] = []
    page.on('request', (r) => {
      if (r.method() === 'POST' && /\/api\/versions\/[^/]+$/.test(new URL(r.url()).pathname)) versionPosts.push(r.url())
    })
    for (let i = 0; i < 3; i++) {
      // 「打开」节点在文档恢复完、工作台挂上之后才打：等恢复门 ready 再多等一拍，让它有机会发出去
      await openAndWait(page, a.baseURL)
      await page.waitForTimeout(1_500)
    }
    // 离开项目（回主页）= 「离开」时刻；再从主页回到当前项目 = 又一次「打开」
    await page.locator('[data-home-button]').first().click()
    const back = page.locator('[data-home-back-current]')
    await expect(back).toBeVisible({ timeout: 30_000 })
    await page.waitForTimeout(1_500)
    await back.click()
    await waitForDocumentReady(page)
    await page.waitForTimeout(1_500)
    expect(versionPosts, '空白排版不该发「打开」/「离开」节点').toEqual([])
    expect(vxDirs(proj)).toEqual([])
  } finally {
    rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
  }
})
