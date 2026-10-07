import { useEffect, useRef, useState, type FocusEvent, type ReactNode } from 'react'
import { perfCount } from '@/perf/core'
import { useTranslation } from 'react-i18next'
import { Check, CircleAlert, Copy, Info, Lightbulb, LoaderCircle, X } from '@/components/ui/icons'
import { Button } from '@/components/ui/Button'
import { ICON_SIZE } from '@/components/ui/Icon'
import { SwapText } from '@/components/ui/SwapText'
import { runUndoRedo } from '@/hooks/useKeyboard'
import { hintDismissTimer, useHintStore } from '@/lib/onboarding/hints'
import { literal, msg, t as translate, type UiMessage } from '@/i18n'
import { useFormatMessage } from '@/i18n/react'
import type { DismissTimer } from '@/lib/dismissTimer'
import { DURATION, usePresence } from '@/lib/motion'
import { cn } from '@/lib/utils'
import { useCanvasToolbarVisible } from './CanvasToolbar'
import { useDocumentStore } from '@/store/documentStore'
import { useInteractionStore } from '@/store/interactionStore'
import { adoptedDismissTimer, useEnvStore, type AdoptedEnvironment } from '@/store/envStore'
import {
  autofillDismissTimer,
  useScriptInputStore,
  type AutofillNotice,
} from '@/store/scriptInputStore'
import { statusDismissTimer, useUiStore, type StatusTone } from '@/store/uiStore'
import { userEnvironmentName } from '@/lib/userEnvironmentText'
import { useWorkspaceStore } from '@/store/workspace'

/**
 * 底部不再有常驻状态栏。这里是两块按需出现的浮层：
 * - CanvasHud：左下角，**只有工具提示**（非选择工具激活时）。拖动中的几何读数（W × H / X, Y / Δ）
 *   2026-10-07 起贴着选区画（`canvas/MeasureChip`），不在左下角——此前移动时这里报的还是指针坐标；
 * - NotificationRail：状态 / 操作提示 / 「刚为编辑加入」等几种通知同一条轨（二审 D1）；带 aria-live。
 * 两者都挂在画布列内部，不占布局高度。
 */

/**
 * 工具提示：落在画布上就是浮层——单行浮动条 = 胶囊 + `shadow-pop`、不画实色边（浮动外观三档，
 * 2026-10-07 设计审计 §10.1；此前是圆角 10 的方盒，与同一角落的 toast 两种浮盒）。
 */
const HUD_BOX =
  'inline-flex min-h-7 max-w-full items-center rounded-full bg-surface px-3 py-1 text-sm text-ink-2 shadow-pop'

export function CanvasHud() {
  perfCount('render.CanvasHud')
  const { t } = useTranslation('workspace')
  const kind = useInteractionStore((s) => s.kind)
  const nudge = useInteractionStore((s) => s.nudge)
  const tool = useUiStore((s) => s.tool)
  const toolbarUp = useCanvasToolbarVisible()

  // 拖动 / 微调进行中不说工具提示：那时读数在选区旁边（MeasureChip），左下角安静
  const interacting = kind !== 'none' || !!nudge
  const hint = !interacting && tool !== 'select' ? t(`toolHint.${tool}`) : null
  if (!hint) return null

  return (
    <div
      data-canvas-hud
      // 左下角位：12px 内距；画布底部有浮动工具条时抬到它上面去（读数 / 提示与工具条同在底边会叠在一起）
      className={cn(
        'pointer-events-none absolute left-3 z-sticky flex max-w-[calc(100%-1.5rem)] flex-col items-start',
        toolbarUp ? 'bottom-16' : 'bottom-3',
      )}
      aria-hidden
    >
      <p className={HUD_BOX}>{hint}</p>
    </div>
  )
}

/**
 * 一条通知的形态。几种来源共用（二审 D1）：
 *   - 普通的（状态 / 提示 / 加入说明……）：图标 + 一句话 + 至多一个动作 / 关闭，**胶囊**（单行浮动条，
 *     浮动外观三档，2026-10-07 设计审计 §10.1）；
 *   - 错误：多行浮动面板 = 圆角 12，左侧一道 danger 锚点色竖条 + 标题 + 至多两行正文 + 「复制详情」——
 *     报错原文常常很长（路径、后端给的句子），此前整段塞进一行胶囊里截不断也读不完。
 *
 * 会自己走的那几种带着各自的 `timer`：指针停在上面、焦点落在它的按钮上就不走表
 * （宪法第二十三节）。按住的原因记在 ref 里，**卸载时一并放开**——用户点 × 把它关掉时指针还在
 * 上面，pointerleave 不会再来一次，不放开的话下一条状态永远不走。
 */
