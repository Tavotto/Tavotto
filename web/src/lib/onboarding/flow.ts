/**
 * 教程引擎：把活动信号与 store 状态翻译成「当前这一步完成了没有」。
 *
 * ```text
 * onActivity ──┐
 * store 订阅 ──┴─▶ evaluate() ─▶ 当前步骤 done? ─▶ markStep + 消费信号 + goTo(next) ─▶ 再评一次
 *                       │
 *                       ├─ 进行中但离开了教程项目 / 文档 ─▶ pause('system')
 *                       └─ 系统暂停且回到了教程 ─▶ resume()
 * ```
 *
 * 三条纪律：
 *   * **只有一个引擎实例**（`startOnboardingEngine` 幂等：第二次调用回同一个
 *     stop）；组件里没有任何一处自己判完成；
 *   * 信号按步骤消费：一条重放的 `history.pushed` 只能完成它该完成的那一步；
 *   * 引擎**不碰文档**、不发请求；它改的只有 onboardingStore。
 */
import { onActivity, type ActivityDetail } from '@/lib/activity'
import { captureTelemetry } from '@/lib/telemetry'
import { useDocumentStore } from '@/store/documentStore'
import { ONBOARDING_FLOW_VERSION, useOnboardingStore } from '@/store/onboardingStore'
import { useProjectStore } from '@/store/projectStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useValidationStore } from '@/store/validationStore'
import { useWorkspaceStore } from '@/store/workspace'
import {
  buildContext,
  EMPTY_SIGNALS,
  nextStepId,
  stepById,
  TYPOGRAPHY_PROPS_FIGURE,
  type StepContext,
  type StepSignals,
} from './steps'
import { ensureTutorialDocument, loadTutorialStatus, useTutorialStore } from './tutorial'

let signals: StepSignals = { ...EMPTY_SIGNALS }
let stop: (() => void) | null = null
/** 本轮引擎生命周期里只主动取一次元数据（失败也不重试——入口按钮会再取） */
let metaRequested = false
/** 同一个 tick 里多份 store 变化只评一次 */
let scheduled = false

/** 测试与 coachmark 层都用它拿当前上下文——**同一份**组装函数 */
export function currentContext(): StepContext {
  return buildContext(useTutorialStore.getState().meta, signals)
}

/** 引擎此刻累计的信号（只读快照；测试用） */
export const signalSnapshot = (): StepSignals => ({ ...signals })

/** 「在教程里」= 本标签页开着教程项目，且文档就是教程画布 */
export function inTutorial(): boolean {
  const ob = useOnboardingStore.getState()
  if (!ob.tutorialDocumentId) return false
  const proj = useProjectStore.getState()
  const projectOk = !ob.tutorialProjectId || proj.project?.id === ob.tutorialProjectId
  return proj.phase === 'open' && projectOk && useDocumentStore.getState().documentId === ob.tutorialDocumentId
}

function isTutorialPanelId(id: string): boolean {
  return currentContext().tutorialPanelIds.has(id)
}

/**
 * 回到教程项目（从项目菜单切回来、或重启后第一次打开）：项目打开链路给的是一份
 * **空白文档**，而教程要的是教程画布。把它装回来——只装教程自己的那份，且只在
 * onboarding 还在这份教程里（进行中 / 被系统暂停）时做；用户自己暂停或跳过的
 * 不动（他没说要回来）。
 */
function onProjectOpened(tutorial: boolean): void {
  if (!tutorial) return
  const ob = useOnboardingStore.getState()
  const wanted = ob.status === 'active' || (ob.status === 'paused' && ob.pausedBy === 'system')
  if (!wanted || !ob.tutorialDocumentId) return
  if (useDocumentStore.getState().documentId === ob.tutorialDocumentId) return
  void ensureTutorialDocument().then(schedule)
}

/** 活动信号 → 累计。**只在教程进行中累计**：不在教程里的动作不该在回来时被当成刚做的 */
function onSignal(d: ActivityDetail): void {
  if (d.kind === 'project.opened') onProjectOpened(d.tutorial)
  const ob = useOnboardingStore.getState()
  if (ob.status === 'active' && inTutorial()) {
    switch (d.kind) {
      case 'element.property_changed':
        if (TYPOGRAPHY_PROPS_FIGURE.has(d.prop) && editingTutorialPanel()) signals.typographyChanged++
        break
      case 'history.pushed':
        if (editingTutorialPanel()) signals.historyPushed++
        break
      case 'problem.focused': {
        const primary = useSelectionStore.getState().primary()
        if (d.ok && primary && isTutorialPanelId(primary)) signals.problemFocused++
        break
      }
      case 'export.scope_changed':
        if (useUiStore.getState().exportOpen) {
          if (d.scope === 'original') signals.exportOriginalSeen++
          else signals.exportCanvasSeen++
        }
        break
      case 'selection.aligned': {
        const ids = useSelectionStore.getState().ids
        const tutorial = currentContext().tutorialPanelIds
        if (ids.filter((id) => tutorial.has(id)).length >= 2) signals.alignedTutorialPanels++
        break
      }
      default:
        break
    }
  }
  schedule()
}

