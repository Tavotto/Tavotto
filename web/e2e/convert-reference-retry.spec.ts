import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { expect, test } from './fixtures'

// Real built UI and project import; deterministic model responses at the HTTP
// boundary. API reference validation and actual model runs have separate gates.
test('转换缺少输入：确认前不发送参考，更正错误路径后能再次提交', async ({ app, page }) => {
  const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-convert-reference-'))
  writeFileSync(path.join(root, 'plot.R'), 'plot(1:3)\n')
  writeFileSync(path.join(root, 'colors.csv'), '1,0,0\n0,1,0\n')
  const posts: Record<string, unknown>[] = []
  await page.addInitScript(() => localStorage.setItem('tavotto.onboarding', JSON.stringify({
    status: 'completed', currentStep: null, completedSteps: [], skippedSteps: [], hintSeen: {},
    startedAt: 1, completedAt: 2, tutorialProjectId: null, tutorialDocumentId: null, pausedBy: null,
  })))
  await page.route(/\/api\/ai\/convert(?:\?|$)/, async (route) => {
    if (route.request().method() !== 'POST') return route.continue()
    posts.push(route.request().postDataJSON())
    if (posts.length === 2) return route.fulfill({ status: 400, json: { code: 'convert_reference_invalid', error: 'convert_reference_invalid' } })
    return route.fulfill({ json: { session: `testref${posts.length}`, script: 'plot.R' } })
  })
  await page.route('**/api/ai/sessions/testref*', (route) => route.fulfill({ json: {
    status: 'done', final: true,
    task: { outcome: 'needs_input', unmet: [{ constraint_id: 'intent', reason_code: 'needs_input', detail: '请提供原始色表' }] },
  } }))
  try {
    const a = await app({ noProject: true })
    await page.setViewportSize({ width: 1280, height: 900 })
    await page.goto(a.baseURL)
    const zone = page.locator('[data-home-dropzone]')
    await expect(zone).toBeVisible({ timeout: 30_000 })
    const dt = await page.evaluateHandle((uri) => {
      const d = new DataTransfer()
      d.setData('text/uri-list', uri)
      d.items.add(new File(['plot(1:3)'], 'plot.R', { type: 'text/plain' }))
      return d
    }, pathToFileURL(path.join(root, 'plot.R')).href)
    await zone.dispatchEvent('drop', { dataTransfer: dt })
    const card = page.locator('[data-foreign-card="r"]')
    await expect(card).toBeVisible()
    await page.getByRole('button', { name: '直接进入工作台', exact: true }).click()
    await card.locator('[data-foreign-start]').click()
    const input = card.locator('[data-convert-references]')
    await expect(input).toBeVisible()
    await input.fill('missing.csv')
    expect(posts).toHaveLength(1)
    await card.locator('[data-convert-references-retry]').click()
    await expect(input).toHaveValue('missing.csv')
    await expect(card.locator('[data-prep-line]')).toContainText('参考')
    await input.fill('colors.csv')
    expect(posts).toHaveLength(2)
    await card.locator('[data-convert-references-retry]').click()
    await expect.poll(() => posts.length).toBe(3)
    await expect(input).toBeVisible()
    expect(posts[1].reference_files).toEqual(['missing.csv'])
    expect(posts[2].reference_files).toEqual(['colors.csv'])
    expect(posts[2].client_request_id).not.toBe(posts[1].client_request_id)
    await expect(card.locator('button[data-convert-references-retry]')).toHaveCount(1)
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
})
