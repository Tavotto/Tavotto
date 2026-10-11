/**
 * 文档恢复协调（CONTRACT 2.4）：冷启动、Picker 打开项目、重试、交接前的等待，都走这一条
 * `resolveProjectDocument`。
 *
 * 为什么要有它：以前「恢复」散在三处——`restoreSession`（冷启动）、`adoptSteps`（Picker /
 * 切项目）、`openLastDocument`（重试）——各自挑一个 id、各自读盘、各自把读不到折成「空白」。
 * 于是同一份证据在冷启动与 Picker 上结局不同；恢复在路上时用户的第一笔编辑 / 交接会抢走
 * 初始空白，恢复让位，旧文档的入口跟着丢（R1）；Picker 里 last 暂态失败时 reset 把空白 id
 * 写进 currentDoc，恢复因 id 相等零请求退出（R2）；正文 GET 一次 500 就静默空白（R4）。
 *
 * 一次恢复的生命周期（门的状态机在 `documentStore`）：
 *
 *   captureClues ──► beginRestore（门关：restoring）──► [占位文档] ──► resolve ──┬─► ready(restored)
 *   （在任何 reset 之前）                                                       ├─► ready(empty / blank_requested)
 *                                                                              └─► needs_user（RestoreIssue）
 *   needs_user ─ 重试 ──► 新一轮 beginRestore（同一份线索）
 *              ─ 打开另一份 / 继续新建 ──► 显式意图，取代
 *
 * 每个 await 回来都先问令牌是否仍有提交权（`isRestoreTokenCurrent`），旧项目 / 旧操作的结果不能改
 * 新文档或新提示；被取代的恢复只结束自己、不碰新操作的状态。
 *
 * 判决规则（4.3）：首选可信且有效 → 恢复；暂态失败先有界重试原目标；首选明确缺失 / 损坏 / 不支持 →
 * 保留失败原因，列出**已只读验证过**的其他候选让用户选，**绝不悄悄换旧稿**；没有首选目标时，后端发现
 * 恰好 1 份属于本项目的有效候选 → 自动采用并说一句来源（产品决定，不适用于「最后稿件失败、仅剩旧稿」）。
 */
import { msg } from '@/i18n'
import { newId } from '@/lib/id'
import { currentProjectId } from '@/lib/session'
import { emptyProject } from '@/types/document'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import {
  acceptProbe,
  armRestoreToken,
  beginRestore,
  canCommitRestore,
  consumeSkipRestoreOnce,
  getRestoreGate,
  installRestoredDocument,
  isRestoreTokenCurrent,
  noteBlockedWrite,
  onBlockedWrite,
  probeDocument,
  readCurrentDocId,
  registerRestoreSession,
  refreshRecentDocsFromIndex,
  relabelReadyGate,
  resetRestoreGateForTests,
  settleRestore,
  supersedeRestore,
  useDocumentStore,
  whenDocumentReady,
  type ProbeResult,
  type RestoreGate,
  type RestoreToken,
} from '@/store/documentStore'
import { fetchRecoveryCandidates, type RecoveryCandidateItem, type SessionIndexStatus } from '@/lib/api'
import { isForeignDocument } from '@/lib/docOwnership'
import { confirmRestored, planRestore, type CandidatePlan, type RestoreCandidate } from '@/lib/projectDocs'

export { whenDocumentReady }
/** 显式意图取代未落定的恢复（普通 `switchDocument` 已经会做；不换文档的入口用这个） */
export const supersede = supersedeRestore

/* ------------------------------ 类型 ------------------------------ */

export type RestoreIssueKind =
  | 'unavailable'
  | 'missing'
  | 'invalid'
  | 'too_new'
  | 'session_unavailable'
  | 'index_degraded'
  | 'choose'
  | 'prepare_failed'