/** 正在图内编辑的是不是教程里要编辑的那张 */
function editingTutorialPanel(): boolean {
  const ctx = currentContext()
  return !!ctx.edit && ctx.elementPanelId === ctx.edit.panel.id
}

function schedule(): void {
  if (scheduled) return
  scheduled = true
  queueMicrotask(() => {
    scheduled = false
    evaluate()
  })
}

/**
 * 评一次。完成链最多走完整张表（每步一次），防止两个互相满足的条件转圈。
 */
export function evaluate(): void {
  const ob = useOnboardingStore.getState()
  // 重启之后元数据在内存里是空的：教程还在进行 / 暂停中就去取一次
  // （只读 GET，不复制、不打开；取到之前步骤表找不到目标，coachmark 会「等待」）
  const ts = useTutorialStore.getState()
  if ((ob.status === 'active' || ob.status === 'paused') && !ts.meta && !ts.busy && !metaRequested) {
    metaRequested = true
    void loadTutorialStatus().then(schedule)
  }
  if (ob.status === 'active' && !inTutorial()) {
    ob.pause('system')
    return
  }
  if (ob.status === 'paused' && ob.pausedBy === 'system' && inTutorial()) {
    ob.resume()
  }
  const s = useOnboardingStore.getState()
  if (s.status !== 'active' || !s.currentStep) return

  for (let guard = 0; guard < 12; guard++) {
    const cur = useOnboardingStore.getState().currentStep
    if (!cur) return
    const def = stepById(cur)
    if (def.manual) return
    const ctx = currentContext()
    if (!def.done(ctx)) return
    completeStep(cur)
  }
}

/**
 * 一步完成：记完成、消费信号、前进。manual 的步骤由 coachmark 按钮调这里。
 *
 * `via`：`done` = 完成条件真的满足了（引擎评估 / 欢迎页与「继续」按钮）；
 * `skipped` = 用户点了「跳过此步」。状态机两者同样处理，**遥测只记前者**——
 * 跳过不是完成。教程整个走完（最后一步之后）另记一条 `tutorial_completed`。
 */
export function completeStep(id: StepDef['id'], via: 'done' | 'skipped' = 'done'): void {
  const ob = useOnboardingStore.getState()
  const def = stepById(id)
  ob.markStep(id)
  for (const k of def.consumes ?? []) signals[k] = 0
  if (via === 'done') {
    captureTelemetry('tutorial_step_completed', {
      step_id: id,
      tutorial_version: ONBOARDING_FLOW_VERSION,
    })
  }
  const next = nextStepId(id)
  if (next) ob.goTo(next)
  else {
    ob.complete()
    captureTelemetry('tutorial_completed', { tutorial_version: ONBOARDING_FLOW_VERSION })
  }
}

type StepDef = ReturnType<typeof stepById>

/** 「返回上一步」：只挪指针；完成记录与信号都不动 */
export function backStep(): void {
  useOnboardingStore.getState().back()
}

/** 「跳过此步」：当作完成处理（用户明确说了不做） */
export function skipStep(): void {
  const cur = useOnboardingStore.getState().currentStep
  if (cur) completeStep(cur, 'skipped')
}

/**
 * 启动引擎。幂等：已经在跑就回同一个 stop。
 * 订阅随 Workspace 生命周期清理（App 里 `useEffect` 的 cleanup 调 stop）。
 */
export function startOnboardingEngine(): () => void {
  if (stop) return stop
  signals = { ...EMPTY_SIGNALS }
  const unsubs = [
    onActivity(onSignal),
    useDocumentStore.subscribe(schedule),
    useUiStore.subscribe(schedule),
    useSelectionStore.subscribe(schedule),
    useWorkspaceStore.subscribe(schedule),
    useValidationStore.subscribe(schedule),
    useRenderStore.subscribe(schedule),
    useProjectStore.subscribe(schedule),
    useOnboardingStore.subscribe(schedule),
  ]
  schedule()
  stop = () => {
    for (const u of unsubs) u()
    stop = null
    signals = { ...EMPTY_SIGNALS }
    metaRequested = false
  }
  return stop
}

/** 测试用：把累计的信号清零 */
export function resetSignalsForTest(): void {
  signals = { ...EMPTY_SIGNALS }
}
