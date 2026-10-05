/**
 * 运行参数草稿（T03）：精确 token，不拆不并；运行开始那一刻取拷贝；换项目丢弃；请求体里空草稿没有 `argv`。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { probeScript } from '@/lib/api'
import { snapshotScriptArgs, useScriptArgvStore } from './scriptArgvStore'

const store = () => useScriptArgvStore.getState()

beforeEach(() => store().clear())

describe('token 草稿', () => {
  it('空串、带空格、中文、重复、负数、`--` 都是一项，原样、按顺序', () => {
    const tokens = ['', ' ', 'a b', '中文', '-3', '--', '--k=v', '--k=v']
    for (const t of tokens) store().addToken('plot.py', t)
    expect(store().drafts['plot.py'].tokens).toEqual(tokens)
    expect(snapshotScriptArgs('plot.py')?.argv).toEqual(tokens)
  })

  it('增、删、改、上移只动被点的那一项（重复项不被合并）', () => {
    for (const t of ['a', 'b', 'a']) store().addToken('s.py', t)
    store().setToken('s.py', 1, 'B')
    store().moveToken('s.py', 2, -1)
    expect(store().drafts['s.py'].tokens).toEqual(['a', 'a', 'B'])
    store().removeToken('s.py', 0)
    expect(store().drafts['s.py'].tokens).toEqual(['a', 'B'])
    store().moveToken('s.py', 0, -1) // 越界什么都不做
    expect(store().drafts['s.py'].tokens).toEqual(['a', 'B'])
  })

  it('快照是拷贝：运行开始之后再编辑草稿，不影响已经取走的那一份', () => {
    store().addToken('s.py', '--scale')
    store().addToken('s.py', '2')
    const snap = snapshotScriptArgs('s.py')
    store().setToken('s.py', 1, '999')
    expect(snap?.argv).toEqual(['--scale', '2'])
  })

  it('空草稿 = 不带参数（undefined）；敏感标记跟着草稿', () => {
    expect(snapshotScriptArgs('s.py')).toBeUndefined()
    store().addToken('s.py', 'x')
    store().setSensitive('s.py', true)
    expect(snapshotScriptArgs('s.py')).toEqual({ argv: ['x'], sensitive: true })
    store().removeToken('s.py', 0)
    expect(snapshotScriptArgs('s.py')).toBeUndefined()
  })

  it('每个脚本各有各的草稿', () => {
    store().addToken('a.py', '1')
    store().addToken('b.py', '2')
    expect(snapshotScriptArgs('a.py')?.argv).toEqual(['1'])
    expect(snapshotScriptArgs('b.py')?.argv).toEqual(['2'])
  })
})

describe('probeScript 的请求体', () => {
  const fetchMock = vi.fn()
  beforeEach(() => {
    fetchMock.mockReset()
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  const bodyOfLastCall = () => JSON.parse(String(fetchMock.mock.calls.at(-1)?.[1]?.body))

  it('没有参数 → 没有 argv 字段（与旧版请求体一致）', async () => {
    await probeScript('plot.py')
    expect(bodyOfLastCall()).toEqual({ script: 'plot.py' })
    await probeScript('plot.py', undefined, { argv: [] })
    expect(Object.keys(bodyOfLastCall())).not.toContain('argv')
  })

  it('有参数 → argv 数组原样（含空串与中文），敏感标记只在勾选时出现', async () => {
    await probeScript('plot.py', undefined, { argv: ['', '中文', '--'] })
    expect(bodyOfLastCall()).toEqual({ script: 'plot.py', argv: ['', '中文', '--'] })
    await probeScript('plot.py', undefined, { argv: ['t'], sensitive: true })
    expect(bodyOfLastCall()).toMatchObject({ argv: ['t'], argv_sensitive: true })
  })
})

describe('按草稿调 probeScript', () => {
  it('没有参数 → 只有脚本名；有参数 → 第三个参数是调用那一刻的拷贝', async () => {
    const { probeWithDraft } = await import('./scriptArgvStore')
    const probe = vi.fn().mockResolvedValue({})
    await probeWithDraft(probe, 's.py')
    expect(probe).toHaveBeenLastCalledWith('s.py')
    store().addToken('s.py', '--n')
    store().addToken('s.py', '3')
    await probeWithDraft(probe, 's.py')
    store().setToken('s.py', 1, '4')
    expect(probe).toHaveBeenLastCalledWith('s.py', undefined, {
      argv: ['--n', '3'],
      sensitive: false,
    })
  })
})

describe('参数被脚本自己的解析器拒绝时的文案', () => {
  it('带 argv_count 的 script_needs_arguments 不再说"没带参数"', async () => {
    const { backendCodeMsg } = await import('@/lib/api')
    const { formatMessage } = await import('@/i18n')
    const given = formatMessage(backendCodeMsg('script_needs_arguments', { argv_count: '3' }, '原文'))
    const none = formatMessage(backendCodeMsg('script_needs_arguments', {}, '原文'))
    expect(given).toContain('3')
    expect(given).not.toEqual(none)
    expect(none).toContain('不带任何参数')
  })
})

describe('带运行配置的图进画布', () => {
  it('文档里的 source 块带着不透明引用（不是参数值）；没有配置时没有这个键', async () => {
    const { addRuntimePanel } = await import('@/store/actions')
    const { useDocumentStore } = await import('@/store/documentStore')
    const { emptyProject } = await import('@/types/document')
    await useDocumentStore.getState().switchDocument(emptyProject(), 'd_argv')
    const base = {
      script: 's.py',
      entry: '__main__',
      stem: 'result',
      capture_source: 'savefig' as const,
      execution_profile: 'safe' as const,
      original_artifact: null,
      size_mm: [100, 80] as [number, number],
      source_fingerprint: 'sha256:x',
      can_writeback_artifact: false,
      can_writeback_source: false,
    }
    const withCfg = addRuntimePanel({
      ...base,
      asset_id: 'runtime:s.py#result~rc_0123456789ab',
      run_config: 'rc_0123456789ab',
    })
    const plain = addRuntimePanel({ ...base, asset_id: 'runtime:s.py#result' })
    expect(withCfg.source?.runConfig).toBe('rc_0123456789ab')
    expect(plain.source).not.toHaveProperty('runConfig')
  })
})
