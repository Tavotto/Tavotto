import { afterEach, describe, expect, it, vi } from 'vitest'
import { routeKeyOf, useExportRouteStore } from './exportRouteStore'
import type { ExportRequestInput } from '@/lib/exportRequest'
import { setCurrentProjectId } from '@/lib/session'

const doc = (overrides: unknown[] = []) =>
  ({ page: { w: 100, h: 80 }, objects: [{ id: 'p', type: 'panel', fileId: 'a.png', overrides, x: 0, y: 0, w: 10, h: 10 }] }) as never

const input = (over: Partial<ExportRequestInput> = {}): ExportRequestInput => ({
  scope: 'canvas',
  formats: ['pdf'],
  filename: 'x',
  ppi: 300,
  documentId: 'd',
  doc: doc(),
  ...over,
})

afterEach(() => {
  setCurrentProjectId(null)
  vi.unstubAllGlobals()
  useExportRouteStore.getState().clear()
})

describe('routeKeyOf：影响来源的输入变了键就变', () => {
  it('编辑 / 格式 / 范围变键；文件名与 PPI 不变键', () => {
    const k = routeKeyOf(input())
    expect(routeKeyOf(input({ filename: 'other', ppi: 600 }))).toBe(k)
    expect(routeKeyOf(input({ formats: ['pdf', 'eps'] }))).not.toBe(k)
    expect(routeKeyOf(input({ doc: doc([{ gid: 'g', prop: 'p', value: 1 }]) }))).not.toBe(k)
    expect(routeKeyOf(input({ scope: 'original', figureId: 'a.png' }))).not.toBe(k)
  })
})

describe('refresh', () => {
  const reply = (body: unknown, ok = true) =>
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), { status: ok ? 200 : 500 })))

  it('带回 route，并连同它回答的键存下；键对不上就取不到', async () => {
    const route = { scope: 'canvas', formats: ['pdf', 'eps'], panels: {} }
    reply({ ok: true, route })
    const i = input({ formats: ['pdf', 'eps'] })
    await useExportRouteStore.getState().refresh(i)
    const s = useExportRouteStore.getState()
    expect(s.routeFor(routeKeyOf(i))).toEqual(route)
    expect(s.routeFor(routeKeyOf(input({ formats: ['pdf'] })))).toBeNull()
  })

  it('请求里带的是用户勾的格式（含此刻 EPS 不可用被丢掉的那一个）', async () => {
    reply({ ok: true, route: { scope: 'canvas', formats: [], panels: {} } })
    await useExportRouteStore.getState().refresh(input({ formats: ['pdf', 'eps'] }))
    const body = JSON.parse(String((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][1].body))
    expect(body.formats).toEqual(['pdf', 'eps'])
  })

  it('旧后端（响应没有 route）/ ok:false / 网络失败：都是「没有 route」，且清掉旧答案', async () => {
    const i = input()
    for (const body of [{ ok: true }, { ok: false, error: { code: 'x' } }]) {
      reply({ ok: true, route: { scope: 'canvas', formats: ['pdf'], panels: {} } })
      await useExportRouteStore.getState().refresh(i)
      expect(useExportRouteStore.getState().routeFor(routeKeyOf(i))).not.toBeNull()
      reply(body)
      await useExportRouteStore.getState().refresh(i)
      expect(useExportRouteStore.getState().routeFor(routeKeyOf(i))).toBeNull()
    }
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('offline') }))
    await useExportRouteStore.getState().refresh(i)
    expect(useExportRouteStore.getState().routeFor(routeKeyOf(i))).toBeNull()
  })

  it('后发的请求赢：慢的旧响应不覆盖新答案', async () => {
    let release: (r: Response) => void = () => {}
    const slow = new Promise<Response>((r) => { release = r })
    const fast = new Response(JSON.stringify({ ok: true, route: { scope: 'canvas', formats: ['pdf'], panels: { new: {} } } }))
    const f = vi.fn().mockReturnValueOnce(slow).mockResolvedValueOnce(fast)
    vi.stubGlobal('fetch', f)
    const first = useExportRouteStore.getState().refresh(input())
    await useExportRouteStore.getState().refresh(input())
    release(new Response(JSON.stringify({ ok: true, route: { scope: 'canvas', formats: ['pdf'], panels: { old: {} } } })))
    await first
    expect(Object.keys(useExportRouteStore.getState().route!.panels)).toEqual(['new'])
  })
})

describe('换项目', () => {
  const routeBody = (tag: string) => ({ ok: true, route: { scope: 'canvas', formats: ['pdf'], panels: { [tag]: {} } } })

  it('项目 id 进键：同名文件、同编辑、同格式的两个项目，键不同', () => {
    setCurrentProjectId('A')
    const a = routeKeyOf(input())
    setCurrentProjectId('B')
    expect(routeKeyOf(input())).not.toBe(a)
  })

  it('A 的答案不被 B 复用（即便 store 没被清，键也对不上；清了就更没有）', async () => {
    setCurrentProjectId('A')
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(routeBody('fromA')))))
    await useExportRouteStore.getState().refresh(input())
    setCurrentProjectId('B')
    expect(useExportRouteStore.getState().routeFor(routeKeyOf(input()))).toBeNull()
    useExportRouteStore.getState().clear()
    setCurrentProjectId('A')
    expect(useExportRouteStore.getState().routeFor(routeKeyOf(input()))).toBeNull()
  })

  it('A 发出的请求在切到 B 之后才回来：不落进 B', async () => {
    setCurrentProjectId('A')
    let release: (r: Response) => void = () => {}
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((r) => { release = r })))
    const pending = useExportRouteStore.getState().refresh(input())
    setCurrentProjectId('B')
    useExportRouteStore.getState().clear() // projectStore.resetForNewProject 里做的那一步
    release(new Response(JSON.stringify(routeBody('fromA'))))
    await pending
    expect(useExportRouteStore.getState().route).toBeNull()
    expect(useExportRouteStore.getState().key).toBeNull()
  })

  it('即使没人调 clear，换代也让迟到响应作废（A→B→A 同 id 也已换代）', async () => {
    setCurrentProjectId('A')
    let release: (r: Response) => void = () => {}
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((r) => { release = r })))
    const pending = useExportRouteStore.getState().refresh(input())
    setCurrentProjectId('B')
    setCurrentProjectId('A')
    release(new Response(JSON.stringify(routeBody('fromA'))))
    await pending
    expect(useExportRouteStore.getState().route).toBeNull()
  })
})
