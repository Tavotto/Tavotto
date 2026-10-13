import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createFakeBackend } from '@/test/fakeSessionBackend'
import type { CanvasObject, Guide } from '@/types/document'
import {
  documentHasContent,
  forgetProjectDocument,
  readProjectDocument,
  rememberProjectDocument,
} from './projectDocs'

describe('projectDocs：按项目记「上次开着哪份文档」', () => {
  beforeEach(() => localStorage.clear())

  it('写了就读得回，按项目隔离，忘掉就没了', () => {
    rememberProjectDocument('p_a', { id: 'd1', name: 'Fig 1' })
    rememberProjectDocument('p_b', { id: 'd2', name: 'Fig 2' })
    expect(readProjectDocument('p_a')).toEqual({ id: 'd1', name: 'Fig 1' })
    expect(readProjectDocument('p_b')).toEqual({ id: 'd2', name: 'Fig 2' })
    forgetProjectDocument('p_a')
    expect(readProjectDocument('p_a')).toBeNull()
    expect(readProjectDocument('p_b')).not.toBeNull()
  })

  it('损坏的记录当作没有', () => {
    localStorage.setItem('tavotto.projectDoc.p_a', '{not json')
    expect(readProjectDocument('p_a')).toBeNull()
    localStorage.setItem('tavotto.projectDoc.p_a', JSON.stringify({ name: 'x' }))
    expect(readProjectDocument('p_a')).toBeNull()
  })

  it('documentHasContent：热态画布、其它画布、画布数三处都看', () => {
    // 判据只数个数，不看字段：一个占位对象就够
    const obj = { id: 'o', type: 'text' } as unknown as CanvasObject
    const guide = { id: 'g', axis: 'x', pos: 1 } as unknown as Guide
    const empty = { objects: [] as CanvasObject[], guides: [] as Guide[] }
    expect(documentHasContent({ doc: empty, canvases: [empty] })).toBe(false)
    expect(documentHasContent({ doc: { objects: [obj], guides: [] }, canvases: [empty] })).toBe(true)
    expect(documentHasContent({ doc: empty, canvases: [empty, { objects: [], guides: [guide] }] })).toBe(true)
    expect(documentHasContent({ doc: empty, canvases: [empty, empty] })).toBe(true)
  })
})


/* -------------------------------------------------------------------------- */
/*  读与落账分开：planRestore 只读、confirmRestored 接受之后才写                    */
/* -------------------------------------------------------------------------- */

const PJ = 'p_a'
const backend = createFakeBackend()
const realFetch = globalThis.fetch

const cacheOf = () => localStorage.getItem(`tavotto.projectDoc.${PJ}`)
const cacheJson = () => JSON.parse(cacheOf() ?? 'null') as Record<string, unknown> | null
const setCache = (v: Record<string, unknown>) => localStorage.setItem(`tavotto.projectDoc.${PJ}`, JSON.stringify(v))
const setIndex = (entries: Record<string, unknown>[]) => localStorage.setItem('tavotto.docIndex', JSON.stringify(entries))
const indexOwnerOf = (id: string) =>
  (JSON.parse(localStorage.getItem('tavotto.docIndex') ?? '[]') as { id: string; projectId?: string }[]).find(
    (e) => e.id === id,
  )?.projectId
const docBody = (name: string) => ({ schema: 3, project: { id: 'x', name }, canvases: [], activeCanvasId: 'c', createdAt: 0, updatedAt: 1 })

/** 全新的模块实例（模块级的 remoteMissing / lastPush / remoteTail 作废） */
async function fresh() {
  vi.resetModules()
  return await import('./projectDocs')
}
async function settle() {
  for (let i = 0; i < 3; i++) await new Promise((r) => setTimeout(r, 5))
}

