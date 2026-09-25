/**
 * 画布跟随样式（ADR 0081）。**「应用样式」只有这一条路：应用 = 绑定。**
 *
 * 当前画布绑一套样式（`FigureDocument.style = {id, snapshot}`），这张画布上的图就
 * 长成那套样式的样子；那套样式改了，画布跟着改。规范（Spec）仍然只检查、从不改图
 * （ADR 0029 那条不动）——这里只管 Style。
 *
 * ### 什么时候写文档（每一次都是一条**用户编辑**档的 commit：进历史、置 dirty）
 *
 * | 时刻 | 写什么 | 历史标签 |
 * | --- | --- | --- |
 * | 用户选一套样式 | 绑定 + 整份样式对齐当前画布 | 按样式「X」排版当前画布 |
 * | 用户在样式面板里改一个值（已绑定） | 先存进样式库那一条，再改快照 + 只对齐改了的那一项 | 修改样式「X」 |
 * | 样式库那一条被别处改过（设置里保存、另一张画布上改过） | 快照 + 只对齐变了的那几项 | 按样式「X」更新 |
 * | 绑定画布上的一张图**第一次**拿到 manifest | 只对齐这一张 | 按样式对齐新图 |
 * | 不跟随样式 / 恢复原样 | 解绑（恢复原样还清掉样式管得到的 override） | 各自一条 |
 *
 * **撤销只退画布，不推回样式库**（§十二，用户 2026-09-25 拍板）：撤销一次「修改样式 / 按样式
 * 更新」，这张画布回到旧值并标成「已脱离样式」（`style.detached`，由那一条历史的 `undoAlso`
 * 落下）；库保留新值，别的画布照样跟上。脱离的画布不跟随、不对齐新图，再选一次绑定才恢复；
 * 重做把标记撤掉，回到绑定状态，库此刻若又变过就照常跟上。
 *
 * 这些都不是「外部派生同步」那一档（`applyDerivedUpdate`，唯一调用方是 `panelSourceSync`，
 * **不进历史**）：它们要能单独撤销，写下去的是用户看得见的样式。
 *
 * ### 什么时候**不**写
 *
 * 写之前一律过 `effectiveChanges`：打开文档、切画布、重渲染、manifest 回来这些时刻，
 * 已经合样式的图算出来是空计划，**一个 commit 都不产生**。「新图」的判据是**这张图上一个
 * 样式管得到的 override 都没有**：对齐过的图带着那些 override；用户在属性页里刻意改过的图
 * 也带着——两种都不去碰。同一会话里处理过的图记在 `seen` 里。
 *
 * ### 结构：对样式库的写入只有一条队列（ADR 0081 §十一）
 *
 * 写样式库是异步的；异步窗口里用户可以切画布、重载文档、撤销 / 重做、再改一个值。此前靠一个
 * `busy` 计数 + 几处各自的守卫，每一轮评审都能在它们的交错处挑出新问题。现在：
 *
 * 1. **所有库写入串成一条队列**（`enqueue`）：改值的存库、复制内置、排在它们后面的绑定，一次只跑一个，
 *    按发起顺序。每个任务**在轮到自己时**才读现场（绑的是哪一条、库里现在是什么），而不是在
 *    发起那一刻读——排在后面的改动天然基于前面那一笔的结果。
 * 2. **写文档之前统一比代次**（`generation`）：项目 · 文档 · 载入代次（`loadSeq`）· 画布。任务
 *    跑完回来代次变了（切了画布、同一份文档被重载、换了项目），就**不往此刻的文档上写**；库已经
 *    存了，原来那张画布回到前台时由 `followLibrary` 按内容不等跟上。
 * 3. **队列非空时同步器不动手**；队列排空的那一刻统一补跑一次 `followLibrary` + `alignNewFigures`
 *    ——期间被挡下的触发不会丢。
 */
import { msg, t, type UiMessage } from '@/i18n'
import type { Manifest, ProfileRecord } from '@/lib/api'
import { profileName } from '@/lib/profileText'
import { currentProjectId } from '@/lib/session'
import { sameRules } from '@/lib/specBinding'
import {
  effectiveChanges,
  isEmptyPlan,
  isLegacyBasis,
  planStyle,
  presetDelta,
  profileToDraft,
  styleOverrideTargets,
  withPageBasis,
  type StyleProfileData,
  type StylePlan,
  type StylePreset,
} from '@/lib/stylePresets'
import type { DocumentStyle, FigureDocument, PanelObject } from '@/types/document'
import { renderStylePlan, writeStylePlan } from './actions'
import { isCopiedBakedBaseline } from '@/lib/bakedBaseline'
import { useAssetStore } from './assetStore'
import { useDocumentStore } from './documentStore'
import { finishActiveGesture } from './gestureCoordinator'
import { useInteractionStore } from './interactionStore'
import { useProfileStore } from './profileStore'
import { exactPanelManifest, renderKeyOf, useRenderStore } from './renderStore'
import { requestRender } from './renderScheduler'
import { useUiStore } from './uiStore'

