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
 *
 * 判决表（探测结局 → 结果）：
 *   正文 found                          → 恢复（指针修订号对不上先复核归属，只有 this+verified 才装）
 *   正文 missing（404，确认过）+ 本机副本 → 副本就是文档本身，恢复并推回磁盘
 *   正文 unavailable / invalid          → 有界重试；仍失败 → needs_user，本机副本只作为「打开本机副本」备选
 *                                         （用户的动作才打开——它可能比磁盘旧），绝不自动当成恢复
 *   正文 missing / too_new（无副本）      → needs_user{missing / too_new}，其余已验证候选作备选
 *   纯打开意图 missing                   → 只退到第一个 durable（last，没有 last 才 prev）；last 失败 / 被扣下 → needs_user
 *   会话 last 读不到（重试用尽）          → needs_user{session_unavailable}，本机线索作已验证备选，**不**自动兜底；
 *                                         只有会话端点 404（unsupported）才按本机线索自动恢复
 *   发现（无首选）                       → 翻完页再数：1 份且 owners 证据且索引正常 → 自动采用；其余有候选 → choose；
 *                                         数不清 → choose / index_degraded；发现端点不可用 → session_unavailable；404 → 空
 */
import { msg } from '@/i18n'
import { newId } from '@/lib/id'
import { currentProjectId } from '@/lib/session'
import { emptyProject } from '@/types/document'
import { useUiStore } from '@/store/uiStore'
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
import { fetchLayoutOwnerEvidence, fetchRecoveryCandidates, type RecoveryCandidateItem, type SessionIndexStatus } from '@/lib/api'
import { correctRecordedProject, isForeignDocument } from '@/lib/docOwnership'
import { confirmRestored, planRestore, type CandidatePlan, type RestoreCandidate } from '@/lib/projectDocs'
import { fitStageNow } from '@/store/zoomToSelection'

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
export type AltCandidate = RestoreCandidate & { localCopy?: true }

export interface RestoreIssue {
  pj: string | null
  gen: number
  kind: RestoreIssueKind
  ref: { id: string; name: string } | null
  alternatives: AltCandidate[]
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
  cand?: RestoreCandidate,
): RestoreOutcome {
  if (!isRestoreTokenCurrent(token)) return { kind: 'superseded' }
  if (!canCommitRestore(token) || !probe.doc) {
    settleRestore(token, { state: 'ready', reason: 'superseded' })
    return { kind: 'kept_explicit' }
  }
  if (plan && token.pj) {
    // 同步前缀先纠正本机归属：本机副本在 acceptProbe 中安排首次写回时就会读取它。
    // 迁移推送异步完成；此时令牌与提交权已经检查，后续安装仍在同一个同步段。
    void confirmRestored(token.pj, { id: probe.id, name: name || probe.doc.project.name }, plan).catch(() => {})
  } else if (cand?.correctsIndex && token.pj) {
    // 备选选择没有保留原计划；已验证的 C1 结论仍须在安装 / 首次保存之前落到本机索引。
    correctRecordedProject(probe.id, token.pj)
  }
  const notice = acceptProbe(probe, token.pj, token)
  installRestoredDocument(token, probe.doc, probe.id, notice)
  settleRestore(token, { state: 'ready', reason })
  return { kind: 'restored', id: probe.id }
}

/**
 * 指针带着的正文修订号（`expectedRevision`，来自后端 last / prev）与探测读到的磁盘修订号对不上：
 * 读指针和读正文之间，另一个共用同一 docId 的项目可能存过——这份正文不一定还是**我们的**。
 * 不盲装：重新问一次归属，只有 `{owner:'this', verified:true}` 且复核针对的修订号就是要装的这份才装；`other` → 找不到（打不开别人的）；
 * 问不到 / 不明 → 暂时没打开（可重试）。没有期望修订号（旧后端 / 本机线索）或读到的修订号为空 → 照旧。
 */
