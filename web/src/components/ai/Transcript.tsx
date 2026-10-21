import { useLayoutEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronRight, Pencil, RefreshCw, Sparkles, Wrench, type IconComponent } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { SwapText } from '@/components/ui/SwapText'
import { t as translate } from '@/i18n'
import { formatTime } from '@/i18n/format'
import { splitNoticeText } from '@/lib/noticeText'
import { cn } from '@/lib/utils'
import { DATA_FIGURE_TASK, sessionAgentLabel, useAiStore, type AiSession } from '@/store/aiStore'
import { Button, IconButton } from '../ui/Button'
import { Reveal } from '../ui/Field'
import { Notice } from '../ui/Notice'
import { CopyAction } from './CopyAction'
import { DiffView } from './DiffView'
import { Markdown } from './Markdown'
import { revertSession } from './revertSession'
import { formatDuration, groupEntries, spanOf, statusLabel, type Step } from './transcriptModel'

/**
 * 改图助手的转录（2026-10-07 设计审计 §6.1 / §6.2，宪法第十八节「2026-10-07 重做」）：从「一轮一张卡」改成「一段对话」。
 *
 * - **用户消息**：右对齐、自适应宽度的气泡（12px 圆角、surface-hover 底、type-reading），超过 3 行折叠；
 *   目标 · 时刻与「复制 / 重新发送」只在 hover / 键盘聚焦时浮出。
 * - **助手回答**：不装卡，ink 正文直接坐在面板上（`Markdown`）。
 * - **过程**（思考 / 动作）是环境级：默认折成一行「已思考 6s · 4 步」，图标列 hover 时换成 chevron；
 *   进行中那一行就是最新一步的原位替换（text-shimmer）。展开后每步一行（耗时右对齐），动作行可再展开看完整参数。
 * - **改了脚本**是显著级：`DiffView` 那张有边框的卡。
 * - 失败是 danger 的 `Notice`，带「重试」；状态行「Codex · 已完成 · 18s」收尾。
 * 节奏由列表容器管（`--item-gap` 8 / `--turn-gap` 16，见 AiPanel），这里的块都不带外边距。
 */

const ai = (key: string, values?: Record<string, unknown>) => translate(key, { ns: 'ai', ...(values ?? {}) })

/** 改动是直接落盘的，措辞不能像「待应用的预览」 */
function statusText(s: AiSession): string {
  if (s.task?.task_type === DATA_FIGURE_TASK) return dataFigureStatusText(s)
  if (s.status === 'running') return ai(s.task?.phase === 'correcting' ? 'session.correcting' : 'session.running')
  // 带任务结局时只翻译 `task.outcome`（检查本身在后端）；其余组合与无 task 一律走原逻辑。
  // completed + 没改动（Agent 判定不必改，后端 `test_unchanged_copy_commits_nothing`）**有意**落到 `doneNoChange`：
  // 「已完成，脚本没有变化」就是这个结局的准确翻译；没写回就没有渲染层核对，说「数据核对一致」反而是假话
  const outcome = s.task?.outcome
  if (s.status === 'done' && s.changed) {
    if (outcome === 'completed') {
      const n = s.task?.corrections ?? 0
      return n > 0 ? ai('session.taskVerifiedCorrected', { count: n }) : ai('session.taskVerified')
    }
    if (outcome === 'partial') return ai('session.taskPartial')
    if (outcome === 'failed') return ai('session.taskFailedChanged')
  }
  if (s.status === 'failed' && !s.changed && outcome === 'failed') return ai('session.taskRejected')
  if (s.status === 'done') return ai(s.changed ? 'session.doneChanged' : 'session.doneNoChange')
  if (s.status === 'reverted') return ai('session.reverted')
  return statusLabel(s.status)
}

/** 从数据出图（TAF Phase 5c）的状态行：只翻译 `task.outcome`；失败走通用的「失败」 */
function dataFigureStatusText(s: AiSession): string {
  if (s.status === 'running') return ai('session.dataFigureRunning')
  if (s.unconfirmed) return ai('session.dataFigureUnconfirmed')
  const script = s.task?.script ?? ''
  switch (s.task?.outcome) {
    case 'completed':
      return ai('session.dataFigureDone', { script })
    case 'partial':
      return ai('session.dataFigurePartial', { script })
    case 'needs_input':
      return ai('session.dataFigureNeedsInput')
    case 'failed':
      // 提交后验证失败但文件已建（`script` 在）：如实说出，别让人以为什么都没发生
      return script ? ai('session.dataFigureFailedWritten', { script }) : statusLabel(s.status)
    default:
      return statusLabel(s.status)
  }
}