const hist = (key: string, values?: Record<string, unknown>): UiMessage =>
  msg(`history.${key}`, values, 'workspace')

/** 样式面板里一格对应的样式条目：图内角色 × 属性，或画布标注的一个字段 */
export type StyleEdit =
  | { kind: 'element'; role: string; prop: string; value: unknown }
  | { kind: 'annotation'; prop: 'sizePt' | 'fontFamily'; value: unknown }

/* ------------------------------- 读 ---------------------------------------- */

const docNow = () => useDocumentStore.getState().doc

/**
 * **只认这张面板此刻这一版的 manifest**（`exactPanelManifest`：同一组 override 渲染出来、没过期）。
 * `panelRender` 会退回旧渲染 / 同文件别的变体的 latest——拿那份去映射 gid，脚本改过之后会把
 * override 写到已经不存在的 gid 上、还把这张图记成「看过了」（Codex #547 P1）。拿不到就当
 * 「还没有 manifest」，走欠账那条路。
 */
const manifestOf = (p: PanelObject): Manifest | null => exactPanelManifest(useRenderStore.getState(), p)

const recordOf = (id: string): ProfileRecord | undefined =>
  useProfileStore.getState().styles.find((r) => r.id === id)

/** 这张画布此刻**跟随**的那一条（没绑 / 已脱离 = null） */
export const canvasStyle = (doc: FigureDocument = docNow()): DocumentStyle | null =>
  doc.style && !doc.style.detached ? doc.style : null

/**
 * 绑定此刻**应该**是什么内容：样式库里有这一条（且清单已经拉回来）就是库里的现值；
 * 找不到（换了台电脑 / 被删了 / 还没拉回来）就是快照——快照照样说了算。
 */
export function resolvedStyle(binding: DocumentStyle): StyleProfileData {
  const rec = useProfileStore.getState().loaded ? recordOf(binding.id) : undefined
  return (rec?.data ?? binding.snapshot) as unknown as StyleProfileData
}

/** 绑定在界面与历史标签上叫什么：库里的名字；库里没有了就说「样式库里找不到」，不露 id */
export function bindingName(binding: DocumentStyle): string {
  const rec = recordOf(binding.id)
  return rec ? profileName(rec) : t('stylePanel.missingStyle', { ns: 'workspace' })
}

const panelsOf = (doc: FigureDocument): PanelObject[] =>
  doc.objects.filter((o): o is PanelObject => o.type === 'panel' && !!o.script)

/**
 * 画布从快照 `prev` 走到新内容 `next` 要落下去的变化量。
 *
 * - 起点是**画布的快照**（它此刻真正长成的样子），不是库里的现值：库那边可能有一笔被挡下还没
 *   落到画布上的更新（停在历史上 / 等 manifest），拿库当起点会把它算成「已同步」而从没写过
 *   （Codex #547 P1）。
 * - 口径从旧（脚本值）变成页面 pt 时是**整份**：数字没变，意思变了——缩放过的图上每一个 pt 值
 *   都要重算。旧样式在设置 / 样式对话框里被存成 page 口径时，也走这里（Codex #547 P1）。
 */
function deltaFrom(prev: StyleProfileData | null, next: StyleProfileData): StylePreset {
  const reinterpreted = !!prev && isLegacyBasis(prev) && !isLegacyBasis(next)
  return presetDelta(reinterpreted ? null : prev, next)
}

/** 一份（变化量）预设落到这些面板 / 这张画布上，只留真会变的部分 */
function changesFor(preset: StylePreset, panels: PanelObject[], doc: FigureDocument, canvas: boolean): StylePlan {
  const raw = planStyle(preset, panels, manifestOf, doc, canvas)
  const plan = canvas ? raw : { ...raw, page: undefined, background: undefined }
  return effectiveChanges(plan, doc, manifestOf, preset)
}

/* ------------------------------- 代次 -------------------------------------- */

/**
 * 「此刻是哪一份文档的哪一张画布」：项目 · 文档 · 载入代次 · 画布。`loadSeq` 在每一次整份替换
 * 文档时都会前进，**即使 id 全都一样**（版面恢复、崩溃恢复重载同一份文档）——只比 id 的话，
 * 旧编辑会写进新载入的那一份（Codex #547 P1）。
 */
function generation(): string {
  const s = useDocumentStore.getState()
  return JSON.stringify([currentProjectId(), s.documentId, s.loadSeq, s.activeCanvasId])
}
/** 会话记账的键：代次 + 绑的是哪一条（换绑定 = 换一本账） */
const ledgerKey = (doc: FigureDocument = docNow()) => `${generation()}|${doc.style?.id ?? ''}`

/* --------------------------- 会话记账：看过的图 / 欠账 ----------------------- */

/**
 * 会话记账里一张图的身份：**面板 id + 素材**。替换素材（`replacePanelAsset`）保留面板 id、换掉文件与
 * override——只按 id 记的话，换进来的新图被当成「看过了」，永远不按绑定的样式对齐（Codex #547 P1）；
 * 旧素材欠着的那一笔也不该落到新素材上（新图按整份样式对齐）。
 */
