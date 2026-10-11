/**
 * 找回候选的纯逻辑（A14 / A15 / A28 / A30）：分页有界、恢复副本的固定目标 id 与幂等、
 * 确认时重读源正文核对 revision、取消 / 失败 / 过期零写入、源槽位字节不变。
 * 真模块 + 有状态假 fetch（`test/fakeSessionBackend`），断言实际 GET / PUT 序列。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createFakeBackend } from '@/test/fakeSessionBackend'
import type { RecoveryCandidateItem } from '@/lib/api'
import {
  CANDIDATE_CAP,
  CANDIDATE_PAGE,
  discoverProjectCandidates,
  loadCandidates,
  recoverCopy,
  recoveryCopyId,
  rowIssue,
  type RecoverCopyDeps,
} from './recoveryCandidates'

const PJ = 'p_a'
const backend = createFakeBackend()
const realFetch = globalThis.fetch

const doc = (name: string, extra: Record<string, unknown> = {}) => ({
  schema: 3,
  project: { id: 'x', name },
  canvases: [{ id: 'c1', name: 'Fig', page: { w: 100, h: 80 }, objects: [], guides: [] }],
  activeCanvasId: 'c1',
  createdAt: 0,
  updatedAt: 1,
  ...extra,
})

beforeEach(() => {
  backend.reset()
  globalThis.fetch = backend.fetch
})
afterEach(() => {
  globalThis.fetch = realFetch
})

/** 列表里看到的那一行（从后端现取，带预览时的 revision） */
async function listed(id: string, scope: 'project' | 'unknown' = 'unknown'): Promise<RecoveryCandidateItem> {
  const res = await loadCandidates(scope, PJ)
  if (res.kind !== 'ok') throw new Error('list failed')
  const hit = res.list.rows.find((r) => r.doc_id === id)
  if (!hit) throw new Error(`${id} not listed`)
  return hit
}

/** 模拟 `switchDocument` + 正常保存链：把副本以目标 id PUT 一次（REVISION_ABSENT 之后的首写） */
function deps(over: Partial<RecoverCopyDeps> = {}) {
  const installed: string[] = []
  const d: RecoverCopyDeps = {
    pj: PJ,
    openExisting: vi.fn(async () => true),
    install: vi.fn(async (pd, id) => {
      installed.push(id)
      await fetch(`/api/autosave/${id}?pj=${PJ}`, { method: 'PUT', body: JSON.stringify(pd) })
      return true
    }),
    ...over,
  }
  return { d, installed }
}

describe('recoveryCopyId', () => {
  it('固定：同源同版本同目标；源改过（revision 变）是另一个目标；符合后端 doc_id 规则', () => {
    const a = recoveryCopyId('d_abcdef12', 'h12345678aaaa')
    expect(a).toBe(recoveryCopyId('d_abcdef12', 'h12345678aaaa'))
    expect(a).toMatch(/^d_rc_d_abcdef12_h1234567$/)
    expect(recoveryCopyId('d_abcdef12', 'h99999999aaaa')).not.toBe(a)
    expect(recoveryCopyId('d_other', 'h12345678aaaa')).not.toBe(a)
    for (const id of [recoveryCopyId('d/../../etc', 'zz'), recoveryCopyId('x'.repeat(200), null), recoveryCopyId('', '')]) {
      expect(id).toMatch(/^[\w-]{1,128}$/)
    }
  })
})

describe('rowIssue', () => {
  it('后端 status 与 schema 都看：太新的构建读不了', () => {
    const base = { doc_id: 'd', status: 'ok', schema: 3 } as RecoveryCandidateItem
    expect(rowIssue(base)).toBeNull()
    expect(rowIssue({ ...base, status: 'invalid' })).toBe('invalid')
    expect(rowIssue({ ...base, status: 'too_large' })).toBe('too_large')
    expect(rowIssue({ ...base, status: 'too_new' })).toBe('too_new')
    expect(rowIssue({ ...base, schema: 99 })).toBe('too_new')
  })
})