const failedStatus = (s: AiSession) => s.status === 'failed' || s.status === 'timeout'


/* ------------------------------------------------------------------ 一轮 */

export function Turn({
  session,
  onResend,
  onRetry,
  canResend,
  canRetry,
}: {
  session: AiSession
  /** 把这条提示词按当前的作用范围再发一次（气泡上的「重新发送」：用户此刻的选择就是新的目标） */
  onResend: (prompt: string) => void
  /** 失败的这一轮原样重试：**钉在这一轮自己的目标上**，不跟着此刻的选择走（Codex #827 P1） */
  onRetry: (session: AiSession) => void
  canResend: boolean
  /** 只在发起这一轮的那张图上给「重试」：同一脚本的兄弟面板也看得到这一轮，但重试不能改到别的面板上（Codex #827 P1） */
  canRetry: boolean
}) {
  useTranslation('ai')
  const caps = useAiStore((s) => s.caps)
  const running = session.status === 'running'
  const groups = groupEntries(session.entries, session.finishedAt)
  const failed = failedStatus(session)
  const total = session.finishedAt != null ? session.finishedAt - session.startedAt : null
  let lastProcess = -1
  groups.forEach((g, i) => {
    if (g.type === 'process') lastProcess = i
  })

  // 任务结局卡（TAF）：只翻译 `task.outcome`。done+partial 是 warn，done+failed+changed 是 danger（回滚用 DiffView 上的按钮）
  const outcome = session.task?.outcome
  const dataFigureTurn = session.task?.task_type === DATA_FIGURE_TASK
  const outcomeCard: 'partial' | 'failed' | null = dataFigureTurn
    ? null
    : session.status === 'done' && outcome === 'partial'
      ? 'partial'
      : session.status === 'done' && outcome === 'failed' && session.changed
        ? 'failed'
        : null
  const rejected = session.status === 'failed' && outcome === 'failed' && !session.changed && !dataFigureTurn
  const unmetIds = (session.task?.unmet ?? []).map((u) => u.constraint_id)
  const unmetDetail = unmetIds.length ? [ai('notice.unmetHeading'), ...unmetIds].join('\n') : null
  // 从数据出图：没有 diff、没有回滚；partial / needs_input 各一张 warn 卡，failed 走下面通用的失败卡（正文 = error）
  const dataCard: 'partial' | 'needs_input' | null =
    dataFigureTurn && session.status === 'done' && (outcome === 'partial' || outcome === 'needs_input') ? outcome : null
  // 提交后验证失败、文件已经写进项目：danger 卡（不出重试——再来一次会建出 `_2`）；详情列 unmet id
  const failedWritten =
    dataFigureTurn && outcome === 'failed' && !!session.task?.script && !session.unconfirmed
  // 结果不确定（没收到答复，可能已经生成）：warn 卡 + 「查询结果」；不是失败卡
  const unconfirmedCard = dataFigureTurn && session.unconfirmed === true && session.status !== 'running'
  const preview = session.task?.preview
  const previewY = preview?.y?.[0]

  return (
    // 新的一轮落位：淡入 + 4px 上浮 + 0.985 缩放，弹簧收尾（settle-in，宪法第十八节）
    <div data-ai-session={session.status} className="flex animate-settle-in flex-col gap-(--item-gap)">
      <UserBubble session={session} onResend={onResend} canResend={canResend} />

      {/* 接回来的运行中会话（刷新 / 重连 / 回切）：说清为什么没有过程输出，别让人以为卡住了 */}
      {session.restored && running && session.entries.length === 0 && (
        <p data-ai-restored className="text-sm text-ink-3">
          {ai('session.restoredNote')}
        </p>
      )}

      {groups.map((g, i) =>
        g.type === 'message' ? (
          <Markdown key={i} text={g.text} streaming={!!g.streaming} />
        ) : (
          <ProcessGroup
            key={i}
            items={g.items}
            live={running && i === groups.length - 1}
            // 失败的那一轮，最后一组过程自动展开：出错的多半就在那几步里
            defaultOpen={failed && i === lastProcess}
          />
        ),
      )}

      {session.changed && session.diff && (
        <div className="animate-settle-in">
          <DiffView diff={session.diff} script={session.script} onRevert={() => revertSession(session)} />
        </div>
      )}

      {dataCard && (
        <Notice
          tone="warn"
          data-ai-task-outcome={outcome}
          detail={dataCard === 'partial' ? unmetDetail : null}
        >
          {dataCard === 'partial'
            ? ai('notice.dataFigurePartial')
            : preview?.x && previewY
              ? ai('notice.dataFigureNeedsInputExample', { x: preview.x, y: previewY })
              : ai('notice.dataFigureNeedsInput')}
        </Notice>
      )}

      {outcomeCard && (
        <Notice
          tone={outcomeCard === 'partial' ? 'warn' : 'danger'}
          data-ai-task-outcome={session.task?.outcome}
          detail={unmetDetail}
        >
          {ai(outcomeCard === 'partial' ? 'notice.taskPartial' : 'notice.taskFailedChanged')}
        </Notice>
      )}

      {failedWritten && (
        <Notice tone="danger" data-ai-task-outcome="failed" detail={unmetDetail}>
          {ai('notice.dataFigureFailedWritten')}
        </Notice>
      )}

      {unconfirmedCard && (
        <Notice
          tone="warn"
          data-ai-data-figure-unconfirmed
          action={
            <Button
              data-ai-data-figure-reconcile
              size="sm"
              onClick={() => void useAiStore.getState().reconcileDataFigure(session.id)}
            >
              {ai('notice.dataFigureReconcile')}
            </Button>
          }
        >
          {ai('notice.dataFigureUnconfirmed')}
        </Notice>
      )}

      {!outcomeCard && !failedWritten && !unconfirmedCard && (failed || session.error) && (
        <Notice
          tone="danger"
          data-ai-error="session"
          {...(rejected ? { 'data-ai-task-outcome': 'failed' } : {})}
          title={rejected ? undefined : statusLabel(session.status)}
          // 一句话留在正文；长的 / 多行的整段进「详情」（带复制）
          detail={
            rejected
              ? [unmetDetail, splitNoticeText(session.error).detail ?? splitNoticeText(session.error).inline]
                  .filter(Boolean)
                  .join('\n\n') || null
              : splitNoticeText(session.error).detail
          }
          action={
            canRetry ? (
              <Button data-ai-retry size="sm" onClick={() => onRetry(session)}>
                {ai('panel.retry')}
              </Button>
            ) : undefined
          }
        >
          {rejected ? ai('notice.taskRejected') : splitNoticeText(session.error).inline}
        </Notice>
      )}

      {/* 状态行是唯一的「还活着」信号：进行中一道亮带扫过文字，完成即停（宪法第十八节）。
          换字原位换（第二十三节）：`data-ai-status` 跟着字走——用例与诊断认它。
          执行器与总耗时是这一行的前后缀，不进 SwapText */}
      <p data-ai-turn-meta className="flex min-w-0 items-baseline gap-1 text-sm text-ink-3">
        <span className="shrink-0">{sessionAgentLabel(caps, session)} ·</span>
        <SwapText
          text={statusText(session)}
          textClassName={running ? 'text-shimmer' : 'text-ink-3'}
          data-ai-status={session.status}
        />
        {total != null && !running && <span className="type-number shrink-0 text-ink-3">· {formatDuration(total)}</span>}
      </p>
    </div>
  )
}

