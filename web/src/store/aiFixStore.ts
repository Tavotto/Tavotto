/**
 * 「✦ 使用你的 AI 来修复」（智能修复主线，ADR 0148 / 0149）的前端状态：按准备卡条目记「这张卡点过修复之后走到哪了」。
 *
 * 流程（维护者裁决：每次失败都出这颗按钮，点它——）：
 * * 没连 AI：打开设置的 AI 页（`setSettingsOpen(true, 'ai')`），记成 `awaiting_connect`；连好回来
 *   （`aiStore.caps` 里出现可用的 AI）就自动接着进入修复，用户不用再点一次；设置关了还没连上就放弃等待；
 * * 已连 AI：调 `startAiFix`（`POST /api/ai/fix`）。后端回 `{ok:true, fix_id}` 就进入 `running`；
 *   `ai_fix_unavailable`（没有内置执行器能用的 API 连接，例如只连了命令行 AI）→ `unavailable`：卡片说
 *   「这个问题 AI 暂时还不会修」+ 主按钮「发送反馈」；`ai_fix_busy`（已有别的修复在跑）→ `busy`；
 * * `running` / `waiting`：后端修复会话在跑。SSE `ai.fix` 只是「重读」提示，状态以 `GET /api/ai/fix/<id>` 为准，
 *   另有轻量轮询兜底（SSE 丢了也不会卡）。`waiting` 带一张要用户点的卡（`card`）：回答走 `answer`，取消走 `cancel`；
 * * 结束（`finished`）：`fixed` → 清掉条目并**重跑准备会话**验证（不信 AI 的一面之词）；`not_fixed` / `failed` →
 *   `done`，卡片说结果、用户点「知道了」回到原来的失败卡；`cancelled` → 直接清掉。
 *
 * * `convert`（AI 判断脚本能跑、只是用位图库画的图）：清掉修复条目，把「这张卡交接给了哪个脚本 / 库」记进 `handoffs`，
 *   并启动既有的「生成可编辑版本」流程（`convertStore.start`，ADR 0143）；准备卡据 `handoffs` 把同一张卡接上它的进度 / 对照 / 采用；
 *   那条转换记录一消失（放弃 / 采用后清掉）交接记录随之清，卡片回到原来的失败卡。
 *
 * 不存任何用户内容以外的东西：卡片文字只在内存里（后端已脱敏），不持久化。
 */
import { create } from 'zustand'
import {
  ApiError,
  answerAiFix,
  cancelAiFix,
  fetchAiFix,
  requestAiFix,
  hasConnectionView,
  hasUsableAi,
  type AiFixCard,
  type AiFixState,
} from '@/lib/api'
import { captureProjectEpoch } from '@/lib/projectEpoch'
import { onCurrentProjectChange } from '@/lib/session'
import { useAiStore } from '@/store/aiStore'
import { useConvertStore } from '@/store/convertStore'
import { requestConnectAi, useConnectAiStore } from '@/store/connectAiStore'
import { useDiagSendStore } from '@/store/diagSendStore'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useUiStore } from '@/store/uiStore'

export type AiFixPhase =
  | 'awaiting_connect'
  | 'starting'
  | 'unavailable'
  | 'busy'
  | 'running'
  | 'waiting'
  | 'done'

export interface AiFixRef {
  session_id: string
  script: string
  /** 点的那一刻报告里当前尝试的 id：之后又跑了一次（新尝试）就不再沿用旧的「暂时不会修」 */
  attemptId: string | null
}

export interface AiFixEntry {
  phase: AiFixPhase
  ref: AiFixRef
  fixId?: string
  /** 发起修复时的项目：切项目之后取消 / 重读都认它，不认当前项目 */
  pj?: string | null
  /** `waiting` 时要用户点的那张卡 */
  card?: AiFixCard
  /** `done` 时：AI 的结局与它说的一句话；`failed` 带 runner 的原因码 */
  outcome?: 'not_fixed' | 'failed'
  summary?: string
  reason?: string
  /** 「发送」卡：用户点过「查看并发送」那一刻的发送计数；之后计数增加 = 真的发出去了 */
  sendBase?: number
  /** 回答请求在途（按钮置忙，防连点） */
  answering?: boolean
  /** 进度与卡住（后端公开状态原样带过来；界面按它说一句话） */
  activity?: AiFixState['activity']
  stalled?: AiFixState['stalled']
}

