import { mkdtempSync, writeFileSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { expect, test } from './fixtures'

// 真实界面 + 真实应用进程；转换会话与采用在 HTTP 边界上给确定的响应（与 convert-reference-retry 同口径：
// 模型运行与后端判定由 tests/test_convert_missing_outputs.py、test_convert_output_content.py 用真 worker 钉）。
test(
  '转换输出清单：状态词、缺的文件行内处理后可采用、让 AI 再对一次后清单更新',
  { tag: ['@feature:convert.output-checklist'] },
  async ({ app, page }) => {
    const root = mkdtempSync(path.join(os.tmpdir(), 'tavotto-convert-checklist-'))
    writeFileSync(path.join(root, 'plot.R'), 'write.csv(d, "a.csv")\nplot(1:3)\n')
    const row = (name: string, state: string, extra: object = {}) => ({ name, line: 1, state, ...extra })
    let realigned = 0
    let adoptBody: Record<string, unknown> | null = null
    const evidence = (files: object[]) => ({
      source_references: [], source_truncated: false, write_attempts: [], attempts_truncated: false, completeness: 'unknown', files,
    })
    const status = () => ({
      session: 'testout1', state: 'ready', script: 'plot.R', target_name: 'plot_matplotlib.py', library: null, source_language: 'r',
      has_preview: false, adopt_adds: ['plot_matplotlib.py'], realigning: false,
      comparison: {
        original: null, candidate: { figures: 1, width_mm: 50, height_mm: 40 }, aspect_delta: null, pixel_diff: null,
        reference: null, verification: 'unknown',
        output_evidence: evidence(
          realigned
            ? [row('same.csv', 'match'), row('diff.csv', 'match'), row('a.csv', 'missing'), row('z.csv', 'missing')]
            : [
                row('same.csv', 'match'),
                row('diff.csv', 'differs', { diff_count: 1, diffs: [{ row: 2, col: 1 }] }),
                row('old.xlsx', 'incomparable', { reason: 'stale' }),
                row('a.csv', 'missing'),
                row('z.csv', 'missing'),
              ],
        ),
      },
    })
    await page.addInitScript(() => localStorage.setItem('tavotto.onboarding', JSON.stringify({
      status: 'completed', currentStep: null, completedSteps: [], skippedSteps: [], hintSeen: {},
      startedAt: 1, completedAt: 2, tutorialProjectId: null, tutorialDocumentId: null, pausedBy: null,
    })))
    await page.route('**/api/ai/capabilities*', async (route) => {
      const real = await (await route.fetch()).json()
      const agent = {
        ...(real.agents?.[0] ?? {}), id: 'codex', display_name: 'Codex', icon_key: 'codex', state: 'ready',
        installed: true, enabled: true, usable: true, models: [], default_model: null, efforts: [], default_effort: null,
      }
      const connection = {
        id: 'chatgpt', vendor: 'chatgpt', label: 'ChatGPT', logo: 'chatgpt', state: 'ready', route: 'cli:codex',
        models: [], default_model: null, efforts: [], default_effort: null,
      }
      await route.fulfill({ json: { ...real, agents: [agent], connections: [connection] } })
    })
    await page.route(/\/api\/ai\/convert(?:\?|$)/, (route) =>
      route.request().method() === 'POST' ? route.fulfill({ json: { session: 'testout1', script: 'plot.R' } }) : route.continue())
    await page.route('**/api/ai/sessions/testout1*', (route) => route.fulfill({ json: {
      status: 'done', final: true, task: { outcome: 'completed', unmet: [] },
    } }))
    await page.route(/\/api\/ai\/convert\/testout1(?:\?|$)/, (route) => route.fulfill({ json: status() }))
    await page.route(/\/api\/ai\/convert\/testout1\/realign(?:\?|$)/, (route) => {
      realigned += 1
      return route.fulfill({ status: 202, json: { session: 'testout1', realigning: true } })
    })
    await page.route(/\/api\/ai\/convert\/testout1\/adopt(?:\?|$)/, (route) => {
      adoptBody = route.request().postDataJSON()
      return route.fulfill({ json: { script: 'plot_matplotlib.py', name: 'plot_matplotlib.py' } })
    })
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

      const list = card.locator('[data-convert-files]')
      await expect(list).toBeVisible({ timeout: 30_000 })
      const box = await list.boundingBox()
      expect(box && box.y >= 0 && box.y + box.height <= 900 && box.x >= 0 && box.x + box.width <= 1280).toBe(true)
      const stateOf = (name: string) => card.locator(`[data-convert-file="${name}"] [data-convert-file-state]`)
      await expect(stateOf('same.csv')).toHaveText('一致')
      await expect(stateOf('diff.csv')).toHaveText('不同')
      await expect(stateOf('old.xlsx')).toHaveText('没法比')
      await expect(stateOf('old.xlsx')).toHaveAttribute('title', '旧结果比输入数据还早，可能已经过期')
      const adopt = card.locator('[data-convert-adopt]')
      await expect(adopt).toHaveText('照样采用')

      // 让 AI 再对一次：发请求、轮询到新清单（不同 → 一致），采用钮回到「采用新版本」
      await card.locator('[data-convert-realign]').click()
      await expect(stateOf('diff.csv')).toHaveText('一致', { timeout: 15_000 })
      expect(realigned).toBe(1)
      await expect(adopt).toHaveText('采用新版本')

      // 缺的两个文件：先点采用只问一句，不采用
      await adopt.click()
      expect(adoptBody).toBeNull()
      await expect(card).toContainText('还有 2 个文件没处理，仍要采用吗？')
      // 「我来指定」取消选择框：那一行保持未处理
      page.once('dialog', (d) => void d.dismiss())
      await card.locator('[data-convert-file="a.csv"] [data-convert-file-specify]').click()
      await expect(card.locator('[data-convert-file="a.csv"] [data-convert-file-specify]')).toBeVisible()
      page.once('dialog', (d) => void d.accept('/somewhere/my-a.csv'))
      await card.locator('[data-convert-file="a.csv"] [data-convert-file-specify]').click()
      await expect(stateOf('a.csv')).toHaveText('已指定')
      await card.locator('[data-convert-file="z.csv"] [data-convert-file-skip]').click()
      await expect(stateOf('z.csv')).toHaveText('不需要')
      await expect(adopt).toHaveText('采用新版本')
      await adopt.click()
      await expect.poll(() => adoptBody).not.toBeNull()
      expect(adoptBody).toEqual({ specified_outputs: [{ name: 'a.csv', file: 'my-a.csv' }] })
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  },
)
