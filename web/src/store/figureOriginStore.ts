import { create } from 'zustand'
import {
  aiDataFigureCommit,
  aiDataFigureDiscard,
  aiDataFigurePropose,
  type AiDataCommitResult,
  type AiDataProposeBody,
} from '@/lib/api'
import { isCommitInProgress, pollCommit } from '@/lib/dataCommitPoll'
import { runExclusive } from '@/lib/dataCommitQueue'
import { ORIGIN_MAX, answersWithKind, findSameDrawing, normScript, type ErrorKind, type FigureOrigin } from '@/lib/dataFigureOrigin'
import { currentProjectId } from '@/lib/session'
import { msg, type UiMessage } from '@/i18n'

/**
 * 「点选误差线时换 SD / SEM / 95% CI」的来历与换图（ADR 0178，实验开关 `ai.taf.enabled` 后面）。
 *
 * 数据页画好一张图、进编辑器之后，`dataFigureStore` 把「这张图是怎么画出来的」（`FigureOrigin`）交到这里，按**脚本路径**记
 * （不持久化；`clear()` 随换项目清空；最多 `ORIGIN_MAX` 份）。换类型 = 带着同一份请求再出一批候选、用 `candidateIdentity` 认回同一种画法、
 * 提交成**新脚本**，再由宿主在同一块面板上把图换成新脚本画的（旧脚本留在项目里不删，撤销回到旧图）。
 *
 * 为什么不借 `dataFigureStore.propose()`：它会改写当前草稿的 `session` / `candidates`，冲掉用户正开着的草稿。这里直接调接口，
 * 草稿的任何状态都不碰；后端会话名额（至多 6 个）由宿主腾一个出来，用完立刻放弃。
 *
 * 规矩同其它会在项目之间存活的 store：项目代际（`clear()` 加一）+ 发起那一刻的项目都核；任何一步不对、认不回同一种画法、
 * 提交没成，一律静默回到原图（`switching` 清掉），不弹错误块——这一行只是个方便，不该变成一个会报错的功能。
 * 提交之后才放弃（项目换了 / 面板被改过 / 被删了）时，新脚本已经落盘留在项目里，没有图放上去；旧图不变。
 * 与数据页的提交共用同一把串行锁（`lib/dataCommitQueue`）。
 */

/** 宿主（项目 / 画布那一头）要做的事。不在这里 import：`projectStore` / `liveSync` / 工作区 import 本 store 会成环（`importArchitecture` 门禁） */
export interface FigureOriginHost {
  /**
   * 新脚本已落盘：刷新项目、运行它、在**旧脚本的那块面板**上原位换成新图（一条可撤销的历史）。成功回新图的来源指纹；
   * 面板不见了 / 被改过 / 项目换了 / 运行没成 = null（什么都没换）
   */
  replaceFigure: (a: { oldScript: string; newScript: string; origin: FigureOrigin; still: () => boolean }) => Promise<{ fingerprint: string } | null>
  /** 腾出一个后端候选会话名额（数据页的留存草稿丢最旧的）：换类型要多开一个会话，不能把用户开着的草稿挤掉 */
  reserveSession: () => void
  /** 浮动通知（一句话，没有按钮） */
  notify: (message: UiMessage) => void
}
const defaultHost: FigureOriginHost = { replaceFigure: async () => null, reserveSession: () => {}, notify: () => {} }
let host: FigureOriginHost = defaultHost
export const configureFigureOriginHost = (h: FigureOriginHost): void => {
  host = h
}

interface FigureOriginStore {
  /** 脚本路径 → 来历；按登记先后排（最旧的在前） */
  figures: Record<string, FigureOrigin>
  /** 正在换：下拉立刻显示新值、转灰带转圈；同一时刻只有一份在换 */
  switching: { script: string; kind: ErrorKind } | null
  /** 数据页出图成功、进编辑器之前登记（同一个脚本再登记 = 覆盖） */
  register: (origin: FigureOrigin) => void
  /** 面板第一次出现在文档里：记下它的来源指纹（只在还没记过时） */
  bindFingerprint: (script: string, fingerprint: string) => void
  /** 还没记到来源指纹的那些脚本（宿主据此决定要不要去文档里找面板） */
  unbound: () => string[]
  /** 把这张图的误差类型换成 `kind`：成功后图被换成新脚本画的；其它任何结局都静默不变 */
  switchKind: (script: string, kind: ErrorKind) => Promise<void>
  clear: () => void
}

