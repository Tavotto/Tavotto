import { msg } from '@/i18n'
import type { WorkdirConfirmation } from '@/lib/api'
import { useEnvStore } from '@/store/envStore'
import { useUiStore } from '@/store/uiStore'

/**
 * build **成功**、但沙盒里 glob 回空而脚本目录里有文件时（图可能缺数据，#1254）的提醒：通知轨上的一条
 * 胶囊（一句话 + 一个「选择运行目录…」），**不是**横条、不占画布。入口与错误块同一个确认框。
 *
 * - 同一个面板、同一份证据只提醒一次（重渲染不再弹）；
 * - 用户已经选过「继续沙盒」（`decided`）就不再提醒——那是他知情后的决定；
 * - 之后这个面板不再带 hint（切到脚本目录、数据读到了）就把它的提醒撤掉。
 */
export const WORKDIR_HINT_OWNER_PREFIX = 'workdir.hint:'
export const workdirHintOwner = (pj: string | null | undefined, key: string) =>
  `${WORKDIR_HINT_OWNER_PREFIX}${pj ?? ''}:${key}`

const noticed = new Set<string>()

export function noticeWorkdirHint(
  owner: string,
  hint: WorkdirConfirmation | null | undefined,
  projectId: string | null,
): void {
  const ui = useUiStore.getState()
  if (!hint) {
    ui.clearStatusOwnedBy(owner)
    return
  }
  if (useEnvStore.getState().env?.project?.workdir?.decided === true) return
  const signature = `${owner}|${hint.script}|${(hint.probes ?? []).join(',')}`
  if (noticed.has(signature)) return
  noticed.add(signature)
  ui.setStatus(msg('engine.workdirHintEmptyFigure', { script: hint.script }, 'errors'), 'info', {
    owner,
    action: {
      label: msg('engine.workdirChooseButton', undefined, 'errors'),
      run: () => useEnvStore.getState().requestWorkdirConfirmation(hint, projectId),
    },
  })
}

/** 测试用：忘掉「提醒过」的记账 */
export function resetWorkdirHintNotices(): void {
  noticed.clear()
}