function Toast({
  tone,
  icon,
  text,
  action,
  onClose,
  closeLabel,
  state,
  timer,
  order,
  ...rest
}: {
  tone: 'info' | 'error' | 'hint'
  icon: ReactNode
  text: string
  action?: { label: string; onClick: () => void }
  onClose?: () => void
  closeLabel?: string
  state: 'open' | 'closed'
  /** 自动收起的计时器；不自己走的那种（错误、加入说明）不传 */
  timer?: DismissTimer
  /** 出现的先后（越新越大）：轨里按它排，最新的那条最靠近底边 */
  order: number
} & Record<`data-${string}`, string | undefined>) {
  const held = useRef(new Set<string>())
  const grip = (reason: string) => {
    if (!timer) return
    held.current.add(reason)
    timer.hold(reason)
  }
  const drop = (reason: string) => {
    if (!timer) return
    held.current.delete(reason)
    timer.release(reason)
  }
  useEffect(
    () => () => {
      for (const reason of held.current) timer?.release(reason)
      held.current.clear()
    },
    [timer],
  )
  const error = tone === 'error'
  return (
    <div
      {...rest}
      data-state={state}
      data-toast-layout={error ? 'panel' : 'pill'}
      onPointerEnter={() => grip('pointer')}
      onPointerLeave={() => drop('pointer')}
      onFocus={() => grip('focus')}
      onBlur={(e: FocusEvent<HTMLDivElement>) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) drop('focus')
      }}
      style={{ order }}
      className={cn(
        // 浮层不画实色 border（宪法第一节）：环在 shadow-pop 里。字 12 / ink（2026-09-15 打磨 N2）。
        // 一种高度 36：带动作的那条由 28 的按钮 + py 4 撑到 36，不带动作的靠 min-h 补齐（N3）
        'pointer-events-auto relative flex max-w-[520px] gap-2 bg-surface text-sm text-ink shadow-pop',
        error
          ? 'items-start overflow-hidden rounded-lg py-2.5 pl-4 pr-2'
          : 'min-h-9 items-center rounded-full py-1 pl-3.5 pr-1.5',
        'data-[state=open]:animate-rise-in data-[state=closed]:animate-rise-out',
      )}
    >
      {error && <span aria-hidden data-toast-bar className="absolute inset-y-0 left-0 w-1 bg-danger" />}
      <span className={cn('flex shrink-0', error && 'pt-px')}>{icon}</span>
      {error ? (
        <ErrorBody text={text} />
      ) : (
        /* 同一条 toast 换文字时原位换（旧字退、新字进），不硬切 */
        <SwapText className="min-w-0 flex-1" text={text} />
      )}
      {action && (
        <Button variant="ghost" size="sm" className="shrink-0 text-ink" onClick={action.onClick}>
          {action.label}
        </Button>
      )}
      {onClose && (
        <Button
          size="icon-xs"
          onClick={onClose}
          aria-label={closeLabel}
          className="shrink-0 text-ink-3 hover:text-ink"
        >
          <X size={ICON_SIZE.sm} />
        </Button>
      )}
    </div>
  )
}

/** 错误 toast 的正文：一行标题、至多两行原文（全文在 title 与「复制详情」里） */
function ErrorBody({ text }: { text: string }) {
  const { t } = useTranslation('workspace')
  const [copied, setCopied] = useState(false)
  useEffect(() => {
    if (!copied) return
    const id = setTimeout(() => setCopied(false), 1500)
    return () => clearTimeout(id)
  }, [copied])
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-0.5">
      <p className="font-medium text-danger-content">{t('status.errorTitle')}</p>
      <SwapText className="min-w-0" textClassName="line-clamp-2 break-words text-ink-2" text={text} title={text} />
      <div className="-ml-2 pt-0.5">
        <Button
          variant="ghost"
          size="sm"
          data-copy-details
          onClick={() => {
            void navigator.clipboard?.writeText(text).then(
              () => setCopied(true),
              () => {},
            )
          }}
        >
          {copied ? <Check size={ICON_SIZE.sm} aria-hidden /> : <Copy size={ICON_SIZE.sm} aria-hidden />}
          {t(copied ? 'status.copiedDetails' : 'status.copyDetails')}
        </Button>
      </div>
    </div>
  )
}

