import { beforeEach, describe, expect, it } from 'vitest'
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

const STATE = { path: '/p/.tavotto/input-remap.json', rules: [], errors: [] }

globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
  if (String(url).includes('/api/runtime/assets')) {
    assetListCalls += 1
    return new Response(JSON.stringify({ assets: [] }), { status: 200 })
  }
  if (String(url).includes('/api/engine/input-remap')) {
    const rule = { kind: 'prefix', from: '/old/data', to: '/new/data' }
    const body = init?.method === 'POST' ? { ok: true, rule, input_remap: STATE } : { ok: true, input_remap: STATE }
    return new Response(JSON.stringify(body), { status: 200 })
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
    } as never,
  })
}

const probeScripts = () => Object.keys(useScriptRunStore.getState().byScript).sort()

const runtimeChecked = () => useRuntimeAssetStore.getState().byId['runtime:lib.py#fig']?.checked

const staleOf = (fileId: string) => useRenderStore.getState().byKey[fileId]?.stale

describe('改指表变了：成功画过的面板同样重画', () => {
  beforeEach(() => {
    seed()
    useEnvStore.setState({ missingInput: null })
  })

  it('新增 / 替换一条规则：失败的与成功的面板都标 stale', async () => {
    expect(await useEnvStore.getState().pointAtData('/old/data/x.csv', '/new/data/x.csv', 'file')).toBeNull()
    expect(staleOf('missing.py')).toBe(true)
    expect(staleOf('ok.py')).toBe(true)
    expect(useRenderStore.getState().tracked['ok.py']).toBe(true)
    expect(runtimeChecked()).toBe(false)
    expect(assetListCalls).toBe(1)
    // 「找不到数据」失败的那条由代际订阅重跑（它会进 starting_runtime），其余失败态原样
    expect(probeScripts()).toEqual(['broken.py', 'lost.py'])
  })

  it('删一条规则：经它画成功的面板标 stale（回到「找不到就报错」）', async () => {
    const rule = { kind: 'prefix', from: '/old/data', to: '/new/data' } as const
    expect(await useEnvStore.getState().forgetInputRemap(rule as never)).toBeNull()
    expect(staleOf('ok.py')).toBe(true)
    expect(staleOf('missing.py')).toBe(true)
    expect(runtimeChecked()).toBe(false)
    expect(assetListCalls).toBe(1)
    expect(probeScripts()).toEqual(['broken.py', 'lost.py'])
  })
})
