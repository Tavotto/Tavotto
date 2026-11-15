/**
 * 失败出口的两个现成按钮（docs/rules/frontend/ui-visual-discipline.md「失败出口三种结局」）：
 *  - 「再试一次」：重做**同一次**原动作；点的时候对象已变了（换了项目 / 排版）由调用方的 `run` 自己判、只收提示；
 *  - 「发给我们看看」：真做不了时的出口，本机备包的对话框（不发送）。
 *
 * 只给**第一次**失败挂「再试一次」：同一个动作刚重试又失败，就别再给一个多半还会失败的重试，换成反馈。
 * 与 `saveFailureActions` 同一套习惯：带 owner、点下去先收掉自己那一条；不引 `lib/canvasFileIntake`（那条链经 workspace 回到 actions 成环）。
 */
import { msg, type UiMessage } from '@/i18n'
import { isDesktop } from '@/lib/desktop'
import { useDiagSendStore } from './diagSendStore'
import { useUiStore, type StatusAction } from './uiStore'
import { fetchNewerDesktopBuild, useUpdateStore } from './updateStore'

/** 已经重试过几次就不再给「再试一次」：第 1 次失败（attempt 0）给重试，重试又失败（attempt 1）起只给反馈 */
export const MAX_RETRY_ATTEMPTS = 1

/** 「发送问题反馈」；引擎说入口关着（用户 / 部署关掉）就不给 */
export function feedbackStatusAction(): StatusAction | undefined {
  if (useDiagSendStore.getState().capability === null) return undefined
  return {
    label: msg('settings.diagnostics.send.cardButton', undefined, 'dialogs'),
    run: () => useDiagSendStore.getState().setOpen(true),
  }
}

/**
 * 失败提示的出口按钮：还能重试（`retryable` 且 `attempt` 没到上限）→「再试一次」，否则 →「发给我们看看」。
 * `run(nextAttempt)` 重做原动作；它又失败时应把 `nextAttempt` 带进下一条提示。
 */
export function retryOrFeedback(opts: {
  owner: string
  attempt: number
  run: (nextAttempt: number) => unknown
  retryable?: boolean
  label?: UiMessage
}): StatusAction | undefined {
  if (opts.retryable === false || opts.attempt >= MAX_RETRY_ATTEMPTS) return feedbackStatusAction()
  return {
    label: opts.label ?? msg('prep.btn.retry', undefined, 'workspace'),
    run: () => {
      useUiStore.getState().clearStatusOwnedBy(opts.owner)
      void opts.run(opts.attempt + 1)
    },
  }
}

/** 「检查更新」之后用一条 toast 说结果；有新版但没下好时，toast 上的动作直接下载并更新（顶栏说明块与「版本太新」的提示共用） */
export async function checkNewerBuild(): Promise<void> {
  const ui = useUiStore.getState()
  const outcome = await fetchNewerDesktopBuild()
  if (outcome === 'ready') {
    ui.setStatus(msg('docBanner.updateReady', undefined, 'workspace'), 'info')
  } else if (outcome === 'found') {
    const version = useUpdateStore.getState().desktopUpdate?.version ?? ''
    ui.setStatus(msg('docBanner.updateFound', { version }, 'workspace'), 'info', {
      action: {
        label: msg('docBanner.updateInstall', undefined, 'workspace'),
        run: () => void useUpdateStore.getState().installDesktop(),
      },
    })
  } else if (outcome === 'none') {
    ui.setStatus(msg('docBanner.updateNone', undefined, 'workspace'), 'info')
  } else {
    ui.setStatus(msg('docBanner.updateCheckFailed', undefined, 'workspace'), 'error')
  }
}

/**
 * 「这份排版来自更新的版本、读不了」的出口：桌面版 →「检查更新」（与顶栏说明块同一个动作）；
 * 浏览器版没有壳里的更新通道（说明块在那里也只给「知道了」），换成「发给我们看看」。
 */
export function tooNewAction(): StatusAction | undefined {
  if (!isDesktop()) return feedbackStatusAction()
  return {
    label: msg('docBanner.checkUpdate', undefined, 'workspace'),
    run: () => void checkNewerBuild(),
  }
}
