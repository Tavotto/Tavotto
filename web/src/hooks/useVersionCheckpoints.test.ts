/**
 * 自动检查点带上画布身份（R-03）。
 *
 * 这条与 `lib/versionTarget.test.ts` 是**两侧**：那边看"拿到身份之后往哪写"，
 * 这边看"身份到底有没有被写进去"。少了这一侧，判据修对了却没人喂它数据，
 * 每个检查点都是 `kind: 'unknown'`。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { emptyProject } from '@/types/document'
import type { TextObject } from '@/types/document'
import { useDocumentStore } from '@/store/documentStore'
import { startVersionCheckpoints } from './useVersionCheckpoints'

const text = (id: string): TextObject => ({
  id, type: 'text', text: 'x', sizePt: 9, bold: false,
  color: '#000', align: 'left', x: 0, y: 0, w: 20, h: 8,
})

const posts: { url: string; body: Record<string, unknown> }[] = []

beforeEach(async () => {
  posts.length = 0
  localStorage.clear()
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
    if (String(url).includes('/api/versions/') && init?.method === 'POST') {
      posts.push({ url: String(url), body: JSON.parse(String(init.body)) })
      return new Response('{"version":{"id":"v1"}}', { status: 200 })
    }
    return new Response('{}', { status: 404 })
  }) as typeof fetch
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_cp')
})

afterEach(() => vi.useRealTimers())

describe('自动检查点', () => {
  it('带上激活画布的 id 与当下的名字', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const stop = startVersionCheckpoints()
    const s = () => useDocumentStore.getState()

    const second = s().addCanvas('对照组')
    s().commit({ key: 'x', ns: 'workspace' }, (d) => {
      d.objects.push(text('t1'))
    })
    await vi.advanceTimersByTimeAsync(20_000)
    stop()

    expect(posts).toHaveLength(1)
    expect(posts[0].body).toMatchObject({
      auto: true,
      canvasId: second,
      canvasName: '对照组',
    })
    // 归档仍然按 documentId（整个项目）——画布身份是**加上去的一维**，
    // 不是把版本时间线拆成每张画布一条
    expect(posts[0].url).toContain('/api/versions/d_cp')
  })
})