describe('planRestore / confirmRestored：只读规划，接受之后才落账', () => {
  beforeEach(() => {
    localStorage.clear()
    backend.reset()
    globalThis.fetch = backend.fetch
  })
  afterEach(() => {
    globalThis.fetch = realFetch
  })

  it('后端 last 在、本机缓存陈旧：规划零写入（A19）；接受后缓存才校正成后端那条', async () => {
    const docs = await fresh()
    backend.putSlot('d_b', docBody('B'), PJ)
    const last = backend.setLast(PJ, 'd_b', 'B')
    setCache({ id: 'd_a', name: 'A' })
    const before = cacheOf()

    const plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.session.kind).toBe('ok')
    expect(plan.preferred).toMatchObject({ id: 'd_b', sources: ['backend_last'], ownership: 'this' })
    expect(plan.fallbacks.map((c) => c.id)).toEqual(['d_a'])
    expect(plan.migrate).toBe(false)
    expect(backend.writes()).toEqual([])
    expect(cacheOf()).toBe(before)

    await docs.confirmRestored(PJ, { id: 'd_b', name: 'B' }, plan)
    expect(cacheJson()).toMatchObject({ id: 'd_b', name: 'B', seenAt: last.at })
    expect(backend.writes()).toEqual([])
  })

  it('后端 ok + last=null、本机缓存：迁移要等接受之后（A08）；规划与未选中都不推', async () => {
    const docs = await fresh()
    setCache({ id: 'd_legacy', name: 'legacy' })
    const before = cacheOf()

    const plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.preferred).toMatchObject({ id: 'd_legacy', sources: ['local_cache'] })
    expect(plan.migrate).toBe(true)
    expect(backend.writes()).toEqual([])
    expect(cacheOf()).toBe(before)
    await settle()
    expect(backend.writes()).toEqual([]) // 没人接受它：什么都不会发生

    backend.putSlot('d_legacy', docBody('legacy'))
    await docs.confirmRestored(PJ, { id: 'd_legacy', name: 'legacy' }, plan)
    expect(backend.writes()).toEqual(['PUT last d_legacy'])
    expect(backend.session(PJ).last?.doc_id).toBe('d_legacy')
    expect(cacheJson()?.pendingGen).toBeUndefined() // durable 确认了，待确认标记摘掉
  })

  it('旧入口 loadProjectDocument：正文不在就不把本机引用推成后端 last（修复前缺失槽位会成为唯一的 last）', async () => {
    const docs = await fresh()
    setCache({ id: 'd_ghost', name: 'ghost' })
    expect(await docs.loadProjectDocument(PJ)).toEqual({ id: 'd_ghost', name: 'ghost' })
    await settle()
    expect(backend.writes()).toEqual([])
    expect(backend.session(PJ).last).toBeNull()
    // 对照：正文在（磁盘上）就照常迁移
    backend.putSlot('d_ghost', docBody('ghost'))
    expect(await docs.loadProjectDocument(PJ)).toEqual({ id: 'd_ghost', name: 'ghost' })
    await settle()
    expect(backend.session(PJ).last?.doc_id).toBe('d_ghost')
  })

  it('旧入口 loadProjectDocument：只有本机副本（磁盘上没有）也算正文在，照常迁移', async () => {
    const docs = await fresh()
    setCache({ id: 'd_local', name: 'local' })
    localStorage.setItem('tavotto.autosave.d_local', JSON.stringify(docBody('local')))
    await docs.loadProjectDocument(PJ)
    await settle()
    expect(backend.writes()).toEqual(['PUT last d_local'])
  })

  it.each([[500], [401], [403], ['network' as const]])(
    '会话读取失败（%s）不是旧端点：session=unavailable，首选留空，本机线索全在 fallbacks（A04/A16）',
    async (failure) => {
      const docs = await fresh()
      backend.state.fail.session = failure
      setCache({ id: 'd_c', name: 'C' })
      const plan = await docs.planRestore(PJ, { globalCurrent: null })
      expect(plan.session).toEqual({ kind: 'unavailable', status: failure === 'network' ? null : failure })
      expect(plan.preferred).toBeNull()
      expect(plan.fallbacks.map((c) => c.id)).toEqual(['d_c'])
      expect(plan.migrate).toBe(false)
      await docs.confirmRestored(PJ, { id: 'd_c', name: 'C' }, plan)
      expect(backend.writes()).toEqual([]) // 后端状态未知：不推
    },
  )

  it('会话读取 404 = 旧后端：沿用本机缓存兼容分支（A29），不推迁移', async () => {
    const docs = await fresh()
    backend.state.fail.session = 404
    setCache({ id: 'd_c', name: 'C' })
    const plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.session).toEqual({ kind: 'unsupported' })
    expect(plan.preferred).toMatchObject({ id: 'd_c', sources: ['local_cache'] })
    expect(plan.migrate).toBe(false)
    await docs.confirmRestored(PJ, { id: 'd_c', name: 'C' }, plan)
    expect(backend.writes()).toEqual([])
  })
})

