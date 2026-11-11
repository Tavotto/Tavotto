import { mkdtempSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { expect, test, waitForFigureEdit } from './fixtures'

// 真实界面 + 真实应用进程：附件的暂存 / 删除走真后端端点（会话认证同其它 /api/ai/*），只在 HTTP 边界上替换
// 能力清单与 `/api/ai/run`（不起任何模型）。后端把附件放进候选 / 数据目录、提示词与清理由 tests/test_ai_attachments.py 用真桥钉。
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=',
  'base64',
)

test(
  '改图助手附文件：回形针 / 预览卡重叠且悬停那张在最上层可点 × / 发送只带暂存 id / 对话里两个气泡可展开',
  { tag: ['@feature:ai.attachments'] },
  async ({ app, page }) => {
    const a = await app()
    const dir = mkdtempSync(path.join(os.tmpdir(), 'tavotto-attach-'))
    const files = [path.join(dir, 'ref-a.png'), path.join(dir, '数据.csv'), path.join(dir, 'ref-b.png')]
    writeFileSync(files[0], PNG)
    writeFileSync(files[1], 'time,signal\n0,1.2\n1,2.8\n')
    writeFileSync(files[2], PNG)
    let runBody: Record<string, unknown> | null = null
    await page.route('**/api/ai/capabilities*', async (route) => {
      const real = await (await route.fetch()).json()
      const agent = {
        ...(real.agents?.[0] ?? {}), id: 'codex', display_name: 'Codex', icon_key: 'codex', state: 'ready',
        installed: true, enabled: true, usable: true, models: ['gpt-5.6-sol'], default_model: 'gpt-5.6-sol',
        efforts: ['low', 'medium'], default_effort: 'medium',
      }
      const connection = {
        id: 'chatgpt', vendor: 'chatgpt', label: 'ChatGPT', logo: 'chatgpt', state: 'ready', route: 'cli:codex',
        models: ['gpt-5.6-sol'], default_model: 'gpt-5.6-sol', efforts: ['low', 'medium'], default_effort: 'medium',
      }
      await route.fulfill({ json: { ...real, agents: [agent], connections: [connection] } })
    })
    await page.route('**/api/ai/run*', async (route) => {
      runBody = route.request().postDataJSON()
      await route.fulfill({ json: { session: 'attach-demo', script: 'fig1_kinetics.py' } })
    })
    await page.goto(a.baseURL)
    await page.locator('[data-asset-list]').getByText('Fig1_kinetics.pdf').dblclick({ timeout: 30_000 })
    await waitForFigureEdit(page)
    await page.getByRole('tab', { name: /改图助手|Assistant/i }).first().click()
    await expect(page.locator('[data-ai-composer]')).toBeVisible({ timeout: 30_000 })

    // 没有附件：没有预览行
    await expect(page.locator('[data-ai-attach-row]')).toHaveCount(0)
    await page.locator('[data-ai-attach-input]').setInputFiles(files)
    const cards = page.locator('[data-ai-attach-chip="ready"]')
    await expect(cards).toHaveCount(3, { timeout: 20_000 })
    const vp = page.viewportSize()!
    for (let i = 0; i < 3; i++) {
      const box = (await cards.nth(i).boundingBox())!
      expect(box.x).toBeGreaterThanOrEqual(0)
      expect(box.x + box.width).toBeLessThanOrEqual(vp.width)
    }
    // 相邻卡片重叠：后一张压住前一张的右侧
    const b0 = (await cards.nth(0).boundingBox())!
    const b1 = (await cards.nth(1).boundingBox())!
    expect(b1.x).toBeLessThan(b0.x + b0.width)

    // 悬停中间那张：它在最上层——它中心偏右的那一点（原本被第三张压住的位置）命中的是它自己，× 点得到
    await cards.nth(1).hover()
    await page.waitForTimeout(400)
    const mid = (await cards.nth(1).boundingBox())!
    const probe = { x: mid.x + mid.width * 0.85, y: mid.y + mid.height * 0.6 }
    const topIsMiddle = await page.evaluate(({ x, y }) => {
      const el = document.elementFromPoint(x, y)
      return el?.closest('[data-ai-attach-chip]')?.getAttribute('title') ?? null
    }, probe)
    expect(topIsMiddle).toBe('数据.csv')
    const x = cards.nth(1).locator('[data-ai-attach-remove]')
    await expect(x).toBeVisible()
    const xb = (await x.boundingBox())!
    expect(await page.evaluate(({ px, py }) => !!document.elementFromPoint(px, py)?.closest('[data-ai-attach-remove]'), { px: xb.x + xb.width / 2, py: xb.y + xb.height / 2 })).toBe(true)

    // 去掉 CSV 再把它加回来（× 走真的删除端点）
    await x.click()
    await expect(cards).toHaveCount(2)
    await page.locator('[data-ai-attach-input]').setInputFiles([files[1]])
    await expect(cards).toHaveCount(3, { timeout: 20_000 })

    await page.locator('[data-ai-input]').fill('按参考图的配色改，数据见 csv')
    await page.locator('[data-ai-send="send"]').click()
    await expect.poll(() => runBody).not.toBeNull()
    const ids = (runBody as unknown as { attachments?: string[] }).attachments ?? []
    expect(ids).toHaveLength(3)
    expect(ids.every((id) => /^[0-9a-f]{32}$/.test(id))).toBe(true)
    expect(JSON.stringify(runBody)).not.toContain('iVBOR') // 请求里只有暂存 id，没有文件字节

    // 发送后：预览行清空，用户消息 = 文字气泡 + 附件气泡（堆叠，点一下展开）
    await expect(page.locator('[data-ai-attach-row]')).toHaveCount(0)
    const stack = page.locator('[data-ai-user-attachments="stack"]')
    await expect(stack).toBeVisible()
    await expect(stack).toHaveAttribute('aria-expanded', 'false')
    await stack.click()
    await expect(stack).toHaveAttribute('aria-expanded', 'true')
    await stack.click()
    await expect(stack).toHaveAttribute('aria-expanded', 'false')
  },
)
