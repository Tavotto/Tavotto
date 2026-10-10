import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal, flushSync } from 'react-dom'
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
  /** 锚点自己的圆角（px）：高亮环的圆角 = 它 + 环与锚点之间那 4px（外层 = 内层 + 间距） */
  radius: number
}

/** 环与锚点之间的间距（px）：环画在锚点外面 4px 处 */
const RING_GAP = 4

/**
 * 环的圆角类：锚点圆角 + 间距，落到最近的圆角 token 上（圆角只走 token，门禁不许内联 borderRadius）。
 * 体系里的圆角就是 4 / 6 / 8 / 12 / 16 / full，「外层 = 内层 + 间距」落在 token 上几乎是精确的（8 → 12、12 → 16）
 */
const RING_RADIUS: [number, string][] = [
  [4, 'rounded-xs'],
  [6, 'rounded-sm'],
  [8, 'rounded-md'],
  [12, 'rounded-lg'],
  [16, 'rounded-panel'],
]
function ringRadiusClass(anchorRadius: number): string {
  if (anchorRadius >= 999) return 'rounded-full'
  const want = anchorRadius + RING_GAP
  return RING_RADIUS.reduce((best, cur) => (Math.abs(cur[0] - want) < Math.abs(best[0] - want) ? cur : best))[1]
}

/** 锚点的圆角：取左上角那一个（锚点几乎都是四角同圆角的控件 / 卡片）；量不到按 0 */
function radiusOf(el: Element | null): number {
  if (!el) return 0
  const cs = getComputedStyle(el)
  // 简写没展开的环境（jsdom）里长写是空串：退回简写
  const v = parseFloat(cs.borderTopLeftRadius || cs.borderRadius)
  return Number.isFinite(v) ? v : 0
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
  if (spec.kind === 'none') return { box: null, container: bodyContainer(), el: null, radius: 0 }
  if (spec.kind === 'selector') {
    const el = document.querySelector(spec.selector)
    if (!el) return null
    const box = boxOf(el)
    if (!box) return null
    const dialog = el.closest<HTMLElement>('[role="dialog"]:not([data-onboarding-coachmark])')
    return { box, container: dialog ?? bodyContainer(), el, radius: radiusOf(el) }
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
    // 图内元素没有自己的圆角（manifest 里的一块 bbox）：环用最小那一档
    radius: 0,
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

/** 上一张卡片卸载时焦点在不在它里面（换步骤 = `ActiveStep` 按 key 重挂，状态只能放在模块里传过去） */
const handoff = { pending: false }

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

  // 换步骤时的焦点交接（2026-10-07 设计审计 §10.2）：用户在上一张卡片里按了「跳过 / 返回 / 主动作」，
  // 那张卡片随步骤卸掉、焦点摔到 body——键盘用户当场失去位置。焦点**原本在卡片里**才交接到新卡片上；
  // 在画布 / 输入框里的焦点不抢（教程只是贴在旁边说话）
  useLayoutEffect(() => {
    const card = cardRef.current
    if (handoff.pending && card) card.focus({ preventScroll: true })
    handoff.pending = false
    return () => {
      // 卸载那一刻焦点还在这张卡片里、而且是**直接换到下一步**（教程仍 active、步骤已变）：下一张接手。
      // 暂停 / 完成 / 跳过整个教程也会卸掉这张卡片，那不是交接——不记下，否则过一阵「继续教程」时
      // 新挂上的卡片会把用户已经放在别处的焦点抢走（#831 Codex P2）
      const s = useOnboardingStore.getState()
      handoff.pending =
        s.status === 'active' &&
        s.currentStep !== stepId &&
        !!card &&
        card.contains(document.activeElement)
    }
    // `ActiveStep` 按 stepId 重挂，stepId 在一个实例里不变：这仍是「挂载 / 卸载各一次」
  }, [stepId])

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
    // 锚点被 DOM 变动**挤动**：素材库重取时网格上方冒出一行「正在检查新文件…」、整排卡片下移
    // 24 px，比卡片与锚点之间的间距大。那一行是组件自己的 state，不经过上面订阅的任何 store；
    // 只靠 300 ms 的兜底重测，停着的卡片要压着锚点底部（文件名那一行）最长 300 ms、接得住点击，
    // 滑行中的卡片则撞上被挤进路径的锚点（windows-exe-smoke，PR #711 / #717 的 run）。所以在
    // 插删节点的那个微任务里当场重测并**同步提交**（`flushSync`）：不把重排交给 React 的调度任务，
    // 慢机器上那个任务可能落在下一帧之后——浏览器画下一帧之前卡片就已让开。
    // 自己的节点也会触发（落位挂上高亮环），重测结果不变时 React 不碰 DOM，不会自激
    const mo = new MutationObserver(() => flushSync(refresh))
    mo.observe(document.body, { childList: true, subtree: true })
    return () => {
      mo.disconnect()
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
  const progress = index >= 0 && index < REAL_STEPS ? ob('progress', { n: index + 1, total: REAL_STEPS }) : null
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
    stepId === 'done'
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
    zIndex: 'var(--z-onboarding)',
    // 时长与曲线只来自 token（宪法第七节）：此前是写死的 120ms + ease-out（打磨 G1）
    transition:
      reduced || !placement?.glide
        ? undefined
        : `left ${DURATION.fast}ms ${EASE_STANDARD}, top ${DURATION.fast}ms ${EASE_STANDARD}`,
    ...(moving ? { pointerEvents: 'none' as const } : {}),
  }
  // 高亮环：对话框里也画（2026-10-07 设计审计 §10.2：此前锚点在导出对话框里时一圈都没有）。
  // 在对话框里换算成对话框内的坐标、跟卡片一起 portal 进去；圆角跟着锚点走（锚点圆角 + 4）
  const frame = inDialog ? container.getBoundingClientRect() : null
  const ring = measured?.box
    ? {
        left: measured.box.x - (frame?.left ?? 0) - RING_GAP,
        top: measured.box.y - (frame?.top ?? 0) - RING_GAP,
        width: measured.box.w + RING_GAP * 2,
        height: measured.box.h + RING_GAP * 2,
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
            'pointer-events-none z-onboarding-ring border-2 border-accent',
            inDialog ? 'absolute' : 'fixed',
            ringRadiusClass(measured?.radius ?? 0),
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
        step={index >= 0 && index < REAL_STEPS ? { n: index + 1, total: REAL_STEPS } : null}
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
