/**
 * 保存失败 / 冲突那几条红色提示的出口按钮（docs/rules/frontend/ui-visual-discipline.md「失败出口三种结局」）。
 *
 * 只在「已经失败、要告诉用户」这一步挂按钮，不碰保存事务本身：
 *  - 冲突类 →「重新加载」：`reloadFromDisk()` 先把本窗口的内存版本存进恢复槽位再读盘，**两边都不丢**
 *    （与顶栏冲突说明块的主按钮同一个函数）。会丢掉某一方的「覆盖」不在 toast 上给，仍只在顶栏芯片的 ⋯ 里、
 *    先问一句；
 *  - 写盘失败类 →「重试保存」：真的重新走一遍原动作。
 *
 * 按钮点下去 toast 不会自己收（`StatusBar` 只转发 `run`），所以每个按钮自己负责收掉或换成结果。
 */
import { msg } from '@/i18n'
import { currentProjectId } from '@/lib/session'
import { reloadFromDisk, saveNow, useDocumentStore } from './documentStore'
import { feedbackStatusAction } from './failureExit'
import { useUiStore, type StatusAction } from './uiStore'

/** 这类 toast 的主人：换项目时不清，但按钮点下去能认出「是不是自己那一条」 */
export const AUTOSAVE_FAILURE_OWNER = 'save.failure'

const clearOwn = (owner: string) => useUiStore.getState().clearStatusOwnedBy(owner)

/**
 * 「重新加载」：取回磁盘上最新那一版（本窗口的编辑先进恢复槽位，不丢）。
 * 点下去时冲突已经被别处裁决了（换了文档 / 已不在冲突态）就只收掉提示，不再动内存里的文档。
 * 读不回来：换成一句话 +「发给我们看看」，不留一个点了没反应的按钮。
 */
export function reloadLatestAction(docId: string, owner: string = AUTOSAVE_FAILURE_OWNER): StatusAction {
  return {
    label: msg('actions.reload', undefined, 'common'),
    run: () => {
      const s = useDocumentStore.getState()
      if (s.documentId !== docId || s.saveState !== 'conflict') {
        clearOwn(owner)
        return
      }
      void reloadFromDisk().then((ok) => {
        if (ok) {
          clearOwn(owner)
          return
        }
        useUiStore
          .getState()
          .setStatus(msg('save.reloadFailed', undefined, 'workspace'), 'error', {
            owner,
            action: feedbackStatusAction(),
          })
      })
    },
  }
}

/** 「重试保存」：`run` 是原动作（自动保存 → 再写一次本机；⌘S → 整条 ⌘S；写项目 → 同一个上下文再写）。 */
export function retrySaveAction(run: () => unknown, owner: string = AUTOSAVE_FAILURE_OWNER): StatusAction {
  return {
    label: msg('docBanner.retry', undefined, 'workspace'),
    run: () => {
      clearOwn(owner)
      void run()
    },
  }
}

/**
 * 自动保存那一轮的「重试保存」：先收掉提示再写；又失败会经 `tavotto:autosave-error` 换上新的一条，
 * 成功了顶栏的保存状态自己回到「已保存」，不另说一句（「已保存在本机」对开着项目的人是错话）。
 */
export function retryAutosaveAction(docId: string = useDocumentStore.getState().documentId): StatusAction {
  const pj = currentProjectId()
  return retrySaveAction(() => {
    // 失败的是 `docId` 那份：点的时候开着的已是别的排版 / 别的项目，就只收掉提示——不对别的排版乱存。
    // 那份的未写盘内容仍在本机副本里（写盘失败时不清），切回去由崩溃恢复那条路提供
    const s = useDocumentStore.getState()
    if (s.documentId !== docId || currentProjectId() !== pj) return
    return saveNow()
  })
}

/**
 * 自动保存失败 / 被挡下（`tavotto:autosave-error` 的 detail）：stale = 另一个窗口已经存过更新的版本，
 * external = 磁盘上那份被外部改过——后端都挡下了这次覆盖；io = 写盘本身没成。
 * 出口：冲突 →「重新加载」，写盘失败 →「重试保存」。
 */
export function reportAutosaveFailure(detail: { id?: string; reason?: string } | undefined): void {
  const reason = detail?.reason
  const docId = detail?.id ?? useDocumentStore.getState().documentId
  const conflict = reason === 'stale' || reason === 'external'
  useUiStore.getState().setStatus(
    msg(
      reason === 'stale'
        ? 'autosave.staleOtherWindow'
        : reason === 'external'
          ? 'docBanner.conflictExternal'
          : 'autosave.diskFailed',
      undefined,
      'workspace',
    ),
    'error',
    {
      owner: AUTOSAVE_FAILURE_OWNER,
      action: conflict ? reloadLatestAction(docId) : retryAutosaveAction(docId),
    },
  )
}
