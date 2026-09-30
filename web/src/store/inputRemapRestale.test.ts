import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useEnvStore } from './envStore'
import { useRenderStore } from './renderStore'
import { useRuntimeAssetStore } from './runtimeAssetStore'
import { useScriptRunStore } from './scriptRunStore'

/**
 * 改指表一变（ADR 0106）：增 / 换 / 删一条规则之后，**成功画过**的面板也要重画——
 * 同 kind / from 的规则被替换、或规则被删掉时，它们画的是旧位置的数据。只重排失败的面板
 * 的话，画布一直挂着旧数据集的样子，而下一次渲染 / 导出用的已是新规则（Codex 评 #716 P1）。
 */

let assetListCalls = 0
let inflightInvalidations = 0
const probed: string[] = []

/** 后端此刻的代次：改指 / 删指各 +1，环境刷新里带着它（重连 / 页面恢复的补拉） */
let serverGeneration = 100
const state = () => ({ rules: [], generation: serverGeneration })

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  if (String(url).includes('/api/runtime/assets')) {
    assetListCalls += 1
    return new Response(JSON.stringify({ assets: [] }), { status: 200 })
  }
  if (String(url).includes('/api/registry/probe')) {
    probed.push(JSON.parse(String(init?.body ?? '{}')).script)
    return new Response('{}', { status: 404 })
  }
  if (String(url).includes('/api/engine/input-remap')) {
    const rule = { kind: 'prefix', from: '/old/data', to: '/new/data' }
    if (init?.method === 'POST' || init?.method === 'DELETE') serverGeneration += 1
    const body = init?.method === 'POST' ? { ok: true, rule, input_remap: state() } : { ok: true, input_remap: state() }
    return new Response(JSON.stringify(body), { status: 200 })
  }
  if (String(url).includes('/api/engine/environment')) {
    return new Response(JSON.stringify({ project: { open: true, input_remap: state() } }), { status: 200 })
  }
  return new Response('{}', { status: 404 })
}) as typeof fetch

function seed() {
  const entry = (fileId: string, status: string, code = '') =>
    ({ ...useRenderStore.getState().byKey[fileId], fileId, status, code, stale: false, lastPatches: [], wantPatches: [] })
  useRenderStore.setState({
    byKey: {
      'ok.py': entry('ok.py', 'ready'),
      'missing.py': entry('missing.py', 'error', 'missing_input'),
    } as never,
    tracked: {},
  })
  // 素材库里试运行成功、还没上画布的 runtime 素材（Codex 评 #716 P1 第二轮）
  useRuntimeAssetStore.setState({
    byId: { 'runtime:lib.py#fig': { status: 'fresh', cached: true, registered: true, profile: 'safe', checked: true } },
    assets: [],
  })
  assetListCalls = 0
  inflightInvalidations = 0
  probed.length = 0
  useRenderStore.setState({ invalidateInflight: () => void (inflightInvalidations += 1) })
  // 「运行并发现图」的结果（Codex 评 #716 P2）：按旧映射捕获的描述符、在飞的那次都要作废；失败态不动
  const run = (phase: string, extra = {}) =>
    ({ phase, descriptors: [], droppedFigures: 0, error: null, cancelRequested: false, gen: 1, ...extra })
  useScriptRunStore.setState({
    byScript: {
      'one.py': run('captured_one', { descriptors: [{ asset_id: 'runtime:one.py#a' }] }),
      'many.py': run('captured_many'),
      'empty.py': run('no_figure'),
      'busy.py': run('running'),
      'lost.py': run('missing_input', { error: { code: 'missing_input', message: '', params: {} } }),
      'broken.py': run('failed', { error: { code: 'script_error', message: '', params: {} } }),
      // 捕获了缺失的 open() 之后没出图、带「找不到数据」载荷（Codex 评 #716 P2 第三轮）：指认之后要重跑
      'nofig.py': run('no_figure', {
        error: { code: 'script_no_figure', message: '', params: {}, missing_input: { requested: 'x.csv' } },
      }),
    } as never,
  })
}

const probeScripts = () => Object.keys(useScriptRunStore.getState().byScript).sort()

const runtimeChecked = () => useRuntimeAssetStore.getState().byId['runtime:lib.py#fig']?.checked

const staleOf = (fileId: string) => useRenderStore.getState().byKey[fileId]?.stale