async function guardRevision(
  token: RestoreToken,
  cand: RestoreCandidate,
  probe: ProbeResult,
): Promise<RestoreOutcome | null> {
  // `expectedAbsent`：指针（backend_pending）是在槽位**还没有正文**时验过的。我们自己的首次保存落地会让后端把
  // pending 晋升成 durable；指针还是 pending 而磁盘上冒出了正文 = 别的项目存的，同样要证明归属，按「对不上」处理
  const expectedAbsent = (cand as RestoreCandidate & { expectedAbsent?: boolean }).expectedAbsent === true
  const expected = cand.expectedRevision
  if ((!expected && !expectedAbsent) || probe.from !== 'disk' || probe.disk.kind !== 'found') return null
  const actual = probe.disk.revision
  if (expected && actual === expected && !expectedAbsent) return null
  // 读到的修订号是空的（代理吃了 X-Tavotto-Revision）：绑定不上，无从证明这份还是指针说的那份——不装，给重试
  if (!actual) return needsUser(token, 'unavailable', { id: cand.id, name: cand.name })
  const ev = await fetchLayoutOwnerEvidence(cand.id, token.pj)
  if (!isRestoreTokenCurrent(token)) return { kind: 'superseded' }
  if (ev !== 'unsupported' && ev !== 'unavailable') {
    // 复核的结论是针对**它算的那个修订号**的：必须正好是我们马上要装的这份（读到的磁盘修订号），
    // 否则复核之后正文又被换过——不装，给重试（再探测一次）。后端没回 revision（旧）同样不装
    const verdictRev = (ev as { revision?: string | null }).revision
    if (ev.owner === 'this' && ev.verified && verdictRev === actual) return null
    if (ev.owner === 'other') return needsUser(token, 'missing', { id: cand.id, name: cand.name })
  }
  return needsUser(token, 'unavailable', { id: cand.id, name: cand.name })
}

/** 读到了就装——除非指针的修订号对不上（`guardRevision`） */
async function installChecked(
  token: RestoreToken,
  probe: ProbeResult,
  plan: CandidatePlan | null,
  cand: RestoreCandidate,
  reason: Reason,
): Promise<RestoreOutcome> {
  const bad = await guardRevision(token, cand, probe)
  if (bad) return bad
  return installProbe(token, probe, plan, cand.name, reason, cand)
}

/** 磁盘读失败（不可用 / 坏）但手边有本机副本：把它作为**用户明确选择**才打开的备选（它可能比磁盘旧） */
function localCopyAlt(c: RestoreCandidate, probe: ProbeResult): AltCandidate[] {
  if ((probe.kind !== 'unavailable' && probe.kind !== 'invalid') || !probe.local) return []
  return [{ ...c, localCopy: true }]
}

