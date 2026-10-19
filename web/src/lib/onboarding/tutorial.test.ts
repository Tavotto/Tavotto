/**
 * 教程与示例项目的入口动作。两件事分开钉：
 *
 *   * 三步原生教程（ADR 0136）：入口只改 onboardingStore，**一个后端接口都不调**（没有 Python、
 *     没有依赖准备、没有教程资源请求）；遥测按「开始 / 每步完成一次 / 完成」记；
 *   * 示例项目（ADR 0039）：只从 `/api/tutorial/*` 与元数据拿一切；示例画布的 documentId 必须是
 *     `document_id`；同一项目不再走认领；失败按 code 分类；重置先确认、忘掉本机那格 autosave；
 *     **onboarding 一个字段都不碰**。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PanelInfo, TutorialMetadata } from '@/lib/api'
import type { PanelObject } from '@/types/document'
import { setTelemetryEnabled } from '@/lib/telemetry'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore, isAutosaveSuspendedFor, restoreSession } from '@/store/documentStore'
import { startDocumentLoadSync, syncLoadedDocument } from '@/store/liveSync'
import {
  configureOnboardingPersistence,
  ONBOARDING_FLOW_VERSION,
  useOnboardingStore,
} from '@/store/onboardingStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { emptyProject } from '@/types/document'
import {
  finishTutorial,
  hasSampleProject,
  homeVariant,
  leaveFromWelcome,
  loadTutorialStatus,
  markDemoStep,
  openSampleProject,
  resetTutorial,
  restartTutorial,
  runTutorialEntry,
  skipTutorial,
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

let calls: { url: string; method: string; body?: string }[]
let responses: Record<string, () => Response>

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

function stubFetch(over: Record<string, () => Response> = {}) {
  responses = {
    '/api/tutorial/open': () => json({ project: PROJECT, tutorial: META, reset: false, created: true, repaired: [] }),
    '/api/tutorial/reset': () => json({ project: PROJECT, tutorial: META, reset: true, cleared: ['tavotto-tutorial.json'] }),
    '/api/tutorial': () =>
      json({
        available: true,
        problems: [],
        tutorial_version: 1,
        metadata: META,
        copy: { exists: true, complete: true, missing: [], registry_ok: true, layouts: ['MyDraft'] },
      }),
    '/api/layouts/Tutorial': () => json(LAYOUT),
    '/api/layouts': () => json({ layouts: ['Tutorial', 'MyDraft'] }),
    '/api/autosave/tavotto-tutorial': () => json({ error: 'nope' }, 404),
    ...over,
  }
  calls = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://x').pathname
    calls.push({ url, method: init?.method ?? 'GET', body: typeof init?.body === 'string' ? init.body : undefined })
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
  setTelemetryEnabled(false)
})

const telemetryEvents = () =>
  calls
    .filter((c) => c.url === '/api/telemetry/event')
    .map((c) => JSON.parse(c.body ?? '{}') as { event: string; properties?: Record<string, unknown> })

describe('三步原生教程的入口（ADR 0136）', () => {
  it('入口打开教程层：没开始 / 已完成 / 已跳过的从第一步开始，进行中 / 暂停的接着那一步', () => {
    expect(runTutorialEntry('picker')).toBe('started')
    const ob = () => useOnboardingStore.getState()
    expect(ob().surface).toBe('tutorial')
    expect(ob().status).toBe('active')
    expect(ob().currentStep).toBe('demo_import')
    markDemoStep('demo_import')
    ob().next()
    ob().closeSurface()
    expect(runTutorialEntry('help')).toBe('resumed')
    expect(ob().currentStep).toBe('demo_move_legend')
    ob().complete()
    expect(tutorialEntry()).toBe('restart')
    expect(runTutorialEntry('settings')).toBe('started')
    expect(ob().currentStep).toBe('demo_import')
    expect(ob().completedSteps).toEqual([])
  })

  it('P0：从入口到完成，一个后端接口都不调（不跑脚本、不准备依赖、不取教程资源）', async () => {
    // beforeEach 给「之前那份文档」落过一笔：它的自动保存是异步的，先让它落地再开始数
    await new Promise((r) => setTimeout(r, 20))
    calls = []
    runTutorialEntry('picker')
    markDemoStep('demo_import')
    useOnboardingStore.getState().next()
    useOnboardingStore.getState().setDemo({ legendOffset: [0, -20] })
    markDemoStep('demo_move_legend')
    useOnboardingStore.getState().next()
    useOnboardingStore.getState().setDemo({ titleSizePt: 11 })
    markDemoStep('demo_change_title')
    useOnboardingStore.getState().next()
    expect(useOnboardingStore.getState().currentStep).toBe('done')
    finishTutorial()
    expect(useOnboardingStore.getState().status).toBe('completed')
    expect(useOnboardingStore.getState().surface).toBe('none')
    // 整条路是同步的：中间一个请求都没有发出去
    expect(calls).toEqual([])
    // 之后也没有补发的（遥测没同意：连 telemetry 端点都不该有；更不该有 run / prepare / tutorial / env 一类）
    await new Promise((r) => setTimeout(r, 20))
    expect(calls).toEqual([])
  })

  it('「下一步」不替用户声称完成：这一步没做到时 next 不动', () => {
    runTutorialEntry('picker')
    useOnboardingStore.getState().next()
    expect(useOnboardingStore.getState().currentStep).toBe('demo_import')
    markDemoStep('demo_import')
    useOnboardingStore.getState().next()
    expect(useOnboardingStore.getState().currentStep).toBe('demo_move_legend')
  })

  it('跳过不是完成；欢迎页「直接进入工作台」：没开始的只收起，退回欢迎页的进行中教程记成跳过', () => {
    useOnboardingStore.getState().openWelcome()
    leaveFromWelcome()
    expect(useOnboardingStore.getState().status).toBe('not_started')
    expect(useOnboardingStore.getState().surface).toBe('none')
    runTutorialEntry('picker')
    useOnboardingStore.getState().openWelcome()
    leaveFromWelcome()
    expect(useOnboardingStore.getState().status).toBe('skipped')
    expect(useOnboardingStore.getState().completedAt).toBeNull()
    runTutorialEntry('picker')
    skipTutorial()
    expect(useOnboardingStore.getState().status).toBe('skipped')
    expect(useOnboardingStore.getState().surface).toBe('none')
  })

  it('重新体验：回到第一步，演示改动清空', () => {
    runTutorialEntry('picker')
    useOnboardingStore.getState().setDemo({ legendOffset: [1, -20], titleSizePt: 12 })
    markDemoStep('demo_import')
    useOnboardingStore.getState().goTo('done')
    restartTutorial()
    const ob = useOnboardingStore.getState()
    expect(ob.currentStep).toBe('demo_import')
    expect(ob.demo).toEqual({ legendOffset: null, titleSizePt: null })
    expect(ob.completedSteps).toEqual([])
  })

  it('finishTutorial 只在完成页生效：别的步骤上调它什么都不做', () => {
    runTutorialEntry('picker')
    finishTutorial()
    expect(useOnboardingStore.getState().status).toBe('active')
  })
})

describe('教程遥测（ADR 0041）：开始 / 每步完成一次 / 完成', () => {
  it('真的开始了才记 tutorial_started：来源 + 流程版本，没有任何 id', () => {
    setTelemetryEnabled(true)
    runTutorialEntry('picker')
    const started = telemetryEvents().filter((e) => e.event === 'tutorial_started')
    expect(started).toHaveLength(1)
    expect(started[0]).toMatchObject({
      properties: { source: 'picker', tutorial_version: ONBOARDING_FLOW_VERSION },
    })
  })

  it('完成页「重新体验」是一次新的开始：记 tutorial_started（来源 replay）', () => {
    setTelemetryEnabled(true)
    runTutorialEntry('picker')
    calls = []
    restartTutorial()
    const started = telemetryEvents().filter((e) => e.event === 'tutorial_started')
    expect(started).toHaveLength(1)
    expect(started[0]).toMatchObject({ properties: { source: 'replay', tutorial_version: ONBOARDING_FLOW_VERSION } })
  })

  it('继续不是开始：进行中再进入口不记', () => {
    setTelemetryEnabled(true)
    runTutorialEntry('help')
    useOnboardingStore.getState().closeSurface()
    calls = []
    expect(runTutorialEntry('help')).toBe('resumed')
    expect(telemetryEvents()).toEqual([])
  })

  it('每步只记一次（返回再做一遍不算又完成了一步）；完成页记 done + tutorial_completed；跳过什么都不记', () => {
    setTelemetryEnabled(true)
    runTutorialEntry('picker')
    calls = []
    markDemoStep('demo_import')
    markDemoStep('demo_import')
    useOnboardingStore.getState().next()
    markDemoStep('demo_move_legend')
    useOnboardingStore.getState().next()
    markDemoStep('demo_change_title')
    useOnboardingStore.getState().next()
    finishTutorial()
    const steps = telemetryEvents()
      .filter((e) => e.event === 'tutorial_step_completed')
      .map((e) => e.properties?.step_id)
    expect(steps).toEqual(['demo_import', 'demo_move_legend', 'demo_change_title', 'done'])
    expect(telemetryEvents().filter((e) => e.event === 'tutorial_completed')).toHaveLength(1)

    runTutorialEntry('picker')
    calls = []
    skipTutorial()
    expect(telemetryEvents()).toEqual([])
  })

  it('没同意就一条都不发', () => {
    setTelemetryEnabled(false)
    runTutorialEntry('settings')
    markDemoStep('demo_import')
    expect(telemetryEvents()).toEqual([])
  })
})

describe('主页两版的判据 homeVariant（只读 onboarding 状态）', () => {
  it('已完成 / 已跳过 → 老手版；没开始 / 进行中 / 暂停 → 新手版', () => {
    expect(homeVariant('completed')).toBe('returning')
    expect(homeVariant('skipped')).toBe('returning')
    expect(homeVariant('not_started')).toBe('newcomer')
    expect(homeVariant('active')).toBe('newcomer')
    expect(homeVariant('paused')).toBe('newcomer')
  })

  it('不传参就读 store 当前那份；清掉本机数据（resetOnboarding）回到新手版', () => {
    useOnboardingStore.getState().start()
    useOnboardingStore.getState().complete()
    expect(homeVariant()).toBe('returning')
    useOnboardingStore.getState().resetOnboarding()
    expect(homeVariant()).toBe('newcomer')
  })
})

describe('openSampleProject：只打开示例项目', () => {
  it('同一条认领链路装示例画布，但 onboarding 一个字段都不动、不记 tutorial_started', async () => {
    useOnboardingStore.getState().start()
    useOnboardingStore.getState().complete()
    const before = { ...useOnboardingStore.getState() }
    setTelemetryEnabled(true)
    const out = await openSampleProject()
    expect(out).toEqual({ ok: true, kind: 'opened' })
    expect(useProjectStore.getState().project?.id).toBe('p_tut')
    expect(useDocumentStore.getState().documentId).toBe('tavotto-tutorial')
    const after = useOnboardingStore.getState()
    expect(after.status).toBe('completed')
    expect(after.completedAt).toBe(before.completedAt)
    expect(after.startedAt).toBe(before.startedAt)
    expect(after.surface).toBe('none')
    expect(homeVariant()).toBe('returning')
    expect(telemetryEvents().filter((p) => p.event === 'tutorial_started')).toEqual([])
  })

  it('三步教程正进行到一半：打开示例项目不碰它（不暂停、不推进、不清演示改动）', async () => {
    runTutorialEntry('picker')
    useOnboardingStore.getState().setDemo({ titleSizePt: 11 })
    useOnboardingStore.getState().closeSurface()
    await openSampleProject()
    const ob = useOnboardingStore.getState()
    expect(ob.status).toBe('active')
    expect(ob.currentStep).toBe('demo_import')
    expect(ob.demo.titleSizePt).toBe(11)
  })

  it('hasSampleProject：后端说副本在、它在最近列表里、或此刻就开着', () => {
    expect(hasSampleProject()).toBe(false)
    useProjectStore.setState({ recent: [{ path: '/x', name: 'Tutorial', tutorial: true }] as never })
    expect(hasSampleProject()).toBe(true)
    useProjectStore.setState({ recent: [] })
    useTutorialStore.setState({
      status: { available: true, problems: [], copy: { exists: true, complete: true, missing: [], registry_ok: true } },
    })
    expect(hasSampleProject()).toBe(true)
  })
})

describe('openSampleProject（示例项目，ADR 0039）', () => {
  it('open → 认领项目 → 示例画布用 document_id 打开；onboarding 不动', async () => {
    const out = await openSampleProject()
    expect(out).toEqual({ ok: true, kind: 'opened' })
    expect(calls.some((c) => c.url === '/api/tutorial/open' && c.method === 'POST')).toBe(true)
    expect(useProjectStore.getState().phase).toBe('open')
    expect(useProjectStore.getState().project?.id).toBe('p_tut')
    expect(useDocumentStore.getState().documentId).toBe('tavotto-tutorial')
    expect(useDocumentStore.getState().doc.objects.map((o) => o.id)).toEqual(['p1'])
    expect(useOnboardingStore.getState().status).toBe('not_started')
    expect(useTutorialStore.getState().meta).toEqual(META)
    // 不读仓库根 examples/、不碰别的端点
    expect(calls.every((c) => !c.url.includes('examples'))).toBe(true)
  })

  it('状态探测（GET /api/tutorial）还在飞时点「用示例了解」照样开始，不判成打不开', async () => {
    // 项目选择器一挂载就 loadTutorialStatus()，用户紧接着点入口——两者只差几毫秒。
    // 探测是只读的，不该与 open 互斥；互斥的只有 open / reset 自己。
    let release: (() => void) | null = null
    stubFetch({
      '/api/tutorial': () => json({ available: true, problems: [], tutorial_version: 1, metadata: META }),
    })
    const slow = new Promise<void>((r) => (release = r))
    const realFetch = globalThis.fetch
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      if (new URL(String(input), 'http://x').pathname === '/api/tutorial') await slow
      return realFetch(input, init)
    }) as typeof fetch
    const probe = loadTutorialStatus()
    expect(useTutorialStore.getState().busy).toBe('status')
    const out = await openSampleProject()
    expect(out.ok).toBe(true)
    expect(calls.some((c) => c.url === '/api/tutorial/open' && c.method === 'POST')).toBe(true)
    release!()
    await probe
    // 真正互斥的仍然互斥：open 在飞时再 open 是打不开
    useTutorialStore.setState({ busy: 'open' })
    expect((await openSampleProject()).ok).toBe(false)
    useTutorialStore.setState({ busy: 'reset' })
    expect((await openSampleProject()).ok).toBe(false)
  })

  it('本机 / 磁盘有进度就用进度（不换成干净画布）', async () => {
    stubFetch({
      // 自动保存端点回的就是文档本身（修订号在响应头里）
      '/api/autosave/tavotto-tutorial': () =>
        json({ ...LAYOUT, updatedAt: 99, canvases: [{ ...LAYOUT.canvases[0], objects: [] }] }),
    })
    await openSampleProject()
    expect(useDocumentStore.getState().documentId).toBe('tavotto-tutorial')
    expect(useDocumentStore.getState().doc.objects).toEqual([])
  })

  /**
   * 本机槽位里留一份上一份副本的教程画布（内容不同：对象清空了）。**槽位必须在索引里**：
   * 不在索引里的槽位会被 `writeIndex` 当孤儿扫掉，那样用例根本量不到「忘没忘掉」
   * （第一版就是这么恒真的——变异掉 forgetLocalDocument 照样绿）。
   */
  const seedStaleLocalSlot = () => {
    localStorage.setItem(
      'tavotto.autosave.tavotto-tutorial',
      JSON.stringify({ ...LAYOUT, updatedAt: Date.now() + 10_000, canvases: [{ ...LAYOUT.canvases[0], objects: [] }] }),
    )
    const index = JSON.parse(localStorage.getItem('tavotto.docIndex') ?? '[]') as unknown[]
    index.unshift({ id: 'tavotto-tutorial', name: 'Tutorial', savedAt: Date.now(), objects: 0 })
    localStorage.setItem('tavotto.docIndex', JSON.stringify(index))
  }

  it('同一份副本再开（created=false）：本机那格进度照用', async () => {
    stubFetch({
      '/api/tutorial/open': () =>
        json({ project: PROJECT, tutorial: META, reset: false, created: false, repaired: [], cleared: [] }),
    })
    seedStaleLocalSlot()
    await openSampleProject()
    expect(useDocumentStore.getState().documentId).toBe('tavotto-tutorial')
    // 先证明尺子是活的：槽位真的被读到了
    expect(useDocumentStore.getState().doc.objects).toEqual([])
  })

  it('后端刚建了全新副本（首次 / 资源升级换了目录）：本机那格进度作废，装的是干净画布', async () => {
    seedStaleLocalSlot()
    await openSampleProject()
    expect(useDocumentStore.getState().documentId).toBe('tavotto-tutorial')
    expect(useDocumentStore.getState().doc.objects.map((o) => o.id)).toEqual(['p1'])
  })

  /**
   * 资源升级换了副本、而用户此刻就停在旧教程里（旧副本的项目开着、当前文档就是教程画布）。
   * 认领新项目的第一步是把当前文档冲刷落盘——那一刻项目 id 已经换成新副本的，
   * 于是旧布局在同一个教程 documentId 下又落回本机 / 磁盘槽位，`loadTutorialDocument`
   * 装回来的还是刚作废的旧布局。旧画布要在调 open 之前就从自动保存链路上摘下来（与重置同一条路）。
   */
  it('升级换副本时当前就是旧教程：换代那次冲刷不许把旧布局写回槽位，装的是干净画布', async () => {
    stubFetch({
      '/api/tutorial/open': () =>
        json({ project: { ...PROJECT, id: 'p_tut_old' }, tutorial: META, reset: false, created: true, repaired: [] }),
    })
    await openSampleProject()
    expect(useProjectStore.getState().project?.id).toBe('p_tut_old')
    expect(useDocumentStore.getState().documentId).toBe('tavotto-tutorial')
    // 在旧教程里挪过图：这就是升级后要作废的布局
    useDocumentStore.getState().commit({ key: 'literal', ns: 'common', values: { text: 'x' } }, (d) => {
      d.objects[0].x = 55
    })
    // 升级：后端建了新副本（新项目 id），磁盘槽位已清；本机槽位由 forgetLocalDocument 清
    stubFetch()
    const out = await openSampleProject()
    expect(out).toEqual({ ok: true, kind: 'opened' })
    expect(useProjectStore.getState().project?.id).toBe('p_tut')
    expect(useDocumentStore.getState().documentId).toBe('tavotto-tutorial')
    expect(useDocumentStore.getState().doc.objects.map((o) => [o.id, o.x])).toEqual([['p1', 10]])
    // 装回干净画布后自动保存恢复：新副本里的编辑照常落盘
    expect(isAutosaveSuspendedFor('tavotto-tutorial')).toBe(false)
  })

  it('同一副本再开（已在教程画布里、不换文档）：open 期间挂起，落地后接回', async () => {
    await openSampleProject()
    expect(useDocumentStore.getState().documentId).toBe('tavotto-tutorial')
    let suspendedDuringOpen: boolean | null = null
    stubFetch({
      '/api/tutorial/open': () => {
        suspendedDuringOpen = isAutosaveSuspendedFor('tavotto-tutorial')
        return json({ project: PROJECT, tutorial: META, reset: false, created: false, repaired: [], cleared: [] })
      },
    })
    const out = await openSampleProject()
    expect(out.ok).toBe(true)
    expect(suspendedDuringOpen).toBe(true)
    expect(isAutosaveSuspendedFor('tavotto-tutorial')).toBe(false)
  })

  it('升级后重启（元数据没取到、onboarding 也没记教程文档 id）：示例项目开着时点「示例」，open 期间照样摘下自动保存', async () => {
    await openSampleProject()
    expect(useDocumentStore.getState().documentId).toBe('tavotto-tutorial')
    // v3 用户：持久化后备（tutorialDocumentId）是 null；内存里的元数据也还没取回
    expect(useOnboardingStore.getState().tutorialDocumentId).toBeNull()
    useTutorialStore.setState({ meta: null, status: null })
    let suspendedDuringOpen: boolean | null = null
    stubFetch({
      '/api/tutorial/open': () => {
        suspendedDuringOpen = isAutosaveSuspendedFor('tavotto-tutorial')
        return json({ project: PROJECT, tutorial: META, reset: false, created: false, repaired: [], cleared: [] })
      },
    })
    const out = await openSampleProject()
    expect(out.ok).toBe(true)
    expect(suspendedDuringOpen).toBe(true)
    expect(isAutosaveSuspendedFor('tavotto-tutorial')).toBe(false)
  })

  it('示例项目里开着的是另一份排版、元数据没取到：先取齐元数据，只挂起真正的示例画布（不挂这份）', async () => {
    await openSampleProject()
    useTutorialStore.setState({ meta: null, status: null })
    useDocumentStore.setState({ documentId: 'd_user_draft' } as never)
    let draftSuspended: boolean | null = null
    let tutorialSuspended: boolean | null = null
    stubFetch({
      '/api/tutorial/open': () => {
        draftSuspended = isAutosaveSuspendedFor('d_user_draft')
        tutorialSuspended = isAutosaveSuspendedFor('tavotto-tutorial')
        return json({ project: PROJECT, tutorial: META, reset: false, created: false, repaired: [], cleared: [] })
      },
    })
    await openSampleProject()
    expect(calls.some((c) => c.url === '/api/tutorial' && c.method === 'GET')).toBe(true)
    expect(draftSuspended).toBe(false)
    expect(tutorialSuspended).toBe(false)
  })

  it('补取元数据在切换事务里：取的那段时间项目处于「切换中」，别的打开排在这次后面', async () => {
    await openSampleProject()
    useTutorialStore.setState({ meta: null, status: null })
    let release: (() => void) | null = null
    const slow = new Promise<void>((r) => (release = r))
    const realFetch = globalThis.fetch
    let switchingDuringProbe: boolean | null = null
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      if (new URL(String(input), 'http://x').pathname === '/api/tutorial') {
        switchingDuringProbe = useProjectStore.getState().switching
        await slow
      }
      return realFetch(input, init)
    }) as typeof fetch
    try {
      const p = openSampleProject()
      await vi.waitFor(() => expect(switchingDuringProbe).not.toBeNull())
      expect(switchingDuringProbe).toBe(true)
      release!()
      expect((await p).ok).toBe(true)
    } finally {
      globalThis.fetch = realFetch
    }
  })

  it('示例项目开着、元数据怎么也取不到：不开（不猜哪份是示例画布），按失败返回', async () => {
    await openSampleProject()
    useTutorialStore.setState({ meta: null, status: null })
    calls = []
    stubFetch({ '/api/tutorial': () => json({ error: 'boom' }, 500) })
    const out = await openSampleProject()
    expect(out.ok).toBe(false)
    expect(calls.filter((c) => c.url === '/api/tutorial/open')).toEqual([])
  })

  it('升级换副本时 open 失败：挂起被接回，旧教程的画布不晾在不保存的状态', async () => {
    stubFetch({
      '/api/tutorial/open': () =>
        json({ project: { ...PROJECT, id: 'p_tut_old' }, tutorial: META, reset: false, created: true, repaired: [] }),
    })
    await openSampleProject()
    expect(useDocumentStore.getState().documentId).toBe('tavotto-tutorial')
    stubFetch({ '/api/tutorial/open': () => json({ error: 'x', code: 'open_project_failed' }, 400) })
    const out = await openSampleProject()
    expect(out.ok).toBe(false)
    expect(isAutosaveSuspendedFor('tavotto-tutorial')).toBe(false)
  })

  it('已经在示例项目里：不再走认领（文档不换成空白）', async () => {
    await openSampleProject()
    const adopt = vi.spyOn(useProjectStore.getState(), 'adoptOpenedProject')
    useDocumentStore.getState().commit({ key: 'literal', ns: 'common', values: { text: 'x' } }, (d) => {
      d.page = { w: 1, h: 1 }
    })
    const out = await openSampleProject()
    expect(out).toEqual({ ok: true, kind: 'opened' })
    expect(adopt).not.toHaveBeenCalled()
    expect(useDocumentStore.getState().doc.page).toEqual({ w: 1, h: 1 })
    expect(calls.filter((c) => c.url === '/api/tutorial/reset')).toEqual([])
  })

  it('失败按 code 分类：资源坏 → unavailable；占用 → locked；404 → no_api；其余 → open_failed', async () => {
    stubFetch({
      '/api/tutorial/open': () =>
        json({ error: 'bad', code: 'tutorial_resources_missing', params: { reason: 'bad' } }, 500),
    })
    expect((await openSampleProject()).ok).toBe(false)
    expect(useTutorialStore.getState().failure?.reason).toBe('unavailable')
    stubFetch({
      '/api/tutorial/open': () => json({ error: 'busy', code: 'tutorial_locked', params: { reason: 'busy' } }, 409),
    })
    expect(useTutorialStore.getState().busy).toBeNull()
    await openSampleProject()
    expect(useTutorialStore.getState().failure?.reason).toBe('locked')
    stubFetch({ '/api/tutorial/open': () => json({ error: 'nf' }, 404) })
    await openSampleProject()
    expect(useTutorialStore.getState().failure?.reason).toBe('no_api')
    stubFetch({ '/api/tutorial/open': () => json({ error: 'x', code: 'open_project_failed' }, 400) })
    await openSampleProject()
    expect(useTutorialStore.getState().failure?.reason).toBe('open_failed')
    expect(useOnboardingStore.getState().status).toBe('not_started')
  })

  it('画布读不出来（layout 404 且没有 autosave）→ document_failed，不假装开始', async () => {
    stubFetch({ '/api/layouts/Tutorial': () => json({ error: 'nf' }, 404) })
    const out = await openSampleProject()
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

describe('教程文档带着恢复副本（#643 评审：待裁决事项不许丢）', () => {
  it('教程文档有待裁决的恢复副本：打开教程时横幅要问；工作台挂载的恢复不再读盘，也不重复', async () => {
    // 同一副本再开（`created: false`）：本机槽位不会被当成上一份副本的残留清掉
    stubFetch({
      '/api/autosave/tavotto-tutorial': () => json({ ...LAYOUT, updatedAt: 1 }),
      '/api/tutorial/open': () =>
        json({ project: PROJECT, tutorial: META, reset: false, created: false, repaired: [] }),
    })
    // 上一轮没裁决完的恢复副本（恢复槽位）：换项目时的索引清理不碰它
    localStorage.setItem('tavotto.recovery.tavotto-tutorial', JSON.stringify({ ...LAYOUT, updatedAt: 5 }))
    expect((await openSampleProject()).ok).toBe(true)
    expect(useDocumentStore.getState().documentId).toBe('tavotto-tutorial')
    expect(useDocumentStore.getState().docNotice).toMatchObject({ kind: 'recovery', docId: 'tavotto-tutorial' })
    // 工作台挂载：文档已经装好，不读盘；那句提示还在，而且只有这一句
    const reads = calls.filter((c) => c.url === '/api/autosave/tavotto-tutorial' && c.method === 'GET').length
    expect(await restoreSession()).toBe(false)
    expect(calls.filter((c) => c.url === '/api/autosave/tavotto-tutorial' && c.method === 'GET').length).toBe(reads)
    expect(useDocumentStore.getState().docNotice).toMatchObject({ kind: 'recovery', docId: 'tavotto-tutorial' })
  })
})

describe('resetTutorial', () => {
  it('先确认（列出另存的画布）；取消什么都不发', async () => {
    await openSampleProject()
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

  it('示例项目不是当前项目：确认框列出重置前现取的 /api/tutorial copy.layouts', async () => {
    useProjectStore.setState({ phase: 'open', project: { ...PROJECT, tutorial: false, id: 'p_other' } })
    useTutorialStore.setState({ meta: META })
    stubFetch({
      '/api/tutorial': () =>
        json({ available: true, problems: [], metadata: META, copy: { exists: true, complete: true, missing: [], registry_ok: true, layouts: ['MyDraft'] } }),
    })
    const p = resetTutorial()
    await vi.waitFor(() => expect(useUiStore.getState().confirm).not.toBeNull())
    const req = useUiStore.getState().confirm!
    expect(req.body.key).toBe('onboarding.reset.bodyWithLayouts')
    expect(req.body.values?.names).toBe('MyDraft')
    expect(calls.some((c) => c.url === '/api/layouts')).toBe(false) // 不读别的项目的列表
    req.resolve(false)
    await p
  })

  it('示例项目开着：确认框只列示例副本里的另存画布，不列 /api/layouts 混进来的数据目录画布（重置不删它们）', async () => {
    await openSampleProject()
    stubFetch({ '/api/layouts': () => json({ layouts: ['Tutorial', 'MyDraft', 'GlobalSketch'] }) })
    const p = resetTutorial()
    await vi.waitFor(() => expect(useUiStore.getState().confirm).not.toBeNull())
    const req = useUiStore.getState().confirm!
    expect(req.body.values?.names).toBe('MyDraft')
    req.resolve(false)
    await p
  })

  it('示例项目是当前项目但副本清点取不到：不弹「没有另存」的确认、不发重置，按失败返回', async () => {
    await openSampleProject()
    stubFetch({ '/api/tutorial': () => json({ error: 'boom' }, 500) })
    const out = await resetTutorial()
    expect(out.ok).toBe(false)
    expect(useUiStore.getState().confirm).toBeNull()
    expect(calls.filter((c) => c.url === '/api/tutorial/reset')).toEqual([])
  })

  it('示例项目不是当前项目、后端清点不全（layouts: null）或没给：不弹确认、不发重置，按失败返回', async () => {
    for (const layouts of [null, undefined]) {
      useProjectStore.setState({ phase: 'open', project: { ...PROJECT, tutorial: false, id: 'p_other' } })
      useTutorialStore.setState({ meta: META })
      useUiStore.setState({ confirm: null } as never)
      calls = []
      stubFetch({
        '/api/tutorial': () =>
          json({ available: true, problems: [], metadata: META, copy: { exists: true, complete: true, missing: [], registry_ok: true, layouts } }),
      })
      const out = await resetTutorial()
      expect(out.ok).toBe(false)
      expect(useUiStore.getState().confirm).toBeNull()
      expect(calls.filter((c) => c.url === '/api/tutorial/reset')).toEqual([])
    }
  })

  it('元数据一直取不到（探测失败 / 后端没给 metadata）：不弹确认、不发重置，按失败返回', async () => {
    await openSampleProject()
    useTutorialStore.setState({ meta: null, status: null })
    calls = []
    stubFetch({ '/api/tutorial': () => json({ error: 'boom' }, 500) })
    const out = await resetTutorial()
    expect(out.ok).toBe(false)
    expect(useUiStore.getState().confirm).toBeNull()
    expect(calls.filter((c) => c.url === '/api/tutorial/reset')).toEqual([])
  })

  it('示例项目不是当前项目且现取状态失败：不弹确认、不发重置，按失败返回', async () => {
    useProjectStore.setState({ phase: 'open', project: { ...PROJECT, tutorial: false, id: 'p_other' } })
    useTutorialStore.setState({ meta: META })
    stubFetch({ '/api/tutorial': () => json({ error: 'boom' }, 500) })
    const out = await resetTutorial()
    expect(out.ok).toBe(false)
    expect(useUiStore.getState().confirm).toBeNull()
    expect(calls.filter((c) => c.url === '/api/tutorial/reset')).toEqual([])
  })

  it('元数据还没取到（重启后示例项目开着）：先取齐再确认——另存的画布照样列出，重置期间照样挂起自动保存', async () => {
    await openSampleProject()
    useTutorialStore.setState({ meta: null, status: null })
    let suspendedDuringReset: boolean | null = null
    stubFetch({
      '/api/tutorial/reset': () => {
        suspendedDuringReset = isAutosaveSuspendedFor('tavotto-tutorial')
        return json({ project: PROJECT, tutorial: META, reset: true, cleared: ['tavotto-tutorial.json'] })
      },
    })
    const p = resetTutorial()
    await vi.waitFor(() => expect(useUiStore.getState().confirm).not.toBeNull())
    const req = useUiStore.getState().confirm!
    expect(req.body.key).toBe('onboarding.reset.bodyWithLayouts')
    expect(req.body.values?.names).toBe('MyDraft')
    req.resolve(true)
    expect((await p).ok).toBe(true)
    expect(suspendedDuringReset).toBe(true)
  })

  it('确认后：POST reset → 忘掉本机那格 → 换成干净画布；三步教程的进度不归它管', async () => {
    await openSampleProject()
    runTutorialEntry('picker')
    markDemoStep('demo_import')
    useOnboardingStore.getState().next()
    useDocumentStore.getState().commit({ key: 'literal', ns: 'common', values: { text: 'x' } }, (d) => {
      d.objects = []
    })
    // 本机槽位里留一份**合法但陈旧**的教程画布（比磁盘新、内容不同）：重置不忘掉它的话，
    // readAutosaveDoc 会把它当成"本机这份就是文档本身"装回来
    localStorage.setItem(
      'tavotto.autosave.tavotto-tutorial',
      JSON.stringify({ ...LAYOUT, updatedAt: Date.now() + 10_000, canvases: [{ ...LAYOUT.canvases[0], objects: [] }] }),
    )
    const p = resetTutorial()
    await new Promise((r) => setTimeout(r, 0))
    useUiStore.getState().confirm!.resolve(true)
    const out = await p
    expect(out).toEqual({ ok: true, kind: 'restarted' })
    expect(calls.some((c) => c.url === '/api/tutorial/reset' && c.method === 'POST')).toBe(true)
    expect(useDocumentStore.getState().documentId).toBe('tavotto-tutorial')
    expect(useDocumentStore.getState().doc.objects.map((o) => o.id)).toEqual(['p1'])
    expect(useOnboardingStore.getState().currentStep).toBe('demo_move_legend')
    // 教程那格本机 autosave 被忘掉（不然旧进度会被推回刚重置的磁盘槽位）；
    // 之前那份文档仍在最近文档索引里——别的文档一个没动
    // （切进干净画布时会重新落一次快照——那是新的，不是旧进度）
    expect(useDocumentStore.getState().recentDocs.some((e) => e.id === 'd_before')).toBe(true)
  })

  it('POST reset 带上确认时看到的清单（后端原样的 copy.layouts），后端据此在锁内重核', async () => {
    await openSampleProject()
    const p = resetTutorial()
    await vi.waitFor(() => expect(useUiStore.getState().confirm).not.toBeNull())
    useUiStore.getState().confirm!.resolve(true)
    expect((await p).ok).toBe(true)
    const post = calls.find((c) => c.url === '/api/tutorial/reset' && c.method === 'POST')!
    expect(JSON.parse(post.body!)).toEqual({ default: true, expected_layouts: ['MyDraft'] })
  })

  it('确认之后后端发现清单变了（409 tutorial_layouts_changed）：按失败返回、给出对应提示，挂起被接回', async () => {
    await openSampleProject()
    stubFetch({
      '/api/tutorial/reset': () =>
        json({ error: 'changed', code: 'tutorial_layouts_changed' }, 409),
    })
    const p = resetTutorial()
    await vi.waitFor(() => expect(useUiStore.getState().confirm).not.toBeNull())
    useUiStore.getState().confirm!.resolve(true)
    const out = await p
    expect(out.ok).toBe(false)
    if (!out.ok) {
      expect(out.reason).toBe('layouts_changed')
      expect(out.message.key).toBe('backend.tutorial_layouts_changed')
    }
    expect(isAutosaveSuspendedFor('tavotto-tutorial')).toBe(false)
  })

  it('重置期间教程画布被挂起：后端清槽位时前端一个字节都不写；装回干净画布后恢复', async () => {
    await openSampleProject()
    expect(useDocumentStore.getState().documentId).toBe('tavotto-tutorial')
    let suspendedDuringReset: boolean | null = null
    stubFetch({
      '/api/tutorial/reset': () => {
        suspendedDuringReset = isAutosaveSuspendedFor('tavotto-tutorial')
        return json({ project: PROJECT, tutorial: META, reset: true, cleared: ['tavotto-tutorial.json'] })
      },
    })
    const p = resetTutorial()
    await new Promise((r) => setTimeout(r, 0))
    useUiStore.getState().confirm!.resolve(true)
    const out = await p
    expect(out.ok).toBe(true)
    expect(suspendedDuringReset).toBe(true)
    expect(isAutosaveSuspendedFor('tavotto-tutorial')).toBe(false)
  })

  it('重置没做成（锁住）：挂起被接回，不把用户的画布晾在不保存的状态', async () => {
    await openSampleProject()
    stubFetch({
      '/api/tutorial/reset': () => json({ error: 'busy', code: 'tutorial_locked', params: { reason: 'busy' } }, 409),
    })
    const p = resetTutorial()
    await new Promise((r) => setTimeout(r, 0))
    useUiStore.getState().confirm!.resolve(true)
    await p
    expect(isAutosaveSuspendedFor('tavotto-tutorial')).toBe(false)
  })

  it('锁住（409 tutorial_locked）→ locked，进度不动', async () => {
    await openSampleProject()
    runTutorialEntry('picker')
    useOnboardingStore.getState().goTo('demo_change_title')
    stubFetch({
      '/api/tutorial/reset': () => json({ error: 'busy', code: 'tutorial_locked', params: { reason: 'busy' } }, 409),
    })
    const p = resetTutorial()
    await new Promise((r) => setTimeout(r, 0))
    useUiStore.getState().confirm!.resolve(true)
    const out = await p
    expect(out.ok).toBe(false)
    if (!out.ok) expect(out.reason).toBe('locked')
    expect(useOnboardingStore.getState().currentStep).toBe('demo_change_title')
  })
})

/**
 * 用户反馈 01：「在新手教学案例中，我双击示例图片，并不能进入图内编辑。」
 *
 * 教程的 `Tutorial.json` 里两块面板都**没有** `script`（它是随包分发的静态文件，
 * 不知道副本落在哪、注册表怎么解析）。`ObjectView` 双击的判据是 `obj.script`：
 * 缺席就落进裁剪。从选择器第一次进教程能用，只是因为工作台挂载那一次对账
 * （`syncLoadedDocument`）恰好把它补上了；「重新开始教程」、在别的项目里点
 * 「开始教程」、切回教程项目——这些路上文档是工作台挂载之后才换进来的，
 * 没有人再对第二次账，双击就此进不去。这里钉的是：换进来的教程画布也要按
 * 素材清单补上 `script`。
 */
describe('教程画布的面板要带 script（用户反馈 01：双击进不了图内编辑）', () => {
  const PANEL_INFO: PanelInfo = {
    id: 'Fig1_kinetics.pdf',
    name: 'Fig1_kinetics',
    folder: '.',
    kind: 'pdf',
    native_w_mm: 75,
    native_h_mm: 58,
    mtime: 1,
    script: 'fig1_kinetics.py',
    cost: 'light',
  }
  const PANELS = { figures_dir: '/data/tutorial/Tutorial', panels: [PANEL_INFO] }
  const scriptOfP1 = () => (useDocumentStore.getState().doc.objects[0] as PanelObject).script
  let stop: (() => void) | null = null

  beforeEach(() => {
    stubFetch({ '/api/panels': () => json(PANELS) })
    useAssetStore.setState({ panels: [], byId: {}, loaded: false })
  })
  afterEach(() => {
    stop?.()
    stop = null
  })

  it('「重新开始教程」装回的干净画布：面板按素材清单补上 script', async () => {
    // 第一次从选择器进教程：工作台随后挂载，挂载那一次对账把 script 补上
    // （这正是第一次能用、重开之后不能用的原因）
    await openSampleProject()
    syncLoadedDocument()
    expect(scriptOfP1()).toBe('fig1_kinetics.py')
    // 工作台挂着：换文档的对账订阅在（App.tsx 的 Workspace effect 起的那一个）
    stop = startDocumentLoadSync()

    const p = resetTutorial()
    await new Promise((r) => setTimeout(r, 0))
    useUiStore.getState().confirm!.resolve(true)
    expect(await p).toEqual({ ok: true, kind: 'restarted' })
    expect(useDocumentStore.getState().documentId).toBe('tavotto-tutorial')
    // 装回来的是随包分发的干净 Tutorial.json（没有 script）：换文档之后必须再对一次账
    expect(scriptOfP1()).toBe('fig1_kinetics.py')
  })

  it('在别的项目里打开示例项目（工作台已挂载）：示例画布的面板同样带上 script', async () => {
    useProjectStore.setState({ phase: 'open', project: { open: true, id: 'p_other', name: 'other' } } as never)
    stop = startDocumentLoadSync()
    const out = await openSampleProject()
    expect(out.ok).toBe(true)
    expect(useDocumentStore.getState().documentId).toBe('tavotto-tutorial')
    expect(scriptOfP1()).toBe('fig1_kinetics.py')
  })
})