/** 一张准备卡（entry key）交接给「生成可编辑版本」的脚本与库 */
export interface AiFixHandoff {
  script: string
  library: string
  /** 报告里的原输出图（项目相对）；对照视图左侧的参考图，可缺 */
  path: string | null
}

interface AiFixStoreState {
  entries: Record<string, AiFixEntry>
  /** 准备卡 entry key → 交接记录；对应的转换记录消失 / 切项目时清掉 */
  handoffs: Record<string, AiFixHandoff>
  /** 点「✦ 使用你的 AI 来修复」：没连 AI 去设置里连，已连就开始修复 */
  request: (key: string, ref: AiFixRef) => Promise<void>
  /** 已连 AI：开始修复（`ai_fix_available === false` 或端点回 `ai_fix_unavailable` 都落成 `unavailable`） */
  startAiFix: (key: string, ref: AiFixRef) => Promise<void>
  /** 重读修复会话的状态并落地 */
  refresh: (key: string) => Promise<void>
  /** SSE `ai.fix`：只是「重读」提示 */
  onHint: (fixId: string) => void
  /** 回答当前卡片 */
  answer: (key: string, answer: string, text?: string) => Promise<void>
  /** 取消修复 */
  cancel: (key: string) => Promise<void>
  /** 「发送」卡：用户点了「查看并发送」（打开发送对话框，预填 AI 起草的说明；源码默认不勾） */
  openSend: (key: string) => void
  clear: (key: string) => void
  reset: () => void
}

/** 轮询兜底的间隔：SSE 提示正常到达时几乎用不上 */
const POLL_MS = 2000
const timers = new Map<string, ReturnType<typeof setTimeout>>()

const stop = (key: string) => {
  const t = timers.get(key)
  if (t !== undefined) clearTimeout(t)
  timers.delete(key)
}