const figKey = (p: PanelObject): string => `${p.id}@${p.fileId}`

/** 「这一会话里已经按当前绑定看过的图」（图按 `figKey`，文字按 id） */
const seen = new Map<string, Set<string>>()
function markSeen(doc: FigureDocument, panelIds: Iterable<string>) {
  const key = ledgerKey(doc)
  const set = seen.get(key) ?? new Set<string>()
  for (const id of panelIds) set.add(id)
  seen.set(key, set)
}

/**
 * 「这张图还欠着这些样式变化」：改样式 / 绑定的那一刻它还没有 manifest，那一次 commit 对不上它。
 * 欠账按 **代次 · 绑定 · 图** 记在会话里，它第一次拿到 manifest 时补上（`alignNewFigures`）。
 * 键里带绑定：A 的欠账不会在画布跟随 B 之后写下去；明确绑上 A 时才清 A 的（`forgetDebts`）。
 */
const pending = new Map<string, Map<string, StyleProfileData>>()

/** 两笔变化量合成一笔（后来的覆盖先来的；图内角色按 role × prop 合） */
function mergeDelta(a: StyleProfileData | undefined, b: StyleProfileData): StyleProfileData {
  if (!a) return structuredClone(b)
  const out = structuredClone(a)
  for (const [role, props] of Object.entries(b.element ?? {})) {
    out.element = out.element ?? {}
    out.element[role] = { ...(out.element[role] ?? {}), ...props }
  }
  if (b.palette?.length) out.palette = b.palette
  if (b.pt_basis) out.pt_basis = b.pt_basis
  return out
}

/**
 * 欠账对着**此刻**的样式核一遍再补：欠账里记的某一项后来从样式里删掉了（`presetDelta` 对删除
 * 不发东西），就不该再写下去；还在的项取样式此刻的值（中间又改过的话以最新为准）（Codex #547 P1）。
 */
function pruneToStyle(debt: StyleProfileData, style: StyleProfileData): StyleProfileData {
  const element: StyleProfileData['element'] = {}
  for (const [role, props] of Object.entries(debt.element ?? {})) {
    for (const prop of Object.keys(props)) {
      const now = style.element?.[role]?.[prop]
      if (now === undefined) continue
      ;(element[role] ??= {})[prop] = now
    }
  }
  return {
    element,
    ...(debt.palette?.length && style.palette?.length ? { palette: style.palette } : {}),
    ...(style.pt_basis ? { pt_basis: style.pt_basis } : {}),
  }
}

/**
 * 这次 commit 对不上的图记下欠账。**「对不上」必须在 commit 之前量**（`missingNow`）：commit 改了
 * override，每一张被写过的图的渲染变体键都变了、此刻都拿不到精确 manifest——commit 之后再量的话，
 * 已经写好的图也会被记一笔欠账，用户在它重画回来之前手改的值之后会被这笔假账冲掉（Codex #547 P1）
 */
const missingNow = (doc: FigureDocument): string[] => panelsOf(doc).filter((p) => !manifestOf(p)).map(figKey)

/**
 * `full`：此刻整份样式（`presetDelta(null, 样式)`）。**还没对齐过的新图**欠的是整份，不是这一笔变化量——
 * 存库在飞时加进来的图（自动对齐被队列挡着）只记变化量的话，渲染回来按欠账补了这一项、记成看过了，
 * 样式的其余部分永远落不上去（Codex #547 P1）。「新图」按没 manifest 时认得出的判据：一个 override 都没有 /
 * 只有抄来的烘焙基线；带着别的 override 的（对齐过的、手改过的）仍只欠变化量，不冲掉手改。
 */
function owe(doc: FigureDocument, delta: StyleProfileData, missingIds: string[], full: StyleProfileData) {
  const ids = new Set(missingIds)
  const missing = panelsOf(doc).filter((p) => ids.has(figKey(p)))
  if (!missing.length) return
  const key = ledgerKey(doc)
  const assets = useAssetStore.getState().byId
  const map = pending.get(key) ?? new Map<string, StyleProfileData>()
  for (const p of missing) {
    // 对齐过的图带着样式写的 override，合样式的图欠整份等于只欠这一笔：只看 override 就够
    const fresh = !p.overrides.length || isCopiedBakedBaseline(p.overrides, assets[p.fileId])
    const owed = fresh ? full : delta
    if (!Object.keys(owed.element ?? {}).length && !owed.palette?.length) continue
    map.set(figKey(p), mergeDelta(map.get(figKey(p)), owed))
  }
  if (map.size) pending.set(key, map)
}

/**
 * 明确地绑上 `styleId`：这张画布（此刻这个代次）上**这一条**的欠账作废——绑定会整份重新对齐。
 * 别的绑定（解绑 / 改绑之前那一条）的欠账留着：撤销只还原文档，把旧绑定带回来时，它那几张
 * 还没对齐的图仍然要补（Codex #547 P1）。欠账按绑定记（`ledgerKey`），不绑着它时不会被结算
 */
function forgetDebts(styleId: string) {
  pending.delete(`${generation()}|${styleId}`)
}