describe('loadCandidates：分页有界、去重、排除当前文档', () => {
  it('第一页只读一页；「加载更多」接着读；翻页之间重复出现的同一份只算一次', async () => {
    for (let i = 0; i < 120; i++) {
      backend.putSlot(`d_${i}`, doc(`n${i}`))
      backend.state.candidates.push({ doc_id: `d_${i}`, scope: 'unknown' })
    }
    const first = await loadCandidates('unknown', PJ)
    if (first.kind !== 'ok') throw new Error('x')
    expect(first.list.rows).toHaveLength(CANDIDATE_PAGE)
    expect(first.list.next).not.toBeNull()
    expect(backend.state.calls.filter((c) => c.startsWith('GET candidates'))).toHaveLength(1)
    // 翻页期间第一页最后一份被后端再次排进第二页
    backend.state.candidates.splice(CANDIDATE_PAGE, 0, { doc_id: `d_${CANDIDATE_PAGE - 1}`, scope: 'unknown' })
    const second = await loadCandidates('unknown', PJ, first.list)
    if (second.kind !== 'ok') throw new Error('x')
    const ids = second.list.rows.map((r) => r.doc_id)
    expect(new Set(ids).size).toBe(ids.length)
    expect(ids.length).toBeGreaterThan(CANDIDATE_PAGE)
    expect(backend.writes()).toEqual([]) // 全程只读
  })

  it('总行数封顶，到顶后不再给下一页（capped）', async () => {
    for (let i = 0; i < CANDIDATE_CAP + 80; i++) {
      backend.putSlot(`d_${i}`, doc('n'))
      backend.state.candidates.push({ doc_id: `d_${i}`, scope: 'unknown' })
    }
    let list = (await loadCandidates('unknown', PJ)) as Extract<Awaited<ReturnType<typeof loadCandidates>>, { kind: 'ok' }>
    let pages = 1
    while (list.list.next && pages < 20) {
      list = (await loadCandidates('unknown', PJ, list.list)) as typeof list
      pages++
    }
    expect(list.list.rows).toHaveLength(CANDIDATE_CAP)
    expect(list.list.capped).toBe(true)
    expect(list.list.next).toBeNull()
    expect(pages).toBe(Math.ceil(CANDIDATE_CAP / CANDIDATE_PAGE))
  })

  it('后端过滤之后一页为空而 next_cursor 仍在：接着翻，翻页有界（最多再 5 页）', async () => {
    const item = (id: string) => ({ doc_id: id, name: id, updatedAt: 1, mtime: 1, revision: 'r', schema: 3, status: 'ok', canvases: 1, objects: 1, owner: 'unknown', evidence: null })
    const asked: string[] = []
    const serve = (emptyBefore: number) => {
      let n = 0
      globalThis.fetch = (async (url: unknown) => {
        asked.push(new URL(String(url), 'http://x').searchParams.get('cursor') ?? '')
        n += 1
        const body = n <= emptyBefore ? { items: [], next_cursor: `c${n}`, index: 'ok' } : { items: [item('d_late')], next_cursor: null, index: 'ok' }
        return new Response(JSON.stringify(body))
      }) as typeof fetch
    }
    serve(3)
    const ok = await loadCandidates('unknown', PJ)
    if (ok.kind !== 'ok') throw new Error('x')
    expect(ok.list.rows.map((r) => r.doc_id)).toEqual(['d_late'])
    expect(asked).toEqual(['', 'c1', 'c2', 'c3'])
    asked.length = 0
    serve(100) // 一直是空页：到界为止，不无限翻；游标留着让用户再点「加载更多」
    const bounded = await loadCandidates('unknown', PJ)
    if (bounded.kind !== 'ok') throw new Error('x')
    expect(bounded.list.rows).toEqual([])
    expect(asked).toHaveLength(5)
    expect(bounded.list.next).toBe('c5')
  })

  it('排除此刻开着的那份；故障分类原样给出', async () => {
    backend.putSlot('d_cur', doc('cur'))
    backend.putSlot('d_x', doc('x'))
    backend.state.candidates.push({ doc_id: 'd_cur', scope: 'project' }, { doc_id: 'd_x', scope: 'project' })
    const res = await loadCandidates('project', PJ, undefined, new Set(['d_cur']))
    if (res.kind !== 'ok') throw new Error('x')
    expect(res.list.rows.map((r) => r.doc_id)).toEqual(['d_x'])
    backend.state.fail.candidates = 404
    expect(await loadCandidates('project', PJ)).toEqual({ kind: 'unsupported' })
    backend.state.fail.candidates = 500
    expect(await loadCandidates('project', PJ)).toEqual({ kind: 'unavailable' })
  })

  it('discoverProjectCandidates：只给本项目明确归属、能恢复的；more 说明第一页之外还有', async () => {
    backend.putSlot('d_ok', doc('ok'))
    backend.putSlot('d_bad', doc('bad'))
    backend.state.candidates.push(
      { doc_id: 'd_ok', scope: 'project' },
      { doc_id: 'd_bad', scope: 'project', status: 'invalid' },
    )
    const res = await discoverProjectCandidates(PJ)
    if (res.kind !== 'ok') throw new Error('x')
    expect(res.items.map((i) => i.doc_id)).toEqual(['d_ok'])
    expect(res.more).toBe(false)
  })
})

