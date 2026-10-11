/**
 * 读取结果不能提前折叠（任务书 4.1）：会话读取 / 槽位探测 / 归属证据 / 找回候选的**结局分类**。
 * 401 / 403 / 5xx / 超时（网络错误）都不是旧端点的 404，不许放行；单个槽位 404 只代表这一个槽位。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  fetchLayoutOwner,
  fetchLayoutOwnerEvidence,
  fetchLayoutSession,
  fetchLayoutSessionResult,
  fetchRecoveryCandidates,
  probeAutosave,
} from './api'

const realFetch = globalThis.fetch
const reply = (body: unknown, status = 200, headers?: Record<string, string>) =>
  new Response(typeof body === 'string' ? body : JSON.stringify(body), { status, headers })
const stub = (fn: (url: string) => Response | Promise<Response>) => {
  const calls: string[] = []
  globalThis.fetch = (async (url: unknown) => {
    calls.push(String(url))
    return fn(String(url))
  }) as typeof fetch
  return calls
}
afterEach(() => {
  globalThis.fetch = realFetch
  vi.restoreAllMocks()
})

describe('fetchLayoutSessionResult', () => {
  it('ok：last / prev / pending / index 原样带出，缺的字段是 null / ok（旧后端只发 last）', async () => {
    stub(() =>
      reply({
        last: { doc_id: 'd_a', name: 'A', at: 5, revision: 'rev-a' },
        prev: { doc_id: 'd_p', name: 'P', at: 3, revision: 7 },
        pending: { doc_id: 'd_n', name: 'N', at: 6, revision: null },
        index: 'recovered',
      }),
    )
    // 指针带 revision（后端记下指针时正文的 hash）；键在而不是字符串的是 null（新后端：校验时槽位不存在）
    expect(await fetchLayoutSessionResult('p1')).toEqual({
      kind: 'ok',
      last: { doc_id: 'd_a', name: 'A', at: 5, revision: 'rev-a' },
      prev: { doc_id: 'd_p', name: 'P', at: 3, revision: null },
      pending: { doc_id: 'd_n', name: 'N', at: 6, revision: null },
      index: 'recovered',
    })
    // 键缺席（旧后端）= 缺席，不折成 null：两者含义不同
    stub(() => reply({ last: { doc_id: 'd_a', name: 'A', at: 5 } }))
    const old = await fetchLayoutSessionResult('p1')
    if (old.kind !== 'ok') throw new Error('x')
    expect(old.last).not.toHaveProperty('revision')
    stub(() => reply({ last: null }))
    expect(await fetchLayoutSessionResult('p1')).toEqual({ kind: 'ok', last: null, prev: null, pending: null, index: 'ok' })
  })

  it('404 = unsupported；401 / 403 / 500 / 网络错误 = unavailable（不是旧端点）', async () => {
    stub(() => reply({ error: 'nf' }, 404))
    expect(await fetchLayoutSessionResult('p1')).toEqual({ kind: 'unsupported' })
    for (const status of [401, 403, 500, 502]) {
      stub(() => reply({ error: 'x' }, status))
      expect(await fetchLayoutSessionResult('p1')).toEqual({ kind: 'unavailable', status })
    }
    globalThis.fetch = (async () => {
      throw new TypeError('Failed to fetch')
    }) as typeof fetch
    expect(await fetchLayoutSessionResult('p1')).toEqual({ kind: 'unavailable', status: null })
  })

  it('旧薄适配 fetchLayoutSession：ok → { last }，其余 → undefined', async () => {
    stub(() => reply({ last: { doc_id: 'd_a', name: 'A', at: 5 } }))
    expect(await fetchLayoutSession('p1')).toEqual({ last: { doc_id: 'd_a', name: 'A', at: 5 } })
    stub(() => reply({}, 500))
    expect(await fetchLayoutSession('p1')).toBeUndefined()
    stub(() => reply({}, 404))
    expect(await fetchLayoutSession('p1')).toBeUndefined()
  })
})

describe('probeAutosave：纯读，四种结局', () => {
  it('found 带 revision；404 = missing；400 = invalid 带后端稳定码；5xx / 401 / 网络 = unavailable', async () => {
    stub(() => reply({ schema: 3 }, 200, { 'X-Tavotto-Revision': 'r1' }))
    expect(await probeAutosave('d_a', 'p1')).toEqual({ kind: 'found', doc: { schema: 3 }, revision: 'r1' })
    stub(() => reply({ schema: 3 }))
    expect(await probeAutosave('d_a', 'p1')).toEqual({ kind: 'found', doc: { schema: 3 }, revision: null })
    stub(() => reply({ error: 'nf' }, 404))
    expect(await probeAutosave('d_a', 'p1')).toEqual({ kind: 'missing' })
    stub(() => reply({ error: 'bad', code: 'non_finite_on_disk' }, 400))
    expect(await probeAutosave('d_a', 'p1')).toEqual({ kind: 'invalid', code: 'non_finite_on_disk' })
    stub(() => reply('oops', 400))
    expect(await probeAutosave('d_a', 'p1')).toEqual({ kind: 'invalid', code: null })
    for (const status of [500, 503, 401, 403]) {
      stub(() => reply({ error: 'x' }, status))
      expect(await probeAutosave('d_a', 'p1')).toEqual({ kind: 'unavailable', status })
    }
    globalThis.fetch = (async () => {
      throw new TypeError('Failed to fetch')
    }) as typeof fetch
    expect(await probeAutosave('d_a', 'p1')).toEqual({ kind: 'unavailable', status: null })
  })

  it('只发一个 GET：不写、不带 body', async () => {
    const calls = stub(() => reply({ schema: 3 }))
    const spy = vi.spyOn(globalThis, 'fetch')
    await probeAutosave('d_a', 'p1')
    expect(calls).toHaveLength(1)
    expect(calls[0]).toContain('/api/autosave/d_a')
    expect((spy.mock.calls[0]?.[1] as RequestInit | undefined)?.method ?? 'GET').toBe('GET')
  })
})

describe('fetchLayoutOwnerEvidence：保留证据等级', () => {
  it('verified 只在 owners 证据且后端说已核对时为真', async () => {
    stub(() => reply({ owner: 'this', evidence: 'owners', verified: true, revision: 'rev-1' }))
    expect(await fetchLayoutOwnerEvidence('d_a', 'p1')).toEqual({ owner: 'this', evidence: 'owners', verified: true, revision: 'rev-1' })
    stub(() => reply({ owner: 'this', evidence: 'assets', verified: true })) // 其它证据等级即使带 verified 也不算
    expect(await fetchLayoutOwnerEvidence('d_a', 'p1')).toEqual({ owner: 'this', evidence: 'assets', verified: false })
    stub(() => reply({ owner: 'this', evidence: 'owners' })) // 旧后端不发 verified
    expect(await fetchLayoutOwnerEvidence('d_a', 'p1')).toEqual({ owner: 'this', evidence: 'owners', verified: false })
    stub(() => reply({ owner: 'weird', evidence: 'nope' }))
    expect(await fetchLayoutOwnerEvidence('d_a', 'p1')).toEqual({ owner: 'unknown', evidence: null, verified: false })
    // 键在而值是 null（新后端：下结论时没有槽位）与键缺席（旧后端）不同
    stub(() => reply({ owner: 'unknown', evidence: null, verified: false, revision: null }))
    expect(await fetchLayoutOwnerEvidence('d_a', 'p1')).toEqual({ owner: 'unknown', evidence: null, verified: false, revision: null })
  })

  it('404 = unsupported；其余失败 = unavailable；旧薄适配 fetchLayoutOwner 仍是 undefined / null / 字符串', async () => {
    stub(() => reply({}, 404))
    expect(await fetchLayoutOwnerEvidence('d_a', 'p1')).toBe('unsupported')
    expect(await fetchLayoutOwner('d_a', 'p1')).toBeUndefined()
    stub(() => reply({}, 500))
    expect(await fetchLayoutOwnerEvidence('d_a', 'p1')).toBe('unavailable')
    expect(await fetchLayoutOwner('d_a', 'p1')).toBeNull()
    stub(() => reply({}, 401))
    expect(await fetchLayoutOwnerEvidence('d_a', 'p1')).toBe('unavailable')
    stub(() => reply({ owner: 'other', evidence: 'owners', verified: true }))
    expect(await fetchLayoutOwner('d_a', 'p1')).toBe('other')
  })
})

describe('fetchRecoveryCandidates', () => {
  it('带 scope / limit / cursor；解析行，丢掉没有 doc_id 的；坏 status 按 invalid', async () => {
    const calls = stub(() =>
      reply({
        items: [
          { doc_id: 'd_a', name: 'A', updatedAt: 5, mtime: 9, revision: 'r', schema: 3, status: 'ok', canvases: 2, objects: 7, owner: 'unknown', evidence: null, runtime: [{ script: 'fig1.py', figure: 'Fig1' }, { script: 3 }], panels: ['a.png'] },
          { name: 'no id' },
          { doc_id: 'd_b', status: 'weird' },
        ],
        next_cursor: 'c2',
        index: 'degraded',
      }),
    )
    const res = await fetchRecoveryCandidates('unknown', 'p1', 'c1', 20)
    expect(calls[0]).toContain('scope=unknown')
    expect(calls[0]).toContain('limit=20')
    expect(calls[0]).toContain('cursor=c1')
    if (typeof res === 'string') throw new Error('unexpected')
    expect(res.next_cursor).toBe('c2')
    expect(res.truncated).toBe(false) // 旧后端不发 = false
    expect(res.index).toBe('degraded')
    expect(res.items).toHaveLength(2)
    expect(res.items[0]).toMatchObject({ doc_id: 'd_a', canvases: 2, objects: 7, owner: 'unknown', runtime: [{ script: 'fig1.py', figure: 'Fig1' }], panels: ['a.png'] })
    expect(res.items[1].status).toBe('invalid')
  })

  it('truncated：后端扫描碰到上限的标记原样带出（next_cursor 为 null 也不代表列全）', async () => {
    stub(() => reply({ items: [], next_cursor: null, truncated: true, index: 'ok' }))
    expect(await fetchRecoveryCandidates('project', 'p1')).toMatchObject({ next_cursor: null, truncated: true })
    stub(() => reply({ items: [], next_cursor: null, truncated: 'yes' }))
    expect(await fetchRecoveryCandidates('project', 'p1')).toMatchObject({ truncated: false })
  })

  it('404 = unsupported；其它失败 = unavailable', async () => {
    stub(() => reply({}, 404))
    expect(await fetchRecoveryCandidates('project', 'p1')).toBe('unsupported')
    stub(() => reply({}, 500))
    expect(await fetchRecoveryCandidates('project', 'p1')).toBe('unavailable')
    // 后端的可重试失败（503 candidates_unavailable）= unavailable，不是 unsupported
    stub(() => reply({ error: 'x', code: 'candidates_unavailable' }, 503))
    expect(await fetchRecoveryCandidates('unknown', 'p1')).toBe('unavailable')
  })
})