/**
 * 恢复没成、等用户处理的那件事。`ref` 是失败的首选（`session_unavailable` 时还不知道 docId，可为 null）；
 * `alternatives` 是**已只读验证过可打开**的其他候选。界面只露一句话 + 一个主按钮，其余折叠。
 */
export interface RestoreIssue {
  pj: string | null
  gen: number
  kind: RestoreIssueKind
  ref: { id: string; name: string } | null
  alternatives: RestoreCandidate[]
}

export type RestoreOutcome =
  | { kind: 'restored'; id: string }
  | { kind: 'prepared' }
  | { kind: 'kept_explicit' }
  | { kind: 'empty' }
  | { kind: 'needs_user'; issue: RestoreIssue }
  | { kind: 'superseded' }

/** 恢复开始前捕获的线索——**必须在任何 reset / currentDoc 写入之前**（重置会改写它们） */
export interface RestoreClues {
  pj: string | null
  /** 本机全局 `tavotto.currentDoc`（确知属于本项目才会被计划采用） */
  globalCurrent: string | null
  /** 捕获那一刻内存里的文档身份 */
  memDocId: string
  /** 本次启动的「开空白」决定（一次性标记，消费结果是共享决定） */
  skip: boolean
}

export function captureClues(pj: string | null): RestoreClues {
  ensureHooks()
  return {
    pj,
    globalCurrent: readCurrentDocId(),
    memDocId: useDocumentStore.getState().documentId,
    skip: consumeSkipRestoreOnce(),
  }
}

/* ------------------------------ 有界重试 ------------------------------ */

/** session / 正文 `unavailable` 各重试 2 次（250 ms、1000 ms），可被取代打断 */
let retryDelays = [250, 1000]
/** 仅给用例：把退避压到 0，时序由手动 gate 控制，不靠真睡 */
export function setRestoreRetryDelays(delays: number[]): void {
  retryDelays = delays
}

/** 睡 `ms`，或在令牌失去提交权时立刻醒（取代即打断退避） */
function pause(ms: number, token: RestoreToken): Promise<void> {
  return new Promise((resolve) => {
    let done = false
    let unsub: (() => void) | undefined
    const finish = () => {
      if (done) return
      done = true
      clearTimeout(timer)
      unsub?.()
      resolve()
    }
    const timer = setTimeout(finish, ms)
    unsub = useDocumentStore.subscribe(() => {
      if (!isRestoreTokenCurrent(token)) finish()
    })
  })
}

async function planWithRetry(token: RestoreToken, clues: RestoreClues): Promise<CandidatePlan | null> {
  for (let attempt = 0; ; attempt++) {
    const plan = await planRestore(token.pj, { globalCurrent: clues.globalCurrent })
    if (!isRestoreTokenCurrent(token)) return null
    if (plan.session.kind !== 'unavailable' || attempt >= retryDelays.length) return plan
    await pause(retryDelays[attempt], token)
    if (!isRestoreTokenCurrent(token)) return null
  }
}

async function probeWithRetry(token: RestoreToken, id: string): Promise<ProbeResult | null> {
  for (let attempt = 0; ; attempt++) {
    const probe = await probeDocument(id, token.pj)
    if (!isRestoreTokenCurrent(token)) return null
    if (probe.kind !== 'unavailable' || attempt >= retryDelays.length) return probe
    await pause(retryDelays[attempt], token)
    if (!isRestoreTokenCurrent(token)) return null
  }
}

/* ------------------------------ 落账与落定 ------------------------------ */

type Reason = 'restored' | 'explicit'

/**
 * 选中的那份：接受探测 → 安装 → 落定。三步在同一个同步段里（中间没有 await），所以提交权在头上判一次就够。
 * 令牌过期 → superseded，什么都不动；占位文档被绕过门改过（身份变了）→ 让位：保留内存里那份，门放成 ready。
 */