/* --------------------------- 样式库写入队列 -------------------------------- */

/** 队列尾巴：下一个任务接在它后面跑 */
let tail: Promise<unknown> = Promise.resolve()
/** 排着 / 跑着的库写入个数。非零时同步器不动手；归零的那一刻补跑一次 */
let inflight = 0

function enqueue<T>(job: () => Promise<T>): Promise<T> {
  inflight += 1
  const run = tail.then(job, job)
  tail = run.then(
    () => undefined,
    () => undefined,
  )
  return run.finally(() => {
    inflight -= 1
    // 排空了：期间被「队列非空」挡下的跟随 / 对齐补跑一次（Codex #547 P2）
    if (inflight === 0) {
      // 改绑的「转发」只对还排着的任务有意义：排空就作废（用户之后自己绑回 A，不该被转到 B）
      redirects.clear()
      followLibrary()
      alignNewFigures()
    }
  })
}

/** 测试用：等队列排空 */
export const whenLibraryIdle = (): Promise<unknown> => tail

/**
 * 我们自己的任务把绑定从 A 换到了 B（改内置 = 复制一份并改绑）：排在后面、发起时绑的是 A 的
 * 改动顺着它落到 B 上。只记我们自己做的改绑；用户改绑不进这里，那种排队中的改动作废。
 */
const redirects = new Map<string, string>() // 键：`${generation()}|${来源样式 id}`
/**
 * 顺着**同一个代次**里我们自己做的改绑走。键带代次、且只在改绑真的 commit 进那张画布之后才记：
 * A 画布上的复制任务回来时代次已经变了（用户切到了也绑着内置样式的 B），它没有改绑 A，也就不该
 * 让 B 上排着的改动以为「内置已经被换成副本」而把自己作废（Codex #547 P1）。
 */
function followRedirects(gen: string, id: string | null): string | null {
  let cur = id
  for (let i = 0; cur && redirects.has(`${gen}|${cur}`) && i < 8; i++) cur = redirects.get(`${gen}|${cur}`)!
  return cur
}

/** 测试用：清掉会话记账与队列状态 */
export function resetStyleBindingSession(): void {
  seen.clear()
  pending.clear()
  redirects.clear()
  queuedBinds.clear()
  inflight = 0
  tail = Promise.resolve()
}

/* ------------------------------- 写文档 ------------------------------------ */

/** 我们自己正在 commit：文档订阅方据此不把我们刚写的对象变化当成「加了新东西」 */
let writing = false

/**
 * 撤销这一条时画布**脱离**样式（§十二）：改样式 / 按样式更新这两类改了快照的写入带上它。
 * 撤销只退画布、不推回库；不脱离的话，下一次编辑清掉 future 后跟随会把库里的新值再套回来
 */
const detachOnUndo = (d: FigureDocument) => {
  if (d.style) d.style.detached = true
}

function commitWith(
  label: UiMessage,
  recipe: (d: FigureDocument) => void,
  plan: StylePlan | null,
  undoAlso?: (d: FigureDocument) => void,
) {
  finishActiveGesture()
  writing = true
  try {
    useDocumentStore.getState().commit(label, recipe, undoAlso ? { undoAlso } : undefined)
  } finally {
    writing = false
  }
  if (plan) renderStylePlan(plan)
}

/**
 * 当前画布绑定一套样式，并**立刻**把整份样式对齐到这张画布上（一次 commit）。
 * `null` = 不跟随样式：只解绑，图保持此刻的样子。
 */
export function bindCanvasStyle(recordId: string | null): void {
  // **明确的绑定也进库写队列**（排在已经在飞的写入后面）：绑定的意思是「以库里此刻那一份为准」，
  // 而一次还在飞的存库会在稍后改写库——先绑、后被改写的话，画布与库就分叉了。串进队列后，
  // 绑定读到的永远是前面所有写入落定之后的库，从结构上没有「在飞时改绑」这回事。
  // 队列空着时当场绑（界面上立刻看到结果）
  if (inflight === 0) {
    bindNow(recordId)
    return
  }
  const origin = generation()
  if (recordId) queuedBinds.add(recordId)
  void enqueue(async () => {
    if (recordId) queuedBinds.delete(recordId)
    if (generation() === origin) {
      bindNow(recordId)
      return
    }
    // 排队期间换了画布 / 文档 / 项目：发起的那张画布已经不在前台，绑不上它（文档写入只有激活画布
    // 这一条路）。**说出来**，不静默丢掉一次用户明确的操作（Codex #547 P1）
    useUiStore.getState().setStatus(msg('stylePanel.bindCancelled', undefined, 'workspace'), 'error')
  })
}

/** 排在队列里、还没执行的绑定要绑的样式 id：前面的任务别把它们当成「没人要」删掉 */
const queuedBinds = new Set<string>()