export const useAiFixStore = create<AiFixStoreState>((set, get) => {
  const put = (key: string, patch: (e: AiFixEntry) => AiFixEntry | null) =>
    set((s) => {
      const cur = s.entries[key]
      if (!cur) return s
      const next = patch(cur)
      if (next === null) {
        const { [key]: _drop, ...rest } = s.entries
        return { entries: rest }
      }
      return { entries: { ...s.entries, [key]: next } }
    })

  const schedule = (key: string) => {
    stop(key)
    const e = get().entries[key]
    if (!e || (e.phase !== 'running' && e.phase !== 'waiting')) return
    timers.set(
      key,
      setTimeout(() => void get().refresh(key), POLL_MS),
    )
  }

  /** 结束：fixed → 清掉并重跑准备会话验证；cancelled → 清掉；其余落成 `done` */
  const settle = (key: string, st: AiFixState, still: () => boolean) => {
    stop(key)
    const entry = get().entries[key]
    if (!entry) return
    if (st.outcome === 'cancelled') return get().clear(key)
    if (st.outcome === 'convert' && st.handoff) {
      const { script, library } = st.handoff
      const path = st.handoff.path ?? null
      get().clear(key)
      if (!still()) return
      set((s) => ({ handoffs: { ...s.handoffs, [key]: { script, library, path } } }))
      void useConvertStore.getState().start(script, library, path, 'python')
      return
    }
    if (st.outcome === 'fixed') {
      get().clear(key)
      // 重跑准备会话：这一次是验证，不是用户新点的「运行」之外的东西——只重新检查并按报告说话
      const prep = useProjectPreparationStore.getState()
      const target = prep.entries[key]?.target
      if (target && still()) void prep.open(target, { run: true, rerun: true })
      return
    }
    put(key, (e) => ({
      ...e,
      phase: 'done',
      card: undefined,
      outcome: st.outcome === 'not_fixed' ? 'not_fixed' : 'failed',
      summary: st.summary,
      reason: st.reason,
    }))
  }

  return {
    entries: {},
    handoffs: {},

    request: async (key, ref) => {
      const guard = captureProjectEpoch()
      let caps = useAiStore.getState().caps
      if (!caps) {
        // 还没探测过本机的 AI：先探测一次再判断（失败当作没连）
        try {
          await useAiStore.getState().loadCaps()
        } catch {
          /* 探测失败：按没连处理 */
        }
        caps = useAiStore.getState().caps
        // 探测在途时用户切走了项目：这次点击属于原项目，不落 store、不起修复（A→B→A 也算作废）
        if (!guard.still()) return
      }
      if (!hasUsableAi(caps)) {
        set((s) => ({ entries: { ...s.entries, [key]: { phase: 'awaiting_connect', ref } } }))
        requestConnectAi({ reason: 'fix' })
        return
      }
      await get().startAiFix(key, ref)
    },

    startAiFix: async (key, ref) => {
      const setPhase = (phase: AiFixPhase, extra: Partial<AiFixEntry> = {}) =>
        set((s) => ({ entries: { ...s.entries, [key]: { phase, ref, ...extra } } }))
      const guard = captureProjectEpoch()
      setPhase('starting')
      if (useAiStore.getState().caps?.ai_fix_available === false) {
        setPhase('unavailable')
        return
      }
      try {
        const res = await requestAiFix({ session_id: ref.session_id, script: ref.script })
        if (!guard.still()) {
          // 请求在途时用户切走了项目：这份结果不属于现在的项目，绝不落进 store；已经起来的修复取消掉（别在后台白跑）
          if (res.ok) void cancelAiFix(res.fix_id, guard.pj).catch(() => {})
          return
        }
        if (res.ok) {
          setPhase('running', { fixId: res.fix_id, pj: guard.pj ?? undefined })
          schedule(key)
          void get().refresh(key)
        } else setPhase(res.reason === 'ai_fix_busy' ? 'busy' : 'unavailable')
      } catch {
        if (!guard.still()) return
        // 请求本身失败（断线 / 老后端没有这个端点）：同样落到反馈，不让用户卡在一个转圈的按钮上
        setPhase('unavailable')
      }
    },

    refresh: async (key) => {
      const cur = get().entries[key]
      const fixId = cur?.fixId
      if (!fixId) return
      const guard = captureProjectEpoch()
      try {
        const st = await fetchAiFix(fixId, cur.pj)
        // 读的期间条目被清掉 / 换了一次修复 / 切了项目：丢弃
        if (get().entries[key]?.fixId !== fixId || !guard.still()) return
        if (st.state === 'finished') return settle(key, st, guard.still)
        put(key, (e) => ({
          ...e,
          phase: st.state === 'waiting' && st.card ? 'waiting' : 'running',
          // 同一张卡不重置「发送」卡的计数基线
          card: st.card ?? undefined,
          activity: st.activity ?? undefined,
          stalled: st.stalled ?? undefined,
          sendBase: st.card && e.card?.id === st.card.id ? e.sendBase : undefined,
        }))
      } catch (e) {
        // 读不到：断线就下一轮再试；会话没了（404，后端重启丢了它）就放弃，回到原来的失败卡
        if (!guard.still()) return
        if (e instanceof ApiError && e.status === 404) return get().clear(key)
      }
      schedule(key)
    },

    onHint: (fixId) => {
      for (const [key, e] of Object.entries(get().entries)) if (e.fixId === fixId) void get().refresh(key)
    },

    answer: async (key, answer, text) => {
      const e = get().entries[key]
      if (!e?.fixId || !e.card || e.answering) return
      const { fixId, card } = e
      put(key, (x) => ({ ...x, answering: true }))
      try {
        await answerAiFix(fixId, { card_id: card.id, answer, ...(text ? { text } : {}) }, e.pj)
      } catch {
        /* 卡片已过期 / 回答被拒：下面的重读会把真实状态摆出来 */
      }
      put(key, (x) => ({ ...x, answering: false }))
      await get().refresh(key)
    },

    cancel: async (key) => {
      const cur = get().entries[key]
      const fixId = cur?.fixId
      if (!fixId) return get().clear(key)
      try {
        await cancelAiFix(fixId, cur.pj)
      } catch {
        /* 会话已经没了：照样收起 */
      }
      await get().refresh(key)
    },

    openSend: (key) => {
      const e = get().entries[key]
      if (!e?.card || e.card.kind !== 'send') return
      const diag = useDiagSendStore.getState()
      put(key, (x) => ({ ...x, sendBase: diag.sentCount }))
      diag.openWithNote(e.card.summary)
    },

    clear: (key) => {
      stop(key)
      put(key, () => null)
    },

    reset: () => {
      for (const key of [...timers.keys()]) stop(key)
      set({ entries: {}, handoffs: {} })
    },
  }
})