function needsUser(
  token: RestoreToken,
  kind: RestoreIssueKind,
  ref: RestoreIssue['ref'],
  alternatives: AltCandidate[] = [],
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
    if (probe.kind !== 'found') continue
    // 提供给用户之前读到的正文修订号记在候选上（候选自己没有时）：用户坐在面板上的这段时间里同一个 docId 可能被别的项目
    // 存过，点选时 `installChecked` 据此发现对不上并复核归属，而不是装「此刻磁盘上的任何东西」
    const seen = probe.from === 'disk' && probe.disk.kind === 'found' ? probe.disk.revision : null
    out.push(!c.expectedRevision && !c.expectedAbsent && seen ? { ...c, expectedRevision: seen } : c)
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
  // 后端 last 读不到（重试之后仍然）：不知道后端记着什么——它可能有比本机线索更新的 last。本机线索不自动当成恢复，
  // 只作为**已验证的备选**交给用户（`session_unavailable`，出口：重试 / 打开某份 / 继续新建）。
  // 本机线索自动兜底只留给后端根本没有这组端点（`unsupported`，计划里 preferred 就是它）。
  if (plan.session.kind === 'unavailable') {
    const alts = await verifiedAlternatives(token, plan.fallbacks, null)
    if (!alts) return { kind: 'superseded' }
    return needsUser(token, 'session_unavailable', null, alts)
  }
  // 未来版本 / 读不全的会话记录即使带着可解析的旧字段，也不能据此自动安装正文。
  if (plan.session.kind === 'ok' && plan.session.index === 'degraded') {
    return needsUser(token, 'index_degraded', null)
  }
  const pref = plan.preferred
  const rest = plan.fallbacks
  // 后端的 durable last 在计划里被**扣下**了（归属问不到 / 本机索引说是别人的）：它仍是「上次开着的」，
  // 不能越过它去装更旧的（prev / 本机线索）——那就是悄悄换旧稿。只有首选本身是比它更新的纯打开意图且读得到时才装首选
  const withheldLast = plan.withheld.find((w) => w.candidate.sources.includes('backend_last')) ?? null
  const blockedByLast = (): Promise<RestoreOutcome> => withheldLastIssue(token, withheldLast!, [...(pref ? [pref] : []), ...rest])
  if (pref && withheldLast && !pref.pendingIntent && !pref.sources.includes('backend_last')) return blockedByLast()
  if (pref) {
    const probe = await probeWithRetry(token, pref.id)
    if (!probe) return { kind: 'superseded' }
    if (probe.kind === 'found') return installChecked(token, probe, plan, pref, 'restored')
    if (pref.pendingIntent && probe.kind === 'missing') {
      // 打开意图还没有任何正文（新文档在首次保存前就退出了）：没有东西可恢复，退回已持久的那份。
      // 这不是「悄悄换旧稿」——最后一次**可恢复**的就是它（last 只指向可恢复正文）。
      // 带 backend_last / backend_prev 来源的不算意图（计划里 `pendingIntent` 只给纯意图）：那些缺了就是故障
      // 后备**只认第一个 durable**（有 last 用 last，没有 last 才用 prev）。last 存在却读不出来时不能越过它
      // 悄悄装 prev——那才是「换旧稿」；它失败 = 故障：needs_user，ref 指它，其余已验证的候选（含 prev）留作出口
      if (withheldLast && !rest.some((c) => c.sources.includes('backend_last'))) return blockedByLast()
      const durable =
        rest.find((c) => c.sources.includes('backend_last')) ?? rest.find((c) => c.sources.includes('backend_prev'))
      if (durable) {
        const p = await probeWithRetry(token, durable.id)
        if (!p) return { kind: 'superseded' }
        if (p.kind === 'found') return installChecked(token, p, plan, durable, 'restored')
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
        if (p.kind === 'found') return installChecked(token, p, plan, fb, 'restored')
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
        [...localCopyAlt(pref, probe), ...alts],
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
  // 发现的行带着扫描那一刻的正文修订号：之后读正文时对不上（共用 docId 的别的项目在两次读之间存过）就要复核归属
  const asCandidate = (i: RecoveryCandidateItem): RestoreCandidate => ({
    id: i.doc_id,
    name: i.name,
    sources: ['discovered'],
    ownership: 'this',
    ...(i.evidence ? { ownershipEvidence: i.evidence } : {}),
    ...(i.revision ? { expectedRevision: i.revision } : {}),
    ...(isForeignDocument(i.doc_id, pj) && i.evidence === 'owners' ? { correctsIndex: true } : {}),
  })
  const asCandidates = items.slice(0, 5).map(asCandidate)
  if (!scan.complete) {
    // 数不清（翻到上限游标还在，或中途读不到）：不自动采用、也不说没有。已经看到的给用户选；一份都没有就给重试
    //（最接近的现有类别 `index_degraded`：「恢复记录读不全」，出口 = 重试 / 继续新建）
    return items.length > 0
      ? needsUser(token, 'choose', null, asCandidates)
      : needsUser(token, 'index_degraded', null)
  }
  // 索引读不全 / 坏过（degraded）时归属表是空的，发现的行只能按较弱的 versions / assets 证据归类；
  // 单独一行只有这种弱证据也一样——自动采用只给最强的 `owners` 证据。这两种情况都交给用户选，不替他认领
  const indexDegraded = plan.session.index === 'degraded' || scan.index === 'degraded'
  if (items.length >= 1 && (indexDegraded || (items.length === 1 && items[0].evidence !== 'owners'))) {
    return needsUser(token, 'choose', null, asCandidates)
  }
  if (items.length === 1) {
    const item = items[0]
    const probe = await probeWithRetry(token, item.doc_id)
    if (!probe) return { kind: 'superseded' }
    if (probe.kind === 'found') {
      const out = await installChecked(token, probe, null, asCandidate(item), 'restored')
      // 产品决定（4.3）：没有任何首选目标时，唯一一份明确属于本项目的有效候选自动采用，并说清来源
      if (out.kind === 'restored') {
        useUiStore
          .getState()
          .setStatus(msg('restore.autoAdopted', { name: item.name || probe.doc?.project.name || '' }, 'workspace'), 'info')
      }
      return out
    }
    // 唯一的候选读不出来也不能说成「没有历史」：带着原因和这一行给出口（版本过新同时挂旧的提示）
    return needsUser(
      token,
      issueKindOf(probe),
      { id: item.doc_id, name: item.name },
      [],
      probe.kind === 'too_new' ? { docId: item.doc_id, schema: probe.schema ?? 0 } : undefined,
    )
  }
  if (items.length >= 2) return needsUser(token, 'choose', null, asCandidates)
  // 索引退化时项目归属可能读不全，零行并不证明没有历史。
  if (indexDegraded) return needsUser(token, 'index_degraded', null)
  // 0 份且索引完整：新项目 / 没有历史。
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
  // 任何一页回包说「被截断」（后端自己的扫描上限到了）→ 数不清，粘性：之后的页再干净也不翻案
  let truncated = false
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
    if (res.truncated) truncated = true
    scanned += res.items.length
    for (const it of res.items) {
      if (seen.has(it.doc_id)) continue
      seen.add(it.doc_id)
      items.push(it)
    }
    if (res.next_cursor === null) return { items, complete: !truncated, index }
    cursor = res.next_cursor
  }
  return { items, complete: false, index }
}

/** 被扣下的 durable last 挡住了回退：给出口，不替用户越过它。其余已验证的候选（含 prev）只作为可选项 */
async function withheldLastIssue(
  token: RestoreToken,
  w: { candidate: RestoreCandidate; reason: 'foreign' | 'owner_unknown' | 'owner_unavailable' },
  others: RestoreCandidate[],
): Promise<RestoreOutcome> {
  const alts = await verifiedAlternatives(token, others, w.candidate.id)
  if (!alts) return { kind: 'superseded' }
  // 问不到归属 = 暂时的，可重试；确知别人的 / 归属不明 = 这个项目打不开它（按「找不到」说，出口是别的或继续新建）
  const kind: RestoreIssueKind = w.reason === 'owner_unavailable' ? 'unavailable' : 'missing'
  return needsUser(token, kind, { id: w.candidate.id, name: w.candidate.name }, alts)
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

/**
 * 启动时由 `projectStore.init()` 在**发布 phase:'open' 之前**先关上的那一轮恢复。
 *
 * 项目 ready 与工作台挂载之间有一段空档：`phase:'open'` 一发布，桌面的 `tavotto:open` 事件、URL 交接就可能先于
 * `Workspace` 的 `restoreSession()` 到达——那时门还是初值 `ready`，`whenDocumentReady` 立刻放行，图落进马上要被恢复
 * 换掉的空白文档（交接丢了）。所以 init 先 `beginStartupRestore`（门 restoring），`restoreSession` 接过这一轮
 * （用它的令牌，不开第二代）。只有门还是「从没发生过任何恢复」的初值时才关；playground / 嵌入 / 不经 init 的用例保持 ready。
 */
let pendingStartup: RestoreToken | null = null
/** 同项目连续点击「重试」沿用原恢复的用户动作；显式打开、切项目都不会进入这条代次链。 */
let retryLineage: { generations: Set<number>; latest: number; pj: string | null } | null = null

export function beginStartupRestore(pj: string | null): void {
  const g = getRestoreGate()
  if (g.state !== 'ready' || g.reason !== 'initial') return
  const token = beginRestore(pj)
  armRestoreToken(token)
  pendingStartup = token
}

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
  // init 已经替这个项目关上了门：接过那一轮（同一个令牌）
  const begun = pendingStartup
  pendingStartup = null
  if (begun && begun.pj === pj && isRestoreTokenCurrent(begun)) {
    lifecycleClues = captureClues(pj)
    const clues = lifecycleClues
    const p = resolveProjectDocument(begun, clues).finally(() => {
      if (inflight.get(pj) === p) inflight.delete(pj)
    })
    inflight.set(pj, p)
    return (await p).kind === 'restored'
  }
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
  const generations = retryLineage?.latest === g.gen && retryLineage.pj === issue.pj
    ? retryLineage.generations
    : new Set<number>()
  generations.add(g.gen)
  const running = runLifecycle(issue.pj, clues) // 同步关门、推进代次；结果稍后才落定
  retryLineage = { generations, latest: getRestoreGate().gen, pj: issue.pj }
  return fitIfRestored(await running)
}

/**
 * 用户在恢复问题上走出口之后才装进来的文档（重试 / 打开另一份）：工作台当初的取景是按占位空白量的，
 * 这里按装进来的页面重新适配（冷启动 / 认领那条路各自在恢复后适配）。
 */
function fitIfRestored(out: RestoreOutcome): RestoreOutcome {
  if (out.kind === 'restored') {
    fitStageNow()
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
  if (probe.kind === 'found') return fitIfRestored(await installChecked(token, probe, null, picked, 'explicit'))
  // 用户明确选了「打开本机副本」：磁盘此刻仍读不了，就用副本（它可能比磁盘旧——所以只在这里、由用户的动作触发）
  if (picked.localCopy && probe.local) {
    const asLocal: ProbeResult = { ...probe, kind: 'found', doc: probe.local, from: 'local' }
    return fitIfRestored(installProbe(token, asLocal, null, picked.name, 'explicit', picked))
  }
  return needsUser(
    token,
    issueKindOf(probe),
    { id, name: picked.name },
    [...localCopyAlt(picked, probe), ...issue.alternatives.filter((a) => a.id !== id)],
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
    fitStageNow()
  }
  return ok
}

/** 仅给用例：又一次全新启动（门回初值、在路上的恢复与线索忘掉）。 */
export function resetRestoreStateForTests(): void {
  inflight.clear()
  lifecycleClues = null
  lastBlockedNote = 0
  pendingStartup = null
  retryLineage = null
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
 *  - `restoring` → `'deferred'`：登记一次，等这轮恢复（含同项目的重试）落定再执行**一次**；
 *    被显式意图取代 / 切项目 / 超时 → 丢掉并说一句；
 *  - `needs_user` → `'blocked'`：用户得先选出口，说一句「上次的排版还没打开」，不排队。
 * `run` 必须是重新调用入口本身（门开了它会走 `'run'` 分支），这样「这个意图此刻还成立吗」由入口自己判。
 */
/** 最近一次排队的入口自己的结局：`ran` = 真的执行了；`dropped` = 被取代 / 超时 / 换了项目而丢掉 */
export type DeferredEntryOutcome = 'ran' | 'dropped'
let lastDeferred: Promise<DeferredEntryOutcome> | null = null

/**
 * 取走**刚刚**那次 `deferUserEntry` 返回 `'deferred'` 的入口的结局（取走即清，必须在入口返回后同步调——
 * 中间不能有 await，否则拿到的可能是别的入口的）。调用方要在「入口真的跑了」之后才做收尾（收卡片、记编辑），
 * 不能只看「文档里恰好有这张图」：恢复被显式打开别的排版取代时，那份排版里也可能有同一张图。
 */
export function takeDeferredEntryOutcome(): Promise<DeferredEntryOutcome> | null {
  const p = lastDeferred
  lastDeferred = null
  return p
}

export function deferUserEntry(run: () => void): EntryGate {
  ensureHooks()
  const g = getRestoreGate()
  if (g.state === 'ready') return 'run'
  if (g.state === 'needs_user') {
    noteBlockedWrite()
    return 'blocked'
  }
  // 排队那一刻的恢复代次，或它的同项目重试链落定，才执行。
  // 被显式意图取代（新建 / 打开排版 / 打开最近 / 教程 / 继续新建 / 打开另一份 / 切项目）的，
  // 较旧的操作不许写进较新意图建出来的文档——丢掉并说一句
  const queuedGen = g.gen
  let settle!: (o: DeferredEntryOutcome) => void
  lastDeferred = new Promise<DeferredEntryOutcome>((r) => (settle = r))
  void whenDocumentReady(g.pj).then((verdict) => {
    const now = getRestoreGate()
    const sameLifecycle =
      verdict === 'ready' &&
      now.state === 'ready' &&
      (now.gen === queuedGen || (retryLineage?.generations.has(queuedGen) && retryLineage.latest === now.gen && retryLineage.pj === g.pj)) &&
      (now.reason === 'restored' || now.reason === 'empty' || now.reason === 'blank_requested')
    if (sameLifecycle) {
      run()
      settle('ran')
    } else {
      settle('dropped')
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
