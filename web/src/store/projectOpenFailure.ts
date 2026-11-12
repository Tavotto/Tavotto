/**
 * 打开 / 新建项目失败的出口（docs/rules/frontend/ui-visual-discipline.md「失败出口三种结局」）：
 *  - 文件夹找不到 →「重新选择文件夹」（现成的文件夹选择入口）。**不**把它从最近列表去掉：移动硬盘没插也会找不到；
 *  - 其他失败 →「再试一次」重做同一次打开，再失败换成「发送问题反馈」。
 *
 * 「找不到」不靠报错文本判：后端对不存在的目录回的是通用码 `open_project_failed`（文本「目录不存在」），
 * 没有专用的稳定码，所以失败后重读一次最近列表，看后端对这条路径给的 `exists`（目录是否在）。
 */
import { msg } from '@/i18n'
import { backendErrorText } from '@/lib/api'
import { currentProjectId } from '@/lib/session'
import { retryOrFeedback } from './failureExit'
import { useProjectStore } from './projectStore'
import { useUiStore } from './uiStore'

const OWNER = 'project.openFailure'

const trimSep = (p: string) => p.replace(/[\\/]+$/, '')
const nameOf = (p: string) => trimSep(p).split(/[\\/]/).pop() || p

export async function reportProjectOpenFailure(o: {
  path: string
  create: boolean
  /** 后端原话（不进界面，只随「复制」带出去，方便发给我们） */
  error?: unknown
  attempt: number
  /** 重做同一次打开（`attempt` 是这次重试的序号） */
  retry: (attempt: number) => unknown
  /** 打开现有的文件夹选择入口 */
  reselect: () => void
}): Promise<void> {
  const pj = currentProjectId()
  await useProjectStore.getState().refreshRecent().catch(() => {})
  const row = useProjectStore.getState().recent.find((r) => trimSep(r.path) === trimSep(o.path))
  const name = row?.name || nameOf(o.path)
  const ui = useUiStore.getState()
  const copyDetail = o.error === undefined ? undefined : backendErrorText(o.error)
  if (!o.create && row?.exists === false) {
    ui.setStatus(msg('workspace.openMissing', { name }, 'project'), 'error', {
      owner: OWNER,
      copyDetail,
      action: {
        label: msg('workspace.reselectFolder', undefined, 'project'),
        run: () => {
          useUiStore.getState().clearStatusOwnedBy(OWNER)
          o.reselect()
        },
      },
    })
    return
  }
  ui.setStatus(msg(o.create ? 'workspace.createFailed' : 'workspace.openFailed', { name }, 'project'), 'error', {
    owner: OWNER,
    copyDetail,
    action: retryOrFeedback({
      owner: OWNER,
      attempt: o.attempt,
      run: (next) => {
        // 点的时候已经打开了别的项目：只收提示，不替用户再去打开一个旧的
        if (currentProjectId() !== pj) return
        return o.retry(next)
      },
    }),
  })
}