function installProbe(
  token: RestoreToken,
  probe: ProbeResult,
  plan: CandidatePlan | null,
  name: string,
  reason: Reason,
): RestoreOutcome {
  if (!isRestoreTokenCurrent(token)) return { kind: 'superseded' }
  if (!canCommitRestore(token) || !probe.doc) {
    settleRestore(token, { state: 'ready', reason: 'superseded' })
    return { kind: 'kept_explicit' }
  }
  const notice = acceptProbe(probe, token.pj, token)
  if (plan && token.pj) {
    // 迁移推送 / 缓存校正：接受之后才做（上面已经判过提交权，下面的安装不会再失败），失败不影响已经装好的文档
    // （下次读还会重试）。**排在安装之前**：它的同步前缀把项目缓存校正成这份——安装时 remember-last 订阅看到的
    // 就是「已经记着」，不会为一次恢复再往后端推一遍同样的 last
    void confirmRestored(token.pj, { id: probe.id, name: name || probe.doc.project.name }, plan).catch(() => {})
  }
  installRestoredDocument(token, probe.doc, probe.id, notice)
  settleRestore(token, { state: 'ready', reason })
  return { kind: 'restored', id: probe.id }
}

function needsUser(
  token: RestoreToken,
  kind: RestoreIssueKind,
  ref: RestoreIssue['ref'],
  alternatives: RestoreCandidate[] = [],
  tooNewNotice?: { docId: string; schema: number },
): RestoreOutcome {
  if (!isRestoreTokenCurrent(token)) return { kind: 'superseded' }
  const issue: RestoreIssue = { pj: token.pj, gen: token.gen, kind, ref, alternatives }
  // 版本过新：旧的「读不了」提示也挂上（docNotice 与保存进度正交，继续新建 / 换文档时随 switchDocument 清掉）
  if (tooNewNotice) {
    useDocumentStore.setState({
      docNotice: { kind: 'schema_too_new', docId: tooNewNotice.docId, schema: tooNewNotice.schema },
    })
  }
  settleRestore(token, { state: 'needs_user', issue })
  return { kind: 'needs_user', issue }
}

const issueKindOf = (probe: ProbeResult): RestoreIssueKind =>
  probe.kind === 'found' ? 'invalid' : probe.kind === 'too_new' ? 'too_new' : probe.kind

/** 已只读验证过可打开的候选（最多 5 份；探测零副作用） */
async function verifiedAlternatives(
  token: RestoreToken,
  candidates: RestoreCandidate[],
  excludeId: string | null,
): Promise<RestoreCandidate[] | null> {
  const out: RestoreCandidate[] = []
  for (const c of candidates.slice(0, 5)) {
    if (c.id === excludeId || out.some((o) => o.id === c.id)) continue
    const probe = await probeDocument(c.id, token.pj)
    if (!isRestoreTokenCurrent(token)) return null
    if (probe.kind === 'found') out.push(c)
  }
  return out
}

/* ------------------------------ 统一入口 ------------------------------ */

/**
 * 同一份状态与证据，冷启动、Picker、重试走的是这一个函数。必须已经 `beginRestore` + `armRestoreToken`。
 * 终态都经 `settleRestore`：不留「永远 restoring」的门。
 */
export async function resolveProjectDocument(token: RestoreToken, clues: RestoreClues): Promise<RestoreOutcome> {
  ensureHooks()
  try {
    return await resolve(token, clues)
  } catch {
    // 协调自身出了意外：门不能永远关着，也不能把它折成「没有文档」——说成暂时没打开，给重试
    return needsUser(token, 'session_unavailable', null)
  }
}