function bindNow(recordId: string | null): void {
  const doc = docNow()
  // 先认出要绑的那一条：排队期间它可能被删了。绑不上就**什么都不动**，并说出来（Codex #547 P1）
  if (recordId !== null && !recordOf(recordId)) {
    useUiStore.getState().setStatus(msg('stylePanel.bindMissing', undefined, 'workspace'), 'error')
    return
  }
  if (recordId === null) {
    if (!doc.style) return
    commitWith(hist('unbindStyle'), (d) => {
      delete d.style
    }, null)
    return
  }
  const rec = recordOf(recordId)
  if (!rec) return
  const data = rec.data as unknown as StyleProfileData
  const preset: StylePreset = { ...profileToDraft(rec), name: profileName(rec) }
  const delta = presetDelta(null, data)
  const plan = changesFor(delta, panelsOf(doc), doc, true)
  // 已经跟随着这一条、快照与库一样、图也都已经合样式（再点一次「用于当前画布」）：什么都不写——
  // 否则一次内容相同的快照赋值也会产生一条历史、把文档标脏（Codex #547 P2）。已脱离的要写：
  // 重新选一次就是恢复跟随
  const cur = canvasStyle(doc)
  if (cur?.id === rec.id && sameRules(cur.snapshot, rec.data) && isEmptyPlan(plan)) return
  forgetDebts(rec.id)
  const missing = missingNow(doc)
  commitWith(
    hist('bindStyle', { name: profileName(rec) }),
    (d) => {
      d.style = { id: rec.id, snapshot: structuredClone(rec.data) }
      writeStylePlan(d, plan, { ...delta, name: preset.name })
    },
    plan,
  )
  owe(docNow(), delta, missing, delta)
  markSeen(docNow(), panelsOf(docNow()).map(figKey).filter((k) => !missing.includes(k)))
  markTextsSeen()
}

/**
 * 把一条改动写进一份样式内容（返回新的一份，不改原来的）。旧样式（没有 `pt_basis`）第一次
 * 被编辑时升级成按页面 pt 记（`withPageBasis`，2026-09-24 拍板）：面板交出的是页面值。
 */
function withEdit(data: StyleProfileData, edit: StyleEdit): StyleProfileData {
  const next = withPageBasis(structuredClone(data))
  if (edit.kind === 'element') {
    next.element = next.element ?? {}
    next.element[edit.role] = { ...(next.element[edit.role] ?? {}), [edit.prop]: edit.value }
  } else {
    next.annotation = { ...(next.annotation ?? {}), [edit.prop]: edit.value } as StyleProfileData['annotation']
  }
  return next
}

/**
 * 已绑定时，样式面板里改一个值 = **改这套样式本身**，然后当前画布上的图按新值对齐
 * （一次 commit）。进库写队列：轮到它时才读「绑的是哪一条、库里现在是什么」，存成功、
 * 且代次与绑定都没变，才动文档；存不进去就一个字都不改，状态栏说原因。
 *
 * 内置样式只读：按 ADR 0029「改内置 = 复制一份」先复制出一份、改在副本上，画布改绑到
 * 副本，状态栏说一句；副本建了但改动没存进去，删掉那份副本。
 */
