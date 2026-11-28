import { msg } from '@/i18n'

/**
 * 脚本写项目里**已有的**文件被系统拒绝（别的程序占着它 / 它是只读的）时，后端把这一笔改写进本次运行的暂存区、
 * 图照常出（ADR 0127 修订 2026-11-28），回包带 `locked_writes`。这里在通知轨上出一枚胶囊说清楚「这次没写进去」——
 * 不是静默成功，也不是横条。
 *
 * - 同一个面板、同一组文件只提醒一次（反复重渲染不再弹）；
 * - 之后这个面板的回包不再带它（文件放开了、写进去了）就撤掉提醒，下次再被拒重新提醒。
 */
export const LOCKED_WRITE_OWNER_PREFIX = 'locked.write:'
export const lockedWriteOwner = (pj: string | null | undefined, key: string) =>
  `${LOCKED_WRITE_OWNER_PREFIX}${pj ?? ''}:${key}`

const noticed = new Map<string, string>()

export interface LockedWriteNoticeDeps {
  ui: {
    setStatus: (status: ReturnType<typeof msg> | null, tone?: 'info', opts?: { owner?: string }) => void
    clearStatusOwnedBy: (owner: string) => void
  }
}

/** 项目相对路径 → 只留文件名（胶囊里一句话，路径前缀没有信息量） */
const baseName = (rel: string) => rel.split('/').filter(Boolean).pop() ?? rel

export function noticeLockedWrites(
  owner: string,
  locked: readonly string[] | null | undefined,
  deps: LockedWriteNoticeDeps,
): void {
  const files = (locked ?? []).filter((x) => typeof x === 'string' && x)
  if (files.length === 0) {
    if (noticed.delete(owner)) deps.ui.clearStatusOwnedBy(owner)
    return
  }
  const signature = files.join('\n')
  if (noticed.get(owner) === signature) return
  noticed.set(owner, signature)
  const file = baseName(files[0])
  const status =
    files.length === 1
      ? msg('engine.lockedWriteSkipped', { file }, 'errors')
      : msg('engine.lockedWriteSkippedMany', { file, n: files.length }, 'errors')
  deps.ui.setStatus(status, 'info', { owner })
}

/** 测试用：忘掉「提醒过」的记账 */
export function resetLockedWriteNotices(): void {
  noticed.clear()
}