/* ------------------------------------------------------------------ 用户消息 */

const FOLD_LINES = 3

function UserBubble({
  session,
  onResend,
  canResend,
}: {
  session: AiSession
  onResend: (prompt: string) => void
  canResend: boolean
}) {
  useTranslation('ai')
  const textRef = useRef<HTMLParagraphElement>(null)
  const [expanded, setExpanded] = useState(false)
  // 超过 3 行才折：换行数是确定的（jsdom 也量得到）；软换行靠真布局量 scrollHeight
  const [overflows, setOverflows] = useState(false)
  const hardLines = session.prompt.split('\n').length
  useLayoutEffect(() => {
    const el = textRef.current
    if (!el || expanded) return
    const measure = () => setOverflows(el.scrollHeight > el.clientHeight + 1)
    measure()
    // 右栏可拖宽窄：变窄后软换行能超出 3 行，不重量就会被 line-clamp 吞掉又没有「展开」（Codex #827 P2）
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [session.prompt, expanded])
  const foldable = hardLines > FOLD_LINES || overflows

  return (
    <div data-ai-user className="group flex flex-col items-end gap-1">
      <div className="flex max-w-[85%] flex-col items-end rounded-lg bg-surface-hover px-3 py-2">
        <p
          ref={textRef}
          className={cn(
            'type-reading self-stretch whitespace-pre-wrap break-words select-text',
            !expanded && 'line-clamp-3',
          )}
        >
          {session.prompt}
        </p>
        {foldable && (
          <button
            type="button"
            data-ai-user-fold
            aria-expanded={expanded}
            onClick={() => setExpanded((v) => !v)}
            className="mt-0.5 rounded-sm text-sm text-ink-3 outline-none hover:text-ink focus-visible:focus-ring"
          >
            {ai(expanded ? 'message.collapse' : 'message.expand')}
          </button>
        )}
      </div>
      {/* 目标 · 时刻与操作只在 hover / 键盘进入时出现（不占注意力，占位不跳） */}
      <div
        data-ai-user-actions
        className="flex items-center gap-0.5 opacity-0 transition-opacity duration-fast group-hover:opacity-100 focus-within:opacity-100"
      >
        <span className="type-meta mr-1 truncate">
          {session.target} · {formatTime(session.startedAt)}
        </span>
        <CopyAction
          data-ai-user-copy
          text={session.prompt}
          label={ai('message.copy')}
          copiedLabel={ai('code.copied')}
        />
        <IconButton
          data-ai-resend
          label={ai('message.resend')}
          iconSize="xs"
          side="top"
          disabled={!canResend}
          className="text-ink-3 hover:text-ink"
          onClick={() => onResend(session.prompt)}
        >
          <RefreshCw size={ICON_SIZE.sm} />
        </IconButton>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ 过程 */

/**
 * 后端（`engine/ai_agents.py`）给 action 文本开头放一个**标记符 + 空格**：`$` = 跑了一条命令，
 * 其它符号（一支笔）= 改了文件 / 调了工具。这里只拆标记、不画它；「一个非字母数字的符号 + 空格」
 * 统一当标记解析，不把那个字形写死在前端。
 */
const ACTION_MARK = /^([^\p{L}\p{N}\s])\s+(.*)$/su

function parseAction(text: string): { Icon: IconComponent; verb: string; arg: string } {
  const m = ACTION_MARK.exec(text)
  if (m && m[1] === '$') return { Icon: Wrench, verb: ai('panel.stepRan'), arg: m[2] }
  if (m) {
    // 「<标记> 名字 目标」：名字是动词位，目标是参数；只有名字时参数为空
    const rest = m[2].trim()
    const sp = rest.indexOf(' ')
    return { Icon: Pencil, verb: sp === -1 ? rest : rest.slice(0, sp), arg: sp === -1 ? '' : rest.slice(sp + 1) }
  }
  return { Icon: Pencil, verb: '', arg: text }
}

/** 一行字的摘要（进行中那一行原位替换用） */
function stepSummary(step: Step): string {
  if (step.kind !== 'action') return step.text.replace(/\s+/g, ' ').trim()
  const { verb, arg } = parseAction(step.text)
  return [verb, arg].filter(Boolean).join(' ')
}

/** 14px 图标列：常态是种类图标，hover 时换成 chevron（不 hover 不显示展开提示，§6.2） */
function IconSlot({ Icon, open, expandable }: { Icon: IconComponent; open: boolean; expandable: boolean }) {
  return (
    <span aria-hidden className="grid size-3.5 shrink-0 place-items-center text-ink-3">
      <Icon
        size={ICON_SIZE.sm}
        className={cn('col-start-1 row-start-1 transition-opacity duration-fast', expandable && 'group-hover/row:opacity-0')}
      />
      {expandable && (
        <ChevronRight
          size={ICON_SIZE.sm}
          className={cn(
            'col-start-1 row-start-1 opacity-0 transition-[opacity,transform] duration-fast group-hover/row:opacity-100',
            open && 'rotate-90',
          )}
        />
      )}
    </span>
  )
}

const ROW_BUTTON =
  'group/row flex w-full min-w-0 items-center gap-2 rounded-md text-left outline-none focus-visible:focus-ring'

function ProcessGroup({ items, live, defaultOpen }: { items: Step[]; live: boolean; defaultOpen: boolean }) {
  useTranslation('ai')
  const [open, setOpen] = useState(defaultOpen)
  // 跑着的一轮失败时同一个组件不重挂，初值不会重算：defaultOpen 变真那一刻把它展开（Codex #827 P2）
  const [prevDefaultOpen, setPrevDefaultOpen] = useState(defaultOpen)
  if (defaultOpen !== prevDefaultOpen) {
    setPrevDefaultOpen(defaultOpen)
    if (defaultOpen) setOpen(true)
  }
  const thinking = items.filter((s) => s.kind !== 'action')
  const actions = items.length - thinking.length
  const spans = thinking.map(spanOf)
  const thoughtMs = spans.length && spans.every((x) => x != null) ? spans.reduce<number>((a, b) => a + (b ?? 0), 0) : null

  let summary: string
  if (live) {
    summary = stepSummary(items.at(-1)!)
  } else {
    const parts: string[] = []
    if (thinking.length) {
      parts.push(thoughtMs != null ? ai('process.thought', { duration: formatDuration(thoughtMs) }) : ai('process.thoughtNoTime'))
    }
    if (actions) parts.push(ai('panel.processSteps', { count: actions }))
    summary = parts.join(' · ')
  }
  const LeadIcon = actions ? Wrench : Sparkles

  return (
    <div data-ai-process>
      <button
        type="button"
        data-ai-process-toggle
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={cn(ROW_BUTTON, 'h-7 text-base text-ink-3 hover:text-ink-2')}
      >
        <IconSlot Icon={LeadIcon} open={open} expandable />
        <span className={cn('min-w-0 truncate', live && 'text-shimmer')}>{summary}</span>
      </button>
      {/* 展开是跟着内容长高（Reveal），不是一整块瞬间跳出来 */}
      <Reveal open={open}>
        <ul className="flex flex-col gap-0.5 pb-1">
          {items.map((it, i) => (
            <StepRow key={i} step={it} />
          ))}
        </ul>
      </Reveal>
    </div>
  )
}

function Duration({ step }: { step: Step }) {
  const ms = spanOf(step)
  return ms == null ? null : <span className="type-meta shrink-0 tabular-nums">{formatDuration(ms)}</span>
}

/**
 * 过程里的一步（环境行）：无框、一行高；14px 图标列 · 动词 · 参数片（等宽、surface-hover 底）· 右侧耗时。
 * 动作行可展开，surface-2 圆角面板里是完整参数（参数片在行里会被截断）。思考是一段话：ink-3、可换行、不展开。
 */
function StepRow({ step }: { step: Step }) {
  useTranslation('ai')
  const [open, setOpen] = useState(false)
  if (step.kind !== 'action') {
    return (
      <li data-ai-step="thinking" className="flex min-w-0 items-start gap-2 py-1 text-sm leading-relaxed text-ink-3">
        <span className="flex h-5 shrink-0 items-center">
          <Sparkles size={ICON_SIZE.sm} aria-hidden />
        </span>
        <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">{step.text}</span>
        <span className="flex h-5 items-center">
          <Duration step={step} />
        </span>
      </li>
    )
  }
  const { Icon, verb, arg } = parseAction(step.text)
  const content = (
    <>
      <IconSlot Icon={Icon} open={open} expandable={!!arg} />
      {verb && <span className="shrink-0 text-ink">{verb}</span>}
      {arg && (
        <code className="min-w-0 truncate rounded-sm bg-surface-hover px-1.5 py-0.5 font-mono text-sm text-ink-2">
          {arg}
        </code>
      )}
      <span className="flex-1" />
      <Duration step={step} />
    </>
  )
  return (
    <li data-ai-step="action" className="min-w-0">
      {arg ? (
        <button
          type="button"
          data-ai-step-toggle
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className={cn(ROW_BUTTON, 'h-7 text-base text-ink-2')}
        >
          {content}
        </button>
      ) : (
        <div className="flex h-7 min-w-0 items-center gap-2 text-base text-ink-2">{content}</div>
      )}
      <Reveal open={open}>
        <pre className="mb-1 ml-5.5 whitespace-pre-wrap break-all rounded-md bg-surface-2 px-2.5 py-2 font-mono text-xs leading-relaxed text-ink-2 select-text">
          {arg}
        </pre>
      </Reveal>
    </li>
  )
}