export function editBoundStyle(edit: StyleEdit): Promise<boolean> {
  const origin = generation()
  // 发起时绑的是哪一条：排队期间用户改绑了别的，这一笔不能落到那一条上（Codex #547 P1）。
  // 排在前面的、我们自己的任务把内置样式复制成副本并改绑时记了 `redirects`，顺着它走
  const originBinding = canvasStyle()?.id ?? null
  return enqueue(async () => {
    // 样式库清单还没拉回来时「库里找不到这一条」不等于「这一条不存在」：先等它回来，否则会按快照
    // 新建一条再改绑、而不是改原来那一条（Codex #547 P2）。load 失败也会落定 `loaded`
    if (!useProfileStore.getState().loaded) await useProfileStore.getState().load()
    const binding = canvasStyle()
    // 排队期间换了画布 / 重载了文档 / 解绑了 / 改绑了：这一笔不属于此刻的画布。**说出来**，不静默
    // 丢掉用户刚敲下的值（Codex #547 P1：改绑排在队列里时紧接着改字号，改绑一落地这一笔就对不上了）
    if (!binding || generation() !== origin || binding.id !== followRedirects(origin, originBinding)) {
      useUiStore.getState().setStatus(msg('stylePanel.editCancelled', undefined, 'workspace'), 'error')
      return false
    }
    const before = resolvedStyle(binding)
    const next = withEdit(before, edit)
    const api = useProfileStore.getState()
    const rec = recordOf(binding.id)
    let stored: ProfileRecord | null
    let copied = false
    if (sameRules(next, before)) {
      // 库里已经是这个值（多半是一笔被挡下、还没落到画布的更新）：不必存库，但**画布**可能还停在
      // 旧值上——照样往下走，把快照到库之间的变化量落下去（Codex #547 P1）；两边都一样才是真的无事可做
      if (!rec || sameRules(binding.snapshot, rec.data)) return true
      stored = rec
    } else if (rec && !rec.read_only) {
      stored = await api.save('style', rec.id, next as unknown as Record<string, unknown>)
    } else if (rec) {
      const copy = await api.duplicate('style', rec.id)
      stored = copy ? await useProfileStore.getState().save('style', copy.id, next as unknown as Record<string, unknown>) : null
      // 副本建了、改动没存进去：删掉——除非等存库的这段时间里已经有画布绑上 / 排队要绑它（Codex #547 P1）
      if (copy && !stored && !adoptedAnywhere(copy.id)) await useProfileStore.getState().remove('style', copy.id)
      copied = !!stored
    } else {
      // 库里已经没有这一条（换了台电脑 / 删了）：按快照新建一条再改
      stored = await api.create('style', t('stylePanel.copyName', { ns: 'workspace' }), next as unknown as Record<string, unknown>)
      copied = !!stored
    }
    if (!stored) {
      const err = useProfileStore.getState().error
      useUiStore
        .getState()
        .setStatus(
          err
            ? msg('stylePanel.saveFailed', { reason: err.message }, 'workspace')
            : msg('stylePanel.saveFailedPlain', undefined, 'workspace'),
          'error',
        )
      return false
    }
    // 回来时代次或绑定变了（Codex #547 P1）：库已经存了，原来那张画布回到前台时跟上。
    // 这一笔复制出来的副本没有画布会绑它：删掉，不在库里留一条没人要的东西
    if (generation() !== origin || canvasStyle()?.id !== binding.id) {
      // 副本在 `duplicate()` 返回那一刻就进了共享清单：等存库的这段时间里，别的画布可能已经绑了它
      // ——那就留着（Codex #547 P1）。没有任何画布绑它才删
      if (copied && !adoptedAnywhere(stored.id)) {
        // 这一笔只活在这份副本里（原样式没被改）：删之前说出来，不让一次改动悄悄消失（Codex #547 P2）
        useUiStore.getState().setStatus(msg('stylePanel.editCancelled', undefined, 'workspace'), 'error')
        await useProfileStore.getState().remove('style', stored.id)
      }
      return false
    }
    const doc = docNow()
    const name = profileName(stored)
    const data = stored.data as unknown as StyleProfileData
    // 升级了口径（旧样式第一次被编辑）：状态栏说一句；变化量的起点是画布快照（`deltaFrom`）
    const upgraded = isLegacyBasis(before)
    const current = canvasStyle()!
    const delta = deltaFrom(current.snapshot as unknown as StyleProfileData, data)
    const plan = changesFor(delta, panelsOf(doc), doc, true)
    const missing = missingNow(doc)
    commitWith(
      hist('editStyle', { name }),
      (d) => {
        d.style = { id: stored.id, snapshot: structuredClone(stored.data) }
        // 写的是**变化量**：标注只改了字号时，不把颜色 / 字体一起重新套一遍（Codex #547 P1）
        writeStylePlan(d, plan, { ...delta, name })
      },
      plan,
      detachOnUndo,
    )
    owe(docNow(), delta, missing, presetDelta(null, data))
    // 改绑真的落进了发起的那张画布：记下转发，让同一个代次里排在后面的改动顺着落到新的那一条上。
    // 复制内置与「库里没有这一条、按快照新建」两条路都算（Codex #547 P1）
    if (copied) redirects.set(`${origin}|${binding.id}`, stored.id)
    if (copied) useUiStore.getState().setStatus(msg('stylePanel.copiedBuiltin', { name }, 'workspace'))
    else if (upgraded) useUiStore.getState().setStatus(msg('stylePanel.upgradedLegacy', { name }, 'workspace'))
    return true
  })
}

/**
 * 恢复原样：清掉当前画布上**所有图**里样式管得到的 override，**并解除绑定**——一次 commit。
 *
 * 为什么同时解绑：绑定说的是「这张画布长成样式 X 的样子」，恢复原样说的是「回到脚本的
 * 样子」，两句话不能同时成立。撤销时绑定与 override 一起回来。
 */
export function restoreCanvasStyle(): boolean {
  const doc = docNow()
  // 带着 override、却还没有 manifest 的图：认不出哪些 override 是样式写的——等它们渲染出来
  if (!restoreReady(doc)) return false
  const targets = panelsOf(doc).flatMap((p) => {
    const m = manifestOf(p)
    return m ? styleOverrideTargets(p, m).map((x) => ({ panelId: p.id, ...x })) : []
  })
  if (!targets.length && !doc.style) return false
  const touched = new Set(targets.map((x) => x.panelId))
  // 欠账不清：撤销恢复原样会把绑定带回来，它那几张还没对齐的图仍然要补（同解绑，Codex #547 P1）
  commitWith(hist('restoreStyle'), (d) => {
    delete d.style
    for (const o of d.objects) {
      if (o.type !== 'panel' || !touched.has(o.id)) continue
      o.overrides = o.overrides.filter(
        (ov) => !targets.some((x) => x.panelId === o.id && x.gid === ov.gid && x.prop === ov.prop),
      )
    }
  }, null)
  for (const o of docNow().objects) {
    if (o.type === 'panel' && touched.has(o.id)) requestRender(o, true)
  }
  return true
}

