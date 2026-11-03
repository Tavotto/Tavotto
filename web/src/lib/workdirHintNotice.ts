import { msg } from '@/i18n'
import type { WorkdirConfirmation } from '@/lib/api'
import type { StatusAction } from '@/store/uiStore'

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

/**
 * 依赖由调用方（`renderStore` / `scriptRunStore`，它们本来就持有这两个 store）递进来：本文件不 import store，
 * 免得把 `envStore` 拉进 `renderStore` 所在的那个 import 环（`importArchitecture.test.ts` 只减不增）。
 */
export interface WorkdirHintNoticeDeps {
  ui: {
    setStatus: (
      status: ReturnType<typeof msg> | null,
      tone?: 'info',
      opts?: { owner?: string; action?: StatusAction },
    ) => void
    clearStatusOwnedBy: (owner: string) => void
  }
  /** 项目的运行目录决定过没有（`env.project.workdir.decided`）；`true` = 用户已知情选了继续沙盒 */
  decided: boolean | undefined
  request: (hint: WorkdirConfirmation, projectId: string | null) => void
}

export function noticeWorkdirHint(
  owner: string,
  hint: WorkdirConfirmation | null | undefined,
  projectId: string | null,
  deps: WorkdirHintNoticeDeps,
): void {
  if (!hint) {
    deps.ui.clearStatusOwnedBy(owner)
    return
  }
  if (deps.decided === true) return
  const signature = `${owner}|${hint.script}|${(hint.probes ?? []).join(',')}`
  if (noticed.has(signature)) return
  noticed.add(signature)
  deps.ui.setStatus(msg('engine.workdirHintEmptyFigure', { script: hint.script }, 'errors'), 'info', {
    owner,
    action: {
      label: msg('engine.workdirChooseButton', undefined, 'errors'),
      run: () => deps.request(hint, projectId),
    },
  })
}

/** 测试用：忘掉「提醒过」的记账 */
export function resetWorkdirHintNotices(): void {
  noticed.clear()
}
