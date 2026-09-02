/**
 * 教程入口动作（ADR 0040）：只从 `/api/tutorial/*` 与元数据拿一切；教程画布的
 * documentId 必须是 `document_id`；同一项目不再走认领；失败按 code 分类；
 * 重置先确认、忘掉本机那格 autosave。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { TutorialMetadata } from '@/lib/api'
import { useDocumentStore } from '@/store/documentStore'
import { configureOnboardingPersistence, useOnboardingStore } from '@/store/onboardingStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject } from '@/types/document'
import {
  loadTutorialStatus,
  resetTutorial,
  startTutorial,
  tutorialEntry,
  useTutorialStore,
} from './tutorial'

const META: TutorialMetadata = {
  schema: 1,
  tutorial_version: 1,
  project_name: 'Tutorial',
  document_name: 'Tutorial',
  document_id: 'tavotto-tutorial',
  expected_stems: ['Fig1_kinetics', 'Fig2_correlation'],
  editable_role_preferences: ['title'],
  panels: [],
}

const LAYOUT = {
  schema: 3,
  project: { id: 'tavotto-tutorial', name: 'Tutorial' },
  canvases: [
    {
      id: 'c1',
      name: 'Figure 1',
      page: { w: 180, h: 90 },
      objects: [
        {
          id: 'p1',
          type: 'panel',
          fileId: 'Fig1_kinetics.pdf',
          fileKind: 'pdf',
          nativeW: 75,
          nativeH: 58,
          x: 10,
          y: 12,
          w: 75,
          h: 58,
          overrides: [],
        },
      ],
      guides: [],
    },
  ],
  activeCanvasId: 'c1',
  createdAt: 1,
  updatedAt: 1,
}

const PROJECT = { open: true, id: 'p_tut', name: 'Tutorial', figures_dir: '/data/tutorial/Tutorial', tutorial: true }

let calls: { url: string; method: string }[]
let responses: Record<string, () => Response>

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

function stubFetch(over: Record<string, () => Response> = {}) {
  responses = {
    '/api/tutorial/open': () => json({ project: PROJECT, tutorial: META, reset: false, created: true, repaired: [] }),
    '/api/tutorial/reset': () => json({ project: PROJECT, tutorial: META, reset: true, cleared: ['tavotto-tutorial.json'] }),
    '/api/tutorial': () => json({ available: true, problems: [], tutorial_version: 1, metadata: META }),
    '/api/layouts/Tutorial': () => json(LAYOUT),
    '/api/layouts': () => json({ layouts: ['Tutorial', 'MyDraft'] }),
    '/api/autosave/tavotto-tutorial': () => json({ error: 'nope' }, 404),
    ...over,
  }
  calls = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://x').pathname
    calls.push({ url, method: init?.method ?? 'GET' })
    const hit = responses[url]
    if (hit) return hit()
    return json({}, 200)
  }) as typeof fetch
}

beforeEach(async () => {
  localStorage.clear()
  configureOnboardingPersistence(null)
  useOnboardingStore.getState().resetOnboarding()
  useTutorialStore.setState({ status: null, meta: null, busy: null, failure: null })
  useUiStore.setState({ status: null, confirm: null })
  useProjectStore.setState({ phase: 'none', project: null, recent: [], opened: [] })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'd_before')
  // 给「之前那份文档」一点内容：空文档不落本机槽位，下面「别的槽位没动」就没得比
  useDocumentStore.getState().commit({ key: 'literal', ns: 'common', values: { text: 'seed' } }, (d) => {
    d.guides.push({ axis: 'x', pos: 10 })
  })
  stubFetch()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('startTutorial', () => {
  it('open → 认领项目 → 教程画布用 document_id 打开 → onboarding 从头开始', async () => {
    const out = await startTutorial()
    expect(out).toEqual({ ok: true, kind: 'started' })
    expect(calls.some((c) => c.url === '/api/tutorial/open' && c.method === 'POST')).toBe(true)
    expect(useProjectStore.getState().phase).toBe('open')
    expect(useProjectStore.getState().project?.id).toBe('p_tut')
    expect(useDocumentStore.getState().documentId).toBe('tavotto-tutorial')
    expect(useDocumentStore.getState().doc.objects.map((o) => o.id)).toEqual(['p1'])
    const ob = useOnboardingStore.getState()
    expect(ob.status).toBe('active')
    expect(ob.currentStep).toBe('welcome')
    expect(ob.tutorialProjectId).toBe('p_tut')
    expect(ob.tutorialDocumentId).toBe('tavotto-tutorial')
    expect(useTutorialStore.getState().meta).toEqual(META)
    // 不读仓库根 examples/、不碰别的端点
    expect(calls.every((c) => !c.url.includes('examples'))).toBe(true)
  })

  it('本机 / 磁盘有进度就用进度（不换成干净画布）', async () => {
    stubFetch({
      // 自动保存端点回的就是文档本身（修订号在响应头里）
      '/api/autosave/tavotto-tutorial': () =>
        json({ ...LAYOUT, updatedAt: 99, canvases: [{ ...LAYOUT.canvases[0], objects: [] }] }),
    })
    await startTutorial()
    expect(useDocumentStore.getState().documentId).toBe('tavotto-tutorial')
    expect(useDocumentStore.getState().doc.objects).toEqual([])
  })

  it('已经在教程项目里：不再走认领（文档不换成空白），暂停的教程继续', async () => {
    await startTutorial()
    useOnboardingStore.getState().pause('user')
    const adopt = vi.spyOn(useProjectStore.getState(), 'adoptOpenedProject')
    useDocumentStore.getState().commit({ key: 'literal', ns: 'common', values: { text: 'x' } }, (d) => {
      d.page = { w: 1, h: 1 }
    })
    const out = await startTutorial()
    expect(out).toEqual({ ok: true, kind: 'resumed' })
    expect(adopt).not.toHaveBeenCalled()
    expect(useDocumentStore.getState().doc.page).toEqual({ w: 1, h: 1 })
    expect(useOnboardingStore.getState().status).toBe('active')
  })

  it('完成之后再开 = 重新开始 onboarding，副本不换', async () => {
    await startTutorial()
    useOnboardingStore.getState().complete()
    expect(tutorialEntry()).toBe('restart')
    const out = await startTutorial()
    expect(out).toEqual({ ok: true, kind: 'started' })
    expect(useOnboardingStore.getState().currentStep).toBe('welcome')
    expect(calls.filter((c) => c.url === '/api/tutorial/reset')).toEqual([])
  })

  it('失败按 code 分类：资源坏 → unavailable；占用 → locked；404 → no_api；其余 → open_failed', async () => {
    stubFetch({
      '/api/tutorial/open': () =>
        json({ error: 'bad', code: 'tutorial_resources_missing', params: { reason: 'bad' } }, 500),
    })
    expect((await startTutorial()).ok).toBe(false)
    expect(useTutorialStore.getState().failure?.reason).toBe('unavailable')
    stubFetch({
      '/api/tutorial/open': () => json({ error: 'busy', code: 'tutorial_locked', params: { reason: 'busy' } }, 409),
    })
    expect(useTutorialStore.getState().busy).toBeNull()
    await startTutorial()
    expect(useTutorialStore.getState().failure?.reason).toBe('locked')
    stubFetch({ '/api/tutorial/open': () => json({ error: 'nf' }, 404) })
    await startTutorial()
    expect(useTutorialStore.getState().failure?.reason).toBe('no_api')
    stubFetch({ '/api/tutorial/open': () => json({ error: 'x', code: 'open_project_failed' }, 400) })
    await startTutorial()
    expect(useTutorialStore.getState().failure?.reason).toBe('open_failed')
    expect(useOnboardingStore.getState().status).toBe('not_started')
  })

  it('画布读不出来（layout 404 且没有 autosave）→ document_failed，不假装开始', async () => {
    stubFetch({ '/api/layouts/Tutorial': () => json({ error: 'nf' }, 404) })
    const out = await startTutorial()
    expect(out.ok).toBe(false)
    if (!out.ok) expect(out.reason).toBe('document_failed')
    expect(useOnboardingStore.getState().status).toBe('not_started')
  })

  it('loadTutorialStatus：GET 不到端点记 no_api（入口据此隐藏）', async () => {
    stubFetch({ '/api/tutorial': () => json({ error: 'nf' }, 404) })
    expect(await loadTutorialStatus()).toBeNull()
    expect(useTutorialStore.getState().failure?.reason).toBe('no_api')
    stubFetch({ '/api/tutorial': () => json({ available: false, problems: ['missing README.md'] }) })
    const st = await loadTutorialStatus()
    expect(st?.available).toBe(false)
  })
})

describe('resetTutorial', () => {
  it('先确认（列出另存的画布）；取消什么都不发', async () => {
    await startTutorial()
    const p = resetTutorial()
    await new Promise((r) => setTimeout(r, 0))
    const req = useUiStore.getState().confirm
    expect(req).not.toBeNull()
    expect(req!.body.key).toBe('onboarding.reset.bodyWithLayouts')
    expect(req!.body.values?.names).toBe('MyDraft')
    req!.resolve(false)
    const out = await p
    expect(out.ok).toBe(false)
    expect(calls.filter((c) => c.url === '/api/tutorial/reset')).toEqual([])
  })

  it('确认后：POST reset → 忘掉本机那格 → 换成干净画布 → onboarding 从头', async () => {
    await startTutorial()
    useOnboardingStore.getState().goTo('export_canvas')
    useDocumentStore.getState().commit({ key: 'literal', ns: 'common', values: { text: 'x' } }, (d) => {
      d.objects = []
    })
    localStorage.setItem('tavotto.autosave.tavotto-tutorial', '{"stale":true}')
    const p = resetTutorial()
    await new Promise((r) => setTimeout(r, 0))
    useUiStore.getState().confirm!.resolve(true)
    const out = await p
    expect(out).toEqual({ ok: true, kind: 'restarted' })
    expect(calls.some((c) => c.url === '/api/tutorial/reset' && c.method === 'POST')).toBe(true)
    expect(useDocumentStore.getState().documentId).toBe('tavotto-tutorial')
    expect(useDocumentStore.getState().doc.objects.map((o) => o.id)).toEqual(['p1'])
    expect(useOnboardingStore.getState().currentStep).toBe('welcome')
    // 教程那格本机 autosave 被忘掉（不然旧进度会被推回刚重置的磁盘槽位）；
    // 之前那份文档仍在最近文档索引里——别的文档一个没动
    // （切进干净画布时会重新落一次快照——那是新的，不是旧进度）
    expect(localStorage.getItem('tavotto.autosave.tavotto-tutorial') ?? '').not.toContain('stale')
    expect(useDocumentStore.getState().recentDocs.some((e) => e.id === 'd_before')).toBe(true)
  })

  it('锁住（409 tutorial_locked）→ locked，进度不动', async () => {
    await startTutorial()
    useOnboardingStore.getState().goTo('export_canvas')
    stubFetch({
      '/api/tutorial/reset': () => json({ error: 'busy', code: 'tutorial_locked', params: { reason: 'busy' } }, 409),
    })
    const p = resetTutorial()
    await new Promise((r) => setTimeout(r, 0))
    useUiStore.getState().confirm!.resolve(true)
    const out = await p
    expect(out.ok).toBe(false)
    if (!out.ok) expect(out.reason).toBe('locked')
    expect(useOnboardingStore.getState().currentStep).toBe('export_canvas')
  })
})