/** 此刻这份项目里有没有哪张画布绑着这一条样式（激活画布看 doc，其余看画布快照；已脱离的也算：选择器里还指着它） */
function adoptedAnywhere(styleId: string): boolean {
  const st = useDocumentStore.getState()
  if (st.doc.style?.id === styleId || queuedBinds.has(styleId)) return true
  return st.canvases.some((c) => c.id !== st.activeCanvasId && c.style?.id === styleId)
}

/** 恢复原样此刻做得了吗：每一张带 override 的图都有 manifest（界面按它置灰那颗钮并说原因） */
export const restoreReady = (doc: FigureDocument = docNow()): boolean =>
  panelsOf(doc).every((p) => !p.overrides.length || !!manifestOf(p))

/* ------------------------------- 跟随 -------------------------------------- */

/** 这张图的 manifest 还在路上（在渲染 / 排着队），而不是永远不会来 */
function awaitingManifest(p: PanelObject): boolean {
  if (manifestOf(p) || p.fileKind === 'runtime') return false
  const r = useRenderStore.getState().byKey[renderKeyOf(p)]
  return r?.status !== 'error'
}

/**
 * 此刻不能**自动**写（用户自己点的绑定 / 改值不受它管）：
 *
 * - 库写入在排队 / 事务开着（拖到一半）/ 正在交互——排空 / 收尾时会补跑；
 * - **停在历史上**（撤销之后还有可重做的 future）：这里的自动写入是一条新的 commit，会把
 *   future 清掉，用户按 ⌘⇧Z 就再也回不去了。与 #543 的「撤销 / 重做后派生同步不清 future」
 *   同一条纪律；用户下一次编辑清掉 future 的那一刻补跑。
 */
const blocked = () => {
  const d = useDocumentStore.getState()
  return (
    inflight > 0 || d.txn != null || d.future.length > 0 || useInteractionStore.getState().kind !== 'none'
  )
}

/**
 * 样式库里那一条与这张画布的快照内容不等 → 按库里的现值更新（只对齐变了的那几项）。
 * 清单还没拉回来、库里没有这一条、内容相等、已脱离——都不写。
 */
export function followLibrary(): boolean {
  const doc = docNow()
  const binding = canvasStyle(doc)
  if (!binding || blocked() || !useProfileStore.getState().loaded) return false
  const rec = recordOf(binding.id)
  if (!rec || sameRules(rec.data, binding.snapshot)) return false
  // 还有图没渲染出来：先不前进快照，等它们到齐再一起跟上
  if (panelsOf(doc).some(awaitingManifest)) return false
  const before = binding.snapshot as unknown as StyleProfileData
  const data = rec.data as unknown as StyleProfileData
  const name = profileName(rec)
  const delta = deltaFrom(before, data)
  const plan = changesFor(delta, panelsOf(doc), doc, true)
  const missing = missingNow(doc)
  commitWith(
    hist('syncStyle', { name }),
    (d) => {
      d.style = { id: rec.id, snapshot: structuredClone(rec.data) }
      writeStylePlan(d, plan, { ...delta, name })
    },
    plan,
    detachOnUndo,
  )
  // 渲染不了的图（渲染出错 / runtime 拿不到精确 manifest）不挡快照前进——挡的话一张坏图就让
  // 整张画布永远跟不上库——但它们欠着这一笔：记账，等它们哪天渲染成功时补上（Codex #547 P1）。
  // 不记的话它身上旧样式的 override 会让「新图」判据跳过它，它就永远停在旧样式上
  owe(docNow(), delta, missing, presetDelta(null, data))
  return true
}

/**
 * 绑定画布上还没看过的图拿到了 manifest：欠着一笔变化的补上这一笔；否则这张图上一个样式管得到的
 * override 都没有（= 从没对齐过，用户也没在属性页里刻意改过）时，按整份样式对齐它。
 * **一张图一条历史**；已经合样式的图算出来是空计划，不写。
 */
