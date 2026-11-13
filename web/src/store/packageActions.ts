import { createPackage } from '@/lib/api'
import { msg } from '@/i18n'
import { openPackageFile } from '@/lib/packageImport'
import { currentProjectId } from '@/lib/session'
import { useDocumentStore } from './documentStore'
import { retryOrFeedback } from './failureExit'
import { useUiStore } from './uiStore'

/**
 * 导出可复现项目包（.tavotto）：布局 + 引用素材 + 源脚本 + 清单。
 *
 * **它从导出对话框搬到了这里**（Prompt 12 §五）：打包出的是一个"项目"，
 * 不是一张图，和「导出这张图」放在同一个弹窗里让两件事互相冒充。搬走
 * 不等于砍掉——导入就在下面一行，一进一出终于在同一个菜单里。
 */
export async function exportPackage(attempt = 0): Promise<void> {
  const ui = useUiStore.getState()
  const doc = useDocumentStore.getState()
  // 「再试一次」只重做**这一份**排版的导出：点的时候换了项目 / 排版就只收提示，不把别的东西打成包
  const documentId = doc.documentId
  const projectId = currentProjectId()
  try {
    const res = await createPackage(doc.projectMeta.name || doc.doc.name, doc.buildProject(), {})
    ui.setStatus(msg('status.packaged', { name: res.name, count: res.assets }, 'workspace'), 'done')
  } catch (e) {
    ui.setStatus(
      msg(
        'status.packageFailed',
        { error: e instanceof Error ? e.message : String(e) },
        'workspace',
      ),
      'error',
      {
        owner: PACKAGE_EXPORT_OWNER,
        action: retryOrFeedback({
          owner: PACKAGE_EXPORT_OWNER,
          attempt,
          run: (next) => {
            if (useDocumentStore.getState().documentId !== documentId || currentProjectId() !== projectId) return
            return exportPackage(next)
          },
        }),
      },
    )
  }
}

/** 导出 / 打开项目包失败那条提示的主人：点「再试一次」先收掉自己那一条 */
const PACKAGE_EXPORT_OWNER = 'package.export.failure'
const PACKAGE_OPEN_OWNER = 'package.open.failure'

/**
 * 打开项目包；失败时「再试一次」重开**同一个文件**（文件对象在手里，不必重新选）。
 * 点的时候已换到别的项目就只收提示——别在不相干的项目里冒出一份新排版。
 */
export async function openPackageWithRetry(file: File, attempt: number): Promise<void> {
  const projectId = currentProjectId()
  try {
    await openPackageFile(file)
  } catch (e) {
    useUiStore.getState().setStatus(
      msg(
        'status.packageOpenFailed',
        { error: e instanceof Error ? e.message : String(e) },
        'workspace',
      ),
      'error',
      {
        owner: PACKAGE_OPEN_OWNER,
        action: retryOrFeedback({
          owner: PACKAGE_OPEN_OWNER,
          attempt,
          run: (next) => {
            if (currentProjectId() !== projectId) return
            return openPackageWithRetry(file, next)
          },
        }),
      },
    )
  }
}
