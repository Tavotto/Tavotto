/**
 * ⌘S 写回项目里的排版文件（ADR 0096）。
 *
 * | 这份排版 | ⌘S |
 * | --- | --- |
 * | 绑定了当前项目里的 `tavottofile/<名>.json` | 本机自动保存照存，再原子写回那个文件 |
 * | 开着项目、还没有项目文件 | 弹「存进项目」命名框（与另存为同一个表单），存成之后绑定 |
 * | 没开项目 | 只存本机，提示里说「存在本机」 |
 *
 * 自动保存**只**写本机槽位——没按保存就不动项目里的文件。项目文件被别处改过时
 * （git pull、另一台电脑、另一个窗口），绑定里记着的修订号对不上，后端 409，这里
 * 把用户带到另存为那一屏的同一个冲突岔口，不静默覆盖。
 */
import { msg } from '@/i18n'
import { ApiError, REVISION_ABSENT, backendErrorText, saveLayout, type DiskDocumentSummary } from '@/lib/api'
import { rememberLayoutRevision } from '@/lib/layoutRevision'
import type { ProjectFileBinding } from '@/lib/projectFile'
import { currentProjectId } from '@/lib/session'
import { projectFileSnapshot, setProjectFile, useDocumentStore } from './documentStore'
import { useUiStore } from './uiStore'

/**
 * 写回绑定的项目文件。返回是否写成；失败时已经把话说完（状态条 / 冲突弹窗）。
 *
 * 基线是**绑定里的修订号**（这份工作副本基于项目文件的哪一版），不是本窗口读到过
 * 什么——刷新之后它照样在，外部修改照样挡得住。不知道（`null`）就发 `absent`：
 * 磁盘上真有一份的话让用户点头（ADR 0024 §3b，基线缺席 = 写之前先确认）。
 */
export async function writeBoundProjectFile(binding: ProjectFileBinding): Promise<boolean> {
  const ui = useUiStore.getState()
  const { documentId } = useDocumentStore.getState()
  const edited = projectFileSnapshot()
  const pd = useDocumentStore.getState().buildProject()
  try {
    const res = await saveLayout(binding.name, pd, binding.revision ?? REVISION_ABSENT, {
      target: 'project',
    })
    const name = res.name ?? binding.name
    const file = res.file ?? binding.file
    rememberLayoutRevision(name, res.revision)
    // 写的途中又改过：项目里这份已经落后了，圆点不能灭
    setProjectFile({ ...binding, name, file, revision: res.revision, dirty: edited() }, documentId)
    ui.setStatus(msg('save.doneProject', { file }, 'workspace'))
    return true
  } catch (e) {
    const revision =
      e instanceof ApiError && e.status === 409 && e.body.code === 'external_change'
        ? e.body.revision
        : null
    if (typeof revision === 'string') {
      ui.setStatus(msg('save.projectConflict', { file: binding.file }, 'workspace'), 'error')
      ui.setLayoutOpen(true, 'saveToProject', {
        name: binding.name,
        conflict: {
          name: binding.name,
          revision,
          summary: ((e as ApiError).body.summary as DiskDocumentSummary | null) ?? null,
        },
      })
    } else {
      ui.setStatus(
        msg('save.projectFailed', { file: binding.file, reason: backendErrorText(e) }, 'workspace'),
        'error',
      )
    }
    return false
  }
}

/** 这份排版有没有可能在此刻「存进项目」：开着项目才有 */
export const canSaveToProject = (): boolean => currentProjectId() !== null
