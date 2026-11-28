import { CHART_PACKAGE_FORMAT, createPackage } from '@/lib/api'
import { msg, t } from '@/i18n'
import { PRODUCT_NAME } from '@/lib/brand'
import { isPackageTooNew, openPackageFile } from '@/lib/packageImport'
import { currentProjectId } from '@/lib/session'
import { useDocumentStore } from './documentStore'
import { retryOrFeedback, tooNewAction } from './failureExit'
import { useUiStore } from './uiStore'

/**
 * 导出可复现项目包（.tavotto）：布局 + 引用素材 + 源脚本 + 清单。
 *
 * **它从导出对话框搬到了这里**（Prompt 12 §五）：打包出的是一个"项目"，
 * 不是一张图，和「导出这张图」放在同一个弹窗里让两件事互相冒充。搬走
 * 不等于砍掉——导入就在下面一行，一进一出终于在同一个菜单里。
 */
/** 同一份文档连点两次导出：先发回执不许覆盖后发那次的结局。 */
let packageExportSequence = 0

type PackageScope = 'project' | 'canvas' | { canvasIds: readonly string[] }

export async function exportPackage(scope: PackageScope = 'project', attempt = 0): Promise<void> {
  const ui = useUiStore.getState()
  const doc = useDocumentStore.getState()
  // 「再试一次」只重做**这一份**排版的导出：点的时候换了项目 / 排版就只收提示，不把别的东西打成包
  const sequence = ++packageExportSequence
  const documentId = doc.documentId
  const loadSeq = doc.loadSeq
  const projectId = currentProjectId()
  const canvasId = scope === 'canvas' ? doc.activeCanvasId : undefined
  const stillHere = () => {
    const now = useDocumentStore.getState()
    return sequence === packageExportSequence
      && now.documentId === documentId
      && now.loadSeq === loadSeq && currentProjectId() === projectId
      && (canvasId === undefined || now.activeCanvasId === canvasId)
  }
  try {
    const stem = canvasId === undefined ? doc.projectMeta.name || doc.doc.name : doc.doc.name
    const project = doc.buildProject()
    // 整份排版也走可安装的交换包，后端只在打包副本上写入包格式。
    const selection = scope === 'project'
      ? { canvasIds: project.canvases.map(canvas => canvas.id) }
      : scope === 'canvas' ? { canvasId } : scope
    const res = await createPackage(stem, project, {}, { pj: projectId, ...selection })
    if (!stillHere()) return
    if (res.missing_chart_sources?.length) {
      throw new Error(t('status.packageIncomplete', { ns: 'workspace', count: res.missing_chart_sources.length }))
    }
    const executionIncomplete = res.document_format === CHART_PACKAGE_FORMAT && res.execution_coverage?.state === 'incomplete'
    const message = res.document_format === CHART_PACKAGE_FORMAT && res.missing.length > 0
      ? msg(executionIncomplete ? 'status.packagedIncompleteWithExecution' : 'status.packagedIncomplete', { name: res.name, count: res.missing.length }, 'workspace')
      : executionIncomplete
        ? msg('status.packagedExecutionIncomplete', { name: res.name }, 'workspace')
        : msg('status.packaged', { name: res.name, count: res.assets }, 'workspace')
    ui.setStatus(message, 'done')
  } catch (e) {
    if (!stillHere()) return
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
            if (!stillHere()) return
            return exportPackage(scope, next)
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
    if (isPackageTooNew(e)) {
      useUiStore.getState().setStatus(msg('status.packageTooNew', { product: PRODUCT_NAME }, 'workspace'), 'error', {
        owner: PACKAGE_OPEN_OWNER,
        action: tooNewAction(),
      })
      return
    }
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
