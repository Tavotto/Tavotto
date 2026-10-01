import { cpSync, existsSync, mkdtempSync, readdirSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { test, expect } from './fixtures'

const REPO = path.resolve(import.meta.dirname, '..', '..')

/**
 * 一个项目的排版不许漏进另一个项目（#715 Windows 真机验收 P1，2026-10-01）。
 *
 * 稳定端口（#718）之后，先后打开的项目共用同一个 origin、同一份 localStorage。验收现场：项目 F 里
 * 放了一张图 → 关掉 Tavotto → 打开新项目 G，G 一打开就是 F 的排版，G 的目录里还写出了同一个
 * doc_id 的时间线节点（`G/tavottofile/versions/<F 的 doc_id>.json`）。
 *
 * 这里用**同一个服务、同一个浏览器 context**（= 同一个 origin、同一份 localStorage、同一个数据目录）
 * 复现「关掉、再打开另一个项目」：F 的标签页关掉，后端把默认项目换成 G，开一个新标签页
 * （sessionStorage 是空的，与一次新启动同一处境）。修复前 G 落在 F 的排版上，这条红。
 */
test('同一个 origin 先后打开两个项目：B 不显示 A 的排版，B 的目录里没有 A 的排版', async ({ app, browser }) => {
  const a = await app()
  const projG = mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-projG-'))
  cpSync(path.join(REPO, 'examples', 'figures'), projG, { recursive: true })
  try {
    const ctx = await browser.newContext()
    const tabF = await ctx.newPage()
    await tabF.goto(a.baseURL)
    const card = (p: typeof tabF) => p.locator('[data-card="Fig1_kinetics.pdf"]')
    await expect(card(tabF)).toBeVisible({ timeout: 30_000 })
    // 判据落在后端真的收下了：自动保存与「上次开着的」两条 PUT 都回 2xx（等待器先挂上）
    const ok = (r: { status(): number }) => r.status() >= 200 && r.status() < 300
    const autosaved = tabF.waitForResponse(
      (r) => r.request().method() === 'PUT' && r.url().includes('/api/autosave/') && ok(r),
      { timeout: 30_000 },
    )
    const remembered = tabF.waitForResponse(
      (r) => r.request().method() === 'PUT' && r.url().includes('/api/layout-session/last') && ok(r),
      { timeout: 30_000 },
    )
    // 与验收现场同一个动作：双击素材卡放上画布（进图内编辑），再回到排版量对象
    await card(tabF).dblclick({ timeout: 30_000 })
    await tabF.locator('[data-context-back]').click()
    await expect(tabF.locator('[data-object-id]')).toHaveCount(1)
    await autosaved
    await remembered
    const docF = await tabF.evaluate(() => localStorage.getItem('tavotto.currentDoc'))
    // 前提：全局 currentDoc 记着 F 的排版——下面量的正是「它会不会被 G 认下」
    expect(docF).toBeTruthy()
    await tabF.close()

    // 「关掉 Tavotto、打开新项目 G」：后端的默认项目换成 G，新标签页 = 新的 sessionStorage
    const opened = await fetch(`${a.baseURL}/api/projects/open`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: projG, default: true }),
    })
    expect(opened.ok).toBe(true)
    const pjG = ((await opened.json()) as { id: string }).id

    const tabG = await ctx.newPage()
    const askedG = tabG.waitForResponse(
      (r) => r.request().method() === 'GET' && r.url().includes('/api/layout-session') && ok(r),
      { timeout: 30_000 },
    )
    await tabG.goto(a.baseURL)
    // 工作台起来了（左栏停在哪一页是本机 UI 偏好，跟着 origin 走——不拿素材卡当就绪信号）
    await expect(tabG.locator('[data-project-switcher]')).toBeVisible({ timeout: 30_000 })
    // 前提：这个标签页开着的确实是 G（不是 F），而且启动恢复已经问过后端「G 上次开着哪份」
    expect(await tabG.evaluate(() => sessionStorage.getItem('tavotto:project'))).toBe(pjG)
    await askedG
    // 恢复在问完后端之后还要读盘、装文档：给它时间。修复前这里出现的是 F 的那一个对象
    await tabG.waitForTimeout(2_000)
    await expect(tabG.locator('[data-object-id]')).toHaveCount(0)
    expect(await tabG.evaluate((pj) => localStorage.getItem(`tavotto.projectDoc.${pj}`), pjG)).toBeNull()
    const lastG = (await (await fetch(`${a.baseURL}/api/layout-session?pj=${pjG}`)).json()) as {
      last: { doc_id: string } | null
    }
    expect(lastG.last?.doc_id ?? null).not.toBe(docF)
    // G 的目录里没有 F 那份排版的任何副本（时间线节点按 doc_id 落在 tavottofile/versions/）
    const versions = path.join(projG, 'tavottofile', 'versions')
    const leaked = existsSync(versions) ? readdirSync(versions).filter((n) => n.startsWith(`${docF}.`)) : []
    expect(leaked).toEqual([])
    await ctx.close()
  } finally {
    rmSync(projG, { recursive: true, force: true })
  }
})