describe('待确认意图与 durable last（A09 / A32）', () => {
  beforeEach(() => {
    localStorage.clear()
    backend.reset()
    globalThis.fetch = backend.fetch
  })
  afterEach(() => {
    globalThis.fetch = realFetch
  })

  it('B 还没有正文（early last 被记成 pending），durable 仍是 A：本机 B 是首选的打开意图，A 在后备（A09）', async () => {
    const docs = await fresh()
    backend.putSlot('d_a', docBody('A'), PJ)
    backend.setLast(PJ, 'd_a', 'A')
    docs.rememberProjectDocument(PJ, { id: 'd_b', name: 'B' })
    await settle()
    expect(backend.session(PJ).pending?.doc_id).toBe('d_b')
    expect(backend.session(PJ).last?.doc_id).toBe('d_a')
    expect(cacheJson()?.pendingGen).toBeTruthy() // pending 回包不算确认

    const reborn = await fresh() // 同 origin 重启
    const plan = await reborn.planRestore(PJ, { globalCurrent: null })
    expect(plan.preferred).toMatchObject({ id: 'd_b', pendingIntent: true })
    expect(plan.preferred?.sources).toContain('local_cache')
    expect(plan.fallbacks.map((c) => c.id)).toEqual(['d_a'])
    expect(plan.fallbacks[0].pendingIntent).toBeUndefined() // A 是 durable：缺了是故障，不是静默跳过
  })

  it('换了 origin（本机缓存空）：后端 pending 比 durable last 更晚才是首选；更早的不是（旧意图不压新 last）', async () => {
    const docs = await fresh()
    backend.setLast(PJ, 'd_a', 'A')
    backend.setPending(PJ, 'd_b', 'B') // 时刻晚于 last
    let plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.preferred).toMatchObject({ id: 'd_b', sources: ['backend_pending'], pendingIntent: true })
    expect(plan.fallbacks.map((c) => c.id)).toEqual(['d_a'])

    backend.setLast(PJ, 'd_c', 'C') // 之后又 durable 打开了 C：pending B 是旧意图
    plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.preferred?.id).toBe('d_c')
    expect(plan.fallbacks.map((c) => c.id)).toEqual(['d_b'])
  })

  it('会话索引降级（三项全空）不折成「确认没有历史」：plan.session.index 原样保留，缓存迁移仍等接受之后', async () => {
    const docs = await fresh()
    backend.session(PJ).index = 'degraded'
    setCache({ id: 'd_c', name: 'C' })
    const plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.session).toMatchObject({ kind: 'ok', index: 'degraded', last: null, prev: null, pending: null })
    expect(plan.migrate).toBe(true)
    expect(backend.writes()).toEqual([])
  })

  it('任一后端指针明说 revision:null 且没有别的来源带 revision → expectedAbsent；字段缺席（旧后端）/ 有 hash 的不标', async () => {
    const docs = await fresh()
    const find = async (id: string) => {
      const plan = await docs.planRestore(PJ, { globalCurrent: null })
      return [plan.preferred!, ...plan.fallbacks].find((c) => c.id === id)!
    }
    // pending 明说 null
    backend.setLast(PJ, 'd_a', 'A', 'rev-a')
    backend.setPending(PJ, 'd_b', 'B', null)
    expect(await find('d_b')).toMatchObject({ sources: ['backend_pending'], expectedAbsent: true })
    expect((await find('d_b')).expectedRevision).toBeUndefined()
    expect((await find('d_a')).expectedAbsent).toBeUndefined()
    expect((await find('d_a')).expectedRevision).toBe('rev-a')
    // last / prev 明说 null 同样
    backend.setLast(PJ, 'd_n', 'N', null)
    expect(await find('d_n')).toMatchObject({ sources: ['backend_last'], expectedAbsent: true })
    backend.session(PJ).prev = backend.setLast(PJ, 'd_p', 'P', null)
    backend.setLast(PJ, 'd_q', 'Q', 'rev-q')
    expect(await find('d_p')).toMatchObject({ sources: ['backend_prev'], expectedAbsent: true })
    // 键缺席（旧后端）：不知道，不当「预期不存在」，也没有 expectedRevision
    backend.state.sessions.clear()
    backend.setLast(PJ, 'd_old', 'old')
    backend.setPending(PJ, 'd_oldp', 'oldp')
    const oldLast = await find('d_old')
    const oldPending = await find('d_oldp')
    expect(oldLast.expectedAbsent).toBeUndefined()
    expect(oldLast.expectedRevision).toBeUndefined()
    expect(oldPending.expectedAbsent).toBeUndefined()
    // 同一份：别的来源带 hash 就不是「预期不存在」
    backend.state.sessions.clear()
    backend.setLast(PJ, 'd_m', 'M', 'rev-m')
    backend.setPending(PJ, 'd_m', 'M', null)
    const merged = await find('d_m')
    expect(merged.expectedAbsent).toBeUndefined()
    expect(merged.expectedRevision).toBe('rev-m')
    // 本机缓存的待确认意图不是后端指针：这里不因指针标；它的 expectedAbsent 来自 /owner（见归属那组用例）
    backend.state.sessions.clear()
    setCache({ id: 'd_c', name: 'C', pendingBase: 0, pendingGen: 'g' })
    backend.putSlot('d_c', docBody('C')) // 槽位在：/owner 的结论绑到它的 revision，不是「预期不存在」
    expect((await find('d_c')).expectedAbsent).toBeUndefined()
  })

  it('备份 prev 进后备（保留来源）', async () => {
    const docs = await fresh()
    backend.setLast(PJ, 'd_a', 'A')
    backend.session(PJ).prev = backend.setLast(PJ, 'd_a', 'A')
    backend.setLast(PJ, 'd_b', 'B')
    const plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.preferred?.id).toBe('d_b')
    expect(plan.fallbacks).toMatchObject([{ id: 'd_a', sources: ['backend_prev'] }])
  })

  it('指针的 revision 带进候选的 expectedRevision（last / prev），没有就缺席；本机来源不编一个', async () => {
    const docs = await fresh()
    backend.setLast(PJ, 'd_p', 'P', 'rev-p')
    backend.session(PJ).prev = { ...backend.session(PJ).last! }
    backend.setLast(PJ, 'd_a', 'A', 'rev-a')
    setCache({ id: 'd_c', name: 'C' })
    const plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.preferred).toMatchObject({ id: 'd_a', expectedRevision: 'rev-a' })
    const byId = Object.fromEntries(plan.fallbacks.map((c) => [c.id, c]))
    expect(byId.d_p.expectedRevision).toBe('rev-p')
    expect(byId.d_c.expectedRevision).toBeUndefined()
    backend.setLast(PJ, 'd_old', 'old') // 旧后端 / 没记 revision 的指针
    expect((await docs.planRestore(PJ, { globalCurrent: null })).preferred?.expectedRevision).toBeUndefined()
  })

  it('同一 docId 的多个来源合并，不重复出现', async () => {
    const docs = await fresh()
    backend.setLast(PJ, 'd_a', 'A')
    setCache({ id: 'd_a', name: 'A' })
    const plan = await docs.planRestore(PJ, { globalCurrent: 'd_a' })
    expect(plan.preferred?.sources).toEqual(['backend_last', 'local_cache', 'global_current'])
    expect(plan.fallbacks).toEqual([])
  })

  it('本机待确认且后端那条不晚于写下时见过的（只比后端的钟）：本机赢；后端更晚写过则后端赢', async () => {
    const docs = await fresh()
    const last = backend.setLast(PJ, 'd_a', 'A')
    setCache({ id: 'd_b', name: 'B', pendingBase: last.at, pendingGen: 'g1' })
    let plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.preferred?.id).toBe('d_b')
    // 服务器钟比浏览器快得多也无妨：只在后端自己的时钟上比
    setCache({ id: 'd_b', name: 'B', pendingBase: last.at - 1, pendingGen: 'g1' })
    plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.preferred?.id).toBe('d_a')
  })

  it('GET 在途时本机缓存被改过：回包作废，现在的缓存赢（#719）', async () => {
    const docs = await fresh()
    backend.setLast(PJ, 'd_old', 'old')
    let release!: () => void
    backend.state.hold.session = new Promise<void>((r) => (release = r))
    const planning = docs.planRestore(PJ, { globalCurrent: null })
    await new Promise((r) => setTimeout(r, 10))
    setCache({ id: 'd_newer', name: 'newer' }) // 同 origin 的别的标签页刚记的
    release()
    const plan = await planning
    expect(plan.preferred?.id).toBe('d_newer')
    expect(plan.fallbacks.map((c) => c.id)).toEqual(['d_old'])
    await docs.confirmRestored(PJ, { id: 'd_old', name: 'old' }, plan)
    expect(cacheJson()?.id).toBe('d_newer') // 缓存在规划之后又变了：不动它
  })

  it('接受本机待确认候选之后补推，并且回包 pending 不摘标记；正文落盘后补记成 durable（A10）', async () => {
    const docs = await fresh()
    docs.rememberProjectDocument(PJ, { id: 'd_b', name: 'B' })
    await settle()
    expect(backend.puts()).toEqual(['PUT last d_b'])
    expect(backend.session(PJ).last).toBeNull()
    expect(cacheJson()?.pendingGen).toBeTruthy()

    // 正文还没落盘就收到别人的广播：不是这份，忽略（A11：旧文档的迟到保存不倒退 last）
    docs.notifyDocumentPersisted('d_zzz', PJ)
    await settle()
    expect(backend.puts()).toEqual(['PUT last d_b'])

    backend.putSlot('d_b', docBody('B'), PJ)
    docs.notifyDocumentPersisted('d_b', PJ)
    await settle()
    expect(backend.puts()).toEqual(['PUT last d_b', 'PUT last d_b'])
    expect(backend.session(PJ).last?.doc_id).toBe('d_b')
    expect(cacheJson()?.pendingGen).toBeUndefined()

    // 已 durable：之后每次保存成功不再重推
    docs.notifyDocumentPersisted('d_b', PJ)
    await settle()
    expect(backend.puts()).toHaveLength(2)
  })

  it('推送失败（5xx）后正文落盘：补记重推，不被「同 id 同名」去重挡住（A10）', async () => {
    const docs = await fresh()
    backend.putSlot('d_b', docBody('B'), PJ)
    backend.state.failOnce.putLast = 500
    docs.rememberProjectDocument(PJ, { id: 'd_b', name: 'B' })
    await settle()
    expect(backend.session(PJ).last).toBeNull()
    docs.notifyDocumentPersisted('d_b', PJ)
    await settle()
    expect(backend.session(PJ).last?.doc_id).toBe('d_b')
  })

  it('上个会话留下的待确认标记（模块里没有推送记录）：正文落盘后同样补推；没有标记则不推', async () => {
    const docs = await fresh()
    setCache({ id: 'd_b', name: 'B', pendingBase: 0, pendingGen: 'g0' })
    backend.putSlot('d_b', docBody('B'), PJ)
    docs.notifyDocumentPersisted('d_b', PJ)
    await settle()
    expect(backend.session(PJ).last?.doc_id).toBe('d_b')

    const again = await fresh()
    again.notifyDocumentPersisted('d_b', PJ) // 缓存已确认、无标记
    again.notifyDocumentPersisted('d_b', null) // 没开项目
    await settle()
    expect(backend.puts()).toHaveLength(1)
  })
})

