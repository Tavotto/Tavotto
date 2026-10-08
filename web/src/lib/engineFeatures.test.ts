/**
 * T10：新前端 × 旧引擎的能力协商。旧端点不认识 `argv`，会静默无参数运行——所以带参数之前先问
 * `/api/version` 的 `features`；没有 `script-argv` 就当场拒绝，**一次运行请求都不发**。
 * 「旧引擎」是模拟的：`/api/version` 回一份没有 `features`（或缺这一项）的老形状。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiError,
  backendErrorMsg,
  createPreparationSession,
  ENGINE_CAPABILITY_MISSING,
  ENGINE_FEATURE_SCRIPT_ARGV,
  fetchEngineFeatures,
  probeScript,
  resetEngineFeatures,
} from '@/lib/api'
import { formatMessage } from '@/i18n'

type Version = Record<string, unknown> | 'down' | 404

const fetchMock = vi.fn()
const runCalls = () =>
  fetchMock.mock.calls.filter(([url]) => !String(url).includes('/api/version')).length

function engine(version: Version) {
  fetchMock.mockImplementation(async (url: string) => {
    if (String(url).includes('/api/version')) {
      if (version === 'down') throw new TypeError('Failed to fetch')
      if (version === 404) return { ok: false, status: 404, json: async () => ({}) }
      return { ok: true, status: 200, json: async () => version }
    }
    return { ok: true, status: 200, json: async () => ({ registered: true }) }
  })
}

beforeEach(() => {
  fetchMock.mockReset()
  resetEngineFeatures()
  vi.stubGlobal('fetch', fetchMock)
})
afterEach(() => vi.unstubAllGlobals())

const OLD_ENGINES: [string, Version][] = [
  ['0.18 之前：没有 features', { build: 'index-a', version: '0.17.0' }],
  ['只有桌面壳那一项', { build: 'index-a', version: '0.18.0', features: ['desktop-remote-window'] }],
  ['更早：连 /api/version 都没有', 404],
]

describe('新前端连旧引擎', () => {
  it.each(OLD_ENGINES)('%s：带参数的试运行被拒绝，运行请求一次都没发', async (_name, version) => {
    engine(version)
    const err = await probeScript('plot.py', undefined, { argv: ['--freq', '2'] }).catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).body.code).toBe(ENGINE_CAPABILITY_MISSING)
    expect(runCalls()).toBe(0)
    // 界面说的是「升级」，不是「没带参数」
    expect(formatMessage(backendErrorMsg(err))).toContain('升级')
  })

  it.each(OLD_ENGINES)('%s：不带参数照旧运行（请求体与旧版逐字节相同）', async (_name, version) => {
    engine(version)
    await probeScript('plot.py')
    expect(runCalls()).toBe(1)
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/api/version'))).toBe(false)
    expect(JSON.parse(String(fetchMock.mock.calls.at(-1)?.[1]?.body))).toEqual({ script: 'plot.py' })
  })

  it('准备会话带参数也先问；按已知图（id）的会话不带参数，不问', async () => {
    engine({ version: '0.17.0' })
    const err = await createPreparationSession({ script: 's.py', argv: ['x'] }, null).catch((e) => e)
    expect((err as ApiError).body.code).toBe(ENGINE_CAPABILITY_MISSING)
    expect(runCalls()).toBe(0)
    await createPreparationSession({ id: 'runtime:s.py#fig' }, null)
    expect(runCalls()).toBe(1)
  })
})

describe('新前端连新引擎', () => {
  it('宣告了 script-argv：参数原样发出，一个标签页只问一次', async () => {
    engine({ version: '0.19.0', features: ['desktop-remote-window', ENGINE_FEATURE_SCRIPT_ARGV] })
    await probeScript('plot.py', undefined, { argv: ['', '中文'] })
    await probeScript('plot.py', undefined, { argv: ['--n', '3'] })
    const versionCalls = fetchMock.mock.calls.filter(([url]) => String(url).includes('/api/version'))
    expect(versionCalls).toHaveLength(1)
    expect(runCalls()).toBe(2)
    expect(JSON.parse(String(fetchMock.mock.calls.at(-1)?.[1]?.body))).toEqual({
      script: 'plot.py',
      argv: ['--n', '3'],
    })
  })

  it('问能力时网络断了：不缓存「什么都不会」，也不发运行请求', async () => {
    engine('down')
    await expect(probeScript('plot.py', undefined, { argv: ['x'] })).rejects.toThrow()
    expect(runCalls()).toBe(0)
    engine({ features: [ENGINE_FEATURE_SCRIPT_ARGV] })
    expect((await fetchEngineFeatures()).has(ENGINE_FEATURE_SCRIPT_ARGV)).toBe(true)
  })
})

describe('/api/version 暂时失败后允许重试（Codex #818 r4221135447）', () => {
  it('第一次 503、第二次 200：第二次拿到真实特性集，而不是缓存的空集', async () => {
    let calls = 0
    fetchMock.mockImplementation(async () => {
      calls += 1
      if (calls === 1) return { ok: false, status: 503, json: async () => ({}) }
      return { ok: true, status: 200, json: async () => ({ features: [ENGINE_FEATURE_SCRIPT_ARGV] }) }
    })
    await expect(fetchEngineFeatures()).rejects.toThrow()
    const features = await fetchEngineFeatures()
    expect(features.has(ENGINE_FEATURE_SCRIPT_ARGV)).toBe(true)
    expect(calls).toBe(2)
  })
})