async function resolve(token: RestoreToken, clues: RestoreClues): Promise<RestoreOutcome> {
  const { pj } = token
  if (clues.skip) {
    // 本次启动开空白（崩溃逃生）：磁盘 / 本机上的文档一个都不动，仍可从「最近文档」取回
    return settleRestore(token, { state: 'ready', reason: 'blank_requested' })
      ? { kind: 'empty' }
      : { kind: 'superseded' }
  }
  const plan = await planWithRetry(token, clues)
  if (!plan) return { kind: 'superseded' }

  let failure: { kind: RestoreIssueKind; ref: { id: string; name: string }; probe: ProbeResult } | null = null
  // 后端 last 读不到（重试之后仍然）：计划不替它挑首选（不知道后端记着什么），本机线索都在 fallbacks。
  // 重试用尽之后按本机线索走——与旧行为一致（后端不可达时退回本机缓存），只是先有界重试、失败有出口
  const degraded = plan.session.kind === 'unavailable'
  const pref = plan.preferred ?? (degraded ? (plan.fallbacks[0] ?? null) : null)
  const rest = plan.preferred ? plan.fallbacks : degraded ? plan.fallbacks.slice(1) : plan.fallbacks
  if (pref) {
    const probe = await probeWithRetry(token, pref.id)
    if (!probe) return { kind: 'superseded' }
    if (probe.kind === 'found') return installProbe(token, probe, plan, pref.name, 'restored')
    if (pref.pendingIntent && probe.kind === 'missing') {
      // 打开意图还没有任何正文（新文档在首次保存前就退出了）：没有东西可恢复，退回已持久的那份。
      // 这不是「悄悄换旧稿」——最后一次**可恢复**的就是它（last 只指向可恢复正文）。
      // 带 backend_last / backend_prev 来源的不算意图（计划里 `pendingIntent` 只给纯意图）：那些缺了就是故障
      // 后备**只认第一个 durable**（有 last 用 last，没有 last 才用 prev）。last 存在却读不出来时不能越过它
      // 悄悄装 prev——那才是「换旧稿」；它失败 = 故障：needs_user，ref 指它，其余已验证的候选（含 prev）留作出口
      const durable =
        rest.find((c) => c.sources.includes('backend_last')) ?? rest.find((c) => c.sources.includes('backend_prev'))
      if (durable) {
        const p = await probeWithRetry(token, durable.id)
        if (!p) return { kind: 'superseded' }
        if (p.kind === 'found') return installProbe(token, p, plan, durable.name, 'restored')
        const alts = await verifiedAlternatives(token, rest, durable.id)
        if (!alts) return { kind: 'superseded' }
        return needsUser(
          token,
          issueKindOf(p),
          { id: durable.id, name: durable.name },
          alts,
          p.kind === 'too_new' ? { docId: durable.id, schema: p.schema ?? 0 } : undefined,
        )
      }
      // 没有 durable 记录（只有本机线索）：按线索依次试，第一个能读的装
      for (const fb of rest) {
        const p = await probeWithRetry(token, fb.id)
        if (!p) return { kind: 'superseded' }
        if (p.kind === 'found') return installProbe(token, p, plan, fb.name, 'restored')
        failure ??= { kind: issueKindOf(p), ref: { id: fb.id, name: fb.name }, probe: p }
        if (p.kind === 'unavailable') break
      }
    } else {
      const alts = await verifiedAlternatives(token, rest, pref.id)
      if (!alts) return { kind: 'superseded' }
      return needsUser(
        token,
        issueKindOf(probe),
        { id: pref.id, name: pref.name },
        alts,
        probe.kind === 'too_new' ? { docId: pref.id, schema: probe.schema ?? 0 } : undefined,
      )
    }
  }
  if (failure) {
    return needsUser(
      token,
      failure.kind,
      failure.ref,
      [],
      failure.kind === 'too_new' ? { docId: failure.ref.id, schema: failure.probe.schema ?? 0 } : undefined,
    )
  }

  // —— 没有可用的目标 ——
  // 后端 last 读不到（暂态）且没有任何本机线索：不知道有没有历史，不能当首次空状态
  if (plan.session.kind === 'unavailable') return needsUser(token, 'session_unavailable', null)
  // 有线索但没认领（归属问不到）：不自动认领，也不说成空项目——给重试出口；确知别人的 / 归属不明的不阻塞
  const unreachable = plan.withheld.find((w) => w.reason === 'owner_unavailable')
  if (unreachable) {
    return needsUser(token, 'unavailable', { id: unreachable.candidate.id, name: unreachable.candidate.name })
  }
  // 没有这组端点（playground / 嵌入画布 / 旧后端）：没有可发现的东西
  if (plan.session.kind === 'unsupported') return finishEmpty(token)

  // 发现（只读）：后端按真实槽位枚举本项目明确归属的有效候选。**翻页在归属过滤之前**，所以空的一页不等于没有、
  // 一页里看到一份也不等于只有一份：一直翻到头（有界）再判数量
  const scan = await discoverAll(token)
  if (!scan) return { kind: 'superseded' }
  // 只有「没有这个端点」才是空；暂态不可用（网络 / 5xx）重试用尽后给出口，不当没有历史
  if (scan === 'unsupported') return finishEmpty(token)
  if (scan === 'unavailable') return needsUser(token, 'session_unavailable', null)
  // 发现不能翻案：计划已经因归属拒绝 / 问不到的线索（withheld）不会从发现里再被捡回来；本机索引确知属于别的项目的，
  // 只有后端 `owners` 证据才算数（仅 assets / versions 的同名证据不是归属转移，A17）
  const withheldIds = new Set(plan.withheld.map((w) => w.candidate.id))
  const items = scan.items.filter(
    (i) =>
      i.status === 'ok' &&
      i.owner === 'this' &&
      !withheldIds.has(i.doc_id) &&
      (!isForeignDocument(i.doc_id, pj) || i.evidence === 'owners'),
  )
  const asCandidates = items.slice(0, 5).map<RestoreCandidate>((i) => ({
    id: i.doc_id,
    name: i.name,
    sources: ['discovered'],
    ownership: 'this',
    ...(i.evidence ? { ownershipEvidence: i.evidence } : {}),
  }))
  if (!scan.complete) {
    // 数不清（翻到上限游标还在，或中途读不到）：不自动采用、也不说没有。已经看到的给用户选；一份都没有就给重试
    //（最接近的现有类别 `index_degraded`：「恢复记录读不全」，出口 = 重试 / 继续新建）
    return items.length > 0
      ? needsUser(token, 'choose', null, asCandidates)
      : needsUser(token, 'index_degraded', null)
  }
  if (items.length === 1) {
    const item = items[0]
    const probe = await probeWithRetry(token, item.doc_id)
    if (!probe) return { kind: 'superseded' }
    if (probe.kind === 'found') {
      const out = installProbe(token, probe, null, item.name, 'restored')
      // 产品决定（4.3）：没有任何首选目标时，唯一一份明确属于本项目的有效候选自动采用，并说清来源
      if (out.kind === 'restored') {
        useUiStore
          .getState()
          .setStatus(msg('restore.autoAdopted', { name: item.name || probe.doc?.project.name || '' }, 'workspace'), 'info')
      }
      return out
    }
    if (probe.kind === 'unavailable') {
      return needsUser(token, 'unavailable', { id: item.doc_id, name: item.name })
    }
    return finishEmpty(token)
  }
  if (items.length >= 2) return needsUser(token, 'choose', null, asCandidates)
  // 0 份：新项目 / 没有历史。恢复记录读不了（索引损坏）时说一句，但不阻塞——旧排版还在，打开排版对话框里找
  if (plan.session.index === 'degraded' || scan.index === 'degraded') {
    useUiStore.getState().setStatus(msg('restore.indexDegraded', undefined, 'workspace'), 'info')
  }
  return finishEmpty(token)
}

