import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { DURATION, EASE_STANDARD, prefersReducedMotion } from '@/lib/motion'
import { backStep, completeStep, currentContext, skipStep } from '@/lib/onboarding/flow'
import {
  COACHMARK_MARGIN,
  offscreen,
  placeCentered,
  placeCoachmark,
  shouldGlide,
  unionBoxes,
  type Box,
  type CoachmarkSide,
} from '@/lib/onboarding/position'
import { REAL_STEP_IDS, STEP_IDS, type StepId } from '@/lib/onboarding/stepIds'
import { stepById, type AnchorSpec, type Precondition, type StepContext } from '@/lib/onboarding/steps'
import { cn } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { useOnboardingStore } from '@/store/onboardingStore'
import { useProjectStore } from '@/store/projectStore'
import { useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { useValidationStore } from '@/store/validationStore'
import { useWorkspaceStore } from '@/store/workspace'
import { Coachmark } from './Coachmark'

/**
 * 教程层：按当前步骤找锚点、量位置、画 coachmark 与高亮环。
 *
 * ### 它不做的事
 *
 * 不判完成（引擎 `lib/onboarding/flow.ts` 判）、不改文档、不改用户偏好、
 * **没有全屏遮罩**——用户随时能点真实界面，coachmark 只是贴在旁边说话。
 *
 * ### 锚点
 *
 * 目标用稳定的 `data-*` 选择器或 manifest bbox 找（`steps.ts`）。找不到时先等
 * `WAIT_MS`（属性页正在重排、抽屉正在展开），超时再说「找不到目标」并给
 * 返回 / 跳过——绝不锁死界面。目标在视口外先滚进来；藏在折叠的侧栏里时
 * 步骤自己的 `reveal()` 把它临时露出来（不写偏好）。
 *
 * ### 前置状态（审计 T36）
 *
 * 找锚点之前先问步骤的 `precondition(ctx)`。不满足时**不找锚点、不「等待」**：
 * 正文照常说这一步要做什么，状态行说清缺什么（「这一步要在 Fig2_correlation 的
 * 图内编辑里进行」），主按钮是补上它的那一个真实动作（`openFastEdit` /
 * `addFigureToLayout` / `returnToLayout`），「跳过此步」照旧。「正在等待目标出现」
 * 只在前置满足之后的短暂窗口里出现，等待计时从那一刻起算。
 *
 * ### 落位
 *
 * 锚点在普通页面上：portal 到 `body`，`position: fixed`。
 * 锚点在一个 Radix 对话框里（导出面板）：portal 进那个对话框的内容节点，
 * `position: absolute`——模态对话框会把外面的指针事件与焦点都挡掉，coachmark
 * 只有进到同一层才点得到、Tab 得到。
 *
 * ### 键盘 / 读屏
 *
 * Esc（焦点在 coachmark 里时）= 暂停；Tab 顺序返回 → 跳过 → 主动作 → 关闭；
 * 换步骤时 `aria-live` 读一遍「第几步、标题、正文」。reduced motion 下不播
 * 位移动画、高亮环不脉动。
 */

/** 目标不在时等多久再说「找不到」 */
export const WAIT_MS = 1500
/** 兜底重测周期（抽屉动画、SVG 换代这类没有 store 变化的重排） */
const TICK_MS = 300

const REAL_STEPS = REAL_STEP_IDS.length
const NONE: AnchorSpec = { kind: 'none' }
const OK: Precondition = { ok: true }

interface Measured {
  box: Box | null
  container: HTMLElement
  /** 锚点 DOM 节点（有的话），用来滚进视野 */
  el: Element | null
}

const bodyContainer = () => document.body

/** `display: contents` 的节点自己没有盒子：并集子节点 */
function boxOf(el: Element): Box | null {
  const r = el.getBoundingClientRect()
  if (r.width > 0 || r.height > 0) return { x: r.left, y: r.top, w: r.width, h: r.height }
  return unionBoxes(
    [...el.children].map((c) => {
      const cr = c.getBoundingClientRect()
      return { x: cr.left, y: cr.top, w: cr.width, h: cr.height }
    }),
  )
}

/** 找锚点。回 `null` = 此刻不在 DOM 里 */
function measure(spec: AnchorSpec): Measured | null {
  if (spec.kind === 'none') return { box: null, container: bodyContainer(), el: null }
  if (spec.kind === 'selector') {
    const el = document.querySelector(spec.selector)
    if (!el) return null
    const box = boxOf(el)
    if (!box) return null
    const dialog = el.closest<HTMLElement>('[role="dialog"]:not([data-onboarding-coachmark])')
    return { box, container: dialog ?? bodyContainer(), el }
  }
  const host = document.querySelector(`[data-element-svg="${CSS.escape(spec.panelId)}"]`)
  if (!host) return null
  const r = host.getBoundingClientRect()
  if (!(r.width > 0 && r.height > 0)) return null
  const [fx, fy, fw, fh] = spec.bbox
  return {
    box: { x: r.left + fx * r.width, y: r.top + fy * r.height, w: fw * r.width, h: fh * r.height },
    container: bodyContainer(),
    el: host,
  }
}

/**
 * 画布对象的 DOM 盒子不受工作区裁剪（`getBoundingClientRect` 回的是完整矩形），
 * 被平移到抽屉后面 / 视口外的对象「在 DOM 里」但用户看不见也点不到。
 * 判据：锚点在 `[data-canvas-stage]` 里，且它的盒子有一部分落在工作区矩形之外。
 */
function hiddenInStage(m: Measured): boolean {
  if (!m.el || !m.box) return false
  const stage = m.el.closest('[data-canvas-stage]')
  if (!stage) return false
  const r = stage.getBoundingClientRect()
  if (!(r.width > 0 && r.height > 0)) return false
  const b = m.box
  return b.x < r.left || b.y < r.top || b.x + b.w > r.right || b.y + b.h > r.bottom
}

const ob = (key: string, values?: Record<string, unknown>) =>
  translate(`onboarding.${key}`, { ns: 'dialogs', ...(values ?? {}) })

export function OnboardingLayer() {
  const status = useOnboardingStore((s) => s.status)
  const stepId = useOnboardingStore((s) => s.currentStep)
  if (status !== 'active' || !stepId) return null
  return <ActiveStep key={stepId} stepId={stepId} />
}

function ActiveStep({ stepId }: { stepId: StepId }) {
  useTranslation('dialogs')
  const def = stepById(stepId)
  const cardRef = useRef<HTMLDivElement>(null)
  const [ctx, setCtx] = useState<StepContext>(() => currentContext())
  const [measured, setMeasured] = useState<Measured | null>(null)
  const [placement, setPlacement] = useState<{
    x: number
    y: number
    side: CoachmarkSide | 'center'
    /** 从上一个位置滑过来，还是直接出现（`shouldGlide`：途中不许扫过锚点） */
    glide: boolean
  } | null>(null)
  // 卡片此刻**可能在**的区域（容器坐标）：停着时就是它的框；滑行中是起点区域与终点的外接
  // 矩形——半路再改道时卡片在那段直线上的某处，只拿上一段的终点当起点会漏判（Codex #654）。
  // 还没落过位是 null（挂载那一帧它在 -9999）。`dest` 是最近一次的落点
  const shown = useRef<Box | null>(null)
  const dest = useRef<Box | null>(null)
  // 正在滑：这段时间卡片不接指针（#581）。`shouldGlide` 只护着锚点，路上压过的别的可点目标
  // 它不管；滑行中的卡片一律让点击穿过去，才是「移动中的浮层不抢点击」的通用保证。
  // `moveSeq` 每滑一次 +1，让复位计时器从最后一次起算
  const [moving, setMoving] = useState(false)
  const [moveSeq, setMoveSeq] = useState(0)
  const [waitedOut, setWaitedOut] = useState(false)
  const revealed = useRef(false)

  // 每次相关状态变化重新组装上下文并重测；再加一个兜底的低频重测
  const refresh = useCallback(() => {
    const next = currentContext()
    setCtx(next)
    // 前置不满足：指着补前置的那个目标（没有就居中），不去找这一步自己的锚点
    const pre = def.precondition?.(next) ?? OK
    const anchorOf = (c: StepContext) => (pre.ok ? def.anchor(c) : (pre.anchor ?? NONE))
    let m = measure(anchorOf(next))
    // 目标不在 DOM 里（折叠的侧栏 / 还没重排的属性页），或者在画布上但被平移到了
    // 工作区可见范围之外：第一次先让步骤把它露出来（只做一次，不写偏好）。
    // 露出来之后**当场再量一次**：首次 refresh 跑在订阅挂上之前，reveal 直接
    // setState 引起的变化这一轮听不到，不补量的话要等下一个 tick 才贴上去
    if (pre.ok && !revealed.current && def.reveal && (!m || hiddenInStage(m))) {
      revealed.current = true
      def.reveal(next)
      const again = currentContext()
      setCtx(again)
      m = measure(anchorOf(again))
    }
    setMeasured(m)
  }, [def])

  useEffect(() => {
    refresh()
    const unsubs = [
      useDocumentStore.subscribe(refresh),
      useUiStore.subscribe(refresh),
      useSelectionStore.subscribe(refresh),
      useWorkspaceStore.subscribe(refresh),
      useValidationStore.subscribe(refresh),
      useRenderStore.subscribe(refresh),
      useProjectStore.subscribe(refresh),
      // 结束页的计数、返回 / 跳过之后的账都在这里变
      useOnboardingStore.subscribe(refresh),
    ]
    window.addEventListener('resize', refresh)
    window.addEventListener('scroll', refresh, true)
    const tick = window.setInterval(refresh, TICK_MS)
    return () => {
      for (const u of unsubs) u()
      window.removeEventListener('resize', refresh)
      window.removeEventListener('scroll', refresh, true)
      window.clearInterval(tick)
    }
  }, [refresh])

  const pre = def.precondition?.(ctx) ?? OK
  const blocked = !pre.ok

  // 「等待目标」的计时从**前置满足那一刻**起算：前置不满足的那段时间里根本没在找
  // 锚点，补上前置之后属性页 / 图内 SVG 还要一拍才出来，不该一到就说「找不到」
  useEffect(() => {
    if (blocked) return
    setWaitedOut(false)
    const wait = window.setTimeout(() => setWaitedOut(true), WAIT_MS)
    return () => window.clearTimeout(wait)
  }, [blocked])

  // 目标在视口外：先滚进来（只动视口，不动文档）
  useEffect(() => {
    const el = measured?.el
    const box = measured?.box
    if (!el || !box) return
    if (offscreen(box, { w: window.innerWidth, h: window.innerHeight })) {
      el.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
    }
  }, [measured?.el, measured?.box])

  // 量卡片尺寸后落位。锚点在对话框里就换算成对话框内的绝对坐标
  useLayoutEffect(() => {
    const card = cardRef.current
    if (!card) return
    const size = { w: card.offsetWidth || 300, h: card.offsetHeight || 120 }
    const container = measured?.container ?? document.body
    const inDialog = container !== document.body
    const frame: Box = inDialog
      ? (() => {
          const r = container.getBoundingClientRect()
          return { x: r.left, y: r.top, w: r.width, h: r.height }
        })()
      : { x: 0, y: 0, w: window.innerWidth, h: window.innerHeight }
    const local: Box | null = measured?.box
      ? { ...measured.box, x: measured.box.x - frame.x, y: measured.box.y - frame.y }
      : null
    const p = local
      ? placeCoachmark(local, size, { w: frame.w, h: frame.h }, { margin: COACHMARK_MARGIN })
      : { ...placeCentered(size, { w: frame.w, h: frame.h }), side: 'center' as const }
    const next: Box = { x: p.x, y: p.y, w: size.w, h: size.h }
    const glide = shouldGlide(shown.current, next, local)
    const prev = dest.current
    const moved = !!prev && (prev.x !== next.x || prev.y !== next.y)
    setPlacement({ ...p, glide })
    dest.current = next
    if (glide && moved && !prefersReducedMotion()) {
      shown.current = unionBoxes([shown.current ?? next, next])
      setMoving(true)
      setMoveSeq((n) => n + 1)
    } else if (moved || !prev) {
      // 第一次落位 / 跳过去了：上一段滑行（若有）已被打断，卡片此刻就停在落点上
      shown.current = next
      setMoving(false)
    }
  }, [measured, ctx])

  // 滑完复位：过渡结束事件为准；它可能不来（被下一次落位打断、元素被挪走），兜底计时器
  // 比过渡长 50 ms。两者都在卸载 / 换步骤时清掉（`ActiveStep` 按步骤 key 重挂），
  // 旧步骤的计时器不会在新步骤上改状态
  useEffect(() => {
    if (!moveSeq) return
    const card = cardRef.current
    const done = () => {
      shown.current = dest.current
      setMoving(false)
    }
    const onEnd = (e: TransitionEvent) => {
      if (e.target === card && (e.propertyName === 'left' || e.propertyName === 'top')) done()
    }
    card?.addEventListener('transitionend', onEnd)
    const fallback = window.setTimeout(done, DURATION.fast + 50)
    return () => {
      card?.removeEventListener('transitionend', onEnd)
      window.clearTimeout(fallback)
    }
  }, [moveSeq])

  const missing = measured === null
  const showMissing = missing && waitedOut

  const variant = def.variant ? def.variant(ctx) : stepId
  const values = def.values?.(ctx)
  const title = ob(`steps.${variant}.title`, values)
  // 前置条件没满足时正文只说「先做什么」，不同时发出这一步自己的指令（审计
  // A06 / A07：「点击图里的标题」与「先打开 Fig2_correlation」并排出现，而右栏
  // 里根本没有那张图）。标题仍是这一步的标题——用户知道自己卡在哪一步上
  const body = blocked ? ob(`precondition.${pre.reason}`, pre.values) : ob(`steps.${variant}.body`, values)
  const index = STEP_IDS.indexOf(stepId)
  const progress = index >= 1 && index <= REAL_STEPS ? ob('progress', { n: index, total: REAL_STEPS }) : null
  const altDone = !blocked && (def.altDone?.(ctx) ?? false)
  // 主动作：前置缺了 → 补前置的那颗；否则步骤自己给的（比如「加入 Fig1_kinetics」）
  const stepAction = blocked ? pre.action : def.action?.(ctx)
  const actionButton = stepAction
    ? { label: ob(`action.${stepAction.key}`, stepAction.values), onClick: stepAction.run }
    : null

  const onClose = () => useOnboardingStore.getState().pause('user')
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault()
      e.stopPropagation()
      onClose()
    }
  }

  const primary =
    stepId === 'welcome'
      ? { label: ob('start'), onClick: () => completeStep('welcome'), autoFocus: true }
      : stepId === 'done'
        ? { label: ob('explore'), onClick: () => useOnboardingStore.getState().complete(), autoFocus: true }
        : altDone
          ? { label: ob('resolvedContinue'), onClick: () => completeStep(stepId) }
          : actionButton
  const secondary =
    stepId === 'done'
      ? {
          label: ob('openOwnProject'),
          onClick: () => {
            useOnboardingStore.getState().complete()
            useProjectStore.getState().showPicker()
          },
        }
      : null

  const container = measured?.container ?? document.body
  const inDialog = container !== document.body
  const reduced = prefersReducedMotion()
  const style: React.CSSProperties = {
    position: inDialog ? 'absolute' : 'fixed',
    left: placement?.x ?? -9999,
    top: placement?.y ?? -9999,
    zIndex: 60,
    // 时长与曲线只来自 token（宪法第七节）：此前是写死的 120ms + ease-out（打磨 G1）
    transition:
      reduced || !placement?.glide
        ? undefined
        : `left ${DURATION.fast}ms ${EASE_STANDARD}, top ${DURATION.fast}ms ${EASE_STANDARD}`,
    ...(moving ? { pointerEvents: 'none' as const } : {}),
  }
  const ring =
    measured?.box && !inDialog
      ? {
          left: measured.box.x - 4,
          top: measured.box.y - 4,
          width: measured.box.w + 8,
          height: measured.box.h + 8,
        }
      : null

  return createPortal(
    <>
      {/* 读屏：换步骤读一遍；DOM 里常驻 */}
      <div aria-live="polite" className="sr-only">
        {progress ? `${progress}. ` : ''}
        {title}. {body}
      </div>
      {ring && (
        <div
          aria-hidden
          data-onboarding-ring
          className={cn(
            'pointer-events-none fixed z-[59] rounded-md border-2 border-accent',
            !reduced && 'animate-fade-in',
          )}
          style={ring}
        />
      )}
      <Coachmark
        ref={cardRef}
        id={`onboarding-${stepId}`}
        title={title}
        body={
          blocked ? (
            <span role="status" data-onboarding-precondition={pre.reason}>
              {body}
            </span>
          ) : (
            body
          )
        }
        progress={progress}
        side={placement?.side ?? 'center'}
        primary={primary}
        secondary={secondary}
        onBack={index > 0 && stepId !== 'done' ? backStep : null}
        onSkip={!def.manual ? skipStep : null}
        onClose={onClose}
        onKeyDown={onKeyDown}
        note={
          blocked ? null : showMissing ? (
            <span role="status">{ob('targetMissing')}</span>
          ) : missing ? (
            <span role="status">{ob('targetWaiting')}</span>
          ) : null
        }
        style={style}
      />
    </>,
    container,
  )
}
