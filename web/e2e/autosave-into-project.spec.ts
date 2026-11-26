import { cpSync, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { addSecondFigure, expect, freePort, test, waitForDocumentReady } from './fixtures'

/**
 * ADR 0211 第二期 PR 2：自动保存把工作副本写进项目（PR 3 起默认开；应急回退 `TAVOTTO_LAYOUT_DIR_WRITER=0`）。
 *
 * 真后端、真页面、真文件系统。**不按任何键**：双击素材卡放一张图 → 停下 → 杀进程（SIGKILL，不给优雅退出的机会）→ 重开，
 * 最后一次编辑在项目的 `tavottofile/<名>.tavotto/layout.json` 里，重开就是它；再换一份全新的本机数据目录 + 空 localStorage
 * （换电脑 / 挪走），从项目里的工作副本（清单记着的 docId）找回同一个 docId 的排版。
 */

const ok = (r: { status(): number }) => r.status() >= 200 && r.status() < 300

const layoutDirs = (project: string): string[] => {
  const store = path.join(project, 'tavottofile')
  return existsSync(store) ? readdirSync(store).filter((n) => n.endsWith('.tavotto')) : []
}

const readLayout = (project: string, dir: string): { canvases: { objects: unknown[] }[] } =>
  JSON.parse(readFileSync(path.join(project, 'tavottofile', dir, 'layout.json'), 'utf8'))

const objectCount = (project: string, dir: string): number =>
  readLayout(project, dir).canvases.reduce((n, c) => n + c.objects.length, 0)

async function openAndWait(page: Page, baseURL: string) {
  await page.goto(baseURL)
  await waitForDocumentReady(page)
}

async function killHard(a: { proc: import('node:child_process').ChildProcess }) {
  a.proc.kill('SIGKILL')
  for (let i = 0; i < 60 && a.proc.exitCode === null && a.proc.signalCode === null; i++) {
    await new Promise((r) => setTimeout(r, 250))
  }
}

test('不按任何键：编辑自动写进项目的 layout.json，杀进程重开就是它；换空的本机存储也能从工作副本找回', { tag: '@feature:file.autosave-restore' }, async ({ app, browser }) => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-autosave-project-'))
  const started: { stop: () => Promise<void> }[] = []
  try {
    const project = path.join(root, 'proj')
    cpSync(path.join(import.meta.dirname, '..', '..', 'examples', 'figures'), project, { recursive: true })
    const dataA = path.join(root, 'dataA')
    mkdirSync(dataA, { recursive: true })
    // 写入端现在默认开（PR 3），不再带 TAVOTTO_LAYOUT_DIR_WRITER
    const base = { TAVOTTO_CONFIG_DIR: path.join(root, 'config') }
    const port = await freePort()

    // ---- 1. 编辑、停下，不按任何键 ----
    const ctx = await browser.newContext()
    const a1 = await app({ figures: project, env: { ...base, TAVOTTO_DATA_DIR: dataA }, port })
    started.push(a1)
    const page = await ctx.newPage()
    await openAndWait(page, a1.baseURL)
    await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
    await expect(page.locator('[data-object-id]')).toHaveCount(1)
    // 停手 5 s 之后才写项目（本机槽位 1 s 就写）：等 layout.json 出现，不发任何按键
    await expect.poll(() => layoutDirs(project).length, { timeout: 30_000 }).toBe(1)
    const dir = layoutDirs(project)[0]
    await expect.poll(() => objectCount(project, dir), { timeout: 30_000 }).toBe(1)
    const manifest = JSON.parse(readFileSync(path.join(project, 'tavottofile', dir, 'package_manifest.json'), 'utf8')) as { doc_id: string | null }
    expect(manifest.doc_id).toMatch(/^d_/)

    // 再改一笔 → 又写进去
    const saved = page.waitForResponse((r) => r.request().method() === 'PUT' && r.url().includes('project_write') && ok(r), { timeout: 30_000 })
    await addSecondFigure(page)
    await expect(page.locator('[data-object-id]')).toHaveCount(2)
    await saved
    await expect.poll(() => objectCount(project, dir), { timeout: 30_000 }).toBe(2)
    // 一个目录、没有留下暂存物
    expect(layoutDirs(project)).toEqual([dir])
    expect(readdirSync(path.join(project, 'tavottofile')).filter((n) => n.endsWith('.stage'))).toEqual([])
    await page.close()

    // ---- 2. 杀进程（不优雅），同一份本机数据重开：最后一次编辑在 ----
    await killHard(a1)
    const a2 = await app({ figures: project, env: { ...base, TAVOTTO_DATA_DIR: dataA }, port })
    started.push(a2)
    const tab = await ctx.newPage()
    await openAndWait(tab, a2.baseURL)
    await expect(tab.locator('[data-object-id]')).toHaveCount(2, { timeout: 30_000 })
    expect(objectCount(project, dir)).toBe(2)
    await tab.close()
    await killHard(a2)

    // ---- 3. 换电脑：全新的本机数据目录 + 空 localStorage，只剩项目文件夹 ----
    const dataB = path.join(root, 'dataB')
    mkdirSync(dataB, { recursive: true })
    const fresh = await browser.newContext()
    expect((await fresh.storageState()).origins).toEqual([])
    const a3 = await app({ figures: project, env: { ...base, TAVOTTO_DATA_DIR: dataB }, port })
    started.push(a3)
    const tabF = await fresh.newPage()
    await openAndWait(tabF, a3.baseURL)
    await expect(tabF.locator('[data-object-id]'), '工作副本找回：两张图都在').toHaveCount(2, { timeout: 30_000 })
    const docId = await tabF.evaluate(() => localStorage.getItem('tavotto.currentDoc'))
    expect(docId, '沿用清单里记的 docId').toBe(manifest.doc_id)
    // 没有另起一个目录
    expect(layoutDirs(project)).toEqual([dir])
    await fresh.close()
  } finally {
    for (const a of started) await a.stop().catch(() => undefined)
    try {
      rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
    } catch (e) {
      console.warn(`清理 ${root} 失败（已忽略）：${String(e)}`)
    }
  }
})