/** 发现翻页的上界：最多 10 页、扫 500 行 */
const DISCOVER_MAX_PAGES = 10
const DISCOVER_MAX_ROWS = 500

/**
 * 翻完本项目的找回候选（只读）。首页 `unavailable` 按有界重试（同 session 读取），仍失败 → `'unavailable'`；
 * `unsupported` → `'unsupported'`。`complete=false`：到上界游标还在，或中途某页读不到。令牌过期回 null。
 */
async function discoverAll(
  token: RestoreToken,
): Promise<
  | { items: RecoveryCandidateItem[]; complete: boolean; index: SessionIndexStatus }
  | 'unsupported'
  | 'unavailable'
  | null
> {
  const items: RecoveryCandidateItem[] = []
  const seen = new Set<string>()
  let cursor: string | undefined
  let index: SessionIndexStatus = 'ok'
  let scanned = 0
  for (let page = 0; page < DISCOVER_MAX_PAGES && scanned < DISCOVER_MAX_ROWS; page++) {
    let res = await fetchRecoveryCandidates('project', token.pj, cursor)
    for (let attempt = 0; res === 'unavailable' && attempt < retryDelays.length; attempt++) {
      if (!isRestoreTokenCurrent(token)) return null
      await pause(retryDelays[attempt], token)
      if (!isRestoreTokenCurrent(token)) return null
      res = await fetchRecoveryCandidates('project', token.pj, cursor)
    }
    if (!isRestoreTokenCurrent(token)) return null
    if (res === 'unsupported') return page === 0 ? 'unsupported' : { items, complete: false, index }
    if (res === 'unavailable') return page === 0 ? 'unavailable' : { items, complete: false, index }
    if (res.index === 'degraded') index = 'degraded'
    scanned += res.items.length
    for (const it of res.items) {
      if (seen.has(it.doc_id)) continue
      seen.add(it.doc_id)
      items.push(it)
    }
    if (res.next_cursor === null) return { items, complete: true, index }
    cursor = res.next_cursor
  }
  return { items, complete: false, index }
}

