/**
 * STATE-06 复现（**预期红 = 产品缺陷**，因此不进测试集）：
 * 项目 A 在途的 `/api/engine/render` 在用户切到项目 B（`renderStore.clear()`）之后才回来，
 * 成功分支无条件写 `byKey[key]` 并推进 `latest[fileId]`——A 的 SVG / manifest 落进 B。
 *
 * 规则出处：web/AGENTS.md「会在项目之间存活的 store 都有项目代际：clear() 换代 + 清 inflight，
 * A 项目的响应绝不落进 B」；renderStore 的失败分支已经按「发请求那一刻的 pj」挡了首开确认框
 * （`projectAtStart`），成功分支没有同样的闸。
 *
 * 跑法（vitest 只收 web/src 下的用例）：
 *   docs/qa/2026-09-24/state/repro/run.sh vitest-repro state06_cross_project_render.test.ts
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { EngineRenderOptions, Manifest } from '@/lib/api'
import { setCurrentProjectId } from '@/lib/session'
import {
  exactPanelManifest,
  panelDisplayView,
  renderKeyOf,
  useRenderStore,
} from '@/store/renderStore'
import type { PanelObject } from '@/types/document'

const engineRender = vi.fn()
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  engineRender: (id: string, patches: unknown[], opts?: EngineRenderOptions) =>
    engineRender(id, patches, opts),
}))

const manifestOf = (stem: string): Manifest =>
  ({ stem, size_mm: [80, 60], elements: [] }) as unknown as Manifest

// 两个项目里都有一张叫 fig1.pdf 的图（同名脚本很常见），面板都还没有 override
const panelIn = (id: string): PanelObject =>
  ({
    id,
    type: 'panel',
    x: 0,
    y: 0,
    w: 40,
    h: 30,
    fileId: 'fig1.pdf',
    fileKind: 'pdf',
    nativeW: 40,
    nativeH: 30,
    script: 'fig1.py',
    overrides: [],
  }) as unknown as PanelObject

beforeEach(() => {
  engineRender.mockReset()
  useRenderStore.getState().clear()
})
afterEach(() => setCurrentProjectId(null))

describe('STATE-06：A 项目在途的渲染回包不得落进 B', () => {
  it('切项目（clear）之后才回来的 A 响应：B 的渲染态里不应出现 A 的 manifest', async () => {
    setCurrentProjectId('proj-a')
    let resolveA!: (v: unknown) => void
    engineRender.mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolveA = r
        }),
    )
    const pendingA = useRenderStore.getState().render('fig1.pdf', [])
    await Promise.resolve()

    // 用户切到 B：projectStore.resetForNewProject() 做的正是这一句
    setCurrentProjectId('proj-b')
    useRenderStore.getState().clear()

    // A 的图姗姗来迟
    resolveA({ rev: 7, manifest: manifestOf('A-fig1'), svg: '<svg>A</svg>' })
    await pendingA

    const st = useRenderStore.getState()
    const b = panelIn('pB')
    // 期望：B 里什么都没有（B 自己的渲染还没发/没回）
    expect(st.byKey[renderKeyOf(b)]?.manifest?.stem, 'A 的 manifest 落进了 B 的变体格').toBeUndefined()
    expect(st.latest['fig1.pdf'], 'A 的回包推进了 B 的 latest 显示退路').toBeUndefined()
    expect(exactPanelManifest(st, b), 'A 的 manifest 成了 B 的几何权威').toBeNull()
    expect(panelDisplayView(st, b)?.kind).toBe('empty')
  })

  it('B 自己的同键渲染先回来、A 的旧回包后到：byKey 不应被 A 覆盖', async () => {
    setCurrentProjectId('proj-a')
    let resolveA!: (v: unknown) => void
    engineRender.mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolveA = r
        }),
    )
    const pendingA = useRenderStore.getState().render('fig1.pdf', [])
    await Promise.resolve()

    setCurrentProjectId('proj-b')
    useRenderStore.getState().clear()
    engineRender.mockResolvedValueOnce({ rev: 1, manifest: manifestOf('B-fig1'), svg: '<svg>B</svg>' })
    await useRenderStore.getState().render('fig1.pdf', [])
    expect(exactPanelManifest(useRenderStore.getState(), panelIn('pB'))?.stem).toBe('B-fig1')

    resolveA({ rev: 7, manifest: manifestOf('A-fig1'), svg: '<svg>A</svg>' })
    await pendingA
    expect(
      exactPanelManifest(useRenderStore.getState(), panelIn('pB'))?.stem,
      'B 的几何权威被 A 的晚到回包覆盖',
    ).toBe('B-fig1')
  })
})
