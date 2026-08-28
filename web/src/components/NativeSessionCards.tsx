import { useTranslation } from 'react-i18next'
import { Loader2, Pause, Play, TriangleAlert, Unplug, X } from 'lucide-react'
import {
  backendCodeMsg,
  isNativeTerminal,
  type NativeSessionInfo,
  type NativeSessionState,
} from '@/lib/api'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { useFormatMessage } from '@/i18n/react'
import { askConfirm } from '@/store/uiStore'
import { sortSessions, useNativeSessionStore } from '@/store/nativeSessionStore'
import { Button } from './ui/Button'

const ns = (key: string, values?: Record<string, unknown>) =>
  translate(`nativeSession.${key}`, { ns: 'workspace', ...(values ?? {}) })

/**
 * `tavotto run` 会话的状态浮层（ADR 0021 §5.1 / §8 / §9）。
 *
 * 一档状态一屏，**十档全覆盖**——不是"六种常见的 + 一个 else"。状态闭集在
 * 后端（`nativesession.STATES`），这边只做展示与三个动作：
 *
 * | 动作 | 它到底做了什么 |
 * |---|---|
 * | 继续运行脚本 | runner **先把 Figure 恢复成脚本原样**再放开屏障（§8.1）；你的编辑会在下一个屏障上重放 |
 * | 放手 | 脚本继续正常跑完，Tavotto 不再控制它。**不杀进程** |
 * | 终止脚本 | 明确的危险操作，退出码固定 5。**只在屏障处可用** |
 *
 * 「终止脚本」为什么只在屏障处：脚本正在跑的时候没有人读控制通道，而那时
 * 真正该做的是用户在自己的终端里按 Ctrl+C——那个进程是他的，信号也是他的。
 * Tavotto 不从 GUI 里去杀一个别的进程的子进程。
 */
export function NativeSessionCards() {
  useTranslation('workspace')
  const sessions = useNativeSessionStore((s) => s.sessions)
  const list = sortSessions(Object.values(sessions))
  if (!list.length) return null
  return (
    <div className="pointer-events-none absolute right-2 top-2 z-10 flex w-72 flex-col gap-1.5">
      {list.map((s) => (
        <SessionCard key={s.session_id} session={s} />
      ))}
    </div>
  )
}

/** 每档状态的语气：跑着的是中性，停下来能编辑的是重点，坏了的是错误。 */
const TONE: Record<NativeSessionState, 'busy' | 'ready' | 'done' | 'bad'> = {
  pending_confirmation: 'busy',
  waiting_for_cli: 'busy',
  starting_python: 'busy',
  running_script: 'busy',
  waiting_for_figure: 'busy',
  barrier: 'ready',
  continuing: 'busy',
  ended: 'done',
  detached: 'done',
  failed: 'bad',
}

const BORDER: Record<'busy' | 'ready' | 'done' | 'bad', string> = {
  busy: 'border-border',
  ready: 'border-accent/50',
  done: 'border-border',
  bad: 'border-danger/50',
}

