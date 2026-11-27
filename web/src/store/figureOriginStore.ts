import { create } from 'zustand'
import {
  aiDataFigureCommit,
  aiDataFigureDigest,
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
import type { PanelDataOrigin } from '@/types/document'

/**
 * 「点选误差线时换 SD / SEM / 95% CI」的来历与换图（ADR 0178，实验开关 `ai.taf.enabled` 后面）。
 *
 * 数据页画好一张图、进编辑器之后，`dataFigureStore` 把「这张图是怎么画出来的」（`FigureOrigin`）交到这里，按**脚本路径**暂存
 * （内存里的待认领表，`clear()` 随换项目清空，最多 `ORIGIN_MAX` 份）；面板第一次出现在文档里，宿主把它写到那块面板的 `PanelObject.dataOrigin`
 * （随项目文档落盘，之后只认面板上的，见 `lib/dataFigureOrigin.readPanelOrigin`）。换类型 = 带着同一份请求再出一批候选、用 `candidateIdentity` 认回同一种画法、
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
  /** 这块面板此刻的来历：读面板上的 `dataOrigin`，且图还没被改过；面板不在 / 没有来历 / 改过了 = null（什么都不做） */
  originOf: (panelId: string) => FigureOrigin | null
  /**
   * 新脚本已落盘：刷新项目、运行它、在**这块面板**上原位换成新图（一条可撤销的历史，新来历在同一次写入里落到面板上）。
   * 换成了 = true；面板不见了 / 被改过 / 项目换了 / 运行没成 = false（什么都没换）
   */
  replaceFigure: (a: { panelId: string; newScript: string; origin: FigureOrigin; kind: ErrorKind; still: () => boolean }) => Promise<boolean>
  /** 腾出一个后端候选会话名额（数据页的留存草稿丢最旧的）：换类型要多开一个会话，不能把用户开着的草稿挤掉 */
  reserveSession: () => void
  /** 浮动通知（一句话，没有按钮） */
  notify: (message: UiMessage) => void
}
const defaultHost: FigureOriginHost = { originOf: () => null, replaceFigure: async () => false, reserveSession: () => {}, notify: () => {} }
let host: FigureOriginHost = defaultHost
export const configureFigureOriginHost = (h: FigureOriginHost): void => {
  host = h
}

/** 数据文件核对状态：`checking` 在核对；字符串 = 磁盘上此刻的 sha256；`null` = 核对不了（文件不在 / 读不了 / 太大） */
export type DigestState = 'checking' | string | null

interface FigureOriginStore {
  /** 脚本路径 → 还没认领到面板的来历；按登记先后排（最旧的在前） */
  pending: Record<string, FigureOrigin>
  /**
   * 脚本路径 → 已经写到面板上的那份来历（连同当时面板的来源指纹）。只为一件事留着：新建图那一步被撤销再重做时，面板是照
   * 历史里「还没有来历」的样子回来的，宿主凭这里把同一份来历补回去（指纹对得上才补）。本次会话内、最多 `ORIGIN_MAX` 份、换项目即清
   */
  claimed: Record<string, { fingerprint: string; origin: PanelDataOrigin }>
  /** 正在换：下拉立刻显示新值、转灰带转圈；同一时刻只有一份在换 */
  switching: { panelId: string; kind: ErrorKind } | null
  /** 数据文件路径 → 核对状态（本次会话内；换项目即清） */
  digests: Record<string, DigestState>
  /** 数据页出图成功、进编辑器之前登记（同一个脚本再登记 = 覆盖） */
  register: (origin: FigureOrigin) => void
  /** 宿主把来历写到面板上之后，从待认领表里拿掉 */
  release: (script: string) => void
  /** 来历写到面板上之后记一笔（见 `claimed`） */
  markClaimed: (script: string, fingerprint: string, origin: PanelDataOrigin) => void
  /** 去磁盘核对这份数据此刻的摘要（每次调用都重新问一次；回来前保留上一次的结果，不闪） */
  checkData: (path: string) => Promise<void>
  /** 把这块面板上那张图的误差类型换成 `kind`：成功后图被换成新脚本画的；其它任何结局都静默不变 */
  switchKind: (panelId: string, kind: ErrorKind) => Promise<void>
  clear: () => void
}