describe('归属：本机索引 vs 后端证据（C1 / A16 / A17）', () => {
  beforeEach(() => {
    localStorage.clear()
    backend.reset()
    globalThis.fetch = backend.fetch
  })
  afterEach(() => {
    globalThis.fetch = realFetch
  })

  const foreignIndex = (id: string) => setIndex([{ id, name: id, savedAt: 1, objects: 1, projectId: 'p_other', projectName: 'Other' }])

  it('本机说 foreign、后端 owners=this 且 verified：候选可认领，本机索引在接受之后才纠正（C1）', async () => {
    const docs = await fresh()
    backend.setLast(PJ, 'd_x', 'X')
    backend.state.ownerAnswers.set(`${PJ}|d_x`, { owner: 'this', evidence: 'owners', verified: true })
    foreignIndex('d_x')
    const plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.preferred).toMatchObject({ id: 'd_x', ownership: 'this', ownershipEvidence: 'owners', correctsIndex: true })
    expect(indexOwnerOf('d_x')).toBe('p_other') // 规划不动本机记录
    await docs.confirmRestored(PJ, { id: 'd_x', name: 'X' }, plan)
    expect(indexOwnerOf('d_x')).toBe(PJ)
    expect(JSON.parse(localStorage.getItem('tavotto.docIndex')!)[0].projectName).toBeUndefined()
  })

  it('本机说 foreign、后端只有 assets / versions 证据：不翻案，候选被扣下（A17）', async () => {
    for (const evidence of ['assets', 'versions'] as const) {
      const docs = await fresh()
      backend.reset()
      backend.setLast(PJ, 'd_x', 'X')
      backend.state.ownerAnswers.set(`${PJ}|d_x`, { owner: 'this', evidence, verified: false })
      foreignIndex('d_x')
      const plan = await docs.planRestore(PJ, { globalCurrent: null })
      expect(plan.preferred).toBeNull()
      expect(plan.withheld).toMatchObject([{ reason: 'foreign', candidate: { id: 'd_x', ownership: 'other' } }])
    }
  })

  it('owners=this 但未核对（verified=false，正文与归属记录 revision 对不上）：同样不翻案', async () => {
    const docs = await fresh()
    backend.setLast(PJ, 'd_x', 'X')
    backend.state.ownerAnswers.set(`${PJ}|d_x`, { owner: 'this', evidence: 'owners', verified: false })
    foreignIndex('d_x')
    expect((await docs.planRestore(PJ, { globalCurrent: null })).preferred).toBeNull()
  })

  it('本机说 foreign、后端 owners=other / 旧后端 404：拒；问不到：拒但不当 foreign、缓存不作废', async () => {
    const docs = await fresh()
    setCache({ id: 'd_x', name: 'X' })
    foreignIndex('d_x')
    backend.state.fail.owner = 500
    let plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.preferred).toBeNull()
    expect(plan.withheld).toMatchObject([{ reason: 'owner_unavailable' }])
    expect(cacheJson()?.id).toBe('d_x') // 此刻只是不知道

    backend.state.fail.owner = undefined
    backend.state.ownerAnswers.set(`${PJ}|d_x`, { owner: 'other', evidence: 'owners', verified: true })
    plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.withheld).toMatchObject([{ reason: 'foreign' }])
    expect(cacheOf()).toBeNull() // 确知别人的：作废（原有行为）
  })

  it('旧后端（owner 404）下，本机 foreign 的缓存同样拒并作废', async () => {
    const docs = await fresh()
    setCache({ id: 'd_x', name: 'X' })
    foreignIndex('d_x')
    backend.state.fail.owner = 404
    const plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.preferred).toBeNull()
    expect(cacheOf()).toBeNull()
  })

  it('本机索引不知道归属的缓存：后端明确 other 才拒（等价于旧的 layout_foreign 拒收），其余认', async () => {
    const docs = await fresh()
    setCache({ id: 'd_x', name: 'X' })
    backend.state.ownerAnswers.set(`${PJ}|d_x`, { owner: 'other', evidence: 'owners', verified: true })
    const plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.preferred).toBeNull()
    expect(plan.withheld[0].reason).toBe('foreign')
    expect(cacheOf()).toBeNull()
    expect(backend.writes()).toEqual([])
  })

  it('仅 global currentDoc：本机索引 own 也向后端核对（可达时）；未知时按后端证据（this 任何等级可；unknown / 问不到不自动认领）', async () => {
    const docs = await fresh()
    setIndex([{ id: 'd_own', name: 'own', savedAt: 1, objects: 1, projectId: PJ }])
    const own = backend.putSlot('d_own', docBody('own'))
    let plan = await docs.planRestore(PJ, { globalCurrent: 'd_own' })
    expect(plan.preferred).toMatchObject({ id: 'd_own', sources: ['global_current'], ownershipEvidence: 'local_index', expectedRevision: own.revision })
    expect(backend.state.calls.filter((c) => c.startsWith('GET owner'))).toEqual(['GET owner d_own'])

    setIndex([])
    backend.state.ownerAnswers.set(`${PJ}|d_g`, { owner: 'this', evidence: 'assets', verified: false })
    plan = await docs.planRestore(PJ, { globalCurrent: 'd_g' })
    expect(plan.preferred).toMatchObject({ id: 'd_g', ownershipEvidence: 'assets' })

    backend.state.ownerAnswers.set(`${PJ}|d_g`, { owner: 'unknown', evidence: null, verified: false })
    plan = await docs.planRestore(PJ, { globalCurrent: 'd_g' })
    expect(plan.preferred).toBeNull()
    expect(plan.withheld).toMatchObject([{ reason: 'owner_unknown', candidate: { id: 'd_g', ownership: 'unknown' } }])

    backend.state.fail.owner = 'network'
    plan = await docs.planRestore(PJ, { globalCurrent: 'd_g' })
    expect(plan.preferred).toBeNull()
    expect(plan.withheld[0].reason).toBe('owner_unavailable')

    backend.state.fail.owner = 404 // 旧后端：照旧认（那里没有跨项目共用的稳定 origin）
    plan = await docs.planRestore(PJ, { globalCurrent: 'd_g' })
    expect(plan.preferred?.id).toBe('d_g')
    expect(backend.writes()).toEqual([])
  })

  it('本机记着「是这个项目的」却被别的 profile 认领给了 G（后端 owners=G）：扣下，不写本机记录，不安装（Codex P1）', async () => {
    const docs = await fresh()
    setIndex([{ id: 'd_x', name: 'x', savedAt: 1, objects: 1, projectId: PJ }])
    setCache({ id: 'd_x', name: 'X' })
    backend.putSlot('d_x', docBody('G 的正文'), 'p_g')
    const before = cacheOf()
    const plan = await docs.planRestore(PJ, { globalCurrent: 'd_x' })
    expect(plan.preferred).toBeNull()
    expect(plan.withheld).toMatchObject([{ reason: 'foreign', candidate: { id: 'd_x', ownership: 'other' } }])
    expect(cacheOf()).toBe(before) // 规划只读：本机缓存、索引都不动
    expect(indexOwnerOf('d_x')).toBe(PJ)
    expect(backend.writes()).toEqual([])
  })

  it('本机记录 + /owner 问不到：扣下（owner_unavailable）；/owner 回 this / 没有归属记录（旧数据）：认，并绑定结论的 revision', async () => {
    const docs = await fresh()
    setIndex([{ id: 'd_x', name: 'x', savedAt: 1, objects: 1, projectId: PJ }])
    setCache({ id: 'd_x', name: 'X' })
    backend.state.fail.owner = 500
    let plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.preferred).toBeNull()
    expect(plan.withheld[0].reason).toBe('owner_unavailable')
    expect(cacheJson()?.id).toBe('d_x')
    backend.state.fail.owner = undefined
    // 旧数据：后端没有这份的归属记录（unknown），按迁移规则仍认——结论绑到当时的正文 revision
    const slot = backend.putSlot('d_x', docBody('X'))
    plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.preferred).toMatchObject({ id: 'd_x', ownership: 'this', expectedRevision: slot.revision })
    expect(plan.preferred?.expectedAbsent).toBeUndefined()
    // 当时槽位上没有正文：预期不存在，之后正文出现要走严格复核
    backend.state.slots.delete('d_x')
    plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.preferred).toMatchObject({ id: 'd_x', expectedAbsent: true })
    expect(plan.preferred?.expectedRevision).toBeUndefined()
    // owners=this：同样绑定
    backend.putSlot('d_x', docBody('X'), PJ)
    plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.preferred?.expectedRevision).toBe(backend.state.slots.get('d_x')!.revision)
    expect(backend.writes()).toEqual([])
  })

  it('会话端点不支持（旧后端）：本机记录照旧认，不问 /owner', async () => {
    const docs = await fresh()
    backend.state.fail.session = 404
    setIndex([{ id: 'd_x', name: 'x', savedAt: 1, objects: 1, projectId: PJ }])
    setCache({ id: 'd_x', name: 'X' })
    const plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.preferred).toMatchObject({ id: 'd_x', ownershipEvidence: 'local_index' })
    expect(backend.state.calls.filter((c) => c.startsWith('GET owner'))).toEqual([])
  })

  it('归属由 /owner 给出时，结论的 revision 带进 expectedRevision（global / 缓存 / C1），指针自己的 revision 优先（Codex P1）', async () => {
    const docs = await fresh()
    // global currentDoc：本机不知道归属，/owner 说 this（assets）
    const slot = backend.putSlot('d_g', docBody('G'))
    backend.state.ownerAnswers.set(`${PJ}|d_g`, { owner: 'this', evidence: 'assets', verified: false })
    let plan = await docs.planRestore(PJ, { globalCurrent: 'd_g' })
    expect(plan.preferred).toMatchObject({ id: 'd_g', ownershipEvidence: 'assets', expectedRevision: slot.revision })
    // 结论之后别的项目又存了同一个 doc id：下结论时的 revision 才是要对的那一版（不是现在槽位的）
    backend.state.ownerAnswers.set(`${PJ}|d_g`, { owner: 'this', evidence: 'assets', verified: false, revision: 'rev-at-verdict' })
    plan = await docs.planRestore(PJ, { globalCurrent: 'd_g' })
    expect(plan.preferred?.expectedRevision).toBe('rev-at-verdict')
    // C1 纠正路径同样
    setIndex([{ id: 'd_x', name: 'x', savedAt: 1, objects: 1, projectId: 'p_other' }])
    backend.setLast(PJ, 'd_x', 'X') // 指针不带 revision
    backend.state.ownerAnswers.set(`${PJ}|d_x`, { owner: 'this', evidence: 'owners', verified: true, revision: 'rev-x' })
    plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.preferred).toMatchObject({ id: 'd_x', correctsIndex: true, expectedRevision: 'rev-x' })
    // C1 纠正路径上 /owner 明说 revision:null（槽位此刻不在）：记成 expectedAbsent，之后冒出来的正文必须再核（Codex #936 r4241253012）
    backend.state.ownerAnswers.set(`${PJ}|d_x`, { owner: 'this', evidence: 'owners', verified: true, revision: null })
    plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.preferred).toMatchObject({ id: 'd_x', correctsIndex: true, expectedAbsent: true })
    expect(plan.preferred?.expectedRevision).toBeUndefined()
    // 指针自带 revision：以指针的为准，不被 /owner 的盖掉
    backend.setLast(PJ, 'd_x', 'X', 'rev-pointer')
    plan = await docs.planRestore(PJ, { globalCurrent: null })
    expect(plan.preferred?.expectedRevision).toBe('rev-pointer')
  })

  it('global currentDoc 在本机索引里确知属于别的项目且后端没有 owners 证据：不认领', async () => {
    const docs = await fresh()
    foreignIndex('d_f')
    backend.state.ownerAnswers.set(`${PJ}|d_f`, { owner: 'unknown', evidence: null, verified: false })
    const plan = await docs.planRestore(PJ, { globalCurrent: 'd_f' })
    expect(plan.preferred).toBeNull()
    expect(plan.withheld[0].reason).toBe('foreign')
  })

  it('切到空项目：仅全局 currentDoc 确知属于旧项目且 /owner 暂不可用，不阻塞新项目', async () => {
    const docs = await fresh()
    foreignIndex('d_a')
    backend.state.fail.owner = 500
    const plan = await docs.planRestore(PJ, { globalCurrent: 'd_a' })
    expect(plan.preferred).toBeNull()
    expect(plan.withheld).toMatchObject([{ reason: 'foreign', candidate: { sources: ['global_current'] } }])
    expect(backend.writes()).toEqual([])

    // 真正带后端 last 的同一 docId 仍需复核：短暂问不到不能当作空项目。
    backend.setLast(PJ, 'd_a', 'B 的上次排版')
    const withLast = await docs.planRestore(PJ, { globalCurrent: 'd_a' })
    expect(withLast.withheld).toMatchObject([{ reason: 'owner_unavailable' }])
  })

  it('没开项目：只认没记在任何项目名下的 global currentDoc', async () => {
    const docs = await fresh()
    setIndex([{ id: 'd_f', name: 'f', savedAt: 1, objects: 1, projectId: 'p_other' }])
    expect((await docs.planRestore(null, { globalCurrent: 'd_f' })).preferred).toBeNull()
    expect((await docs.planRestore(null, { globalCurrent: 'd_free' })).preferred?.id).toBe('d_free')
  })
})

describe('严格同源：本机槽位键', () => {
  it('docOwnership 的槽位前缀与 documentStore 的是同一对', async () => {
    const { readFileSync } = await import('node:fs')
    const { LOCAL_SLOT_PREFIX, LOCAL_RECOVERY_PREFIX } = await import('./docOwnership')
    const src = readFileSync(`${process.cwd()}/src/store/documentStore.ts`, 'utf8')
    expect(src).toContain(`const SLOT_PREFIX = '${LOCAL_SLOT_PREFIX}'`)
    expect(src).toContain(`const RECOVERY_PREFIX = '${LOCAL_RECOVERY_PREFIX}'`)
  })
})