function finishEmpty(token: RestoreToken): RestoreOutcome {
  return settleRestore(token, { state: 'ready', reason: 'empty' }) ? { kind: 'empty' } : { kind: 'superseded' }
}

/* ------------------------------ 教程 prepare ------------------------------ */

/**
 * 带 `prepareDocument` 的入口（教程）：准备成功与否由**返回值 + 门**双重确认，不用 Promise resolve、
 * 也不用「id 相等」冒充：
 *  - 返回 true 且门已被它装文档时的普通 `switchDocument` 取代（代次前进、ready）→ `prepared`；
 *  - 返回 false，或返回 true 却没有任何文档装进来 → `needs_user{prepare_failed}`（出口：继续新建），
 *    初始空白**不**标成 ready。
 */
export async function settlePrepared(
  token: RestoreToken,
  prepare: () => Promise<boolean>,
): Promise<RestoreOutcome> {
  let ok = false
  try {
    ok = await prepare()
  } catch {
    ok = false
  }
  const g = getRestoreGate()
  if (ok && g.state === 'ready' && g.gen > token.gen && g.pj === token.pj) {
    relabelReadyGate(g.gen, 'prepared')
    return { kind: 'prepared' }
  }
  if (isRestoreTokenCurrent(token)) return needsUser(token, 'prepare_failed', null)
  return ok ? { kind: 'kept_explicit' } : { kind: 'superseded' }
}

/* ------------------------------ 冷启动 ------------------------------ */

/** 本次页面生命周期里记住的线索（重试复用同一份：捕获发生在任何 reset 之前） */
let lifecycleClues: RestoreClues | null = null
/** 同一项目上在路上的冷启动恢复（StrictMode 第二轮 / 重复挂载加入它，不开第二轮） */
const inflight = new Map<string | null, Promise<RestoreOutcome>>()

async function runLifecycle(pj: string | null, clues: RestoreClues): Promise<RestoreOutcome> {
  lifecycleClues = clues
  const token = beginRestore(pj)
  armRestoreToken(token)
  return resolveProjectDocument(token, clues)
}

