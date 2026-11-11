/**
 * 保存失败 / 冲突那几条红色提示的出口按钮（docs/rules/frontend/ui-visual-discipline.md「失败出口三种结局」）。
 *
 * 只在「已经失败、要告诉用户」这一步挂按钮，不碰保存事务本身：
 *  - 冲突类 →「重新加载」：`reloadFromDisk()` 先把本窗口的内存版本存进恢复槽位再读盘，**两边都不丢**
 *    （与顶栏冲突说明块的主按钮同一个函数）。会丢掉某一方的「覆盖」不在 toast 上给，仍只在顶栏芯片的 ⋯ 里、
 *    先问一句；
 *  - 写盘失败类 →「再存一次」：真的重新走一遍原动作。
 *
 * 按钮点下去 toast 不会自己收（`StatusBar` 只转发 `run`），所以每个按钮自己负责收掉或换成结果。
 */
import { msg } from '@/i18n'
import { useDiagSendStore } from './diagSendStore'
import { reloadFromDisk, saveNow, useDocumentStore } from './documentStore'
import { useUiStore, type StatusAction } from './uiStore'

/** 这类 toast 的主人：换项目时不清，但按钮点下去能认出「是不是自己那一条」 */
export const AUTOSAVE_FAILURE_OWNER = 'save.failure'

/**
 * 「发送问题反馈」按钮——与 `lib/canvasFileIntake.feedbackAction` 同一份行为（引擎说入口关着就不给）。
 * 不直接引它：那个文件经 workspace 回到 actions，会让保存链成环。
 */
function feedbackStatusAction(): StatusAction | undefined {
  if (useDiagSendStore.getState().capability === null) return undefined
  return {
    label: msg('settings.diagnostics.send.cardButton', undefined, 'dialogs'),
    run: () => useDiagSendStore.getState().setOpen(true),
  }
}

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

/** 「再存一次」：`run` 是原动作（自动保存 → 再写一次本机；⌘S → 整条 ⌘S；写项目 → 同一个上下文再写）。 */
export function retrySaveAction(run: () => unknown, owner: string = AUTOSAVE_FAILURE_OWNER): StatusAction {
  return {
    label: msg('save.actionRetry', undefined, 'workspace'),
    run: () => {
      clearOwn(owner)
      void run()
    },
  }
}

/**
 * 自动保存那一轮的「再存一次」：先收掉提示再写；又失败会经 `tavotto:autosave-error` 换上新的一条，
 * 成功了顶栏的保存状态自己回到「已保存」，不另说一句（「已保存在本机」对开着项目的人是错话）。
 */
export function retryAutosaveAction(): StatusAction {
  return retrySaveAction(() => saveNow())
}

/**
 * 自动保存失败 / 被挡下（`tavotto:autosave-error` 的 detail）：stale = 另一个窗口已经存过更新的版本，
 * external = 磁盘上那份被外部改过——后端都挡下了这次覆盖；io = 写盘本身没成。
 * 出口：冲突 →「重新加载」，写盘失败 →「再存一次」。
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
      action: conflict ? reloadLatestAction(docId) : retryAutosaveAction(),
    },
  )
}

const DOC_OPEN_ELSEWHERE_OWNER = 'autosave.docConflict'

/**
 * 同一份排版在另一个窗口也开着（`tavotto:doc-conflict`）：出口是「另存为」——这个窗口改在自己的一份上，
 * 两边都不丢、也不再互相覆盖（与顶栏冲突说明块的「另存为…」同一个入口）。不在这里替用户选覆盖或放弃。
 */
export function reportDocOpenElsewhere(): void {
  useUiStore.getState().setStatus(msg('autosave.docConflict', undefined, 'workspace'), 'error', {
    owner: DOC_OPEN_ELSEWHERE_OWNER,
    action: {
      label: msg('docBanner.saveAs', undefined, 'workspace'),
      run: () => {
        clearOwn(DOC_OPEN_ELSEWHERE_OWNER)
        useUiStore.getState().setLayoutOpen(true, 'save')
      },
    },
  })
}
