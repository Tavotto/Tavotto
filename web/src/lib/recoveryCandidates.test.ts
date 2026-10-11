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
  discoverProjectCandidates,
  hasMore,
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
function deps(over: Partial<RecoverCopyDeps> = {}, pj: string = PJ) {
  const installed: string[] = []
  const d: RecoverCopyDeps = {
    pj,
    openExisting: vi.fn(async () => true),
    install: vi.fn(async (pd, id) => {
      installed.push(id)
      await fetch(`/api/autosave/${id}?pj=${pj}`, { method: 'PUT', body: JSON.stringify(pd) })
      return true
    }),
    ...over,
  }
  return { d, installed }
}

describe('recoveryCopyId', () => {
  it('固定：同源同版本同目标；源改过（revision 变）是另一个目标；符合后端 doc_id 规则', () => {
    const a = recoveryCopyId('d_abcdef12', 'h12345678aaaa', 'p_a')
    expect(a).toBe(recoveryCopyId('d_abcdef12', 'h12345678aaaa', 'p_a'))
    expect(a).toMatch(/^d_rc_d_abcdef12_h1234567_[0-9a-f]{8}$/)
    expect(recoveryCopyId('d_abcdef12', 'h99999999aaaa', 'p_a')).not.toBe(a)
    expect(recoveryCopyId('d_other', 'h12345678aaaa', 'p_a')).not.toBe(a)
    expect(recoveryCopyId('d_abcdef12', 'h12345678aaaa', 'p_b')).not.toBe(a) // 目标项目也是身份的一部分
    expect(recoveryCopyId('d_abcdef12', 'h12345678aaaa', null)).not.toBe(a)
    for (const id of [recoveryCopyId('d/../../etc', 'zz', 'p/..'), recoveryCopyId('x'.repeat(200), null, null), recoveryCopyId('', '', 'p_a')]) {
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
  it('450 行分页：首批 200 并标 capped；点「加载更多」400；再点 450，没有更多；全程无重复（可达性）', async () => {
    for (let i = 0; i < 450; i++) {
      backend.putSlot(`d_${i}`, doc(`n${i}`))
      backend.state.candidates.push({ doc_id: `d_${i}`, scope: 'unknown' })
    }
    const first = await loadCandidates('unknown', PJ)
    if (first.kind !== 'ok') throw new Error('x')
    expect(first.list.rows).toHaveLength(CANDIDATE_CAP)
    expect(first.list).toMatchObject({ capped: true })
    expect(hasMore(first.list)).toBe(true)
    const second = await loadCandidates('unknown', PJ, first.list)
    if (second.kind !== 'ok') throw new Error('x')
    expect(second.list.rows).toHaveLength(2 * CANDIDATE_CAP)
    expect(second.list.capped).toBe(true)
    const third = await loadCandidates('unknown', PJ, second.list)
    if (third.kind !== 'ok') throw new Error('x')
    expect(third.list.rows).toHaveLength(450)
    expect(third.list.capped).toBe(false)
    expect(hasMore(third.list)).toBe(false)
    const ids = third.list.rows.map((r) => r.doc_id)
    expect(new Set(ids).size).toBe(450)
    expect(ids).toEqual(Array.from({ length: 450 }, (_, i) => `d_${i}`)) // 顺序也原样
    expect(backend.writes()).toEqual([]) // 全程只读
  })

  it('一批被截在页中间：被截掉的行先放进下一批，再读 next；最后一页被截一半也不丢（Codex P2）', async () => {
    const mk = (n: number, from: number) =>
      Array.from({ length: n }, (_, i) => ({ doc_id: `d_${from + i}`, name: 'x', updatedAt: 1, mtime: 1, revision: 'r', schema: 3, status: 'ok', canvases: 1, objects: 1, owner: 'this', evidence: 'owners' }))
    const pages = [
      { items: mk(150, 0), next_cursor: 'c1' },
      { items: mk(150, 150), next_cursor: 'c2' },
      { items: mk(150, 300), next_cursor: null },
    ]
    const asked: string[] = []
    globalThis.fetch = (async (url: unknown) => {
      const c = new URL(String(url), 'http://x').searchParams.get('cursor') ?? ''
      asked.push(c)
      return new Response(JSON.stringify({ ...pages[c === '' ? 0 : Number(c.slice(1))], truncated: false, index: 'ok' }))
    }) as typeof fetch
    const one = await loadCandidates('project', PJ)
    if (one.kind !== 'ok') throw new Error('x')
    expect(one.list.rows).toHaveLength(200)
    expect(one.list.leftover).toHaveLength(100) // 第二页被截掉的 100 行
    expect(one.list).toMatchObject({ next: 'c2', capped: true })
    const two = await loadCandidates('project', PJ, one.list)
    if (two.kind !== 'ok') throw new Error('x')
    expect(asked).toEqual(['', 'c1', 'c2']) // 没有重读已经读过的页
    expect(two.list.rows).toHaveLength(400)
    expect(two.list).toMatchObject({ next: null, capped: true }) // 最后一页被截一半：next 为 null 也还有
    expect(two.list.leftover).toHaveLength(50)
    expect(hasMore(two.list)).toBe(true)
    const three = await loadCandidates('project', PJ, two.list)
    if (three.kind !== 'ok') throw new Error('x')
    expect(asked).toEqual(['', 'c1', 'c2']) // 只吃 leftover，不再请求
    expect(three.list.rows).toHaveLength(450)
    expect(hasMore(three.list)).toBe(false)
    expect(new Set(three.list.rows.map((r) => r.doc_id)).size).toBe(450)
  })

  it('翻页之间重复出现的同一份只算一次', async () => {
    for (let i = 0; i < 80; i++) {
      backend.putSlot(`d_${i}`, doc(`n${i}`))
      backend.state.candidates.push({ doc_id: `d_${i}`, scope: 'unknown' })
    }
    backend.state.candidates.splice(50, 0, { doc_id: 'd_49', scope: 'unknown' })
    const res = await loadCandidates('unknown', PJ)
    if (res.kind !== 'ok') throw new Error('x')
    const ids = res.list.rows.map((r) => r.doc_id)
    expect(new Set(ids).size).toBe(ids.length)
    expect(ids).toHaveLength(80)
  })

  it('truncated 带进列表结果与发现结果（没有下一页也不代表列全）', async () => {
    backend.putSlot('d_x', doc('x'))
    backend.state.candidates.push({ doc_id: 'd_x', scope: 'project' })
    backend.state.truncated = true
    const res = await loadCandidates('project', PJ)
    if (res.kind !== 'ok') throw new Error('x')
    expect(res.list).toMatchObject({ next: null, truncated: true })
    const found = await discoverProjectCandidates(PJ)
    expect(found).toMatchObject({ kind: 'ok', more: false, truncated: true })
    backend.state.truncated = false
    expect(await discoverProjectCandidates(PJ)).toMatchObject({ truncated: false })
  })

  it('truncated 是粘性的：中间一页截断、最后一页没截断，结果仍是 true；加载更多也继承', async () => {
    const item = { doc_id: 'd_late', name: 'x', updatedAt: 1, mtime: 1, revision: 'r', schema: 3, status: 'ok', canvases: 1, objects: 1, owner: 'this', evidence: 'owners' }
    const pages = [
      { items: [], next_cursor: 'c1', truncated: true },
      { items: [item], next_cursor: null, truncated: false },
    ]
    let n = 0
    globalThis.fetch = (async () => new Response(JSON.stringify({ ...pages[Math.min(n++, 1)], index: 'ok' }))) as typeof fetch
    const res = await loadCandidates('project', PJ)
    if (res.kind !== 'ok') throw new Error('x')
    expect(res.list.rows).toHaveLength(1)
    expect(res.list.truncated).toBe(true)
    // 加载更多：上一次已截断，这一页没截断，仍是 true
    globalThis.fetch = (async () => new Response(JSON.stringify({ items: [], next_cursor: null, truncated: false, index: 'ok' }))) as typeof fetch
    const more = await loadCandidates('project', PJ, { ...res.list, next: 'c9' })
    if (more.kind !== 'ok') throw new Error('x')
    expect(more.list.truncated).toBe(true)
  })

  it('早一页归属索引退化，后页恢复正常：当前批和加载更多仍提示重试', async () => {
    const pages = [
      { items: [], next_cursor: 'c1', index: 'degraded' },
      { items: [{ doc_id: 'd_seen', name: '可见', updatedAt: 1, mtime: 1, revision: 'r', schema: 3, status: 'ok', canvases: 1, objects: 1, owner: 'this', evidence: 'owners' }], next_cursor: null, index: 'ok' },
    ]
    let n = 0
    globalThis.fetch = (async () => new Response(JSON.stringify({ ...pages[Math.min(n++, 1)], truncated: false }))) as typeof fetch
    const first = await loadCandidates('project', PJ)
    if (first.kind !== 'ok') throw new Error('list failed')
    expect(first.list.rows.map((r) => r.doc_id)).toEqual(['d_seen'])
    expect(first.list.index).toBe('degraded')
    const more = await loadCandidates('project', PJ, { ...first.list, next: 'c2' })
    if (more.kind !== 'ok') throw new Error('list failed')
    expect(more.list.index).toBe('degraded')
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
    const target = recoveryCopyId('d_old', item.revision, PJ)

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
    expect(again.d.openExisting).toHaveBeenCalledWith(recoveryCopyId('d_old', item.revision, PJ), expect.any(String))
    const t = recoveryCopyId('d_old', item.revision, PJ)
    expect(backend.state.calls).toEqual([`GET autosave ${t}`, `GET owner ${t}`]) // 重开前核对它确实是这个项目的
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

  it('A、B 两个项目恢复同一份源的同一个版本：各得各的副本，B 不会打开 A 的（Codex P1）', async () => {
    backend.putSlot('d_old', doc('旧'))
    backend.state.candidates.push({ doc_id: 'd_old', scope: 'unknown' })
    const item = await listed('d_old')
    const a = deps({}, 'p_a')
    const ra = await recoverCopy(item, a.d)
    // A 的副本在 A 里被编辑过
    const idA = (ra as { id: string }).id
    const editedA = backend.putSlot(idA, doc('A 改过的副本', { updatedAt: 500 }), 'p_a')
    const b = deps({}, 'p_b')
    const rb = await recoverCopy(item, b.d)
    expect(rb).toMatchObject({ kind: 'opened', existed: false })
    const idB = (rb as { id: string }).id
    expect(idB).not.toBe(idA)
    expect(b.d.openExisting).not.toHaveBeenCalled()
    expect(JSON.parse(backend.state.slots.get(idB)!.body).project.name).toBe('旧') // B 的是源的内容
    expect(backend.state.slots.get(idA)).toEqual(editedA) // A 的副本一个字节没动
    expect(backend.state.owners.get(idB)).toBe('p_b')
  })

  it('已存在的目标不属于当前项目：不重开，说清楚（owners 证据 / 本机索引都不成立）', async () => {
    backend.putSlot('d_old', doc('旧'))
    backend.state.candidates.push({ doc_id: 'd_old', scope: 'unknown' })
    const item = await listed('d_old')
    const target = recoveryCopyId('d_old', item.revision, PJ)
    backend.putSlot(target, doc('别人的'), 'p_other') // 目标 id 上躺着别的项目的槽位
    const { d } = deps()
    expect(await recoverCopy(item, d)).toEqual({ kind: 'issue', reason: 'foreign' })
    expect(d.openExisting).not.toHaveBeenCalled()
    expect(d.install).not.toHaveBeenCalled()
    // 归属未知 / 证据未核对 / 问不到：同样不重开
    backend.state.owners.delete(target)
    expect(await recoverCopy(item, deps().d)).toEqual({ kind: 'issue', reason: 'foreign' })
    backend.state.ownerAnswers.set(`${PJ}|${target}`, { owner: 'this', evidence: 'owners', verified: false })
    expect(await recoverCopy(item, deps().d)).toEqual({ kind: 'issue', reason: 'foreign' })
    backend.state.fail.owner = 500
    expect(await recoverCopy(item, deps().d)).toEqual({ kind: 'issue', reason: 'foreign' })
    backend.state.fail.owner = undefined
    // 归属证据是对另一版正文下的结论（之后被存了新的一版）：同样不重开
    backend.state.ownerAnswers.set(`${PJ}|${target}`, { owner: 'this', evidence: 'owners', verified: true, revision: 'older-version' })
    expect(await recoverCopy(item, deps().d)).toEqual({ kind: 'issue', reason: 'foreign' })
    // 响应头被吃了：有结论的 revision 却读不到正文的 revision，没法核对，不算
    backend.state.ownerAnswers.delete(`${PJ}|${target}`)
    backend.state.owners.set(target, PJ)
    backend.state.stripRevisionHeader = true
    expect(await recoverCopy(item, deps().d)).toEqual({ kind: 'issue', reason: 'foreign' })
    backend.state.stripRevisionHeader = false
    // 本机索引记着当前项目也不够：它可能是别的 origin / profile 留下的陈旧记录，而后端 owners 已是别的项目
    backend.state.ownerAnswers.delete(`${PJ}|${target}`)
    backend.state.owners.set(target, 'p_b')
    localStorage.setItem('tavotto.docIndex', JSON.stringify([{ id: target, name: 'x', savedAt: 1, objects: 1, projectId: PJ }]))
    const stalePath = deps()
    expect(await recoverCopy(item, stalePath.d)).toEqual({ kind: 'issue', reason: 'foreign' })
    expect(stalePath.d.openExisting).not.toHaveBeenCalled()
    expect(stalePath.d.install).not.toHaveBeenCalled()
    // 旧后端（/owner 404）：拒绝，不退回本机索引
    backend.state.owners.set(target, PJ)
    backend.state.fail.owner = 404
    expect(await recoverCopy(item, deps().d)).toEqual({ kind: 'issue', reason: 'foreign' })
    backend.state.fail.owner = undefined
    // 后端证据成立（owners=this、已核对、revision 对得上）：打开
    const ok = deps()
    expect(await recoverCopy(item, ok.d)).toMatchObject({ kind: 'opened', existed: true })
    localStorage.clear()
    expect(backend.writes()).toEqual([])
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
    expect((res as { id: string }).id).toBe(recoveryCopyId('d_old', fresh.revision, PJ))
  })

  it('读不到目标（5xx / 网络）不往下走：不知道它在不在，装下去可能生出第二份', async () => {
    backend.putSlot('d_old', doc('旧'))
    backend.state.candidates.push({ doc_id: 'd_old', scope: 'unknown' })
    const item = await listed('d_old')
    backend.state.failGetDoc.set(recoveryCopyId('d_old', item.revision, PJ), 500)
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