/**
 * 状态 toast 的图标按语气取（`StatusTone`，2026-10-07 审计 P0）：只有报告「做成了」的那句打 ✓，
 * 进行中的转圈（与 `Button` 忙碌态同一个 LoaderCircle），其余中性的一句给 Info——此前
 * 「正在构建…」也打勾。颜色除错误外都是 ink-3：图标只分语气，不抢字的戏；错误用 danger 锚点。
 */
function StatusIcon({ tone }: { tone: StatusTone }) {
  if (tone === 'error') return <CircleAlert size={ICON_SIZE.sm} className="shrink-0 text-danger" aria-hidden />
  if (tone === 'done') return <Check size={ICON_SIZE.sm} className="shrink-0 text-ink-3" aria-hidden />
  if (tone === 'progress')
    return <LoaderCircle size={ICON_SIZE.sm} className="shrink-0 animate-spin text-ink-3" aria-hidden />
  return <Info size={ICON_SIZE.sm} className="shrink-0 text-ink-3" aria-hidden />
}

/**
 * 通知轨里每条的「出现先后」（越新越大）：轨按它排（CSS `order`，DOM 顺序不动——读屏区与退场动画都不受影响），
 * **最新的一条最靠近底边**（2026-10-07 设计审计 §10.1；此前按来源写死顺序，新来的状态有时排在旧提示上面）。
 * 身份变了（新的一条状态 / 新的提示 token）才算新的一条；同一条换字不挪位置。
 */
function useArrivalOrder() {
  const seq = useRef(0)
  const seen = useRef(new Map<string, { id: unknown; n: number }>())
  return (slot: string, id: unknown): number => {
    if (id == null || id === false) return 0
    const cur = seen.current.get(slot)
    if (cur && cur.id === id) return cur.n
    const n = ++seq.current
    seen.current.set(slot, { id, n })
    return n
  }
}

/**
 * 通知轨：底部居中**一条**，最多两条叠着（新的在下、靠近底边；旧的顺延到上面），
 * 三种来源同一种盒子（2026-09-14 二审 D1）：
 *   - 状态（`uiStore.status`）：普通状态 4.5s 自己走，错误保留到用户关闭；
 *   - 操作提示（`useHintStore`）：可关、到时自己走；
 *   （「自己走」的表在指针停在上面、焦点在它的按钮上、页面不可见时都停——`lib/dismissTimer`）
 *   - 「刚为编辑加入本文档」：带「撤销」动作，加进来的那一次显示、离开快速编辑即消失。
 * 此前三者各占一条轨（状态居中、提示右下、加入说明常驻在上下文栏第二行）——那行常驻说明
 * 存在的理由正是「toast 只有一个槽位、会被『渲染完成』盖掉」。HUD 留在左下：它是读数不是消息。
 * aria-live 的契约不变：状态区 `data-status-live`（e2e 认它，不认 role=status）、错误 assertive；
 * 提示区只有 aria-live 没有 role（`role=status` 全产品只留状态区那一个）。
 */
