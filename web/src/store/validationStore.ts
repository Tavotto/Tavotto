/**
 * 检查的编排（ADR 0030）：什么时候跑、跑哪几片、上一次的结果怎么留。
 *
 * **求值不在这里**（`lib/preflight.ts`），**问题怎么接成可定位的**也不在这里
 * （`lib/validation.ts`）。这一层只管四件事：
 *
 * 1. **防抖 + 代次**。文档每敲一个字都重跑一遍是没意义的；而跑到一半用户又
 *    改了，回来的旧结果必须**丢掉**——用代次判，不是用"谁最后回来谁说了算"。
 * 2. **增量**。只有激活画布变了就只重算那一片，别的画布沿用上一次的结果。
 * 3. **失败不清空**。检查抛异常时保留上一次成功的清单，并把"这次没查成"
 *    单独说出来——「查不了」与「没问题」是两个答案，压成一个的话用户会带着
 *    一屏静悄悄的绿去投稿。
 * 4. **不改文档**。检查只读；任何写入都会与 autosave / 刷新 / 派生同步转成
 *    死循环（`UX_CONTRACTS.md` §1a）。
 */
import { create } from 'zustand'
import { resolveDocumentSpec } from '@/lib/specBinding'
import {
  filterIssues,
  summaryFor,
  validateCanvas,
  type CanvasInput,
  type CanvasResult,
  type IssueFilter,
  type ValidationIssue,
  type ValidationSummary,
} from '@/lib/validation'
import { canvasToDoc } from '@/types/document'
import { useAssetStore } from './assetStore'
import { useDocumentStore } from './documentStore'
import { useProfileStore } from './profileStore'
import { useRenderStore } from './renderStore'

/** 文档静下来多久之后才查一遍（ms）。指针交互期间另有闸门，见 `schedule()`。 */
export const VALIDATION_DEBOUNCE_MS = 250

interface ValidationState {
  /** 最后一次**成功**的结果，按画布分片 */
  results: CanvasResult[]
  /** 扁平化的问题清单（派生自 `results`，随它一起写） */
  issues: ValidationIssue[]
  /** 已经算完过至少一次（区分"还没查"与"查过、没问题"） */
  ready: boolean
  /** 这一次没查成。**上面那份 `issues` 仍然是上次的真结果，不清空** */
  failed: boolean
  /** 正在跑 */
  running: boolean
  /** 上一次跑用了多久（ms）——性能预算的看护点 */
  lastDurationMs: number | null
  generation: number
}

export const useValidationStore = create<ValidationState>(() => ({
  results: [],
  issues: [],
  ready: false,
  failed: false,
  running: false,
  lastDurationMs: null,
  generation: 0,
}))

const flatten = (results: CanvasResult[]): ValidationIssue[] => results.flatMap((r) => r.issues)

/* ------------------------------ 输入组装 ---------------------------------- */

/**
 * 当前项目的全部画布 → 检查输入。
 *
 * **激活画布用 `doc`，不用 `canvases` 里那份快照**：那份要等切画布时才回写，
 * 用它检查等于一直在查上一次切走时的样子。
 */
export function collectCanvases(): CanvasInput[] {
  const s = useDocumentStore.getState()
  const catalog = useProfileStore.getState().catalog()
  return s.canvases.map((c) => {
    const active = c.id === s.activeCanvasId
    const doc = active ? s.doc : canvasToDoc(c)
    // 名字同理：`canvases[].name` 要等切画布才回写，激活画布的现值在 `doc` 上
    const canvasName = active ? s.doc.name : c.name
    // 「这个项目按哪套规范检查」的唯一判据（ADR 0029）——**不在这里再挑一遍**
    const profile = resolveDocumentSpec(doc.profile, catalog).profile
    return { canvasId: c.id, canvasName, doc, profile }
  })
}

/* -------------------------------- 跑一遍 ---------------------------------- */

/**
 * 立刻算一遍（同步）。`only` 给出要重算的画布 id，其余沿用上一次的分片。
 *
 * 代次在**进入**时取一次，写回前再比一次：中途用户又改了（`schedule()` 会
 * 推进代次）就把算出来的东西整份丢掉——半新半旧的清单比过时的清单更坏，
 * 它看起来像是刚查过的。
 */
export function runValidation(only?: Set<string>): void {
  const gen = useValidationStore.getState().generation
  useValidationStore.setState({ running: true })
  const t0 = typeof performance !== 'undefined' ? performance.now() : Date.now()
  try {
    const s = useDocumentStore.getState()
    const assets = useAssetStore.getState().byId
    const render = useRenderStore.getState()
    const prev = new Map(useValidationStore.getState().results.map((r) => [r.canvasId, r]))
    const results = collectCanvases().map((input) => {
      const cached = prev.get(input.canvasId)
      if (only && !only.has(input.canvasId) && cached) {
        // 名字可能刚改过：分片本身沿用，标题跟上
        return cached.canvasName === input.canvasName
          ? cached
          : { ...cached, canvasName: input.canvasName }
      }
      return validateCanvas(input, s.documentId, assets, {
        byKey: render.byKey,
        latest: render.latest,
      })
    })
    if (useValidationStore.getState().generation !== gen) {
      useValidationStore.setState({ running: false })
      return
    }
    const t1 = typeof performance !== 'undefined' ? performance.now() : Date.now()
    useValidationStore.setState({
      results,
      issues: flatten(results),
      ready: true,
      failed: false,
      running: false,
      lastDurationMs: t1 - t0,
    })
  } catch {
    // **不清空**：上一次的结果仍然是当时的真话，把它换成空清单等于宣布"没问题"
    if (useValidationStore.getState().generation === gen) {
      useValidationStore.setState({ running: false, failed: true })
    }
  }
}