/** 此刻还在等用户连 AI 的条目 */
const waiting = () => Object.entries(useAiFixStore.getState().entries).filter(([, e]) => e.phase === 'awaiting_connect')

// 连好 AI 回来自动接着修：caps 里出现可用的 AI，就把所有在等的条目接着走 startAiFix
useAiStore.subscribe((s, prev) => {
  if (s.caps === prev.caps || !hasUsableAi(s.caps)) return
  for (const [key, e] of waiting()) void useAiFixStore.getState().startAiFix(key, e.ref)
})

// 老后端（没有连接视图）走的是设置页：设置关了还没连上同样放弃等待
useUiStore.subscribe((s, prev) => {
  if (!prev.settingsOpen || s.settingsOpen) return
  if (hasConnectionView(useAiStore.getState().caps)) return
  for (const [key] of waiting()) useAiFixStore.getState().clear(key)
})

// 连接弹窗关了还没连上：放弃等待（之后偶然连上 AI 不该冷不丁开始修）。连上了的那条已由上面的订阅接走
// （连好时 caps 先于弹窗关闭刷新，那条已离开 `awaiting_connect`）
useConnectAiStore.subscribe((s, prev) => {
  if (!prev.open || s.open) return
  if (hasUsableAi(useAiStore.getState().caps)) return
  for (const [key] of waiting()) useAiFixStore.getState().clear(key)
})

// 交接出去的转换记录一消失（放弃 / 采用后清掉）：交接记录随之清，准备卡回到原来的失败卡
useConvertStore.subscribe((s, prev) => {
  const { handoffs } = useAiFixStore.getState()
  const gone = Object.entries(handoffs).filter(([, h]) => prev.entries[h.script] && !s.entries[h.script])
  if (gone.length === 0) return
  const next = { ...handoffs }
  for (const [key] of gone) delete next[key]
  useAiFixStore.setState({ handoffs: next })
})

// 「发送」卡：用户在发送对话框里真的点了「发送」并成功（发送计数增加）→ 回答 `sent`
useDiagSendStore.subscribe((s, prev) => {
  if (s.sentCount === prev.sentCount) return
  for (const [key, e] of Object.entries(useAiFixStore.getState().entries)) {
    if (e.card?.kind === 'send' && e.sendBase !== undefined && s.sentCount > e.sendBase) {
      void useAiFixStore.getState().answer(key, 'sent')
    }
  }
})

// 切项目：属于旧项目的条目一律丢掉（A 的卡片绝不出现在 B 的同名脚本上），在跑的修复取消（带发起时的项目 id，
// 不然会打到 B 上）；在途请求靠 `captureProjectEpoch` 作废
onCurrentProjectChange(() => {
  const { entries } = useAiFixStore.getState()
  for (const e of Object.values(entries)) {
    if (e.fixId && e.phase !== 'done') void cancelAiFix(e.fixId, e.pj).catch(() => {})
  }
  useAiFixStore.getState().reset()
})
