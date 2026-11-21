import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { expect, test } from './fixtures'

/**
 * 设置 → 改图助手 →「记住修好的做法」（ADR 0197 第 3 层·在线）。真后端：本机记录文件先写好两条，
 * 界面里看、逐条删、全部删除（要确认）、关掉开关——每一步都核对后端那份文件 / 接口真的变了。
 */

const entry = (id: string, shape: string, kind: string, fields: Record<string, unknown>) => ({
  id,
  shape,
  kind,
  fields,
  script: 'fig.py',
  created: Date.now() / 1000,
  last_used: Date.now() / 1000,
  times: 1,
})

test(
  '记住修好的做法：设置里能看到每一条、逐条删、全部删除先确认、关掉开关后重新打开仍是关',
  { tag: '@feature:ai.fix-memory' },
  async ({ app, page }) => {
    const a = await app()
    const file = path.join(a.dataDir, 'ai', 'fix_memory.json')
    mkdirSync(path.dirname(file), { recursive: true })
    writeFileSync(
      file,
      JSON.stringify({
        schema: 1,
        entries: [
          entry('aaaaaaaaaaaa', 'script_needs_arguments', 'run_args', { args: ['--data', 'results/run3.csv'] }),
          entry('bbbbbbbbbbbb', 'missing_input', 'data_link', { asked: 'data/a.csv', linked: '~/lab/a.csv' }),
        ],
      }),
    )
    const onDisk = () => JSON.parse(readFileSync(file, 'utf-8')).entries.map((e: { id: string }) => e.id)

    await page.goto(a.baseURL)
    await page.locator('[data-rail="settings"]').click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('navigation').locator('[data-section="ai"]').click()
    const row = page.locator('[data-fix-memory]')
    await expect(row).toBeVisible({ timeout: 30_000 })
    // 能力表异步到，页面高度会变：滚到位再判，直到这一行稳定在视野里
    await expect(async () => {
      await row.scrollIntoViewIfNeeded()
      await expect(row).toBeInViewport()
    }).toPass()

    const view = page.locator('[data-fix-memory-view]')
    await expect(view).toBeVisible()
    await view.click()
    const list = page.locator('[data-fix-memory-list]')
    await expect(list.locator('[data-fix-memory-entry]')).toHaveCount(2)
    await expect(list.locator('[data-fix-memory-entry="aaaaaaaaaaaa"] code')).toHaveText('--data results/run3.csv')

    // 逐条删：界面少一行，本机文件里也没了
    await page.locator('[data-fix-memory-delete="aaaaaaaaaaaa"]').click()
    await expect(list.locator('[data-fix-memory-entry]')).toHaveCount(1)
    expect(onDisk()).toEqual(['bbbbbbbbbbbb'])

    // 全部删除先确认：取消不删，确认才删
    await page.locator('[data-fix-memory-clear]').click()
    await page.locator('[data-confirm-cancel]').click()
    expect(onDisk()).toEqual(['bbbbbbbbbbbb'])
    await page.locator('[data-fix-memory-clear]').click()
    await page.locator('[data-confirm-ok]').click()
    await expect(page.locator('[data-fix-memory-empty]')).toBeVisible()
    await expect
      .poll(async () => (await (await page.request.get(`${a.baseURL}/api/ai/fix-memory`)).json()).entries)
      .toEqual([])
    await page.keyboard.press('Escape')

    // 关掉开关：接口报关；关掉设置再打开，开关仍是关
    const sw = row.locator('[role="switch"]')
    await expect(sw).toHaveAttribute('aria-checked', 'true')
    await sw.click()
    await expect
      .poll(async () => (await (await page.request.get(`${a.baseURL}/api/ai/fix-memory`)).json()).enabled)
      .toBe(false)
    await page.keyboard.press('Escape')
    await page.locator('[data-rail="settings"]').click()
    await dialog.getByRole('navigation').locator('[data-section="ai"]').click()
    await expect(page.locator('[data-fix-memory] [role="switch"]')).toHaveAttribute('aria-checked', 'false')
  },
)