/* ------------------------------- 调度 ------------------------------------- */

let timer: ReturnType<typeof setTimeout> | null = null
let pending: Set<string> | null = null
/** null = 这一轮要全量重算（画布增删、换项目、换规范） */
let pendingAll = false

/**
 * 排一次检查。同一批连续修改只跑一次；每次排队都推进代次，于是**还在飞的
 * 那一轮回来时会被丢掉**。
 */
export function schedule(canvasId?: string): void {
  if (canvasId == null) pendingAll = true
  else (pending ??= new Set()).add(canvasId)
  useValidationStore.setState((s) => ({ generation: s.generation + 1 }))
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => {
    timer = null
    const only = pendingAll ? undefined : (pending ?? undefined)
    pending = null
    pendingAll = false
    runValidation(only)
  }, VALIDATION_DEBOUNCE_MS)
}

/** 丢掉排队中的那一轮（换项目时用；不动已经算出来的结果）。 */
export function cancelScheduled(): void {
  if (timer) clearTimeout(timer)
  timer = null
  pending = null
  pendingAll = false
}

/** 换项目：清空结果，等新文档到齐再算。**清空是有意的**——上一份项目的问题
 *  挂在新项目上是彻头彻尾的假话，与"查不了"完全不是一回事。 */
export function resetValidation(): void {
  cancelScheduled()
  useValidationStore.setState((s) => ({
    results: [],
    issues: [],
    ready: false,
    failed: false,
    running: false,
    generation: s.generation + 1,
  }))
}

/* ------------------------------ 订阅装配 ---------------------------------- */

/**
 * 唯一的驱动点。**一个订阅集合，不是每个组件各挂一个**——各挂一个的后果是
 * 打开两个面板就跑两遍，而两遍的结果谁写在后面全看渲染顺序。
 */
export function startValidation(): () => void {
  const doc = useDocumentStore.getState()
  let documentId = doc.documentId
  let activeCanvasId = doc.activeCanvasId
  let docRef: unknown = doc.doc
  let canvasesRef: unknown = doc.canvases

  schedule()

  const stopDoc = useDocumentStore.subscribe((s) => {
    if (s.documentId !== documentId) {
      documentId = s.documentId
      activeCanvasId = s.activeCanvasId
      docRef = s.doc
      canvasesRef = s.canvases
      resetValidation()
      schedule()
      return
    }
    // 画布增删 / 切换：整份重算（切换会把旧激活画布回写进 canvases）
    if (s.canvases !== canvasesRef || s.activeCanvasId !== activeCanvasId) {
      canvasesRef = s.canvases
      activeCanvasId = s.activeCanvasId
      docRef = s.doc
      schedule()
      return
    }
    if (s.doc !== docRef) {
      docRef = s.doc
      // 只动了激活画布：只重算那一片
      schedule(s.activeCanvasId)
    }
  })

  // manifest 是稍后才回来的：没有它字号/字体/线宽整组查不了，回来时要补一遍
  let renderRef: unknown = useRenderStore.getState().byKey
  const stopRender = useRenderStore.subscribe((s) => {
    if (s.byKey === renderRef) return
    renderRef = s.byKey
    schedule()
  })

  let assetsRef: unknown = useAssetStore.getState().byId
  const stopAssets = useAssetStore.subscribe((s) => {
    if (s.byId === assetsRef) return
    assetsRef = s.byId
    schedule()
  })

  // 规范清单变了（用户在设置里改了自建规范）：阈值跟着变，全量重算
  let specsRef: unknown = useProfileStore.getState().specs
  const stopProfiles = useProfileStore.subscribe((s) => {
    if (s.specs === specsRef) return
    specsRef = s.specs
    schedule()
  })

  return () => {
    cancelScheduled()
    stopDoc()
    stopRender()
    stopAssets()
    stopProfiles()
  }
}

/* -------------------------------- 对外 API -------------------------------- */

/**
 * 摘要。**导出面板消费的就是这一份**——它不再自己跑第二遍求值器
 * （Prompt 12 的入口，ADR 0030 §导出）。
 */
export function getValidationSummary(
  scope: 'project' | 'activeCanvas' = 'project',
  extra: ValidationIssue[] = [],
): ValidationSummary {
  const { issues, ready, failed } = useValidationStore.getState()
  const activeCanvasId = useDocumentStore.getState().activeCanvasId
  return summaryFor(issues, {
    canvasId: scope === 'activeCanvas' ? activeCanvasId : undefined,
    extra,
    ready,
    failed,
  })
}

/** 按筛选取问题（面板与导出共用同一份数据）。 */
export function listIssues(filter?: IssueFilter): ValidationIssue[] {
  return filterIssues(useValidationStore.getState().issues, filter)
}

/**
 * 某张画布的**聚合投影**（proof report 认的形状）。
 * 导出对话框拿它写留档——**同一次求值的另一份投影**，不是第二遍检查。
 */
export function rawIssuesFor(canvasId: string) {
  return useValidationStore.getState().results.find((r) => r.canvasId === canvasId)?.raw ?? []
}
