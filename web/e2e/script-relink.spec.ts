import { cpSync, mkdirSync, mkdtempSync, readFileSync, renameSync, rmSync, unlinkSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import type { Page } from '@playwright/test'
import { expect, freePort, test, waitForDocumentReady } from './fixtures'

/**
 * ADR 0211 第一期 6：只挪了脚本（`fig1_kinetics.py` → `scripts/`），图的错误卡上一步接回——真后端、真 worker、真浏览器。
 *
 * * 有内容指纹（首次打开起草时记下）且项目里恰好一份字节相同：错误卡主按钮位是「接到 scripts/fig1_kinetics.py」，
 *   点了跑一遍、登记换到新位置、图回来，之前的图内修改（落盘文档里的 overrides）还在；
 * * 老登记没有指纹：给「选一个脚本接上」名单，选了同样接回。
 *
 * 改造前：只有「重试渲染」，重试必败；文案让用户把脚本放回原处。
 */

const OVERRIDE = { gid: 'figure', prop: 'facecolor', value: '#ffeecc' }
const isProbe = (r: { request(): { method(): string }; url(): string }) =>
  r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/registry/probe'

type Registry = { scripts: Record<string, { stems: string[]; sha256?: string }> }
const readRegistry = (proj: string) =>
  JSON.parse(readFileSync(path.join(proj, 'tavotto_registry.json'), 'utf8')) as Registry

async function placeFigureWithOverride(page: Page, baseURL: string): Promise<string> {
  await page.goto(baseURL)
  await waitForDocumentReady(page)
  const saved = page.waitForResponse(
    (r) => r.request().method() === 'PUT' && new URL(r.url()).pathname.startsWith('/api/autosave/') && r.ok(),
    { timeout: 120_000 },
  )
  await page.locator('[data-card="Fig1_kinetics.pdf"]').dblclick({ timeout: 30_000 })
  await expect(page.locator('[data-object-id]')).toHaveCount(1, { timeout: 60_000 })
  const docId = decodeURIComponent(new URL((await saved).url()).pathname.split('/').pop()!)
  // 图内修改：给这张图记一条 override（落盘文档里的事实，之后逐字比）
  await page.evaluate(
    async ({ id, override }) => {
      const res = await fetch(`/api/autosave/${id}`)
      const rev = res.headers.get('X-Tavotto-Revision') ?? ''
      const doc = (await res.json()) as { canvases: { objects: { type: string; overrides?: unknown[] }[] }[] }
      for (const c of doc.canvases) for (const o of c.objects) if (o.type === 'panel') o.overrides = [override]
      const put = await fetch(`/api/autosave/${id}?base_revision=${encodeURIComponent(rev)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(doc),
      })
      if (!put.ok) throw new Error(`autosave PUT ${put.status}`)
    },
    { id: docId, override: OVERRIDE },
  )
  return docId
}

async function savedOverrides(page: Page, docId: string) {
  return page.evaluate(async (id) => {
    const doc = (await (await fetch(`/api/autosave/${id}`)).json()) as {
      canvases: { objects: { type: string; overrides?: unknown[] }[] }[]
    }
    return doc.canvases.flatMap((c) => c.objects).filter((o) => o.type === 'panel').map((o) => o.overrides)
  }, docId)
}

/** 重开（新实例：旧 worker 里还缓存着挪走前的脚本），进图内编辑，等出「找不到这个脚本的文件」那张错误卡 */
async function reopenAndHitMissingScript(page: Page, baseURL: string) {
  await page.goto(baseURL)
  await waitForDocumentReady(page)
  await expect(page.locator('[data-object-id]')).toHaveCount(1, { timeout: 60_000 })
  await page.locator('[data-object-id]').dblclick()
  await expect(page.locator('[data-render-error]')).toBeVisible({ timeout: 120_000 })
}

test.describe('只挪了脚本：错误卡上一步接回', () => {
  test.setTimeout(420_000)

  for (const withFingerprint of [true, false]) {
    test(withFingerprint ? '有指纹且字节对上：「接到 scripts/fig1_kinetics.py」一键接回，图内修改还在' : '老登记没有指纹：从名单里选一个接上', async ({ app, page }) => {
      const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-e2e-relink-'))
      try {
        const proj = path.join(root, 'proj')
        cpSync(path.join(import.meta.dirname, '..', '..', 'examples', 'figures'), proj, { recursive: true })
        // 有指纹：拿掉示例自带的注册表，首次打开时起草（会写指纹）；没有指纹：留着示例那份老登记
        if (withFingerprint) unlinkSync(path.join(proj, 'tavotto_registry.json'))
        const env = { TAVOTTO_DATA_DIR: path.join(root, 'data'), TAVOTTO_CONFIG_DIR: path.join(root, 'config') }
        mkdirSync(env.TAVOTTO_DATA_DIR, { recursive: true })
        const port = await freePort()

        const a1 = await app({ figures: proj, env, port })
        const docId = await placeFigureWithOverride(page, a1.baseURL)
        expect(Boolean(readRegistry(proj).scripts['fig1_kinetics.py']?.sha256)).toBe(withFingerprint)
        await a1.stop()

        // 只挪脚本
        mkdirSync(path.join(proj, 'scripts'))
        renameSync(path.join(proj, 'fig1_kinetics.py'), path.join(proj, 'scripts', 'fig1_kinetics.py'))
        const a2 = await app({ figures: proj, env, port })
        await reopenAndHitMissingScript(page, a2.baseURL)

        const probed = page.waitForResponse(isProbe, { timeout: 180_000 })
        if (withFingerprint) {
          const one = page.locator('[data-script-relink="scripts/fig1_kinetics.py"]')
          await expect(one).toHaveText('接到 scripts/fig1_kinetics.py', { timeout: 30_000 })
          await one.click()
        } else {
          await expect(page.locator('[data-script-relink]')).toHaveCount(0)
          await page.locator('[data-script-relink-choose]').click({ timeout: 30_000 })
          await page.locator('[data-script-relink-option="scripts/fig1_kinetics.py"]').first().click()
        }
        expect((await probed).ok()).toBe(true)
        // 图回来：错误卡消失；登记换到新位置（带上新指纹）
        await expect(page.locator('[data-render-error]')).toHaveCount(0, { timeout: 120_000 })
        const reg = readRegistry(proj)
        expect(reg.scripts['scripts/fig1_kinetics.py']?.stems).toContain('Fig1_kinetics')
        expect(reg.scripts['scripts/fig1_kinetics.py']?.sha256).toMatch(/^[0-9a-f]{64}$/)
        expect(reg.scripts['fig1_kinetics.py']?.stems ?? []).not.toContain('Fig1_kinetics')
        // 图内修改还在（落盘文档逐字）
        expect(await savedOverrides(page, docId)).toEqual([[OVERRIDE]])
        await a2.stop()
      } finally {
        rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 })
      }
    })
  }
})