let epoch = 0
let ridCounter = 0
const newRid = (): string => `df2e-${Date.now().toString(36)}-${(ridCounter++).toString(36)}-${Math.random().toString(36).slice(2, 8)}`

export const useFigureOriginStore = create<FigureOriginStore>((set, get) => ({
  pending: {},
  claimed: {},
  switching: null,
  digests: {},

  register: (origin) => {
    const next = { ...get().pending }
    delete next[origin.script] // 覆盖 = 挪到最新
    next[origin.script] = origin
    for (const k of Object.keys(next)) {
      if (Object.keys(next).length <= ORIGIN_MAX) break
      delete next[k]
    }
    set({ pending: next })
  },

  release: (script) => {
    const key = normScript(script)
    if (!get().pending[key]) return
    const next = { ...get().pending }
    delete next[key]
    set({ pending: next })
  },

  markClaimed: (script, fingerprint, origin) => {
    const key = normScript(script)
    const next = { ...get().claimed }
    delete next[key]
    next[key] = { fingerprint, origin }
    for (const k of Object.keys(next)) {
      if (Object.keys(next).length <= ORIGIN_MAX) break
      delete next[k]
    }
    set({ claimed: next })
  },

  checkData: async (path) => {
    const ep = epoch
    const pj = currentProjectId()
    const prev = get().digests[path]
    set({ digests: { ...get().digests, [path]: prev === undefined ? 'checking' : prev } })
    let sha: string | null = null
    try {
      sha = (await aiDataFigureDigest(path, pj)).sha256
    } catch {
      sha = null
    }
    if (ep !== epoch || pj !== currentProjectId()) return
    set({ digests: { ...get().digests, [path]: sha } })
  },

  switchKind: async (panelId, kind) => {
    if (get().switching) return
    const origin = host.originOf(panelId)
    if (!origin || origin.kind === kind) return
    const ep = epoch
    const pj = currentProjectId()
    const still = () => ep === epoch && pj === currentProjectId()
    set({ switching: { panelId, kind } })
    try {
      await runExclusive(() => run(panelId, origin, kind, pj, still))
    } catch {
      /* 静默：回到原图 */
    } finally {
      if (ep === epoch) set({ switching: null })
    }
  },

  clear: () => {
    epoch += 1
    set({ pending: {}, claimed: {}, switching: null, digests: {} })
  },
}))

/** 换类型的整条路；任何一步不成立就 return（调用方统一静默） */
async function run(panelId: string, origin: FigureOrigin, kind: ErrorKind, pj: string | null, still: () => boolean): Promise<void> {
  if (!still()) return
  host.reserveSession()
  const body: AiDataProposeBody = {
    table: origin.file.path,
    ...(origin.file.sheet == null ? {} : { sheet: origin.file.sheet }),
    render: 'lazy',
    understand: false, // 这次重出不是用户在看数据：绝不为它多发一次「读懂数据」的模型出站
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
    // 数据文件自画这张图以来变了：不按新数据悄悄换（同样大小改了内容也认得出）
    if (res.data_sha256 !== origin.dataSha256) return
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
    const placed = await host.replaceFigure({ panelId, newScript: out.script, origin, kind, still })
    if (!placed || !still()) return
    // 与数据页出图同一句话：这类图没法逐项核对 / 没能核对（已核对不提）
    if (out.data_check === 'unverifiable') host.notify(msg('dataFigureV2.result.unverifiable', undefined, 'ai'))
    else if (out.data_check !== 'verified') host.notify(msg('dataFigureV2.result.unknown', undefined, 'ai'))
  } catch {
    // 没收到结果 / 被后端拒绝都一样：不换（请求号每次都是新的，没有要保留的重试状态）
  } finally {
    if (session) void aiDataFigureDiscard(session, pj).catch(() => undefined)
  }
}
