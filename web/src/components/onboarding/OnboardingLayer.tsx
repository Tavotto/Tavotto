import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { t as translate } from '@/i18n'
import { prefersReducedMotion } from '@/lib/motion'
import { backStep, completeStep, currentContext, skipStep } from '@/lib/onboarding/flow'
import {
  COACHMARK_MARGIN,
  offscreen,
  placeCentered,
  placeCoachmark,
  unionBoxes,
  type Box,
  type CoachmarkSide,
} from '@/lib/onboarding/position'
import { STEP_IDS, type StepId } from '@/lib/onboarding/stepIds'
import { stepById, type AnchorSpec, type StepContext } from '@/lib/onboarding/steps'
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

const REAL_STEPS = STEP_IDS.length - 2 // 去掉 welcome / done

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
  const [placement, setPlacement] = useState<{ x: number; y: number; side: CoachmarkSide | 'center' } | null>(null)
  const [waitedOut, setWaitedOut] = useState(false)
  const revealed = useRef(false)

  // 每次相关状态变化重新组装上下文并重测；再加一个兜底的低频重测
  const refresh = useCallback(() => {
    const next = currentContext()
    setCtx(next)
    const spec = def.anchor(next)
    const m = measure(spec)
    setMeasured(m)
    // 目标不在 DOM 里（折叠的侧栏 / 还没重排的属性页），或者在画布上但被平移到了
    // 工作区可见范围之外：第一次先让步骤把它露出来（只做一次，不写偏好）
    if (!revealed.current && def.reveal && (!m || hiddenInStage(m))) {
      revealed.current = true
      def.reveal(next)
    }
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
    ]
    window.addEventListener('resize', refresh)
    window.addEventListener('scroll', refresh, true)
    const tick = window.setInterval(refresh, TICK_MS)
    const wait = window.setTimeout(() => setWaitedOut(true), WAIT_MS)
    return () => {
      for (const u of unsubs) u()
      window.removeEventListener('resize', refresh)
      window.removeEventListener('scroll', refresh, true)
      window.clearInterval(tick)
      window.clearTimeout(wait)
    }
  }, [refresh])

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
    if (!measured?.box) {
      const c = placeCentered(size, { w: frame.w, h: frame.h })
      setPlacement({ x: c.x, y: c.y, side: 'center' })
      return
    }
    const local: Box = { ...measured.box, x: measured.box.x - frame.x, y: measured.box.y - frame.y }
    const p = placeCoachmark(local, size, { w: frame.w, h: frame.h }, { margin: COACHMARK_MARGIN })
    setPlacement(p)
  }, [measured, ctx])

  const missing = measured === null
  const showMissing = missing && waitedOut

  const variant = def.variant ? def.variant(ctx) : stepId
  const title = ob(`steps.${variant}.title`)
  const body = ob(`steps.${variant}.body`)
  const index = STEP_IDS.indexOf(stepId)
  const progress = index >= 1 && index <= REAL_STEPS ? ob('progress', { n: index, total: REAL_STEPS }) : null
  const altDone = def.altDone?.(ctx) ?? false

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
          : null
  const secondary =
    stepId === 'done'
      ? {
          label: ob('openOwnProject'),
          onClick: () => {
            useOnboardingStore.getState().complete()
            useProjectStore.setState({ phase: 'none' })
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
    transition: reduced || !placement ? undefined : 'left 120ms ease-out, top 120ms ease-out',
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
        body={body}
        progress={progress}
        side={placement?.side ?? 'center'}
        primary={primary}
        secondary={secondary}
        onBack={index > 0 && stepId !== 'done' ? backStep : null}
        onSkip={!def.manual ? skipStep : null}
        onClose={onClose}
        onKeyDown={onKeyDown}
        note={
          showMissing ? (
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