const settledFor = (g: RestoreGate, pj: string | null) =>
  g.state !== 'restoring' && g.reason !== 'initial' && g.pj === pj

/**
 * 启动恢复（工作台挂载时调）。返回：这一次**恢复出了一份文档**。
 *
 * 不再按 `currentDoc === documentId` 判「已经装好了」：门已经对当前项目落定（ready 或 needs_user）就直接回落定结果
 * ——Picker 打开项目 / 教程 prepare 已经走过同一条协调；同一项目上还在路上就加入它（StrictMode 第二轮）。
 * 没有跨项目永久的 hasRestored：A→B→A 每次都是新的生命周期（B 的认领开一轮，再回 A 又开一轮）。
 */
export async function restoreSession(): Promise<boolean> {
  refreshRecentDocsFromIndex()
  const pj = currentProjectId()
  const g = getRestoreGate()
  if (settledFor(g, pj)) return false
  const running = inflight.get(pj)
  if (running) return (await running).kind === 'restored'
  // 别处（Picker 的认领）正在为这个项目恢复：等它落定，不开第二轮
  if (g.state === 'restoring' && g.pj === pj) {
    await whenDocumentReady(pj)
    return false
  }
  const p = runLifecycle(pj, captureClues(pj)).finally(() => {
    if (inflight.get(pj) === p) inflight.delete(pj)
  })
  inflight.set(pj, p)
  return (await p).kind === 'restored'
}

/* ------------------------------ needs_user 的出口 ------------------------------ */

/** 重试：同一份线索，新一轮恢复（占位文档还在，门重新关上） */
export async function retryRestore(): Promise<RestoreOutcome> {
  const g = getRestoreGate()
  const issue = useDocumentStore.getState().restoreIssue
  if (g.state !== 'needs_user' || !issue || issue.pj !== currentProjectId()) return { kind: 'superseded' }
  const clues = { ...(lifecycleClues ?? captureClues(issue.pj)), pj: issue.pj, skip: false }
  return fitIfRestored(await runLifecycle(issue.pj, clues))
}

/**
 * 用户在恢复问题上走出口之后才装进来的文档（重试 / 打开另一份）：工作台当初的取景是按占位空白量的，
 * 这里按装进来的页面重新适配（冷启动 / 认领那条路各自在恢复后适配）。
 */
function fitIfRestored(out: RestoreOutcome): RestoreOutcome {
  if (out.kind === 'restored') {
    const page = useDocumentStore.getState().doc.page
    useViewportStore.getState().fit(page.w, page.h)
  }
  return out
}

/** 打开另一份已验证的候选（显式意图：取代未决的恢复） */
export async function openAlternative(id: string): Promise<RestoreOutcome> {
  const g = getRestoreGate()
  const issue = useDocumentStore.getState().restoreIssue
  if (g.state !== 'needs_user' || !issue || issue.pj !== currentProjectId()) return { kind: 'superseded' }
  const picked = issue.alternatives.find((a) => a.id === id)
  if (!picked) return { kind: 'superseded' }
  const token = beginRestore(issue.pj)
  armRestoreToken(token)
  const probe = await probeWithRetry(token, id)
  if (!probe) return { kind: 'superseded' }
  if (probe.kind === 'found') return fitIfRestored(installProbe(token, probe, null, picked.name, 'explicit'))
  return needsUser(
    token,
    issueKindOf(probe),
    { id, name: picked.name },
    issue.alternatives.filter((a) => a.id !== id),
    probe.kind === 'too_new' ? { docId: id, schema: probe.schema ?? 0 } : undefined,
  )
}

/**
 * 继续新建：建立正式的新文档（占位转正）。旧候选与旧正文一个字节不动，
 * 旧恢复的晚到响应从此没有提交权（门已经被显式意图取代）。
 */