let epoch = 0
let ridCounter = 0
const newRid = (): string => `df2e-${Date.now().toString(36)}-${(ridCounter++).toString(36)}-${Math.random().toString(36).slice(2, 8)}`

export const useFigureOriginStore = create<FigureOriginStore>((set, get) => ({
  figures: {},
  switching: null,

  register: (origin) => {
    const next = { ...get().figures }
    delete next[origin.script] // 覆盖 = 挪到最新
    next[origin.script] = origin
    for (const k of Object.keys(next)) {
      if (Object.keys(next).length <= ORIGIN_MAX) break
      delete next[k]
    }
    set({ figures: next })
  },

  bindFingerprint: (script, fingerprint) => {
    const key = normScript(script)
    const o = get().figures[key]
    if (!o || o.fingerprint != null) return
    set({ figures: { ...get().figures, [key]: { ...o, fingerprint } } })
  },

  unbound: () => Object.values(get().figures).filter((o) => o.fingerprint == null).map((o) => o.script),

  switchKind: async (script, kind) => {
    const key = normScript(script)
    const origin = get().figures[key]
    if (!origin || get().switching || origin.kind === kind || origin.fingerprint == null) return
    const ep = epoch
    const pj = currentProjectId()
    const still = () => ep === epoch && pj === currentProjectId()
    set({ switching: { script: key, kind } })
    try {
      await runExclusive(() => run(origin, kind, pj, still))
    } catch {
      /* 静默：回到原图 */
    } finally {
      if (ep === epoch) set({ switching: null })
    }
  },

  clear: () => {
    epoch += 1
    set({ figures: {}, switching: null })
  },
}))

/** 换类型的整条路；任何一步不成立就 return（调用方统一静默） */
async function run(origin: FigureOrigin, kind: ErrorKind, pj: string | null, still: () => boolean): Promise<void> {
  if (!still()) return
  host.reserveSession()
  const body: AiDataProposeBody = {
    table: origin.file.path,
    ...(origin.file.sheet == null ? {} : { sheet: origin.file.sheet }),
    render: 'lazy',
    answers: answersWithKind(origin, kind),
    ...(origin.headerRow != null ? { header_rows: { [origin.file.path]: origin.headerRow } } : {}),
    ...(origin.spec ? { spec: origin.spec } : {}),
    ...(origin.fontRange ? { font_range: origin.fontRange } : {}),
  }
  let session: string | null = null
  try {
    const res = await aiDataFigurePropose(body, pj)
    if (res.kind === 'candidates') session = res.session
    if (!still() || res.kind !== 'candidates') return
    const item = findSameDrawing(res.items, origin, kind)
    if (!item) return // 认不回同一种画法 / 新一批里不是算出来的这种误差：不换
    const req = { session: res.session, id: item.id, client_request_id: newRid() }
    let out: AiDataCommitResult = await aiDataFigureCommit(req, pj)
    if (!still()) return
    if (isCommitInProgress(out)) {
      const waited = await pollCommit(() => aiDataFigureCommit(req, pj), still)
      if (!waited || !still()) return
      out = waited
    }
    if (!out.script || !out.stem || out.data_check === 'mismatch' || out.task?.outcome === 'failed') return
    const placed = await host.replaceFigure({ oldScript: origin.script, newScript: out.script, origin, still })
    if (!placed || !still()) return
    // 与数据页出图同一句话：这类图没法逐项核对 / 没能核对（已核对不提）
    if (out.data_check === 'unverifiable') host.notify(msg('dataFigureV2.result.unverifiable', undefined, 'ai'))
    else if (out.data_check !== 'verified') host.notify(msg('dataFigureV2.result.unknown', undefined, 'ai'))
    useFigureOriginStore.getState().register({ ...origin, script: normScript(out.script), kind, fingerprint: placed.fingerprint })
  } catch {
    // 没收到结果 / 被后端拒绝都一样：不换（请求号每次都是新的，没有要保留的重试状态）
  } finally {
    if (session) void aiDataFigureDiscard(session, pj).catch(() => undefined)
  }
}