function SessionCard({ session }: { session: NativeSessionInfo }) {
  const fmt = useFormatMessage()
  const store = useNativeSessionStore()
  const busy = !!store.busy[session.session_id]
  const error = store.errors[session.session_id] ?? null
  const conflicts = store.conflicts[session.session_id] ?? []
  const tone = TONE[session.state]
  const terminal = isNativeTerminal(session.state)
  const atBarrier = session.state === 'barrier'

  const act = (fn: (id: string) => Promise<void>) => () => fn(session.session_id)

  const confirmTerminate = async () => {
    const ok = await askConfirm({
      title: msg('nativeSession.terminateConfirm.title', undefined, 'workspace'),
      body: msg(
        'nativeSession.terminateConfirm.body',
        { target: session.target_display },
        'workspace',
      ),
      confirmLabel: msg('nativeSession.terminate', undefined, 'workspace'),
      danger: true,
    })
    if (ok) await store.terminate(session.session_id)
  }

  return (
    <section
      aria-label={ns('cardAria', { target: session.target_display })}
      data-state={session.state}
      className={`pointer-events-auto rounded-sm border bg-surface px-2 py-1.5 shadow-sm ${BORDER[tone]}`}
    >
      <header className="flex items-start gap-1.5">
        <StateIcon state={session.state} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium text-ink" title={session.target_display}>
            {session.target_display}
          </p>
          <p className="text-[11px] leading-relaxed text-ink-3">{stateLine(session)}</p>
        </div>
        {terminal && (
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={translate('actions.close')}
            onClick={() => store.dismiss(session.session_id)}
          >
            <X size={12} />
          </Button>
        )}
      </header>

      {/* 冲突**如实报**：这些 stem 已经被另一条还活着的会话占着（用户在两个
          终端跑了同一个脚本）。静默抢过来的表现是他看到的图突然换成了另一次
          运行的，而界面什么都没说（ADR 0021 §9.2）。 */}
      {conflicts.length > 0 && (
        <p className="mt-1 rounded-sm bg-warn-subtle px-1.5 py-1 text-[11px] leading-relaxed text-ink-2">
          {ns('assetConflict', { stems: conflicts.join('、'), count: conflicts.length })}
        </p>
      )}

      {error && (
        <p
          role="alert"
          className="mt-1 flex items-start gap-1 text-[11px] leading-relaxed text-danger"
        >
          <TriangleAlert size={11} className="mt-0.5 shrink-0" />
          <span className="min-w-0 flex-1">
            {fmt(backendCodeMsg(error.code, error.params, error.message) as UiMessage)}
          </span>
        </p>
      )}

      {/* **三个动作都只在屏障处**。不是排版偏好——`NativeSession` 的每一条
          命令都走 `_require_barrier()`，别的状态下点下去必然拿到
          `native_session_not_at_barrier`。一个必然失败的按钮和一条"作废之后
          还留着的运行并连接"是同一个形状：看起来能做，点了只会得到一条
          描述正常状态的错误。
          脚本正在跑时用户该做什么，卡片那行状态说了（Ctrl+C 在他自己的
          终端里，那个进程是他的）。 */}
      {!terminal && atBarrier && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          <Button
            variant="primary"
            size="sm"
            loading={busy}
            onClick={act(store.resume)}
            title={ns('resumeHint')}
          >
            <Play size={11} />
            {ns('resume')}
          </Button>
          <Button variant="outline" size="sm" disabled={busy} onClick={act(store.detach)}>
            <Unplug size={11} />
            {ns('detach')}
          </Button>
          <Button variant="danger" size="sm" disabled={busy} onClick={confirmTerminate}>
            {ns('terminate')}
          </Button>
        </div>
      )}
    </section>
  )
}

function StateIcon({ state }: { state: NativeSessionState }) {
  const tone = TONE[state]
  if (tone === 'busy') {
    return <Loader2 size={13} className="mt-0.5 shrink-0 animate-spin text-ink-3" />
  }
  if (tone === 'ready') return <Pause size={13} className="mt-0.5 shrink-0 text-accent" />
  if (tone === 'bad') return <TriangleAlert size={13} className="mt-0.5 shrink-0 text-danger" />
  return <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-border-strong" />
}

/**
 * 这一行说的是「现在是什么情况」，不是状态名。
 *
 * 三档终态各带上它自己的事实：`ended` 报退出码与捕获张数（脚本的退出码是
 * **原样透传**的，不是 Tavotto 的判断）、`failed` 报后端给的稳定 code、
 * `detached` 说清楚脚本还在自己跑。
 */
function stateLine(s: NativeSessionInfo): string {
  switch (s.state) {
    case 'barrier':
      return s.script_error
        ? ns('state.barrierAfterError')
        : ns('state.barrier', { count: s.stems.length })
    case 'ended':
      return ns(s.exit_code ? 'state.endedWithCode' : 'state.ended', {
        code: s.exit_code ?? 0,
        figures: s.figures_captured,
      })
    case 'failed':
      return translate(
        `backend.${s.terminal_error?.code ?? ''}`,
        // 没有对应文案时退回后端原文；再没有就一句通用的
        {
          ns: 'errors',
          defaultValue: s.terminal_error?.message || ns('state.failed'),
        },
      )
    default:
      return ns(`state.${s.state}`)
  }
}