describe('recoverCopy：恢复副本', () => {
  it('成功：先看目标（404）再重读源，以固定目标 id 装进文档；源槽位字节不变，recoverCopy 自己不写任何东西（A15）', async () => {
    const src = backend.putSlot('d_old', doc('旧排版', { canvases: [{ id: 'c1', name: 'F', page: { w: 100, h: 80 }, objects: [{ id: 'o1', type: 'text', text: 'hi', x: 1, y: 2, w: 3, h: 4 }], guides: [] }] }))
    backend.state.candidates.push({ doc_id: 'd_old', scope: 'unknown', runtime: [{ script: 'fig1.py', figure: 'Fig1_PRISMA_Flowchart' }] })
    const item = await listed('d_old')
    expect(item.runtime).toEqual([{ script: 'fig1.py', figure: 'Fig1_PRISMA_Flowchart' }])
    backend.state.calls.length = 0
    const { d, installed } = deps()
    const target = recoveryCopyId('d_old', item.revision)

    const res = await recoverCopy(item, d)
    expect(res).toEqual({ kind: 'opened', id: target, existed: false })
    expect(installed).toEqual([target])
    // 实际序列：GET 目标 → GET 源 → （装进文档之后正常保存链）PUT 目标。没有对源的写。
    expect(backend.state.calls).toEqual([`GET autosave ${target}`, 'GET autosave d_old', `PUT autosave ${target}`])
    expect(backend.state.slots.get('d_old')).toEqual(src) // 字节、revision、mtime 都没动
    expect(backend.state.owners.has('d_old')).toBe(false) // 源归属不动
    // 装进去的是源正文的内容（对象 id / 几何 / 文字都在）
    const copy = JSON.parse(backend.state.slots.get(target)!.body)
    expect(copy.canvases[0].objects[0]).toMatchObject({ id: 'o1', text: 'hi', x: 1, y: 2, w: 3, h: 4 })
  })

  it('目标已存在（上次恢复过 / 重复点击）：直接打开它，不再读源、不再装第二份（幂等）', async () => {
    backend.putSlot('d_old', doc('旧'))
    backend.state.candidates.push({ doc_id: 'd_old', scope: 'unknown' })
    const item = await listed('d_old')
    const { d } = deps()
    await recoverCopy(item, d)
    backend.state.calls.length = 0
    const again = deps()
    const res = await recoverCopy(item, again.d)
    expect(res).toMatchObject({ kind: 'opened', existed: true })
    expect(again.d.install).not.toHaveBeenCalled()
    expect(again.d.openExisting).toHaveBeenCalledWith(recoveryCopyId('d_old', item.revision))
    expect(backend.state.calls).toEqual([`GET autosave ${recoveryCopyId('d_old', item.revision)}`])
    expect(backend.writes()).toEqual([])
  })

  it('双击：并发两次确认只装一次，两次拿到同一个结果', async () => {
    backend.putSlot('d_old', doc('旧'))
    backend.state.candidates.push({ doc_id: 'd_old', scope: 'unknown' })
    const item = await listed('d_old')
    const { d, installed } = deps()
    const [a, b] = await Promise.all([recoverCopy(item, d), recoverCopy(item, d)])
    expect(a).toEqual(b)
    expect(installed).toHaveLength(1)
    expect(backend.puts()).toHaveLength(1)
  })

  it('预览之后源被改了（revision 变）：stale，什么都不装、不写（A30）', async () => {
    backend.putSlot('d_old', doc('旧'))
    backend.state.candidates.push({ doc_id: 'd_old', scope: 'unknown' })
    const item = await listed('d_old')
    const changed = backend.putSlot('d_old', doc('旧', { updatedAt: 99 })) // 别处又存了一次
    const { d } = deps()
    expect(await recoverCopy(item, d)).toEqual({ kind: 'stale' })
    expect(d.install).not.toHaveBeenCalled()
    expect(backend.writes()).toEqual([])
    expect(backend.state.slots.get('d_old')).toEqual(changed)
    // 刷新列表后用新 revision 再确认：目标 id 随版本而变，恢复的是新的这一版
    const fresh = await listed('d_old')
    expect(fresh.revision).not.toBe(item.revision)
    const second = deps()
    const res = await recoverCopy(fresh, second.d)
    expect(res).toMatchObject({ kind: 'opened', existed: false })
    expect((res as { id: string }).id).toBe(recoveryCopyId('d_old', fresh.revision))
  })

  it('读不到目标（5xx / 网络）不往下走：不知道它在不在，装下去可能生出第二份', async () => {
    backend.putSlot('d_old', doc('旧'))
    backend.state.candidates.push({ doc_id: 'd_old', scope: 'unknown' })
    const item = await listed('d_old')
    backend.state.failGetDoc.set(recoveryCopyId('d_old', item.revision), 500)
    const { d } = deps()
    expect(await recoverCopy(item, d)).toEqual({ kind: 'issue', reason: 'unavailable' })
    expect(d.install).not.toHaveBeenCalled()
    expect(backend.writes()).toEqual([])
  })

  it.each([
    ['源已不在', (b: typeof backend) => b.state.slots.delete('d_old'), 'missing'],
    ['源 5xx', (b: typeof backend) => b.state.failGetDoc.set('d_old', 503), 'unavailable'],
    ['源被后端判不可用（400）', (b: typeof backend) => b.state.failGetDoc.set('d_old', 400), 'invalid'],
  ])('%s：没有恢复、不装、不写', async (_label, mutate, reason) => {
    backend.putSlot('d_old', doc('旧'))
    backend.state.candidates.push({ doc_id: 'd_old', scope: 'unknown' })
    const item = await listed('d_old')
    mutate(backend)
    const { d } = deps()
    expect(await recoverCopy(item, d)).toEqual({ kind: 'issue', reason })
    expect(d.install).not.toHaveBeenCalled()
    expect(backend.writes()).toEqual([])
  })

  it('来自更新版本（schema 更高）：拒绝，不降级覆盖', async () => {
    backend.putSlot('d_new', doc('新', { schema: 99 }))
    backend.state.candidates.push({ doc_id: 'd_new', scope: 'unknown' })
    const item = await listed('d_new')
    expect(rowIssue(item)).toBe('too_new')
    const { d } = deps()
    expect(await recoverCopy(item, d)).toEqual({ kind: 'issue', reason: 'too_new' })
    expect(d.install).not.toHaveBeenCalled()
  })

  it('用户取消装载（install 回 false）/ 项目已切换（stillCurrent 假）：cancelled，不写', async () => {
    backend.putSlot('d_old', doc('旧'))
    backend.state.candidates.push({ doc_id: 'd_old', scope: 'unknown' })
    const item = await listed('d_old')
    const declined = deps({ install: vi.fn(async () => false) })
    expect(await recoverCopy(item, declined.d)).toEqual({ kind: 'issue', reason: 'cancelled' })
    const switched = deps({ stillCurrent: () => false })
    expect(await recoverCopy(item, switched.d)).toEqual({ kind: 'issue', reason: 'cancelled' })
    expect(switched.d.install).not.toHaveBeenCalled()
    expect(backend.writes()).toEqual([])
  })

  it('用户从不点确认（取消）= 只有列表那一次只读 GET，零写入', async () => {
    backend.putSlot('d_old', doc('旧'))
    backend.state.candidates.push({ doc_id: 'd_old', scope: 'unknown' })
    await listed('d_old')
    expect(backend.state.calls).toEqual(['GET candidates unknown'])
    expect(backend.writes()).toEqual([])
  })
})