export function NotificationRail() {
  const { t } = useTranslation('workspace')
  const fmt = useFormatMessage()
  const status = useUiStore((s) => s.status)
  const tone = useUiStore((s) => s.statusTone)
  // 退场那 90ms 里 status 已经是 null 了，得把最后一条文案留着播完，
  // 否则会看到一个空壳滑下去。留的是**描述符**，不是翻好的字符串：
  // 退场期间切语言也跟着换。
  const last = useRef<{ status: UiMessage | null; tone: typeof tone }>({
    status: null,
    tone: 'info',
  })
  useEffect(() => {
    if (status) last.current = { status, tone }
  }, [status, tone])
  const shown = status ? { status, tone } : last.current
  const shownText = fmt(shown.status)
  const liveText = fmt(status)
  const statusPresence = usePresence(!!status, DURATION.exit)

  const hint = useHintStore((s) => s.current)
  const hintToken = useHintStore((s) => s.token)
  const dismissHint = useHintStore((s) => s.dismiss)
  const hintText = hint ? t(`hints.${hint}`) : ''
  const toolbarUp = useCanvasToolbarVisible()

  // 「编辑原图」这一次把图加进了文档（此前不在）：说出口，并给撤销；回排版 / 撤销即消失
  const justAdded = useWorkspaceStore(
    (s) => s.addedForEdit !== null && s.addedForEdit === s.activePanelId,
  )
  const addedPresence = usePresence(justAdded, DURATION.exit)
  // 「移除」= 撤销加入那一步；只在撤销栈还停在加入那一刻时给（之后再改了别的，撤销撤的就是
  // 别的了）。名字用「移除」不用「撤销」：顶栏已有一颗「撤销」，同名两颗读屏与用例都分不清
  const addedDepth = useWorkspaceStore((s) => s.addedForEditDepth)
  const historyDepth = useDocumentStore((s) => s.past.length)
  const canRemoveAdded = justAdded && historyDepth === addedDepth

  /**
   * **最多两条**（二审 D1）。三种来源此前各自 `usePresence`、互不让位，双击素材卡进
   * 快速编辑时实测三条同时在屏（加入说明 + 操作提示 + 「正在构建…」）。
   *
   * 让位顺序：加入说明最高（它带着「移除」这个一次性出口，错过就找不回来），状态次之
   * （它报告的是刚发生的事），**操作提示第一个让**——提示本来就会重来（`hints` 有 seen
   * 记录，没被看见的那条下次还会出），少说一次不丢信息。让位只是不渲染，不调 `dismiss`：
   * 状态那条走完之后提示自己就回来了。
   */
  // 「已改用你的环境」（ADR 0079）：与加入说明同一档——它带着「改回」这个一次性出口。
  // 退场那 90ms 里载荷已经清掉了，留最后一份播完（同 status 那条）
  const adopted = useEnvStore((s) => s.adoptedEnvironment)
  const lastAdopted = useRef<AdoptedEnvironment | null>(null)
  if (adopted) lastAdopted.current = adopted
  const adoptedHasSlot = !(justAdded && !!status)
  const adoptedPresence = usePresence(!!adopted && adoptedHasSlot, DURATION.exit)
  const adoptedShown = adopted ?? lastAdopted.current
  const adoptedText = adoptedShown
    ? translate('engine.userEnvAdopted', {
        ns: 'errors',
        name: userEnvironmentName(adoptedShown),
        version: adoptedShown.python_version,
      })
    : ''
  const revertAdopted = () => {
    void useEnvStore
      .getState()
      .revertAdoptedEnvironment()
      .then((error) =>
        useUiStore
          .getState()
          .setStatus(
            error ? literal(error) : msg('engine.userEnvReverted', undefined, 'errors'),
            error ? 'error' : 'done',
          ),
      )
  }

  // 「已用上次的答案：…（修改）」（ADR 0099 §四）：与「已改用你的环境」同一档——带一个一次性出口。
  // 两条同时在时让它让位（最多两条的规则），它 12 秒就走，环境那条更重要
  const autofilled = useScriptInputStore((s) => s.autofilled)
  const lastAutofilled = useRef<AutofillNotice | null>(null)
  if (autofilled) lastAutofilled.current = autofilled
  const autofillHasSlot = !adopted && !(justAdded && !!status)
  const autofillPresence = usePresence(!!autofilled && autofillHasSlot, DURATION.exit)
  const autofillShown = autofilled ?? lastAutofilled.current
  const autofillText = autofillShown
    ? translate('scriptInput.autofilled', { ns: 'dialogs', answer: autofillShown.answer })
    : ''
  const changeAutofilled = () => {
    const script = autofillShown?.script
    useScriptInputStore.getState().dismissAutofilled()
    if (script) {
      void useScriptInputStore.getState().loadAnswers()
      useScriptInputStore.getState().openManager(script)
    }
  }

  const hintHasSlot = [justAdded, !!adopted || !!autofilled, !!status].filter(Boolean).length < 2
  const hintPresence = usePresence(!!hint && hintHasSlot, DURATION.exit)

  // 出现的先后：最新的一条最靠近底边（见 useArrivalOrder）
  const arrival = useArrivalOrder()
  const addedOrder = arrival('added', addedPresence.mounted && `added:${addedDepth}`)
  const adoptedOrder = arrival('adopted', adoptedPresence.mounted && adoptedShown?.token)
  const autofillOrder = arrival('autofill', autofillPresence.mounted && autofillShown?.token)
  const hintOrder = arrival('hint', hintPresence.mounted && hint && hintToken)
  const statusOrder = arrival('status', statusPresence.mounted && shown.status)

  return (
    <div
      // 画布底部有浮动工具条时整列抬到它上面去（toast 居中、工具条也居中，不抬就叠在一起）
      className={cn(
        'pointer-events-none absolute inset-x-0 z-canvas-chrome flex flex-col items-center gap-1.5 px-4',
        toolbarUp ? 'bottom-16' : 'bottom-3',
      )}
    >
      {/* aria-live 常驻在 DOM 里，读屏器才能捕捉内容变化。
          `data-status-live` 是这块播报区的**稳定机器标识**：`role="status"` 全产品有十几个
          产出点（快速编辑那行常驻说明、素材库、导出面板、问题面板……），所以
          `[role="status"]` 取第一个拿到的是「文档里排在最前的那个 status」，不是
          「应用刚说了什么」。要问后者的（e2e）一律认这个属性，见 `web/AGENTS.md`。 */}
      {/* `data-status-key`：刚说的那句话的消息键（e2e 认它，不认译文——换语言照样判得了） */}
      <div
        aria-live="polite"
        role="status"
        data-status-live
        data-status-key={tone !== 'error' && status ? status.key : undefined}
        className="sr-only"
      >
        {tone !== 'error' ? liveText : ''}
      </div>
      <div aria-live="assertive" role="alert" className="sr-only">
        {tone === 'error' ? liveText : ''}
      </div>
      <div aria-live="polite" className="sr-only">
        {hintText}
      </div>
      <div aria-live="polite" className="sr-only">
        {adopted ? adoptedText : ''}
      </div>
      <div aria-live="polite" className="sr-only">
        {autofilled ? autofillText : ''}
      </div>
      {/* 显示顺序 = 出现的先后（`order`），最新的最靠近底边；DOM 顺序固定 */}
      {addedPresence.mounted && (
        <Toast
          order={addedOrder}
          tone="info"
          state={addedPresence.state}
          data-fast-edit-added-note=""
          icon={<Info size={ICON_SIZE.sm} className="shrink-0 text-ink-3" aria-hidden />}
          text={t('fastEdit.addedForEdit')}
          action={canRemoveAdded ? { label: t('fastEdit.removeAdded'), onClick: () => runUndoRedo(false) } : undefined}
        />
      )}
      {adoptedPresence.mounted && adoptedShown && (
        <Toast
          key={adoptedShown.token}
          order={adoptedOrder}
          tone="info"
          state={adoptedPresence.state}
          data-environment-adopted={adoptedShown.source}
          timer={adoptedDismissTimer}
          icon={<Info size={ICON_SIZE.sm} className="shrink-0 text-ink-3" aria-hidden />}
          text={adoptedText}
          action={{ label: translate('engine.userEnvRevert', { ns: 'errors' }), onClick: revertAdopted }}
          onClose={() => useEnvStore.getState().dismissAdoptedEnvironment()}
          closeLabel={translate('actions.close')}
        />
      )}
      {autofillPresence.mounted && autofillShown && (
        <Toast
          key={`autofill-${autofillShown.token}`}
          order={autofillOrder}
          tone="info"
          state={autofillPresence.state}
          data-script-input-autofilled=""
          timer={autofillDismissTimer}
          icon={<Info size={ICON_SIZE.sm} className="shrink-0 text-ink-3" aria-hidden />}
          text={autofillText}
          action={{ label: translate('scriptInput.autofilledAction', { ns: 'dialogs' }), onClick: changeAutofilled }}
          onClose={() => useScriptInputStore.getState().dismissAutofilled()}
          closeLabel={translate('actions.close')}
        />
      )}
      {hintPresence.mounted && hint && (
        <Toast
          key={hintToken}
          order={hintOrder}
          tone="hint"
          state={hintPresence.state}
          data-onboarding-hint={hint}
          timer={hintDismissTimer}
          icon={<Lightbulb size={ICON_SIZE.sm} className="shrink-0 text-ink-3" aria-hidden />}
          text={hintText}
          onClose={dismissHint}
          closeLabel={translate('actions.close')}
        />
      )}
      {statusPresence.mounted && (
        <Toast
          order={statusOrder}
          tone={shown.tone === 'error' ? 'error' : 'info'}
          state={statusPresence.state}
          timer={statusDismissTimer}
          // 语气认这个钩子（用例 / e2e），不认图标的类名
          data-status-tone={shown.tone}
          icon={<StatusIcon tone={shown.tone} />}
          text={shownText}
          onClose={shown.tone === 'error' ? () => useUiStore.getState().setStatus(null) : undefined}
          closeLabel={t('status.dismissError')}
        />
      )}
    </div>
  )
}