describe('改指表变了：成功画过的面板同样重画', () => {
  beforeEach(() => {
    seed()
    serverGeneration = 100
    useEnvStore.setState({ missingInput: null, inputRemapSeen: 100 })
  })

  it('发起的窗口收到响应就本地作废：事件丢了也不漏；随后到的同代事件不再作废第二次', async () => {
    expect(await useEnvStore.getState().pointAtData('/old/data/x.csv', '/new/data/x.csv', 'file')).toBeNull()
    // 没有任何事件送进来：响应带回的代次 101 就够了
    await vi.waitFor(() => expect(inflightInvalidations).toBe(1))
    expect(staleOf('ok.py')).toBe(true)
    await vi.waitFor(() => expect(probed).toEqual(['nofig.py']))
    // 事件流照旧广播同一代：按代次去重
    useEnvStore.getState().onInputRemapChanged(101, 'added')
    await new Promise((r) => setTimeout(r, 0))
    expect(inflightInvalidations).toBe(1)
    // 删指同理：响应带回 102，本地作废一次
    expect(await useEnvStore.getState().forgetInputRemap({ kind: 'prefix', from: '/old/data', to: '/new/data' } as never)).toBeNull()
    await vi.waitFor(() => expect(inflightInvalidations).toBe(2))
    expect(useEnvStore.getState().inputRemapSeen).toBe(102)
  })

  it('同一代通知两次只作废一次；更早的代次不理', async () => {
    useEnvStore.getState().onInputRemapChanged(103, 'added')
    useEnvStore.getState().onInputRemapChanged(103, 'added')
    useEnvStore.getState().onInputRemapChanged(102, 'removed')
    await vi.waitFor(() => expect(inflightInvalidations).toBe(1))
    await new Promise((r) => setTimeout(r, 0))
    expect(inflightInvalidations).toBe(1)
  })

  it('重连 / 页面恢复补拉：代次落后就补一次作废，再拉一次不重复；从没见过代次的窗口只记起点', async () => {
    serverGeneration = 104 // 断线期间别的窗口改了两次表，两条事件都丢了
    await useEnvStore.getState().refresh()
    await vi.waitFor(() => expect(inflightInvalidations).toBe(1))
    expect(staleOf('ok.py')).toBe(true)
    expect(useEnvStore.getState().inputRemapSeen).toBe(104)
    await useEnvStore.getState().refresh()
    await new Promise((r) => setTimeout(r, 0))
    expect(inflightInvalidations).toBe(1)
    // 刚开项目（换项目清空了已见代次）：第一次拉到的只是起点，不作废
    seed()
    useEnvStore.setState({ inputRemapSeen: null })
    await useEnvStore.getState().refresh()
    await new Promise((r) => setTimeout(r, 0))
    expect(inflightInvalidations).toBe(0)
    expect(useEnvStore.getState().inputRemapSeen).toBe(104)
  })

  it('新增 / 替换一条规则（事件 reason=added）：失败的与成功的面板都标 stale', async () => {
    useEnvStore.getState().onInputRemapChanged(101, 'added')
    await vi.waitFor(() => expect(inflightInvalidations).toBe(1))
    expect(staleOf('missing.py')).toBe(true)
    expect(staleOf('ok.py')).toBe(true)
    expect(useRenderStore.getState().tracked['ok.py']).toBe(true)
    expect(runtimeChecked()).toBe(false)
    expect(assetListCalls).toBe(1)
    expect(inflightInvalidations).toBe(1) // 在途的旧条件渲染作废（Codex 评 #716 P1）
    // 「没出图 + 找不到数据」那条：先收集、再作废、再重跑——真的发出了一次试运行；其余失败态原样
    expect(probed).toEqual(['nofig.py'])
    expect(probeScripts()).toEqual(['broken.py', 'lost.py', 'nofig.py'])
  })

  it('删一条规则（事件 reason=removed）：经它画成功的面板标 stale（回到「找不到就报错」）', async () => {
    useEnvStore.getState().onInputRemapChanged(101, 'removed')
    await vi.waitFor(() => expect(inflightInvalidations).toBe(1))
    expect(staleOf('ok.py')).toBe(true)
    expect(staleOf('missing.py')).toBe(true)
    expect(runtimeChecked()).toBe(false)
    expect(assetListCalls).toBe(1)
    expect(inflightInvalidations).toBe(1) // 在途的旧条件渲染作废（Codex 评 #716 P1）
    expect(probeScripts()).toEqual(['broken.py', 'lost.py'])
    expect(probed).toEqual([]) // 删规则不重跑：只作废
  })
})