export async function continueNew(): Promise<boolean> {
  if (getRestoreGate().state === 'ready') return false
  const ok = await useDocumentStore
    .getState()
    .switchDocument(emptyProject(), newId('d'), undefined, { reason: 'continued_new' })
  if (ok) {
    const page = useDocumentStore.getState().doc.page
    useViewportStore.getState().fit(page.w, page.h)
  }
  return ok
}

/** 仅给用例：又一次全新启动（门回初值、在路上的恢复与线索忘掉）。 */
export function resetRestoreStateForTests(): void {
  inflight.clear()
  lifecycleClues = null
  lastBlockedNote = 0
  resetRestoreGateForTests()
}

/* ------------------------------ 用户级入口：恢复中排队 ------------------------------ */

export type EntryGate = 'run' | 'deferred' | 'blocked'

/**
 * 用户级「往文档里写」的入口（素材卡双击 / 加入排版 / 拖放 / 粘贴 / 选图对话框……）刚挂载就可能触发，
 * 那时恢复还在路上：门会拒掉底层的 `commit`，动作就悄悄丢了（e2e 抓到过：双击素材卡，状态条说"已加入"，画布 0 个对象）。
 *
 * 在**入口**（不是 `commit`——不做通用的 recipe 队列）这样处理：
 *  - 门开着 → `'run'`，调用方照常执行；
 *  - `restoring` → `'deferred'`：登记一次，等**排队时那一代**恢复自己落定再执行**一次**；
 *    被显式意图取代 / 切项目 / 超时 → 丢掉并说一句；
 *  - `needs_user` → `'blocked'`：用户得先选出口，说一句「上次的排版还没打开」，不排队。
 * `run` 必须是重新调用入口本身（门开了它会走 `'run'` 分支），这样「这个意图此刻还成立吗」由入口自己判。
 */
export function deferUserEntry(run: () => void): EntryGate {
  ensureHooks()
  const g = getRestoreGate()
  if (g.state === 'ready') return 'run'
  if (g.state === 'needs_user') {
    noteBlockedWrite()
    return 'blocked'
  }
  // 排队那一刻的恢复代次：只有**这一代**自己落定（恢复出来 / 确认没有历史 / 开空白启动）才执行。
  // 被显式意图取代（新建 / 打开排版 / 打开最近 / 教程 / 继续新建 / 打开另一份 / 切项目）的，
  // 较旧的操作不许写进较新意图建出来的文档——丢掉并说一句
  const queuedGen = g.gen
  void whenDocumentReady(g.pj).then((verdict) => {
    const now = getRestoreGate()
    const sameLifecycle =
      verdict === 'ready' &&
      now.state === 'ready' &&
      now.gen === queuedGen &&
      (now.reason === 'restored' || now.reason === 'empty' || now.reason === 'blank_requested')
    if (sameLifecycle) run()
    else {
      useUiStore
        .getState()
        .setStatus(msg(verdict === 'timeout' ? 'restore.entryTimeout' : 'restore.entrySuperseded', undefined, 'workspace'), 'error')
    }
  })
  return 'deferred'
}

/* ------------------------------ 被拒写入的提示 ------------------------------ */

let hooked = false
let lastBlockedNote = 0
const BLOCKED_NOTE_GAP_MS = 4000

/** 登记一次（模块实例内）：门关着时用户动作被拒，说一句「上次的排版还没打开」，同一句节流 */
function ensureHooks(): void {
  if (hooked) return
  hooked = true
  onBlockedWrite(() => {
    const now = Date.now()
    if (now - lastBlockedNote < BLOCKED_NOTE_GAP_MS) return
    lastBlockedNote = now
    useUiStore.getState().setStatus(msg('restore.blockedWrite', undefined, 'workspace'), 'info')
  })
}

// 登记成 `documentStore.restoreSession` 的实现（见那里的说明：不让 documentStore import 本模块）
registerRestoreSession(restoreSession)