export function alignNewFigures(): number {
  const doc = docNow()
  const binding = canvasStyle(doc)
  if (!binding || blocked()) return 0
  const key = ledgerKey(doc)
  const done = seen.get(key) ?? new Set<string>()
  const style = resolvedStyle(binding)
  const preset: StylePreset = { ...style, name: bindingName(binding) }
  const owed = pending.get(key)
  let aligned = 0
  for (const panel of panelsOf(doc)) {
    const m = manifestOf(panel)
    if (!m) continue
    const fig = figKey(panel)
    const debt = owed?.get(fig)
    const current = docNow()
    const target = current.objects.find((o): o is PanelObject => o.id === panel.id && o.type === 'panel')
    if (!target) continue
    if (debt) {
      owed!.delete(fig)
      markSeen(doc, [fig])
      const plan = changesFor({ ...pruneToStyle(debt, style), name: '' } as StylePreset, [target], current, false)
      if (isEmptyPlan(plan)) continue
      commitWith(hist('syncStyle', { name: bindingName(binding) }), (d) => writeStylePlan(d, plan, preset), plan)
      aligned += 1
      continue
    }
    if (done.has(fig)) continue
    markSeen(doc, [fig])
    // 带着样式管得到的 override = 对齐过 / 用户手改过——不碰。**例外是素材自带的烘焙基线**
    // （`addPanel` 把 `baked_overrides` 原样抄进来）：那不是用户的手改，绑定之后加进来的图与
    // 先加图再绑定应当得到同一个结果（Codex #547 P2）
    // 只比内容、不看基线是否仍有效：文件被外部改过，抄进来的基线也不是用户的手改（Codex #547 P2）
    const baseline = isCopiedBakedBaseline(panel.overrides, useAssetStore.getState().byId[panel.fileId])
    if (!baseline && styleOverrideTargets(panel, m).length) continue
    const plan = changesFor(presetDelta(null, style), [target], current, false)
    if (isEmptyPlan(plan)) continue
    commitWith(hist('alignNewFigure'), (d) => writeStylePlan(d, plan, preset), plan)
    aligned += 1
  }
  // 绑定之后新加的画布标注 / 序号标签：同一条纪律（只对齐一次、单独一条历史）。已有的文字在
  // 换进画布 / 绑定的那一刻就记成「看过了」（`markTextsSeen`），重开文档不会被重排（Codex #547 P2）
  const fresh = docNow().objects.filter((o) => o.type === 'text' && !done.has(o.id)).map((o) => o.id)
  if (fresh.length && (style.annotation || style.subLabel)) {
    const current = docNow()
    markSeen(current, fresh)
    const texts: StylePreset = { name: preset.name, element: {}, annotation: style.annotation, subLabel: style.subLabel }
    const raw = planStyle(texts, [], () => null, current, true)
    const only = new Set(fresh)
    const plan = effectiveChanges(
      {
        ...raw,
        annotationIds: raw.annotationIds.filter((id) => only.has(id)),
        subLabelIds: raw.subLabelIds.filter((id) => only.has(id)),
        page: undefined,
        background: undefined,
      },
      current,
      manifestOf,
      texts,
    )
    if (!isEmptyPlan(plan)) {
      commitWith(hist('alignNewText'), (d) => writeStylePlan(d, plan, texts), null)
      aligned += 1
    }
  } else if (fresh.length) {
    markSeen(docNow(), fresh)
  }
  return aligned
}

/** 此刻画布上的文字都算「看过了」（换进画布 / 绑定的那一刻调：它们不是绑定之后新加的） */
function markTextsSeen() {
  const doc = docNow()
  if (!canvasStyle(doc)) return
  markSeen(doc, doc.objects.filter((o) => o.type === 'text').map((o) => o.id))
}

/**
 * 绑定了样式的文档要**跟随**，前提是样式库清单拉回来过。清单平时只在打开样式面板 / 设置 /
 * 导出对话框时才拉：重开一份绑定的文档、一直不碰那几处的话，它会永远停在快照上，库里的更新
 * （包括升级后变了的内置样式）一次都跟不上（Codex #547 P1）。同步器起来时、换进一份绑定的
 * 文档时各拉一次；拉回来之后 `useProfileStore` 的订阅会接着跑 `followLibrary`。
 */
function ensureLibrary() {
  const p = useProfileStore.getState()
  if (!canvasStyle() || p.loaded || p.loading) return
  void p.load()
}

/**
 * 同步器：订阅文档、样式库、渲染态、交互四处，把上面的「跟随」接到对的时刻上。
 * 返回退订函数（App 挂一次；测试各自起停）。
 */
export function startStyleBindingSync(): () => void {
  let last = useDocumentStore.getState()
  const unDoc = useDocumentStore.subscribe((s) => {
    const prev = last
    last = s
    // 事务收尾 / 离开「停在历史上」（future 被一次新编辑清空）：期间被挡下的触发补看一次
    if ((prev.txn && !s.txn) || (prev.future.length > 0 && s.future.length === 0)) {
      followLibrary()
      alignNewFigures()
    }
    if (s.doc === prev.doc) return
    const switched =
      s.documentId !== prev.documentId || s.activeCanvasId !== prev.activeCanvasId || s.loadSeq !== prev.loadSeq
    if (switched) {
      // 换进来一张画布：库里那条变过就跟上
      ensureLibrary()
      markTextsSeen()
      followLibrary()
      alignNewFigures()
      return
    }
    // 画布上的对象变了（加了一段标注 / 一张图）：看看有没有要对齐的新东西
    if (!writing && s.doc.objects !== prev.doc.objects) alignNewFigures()
  })
  const unProfiles = useProfileStore.subscribe((s, prev) => {
    if (s.styles !== prev.styles || s.loaded !== prev.loaded) followLibrary()
  })
  const unRender = useRenderStore.subscribe((s, prev) => {
    if (s.byKey !== prev.byKey) {
      followLibrary()
      alignNewFigures()
    }
  })
  const unInteraction = useInteractionStore.subscribe((s, prev) => {
    if (prev.kind !== 'none' && s.kind === 'none') {
      followLibrary()
      alignNewFigures()
    }
  })
  ensureLibrary()
  markTextsSeen()
  followLibrary()
  alignNewFigures()
  return () => {
    unDoc()
    unProfiles()
    unRender()
    unInteraction()
  }
}
