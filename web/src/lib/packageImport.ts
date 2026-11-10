/**
 * 把一个项目包文件（`.tavotto` / `.magplot` / zip）作为新文档打开：顶栏「导入项目包…」与画布拖放 / 粘贴共用这一条
 * （ADR 0176：拖进画布的项目包走现有的导入流程，不另起一套）。
 *
 * 后端检视（不写入图库）→ 作为新文档打开；有缺失素材时接到统一的重新链接对话框，绝不静默出空面板。
 * 成功的那句话在这里说；**失败原样抛出**，由调用方决定怎么说（顶栏说原因，画布拖放给「发送问题反馈」）。
 */
import { msg } from '@/i18n'
import { openPackage } from '@/lib/api'
import { requestRelinkMissing } from '@/lib/clipboard'
import { openLayoutDocument } from '@/store/actions'
import { useUiStore } from '@/store/uiStore'

export async function openPackageFile(file: File): Promise<void> {
  const ui = useUiStore.getState()
  const res = await openPackage(file)
  await openLayoutDocument(res.doc)
  const missing = requestRelinkMissing()
  const drift = res.drifted.length
  if (!missing && !drift) {
    ui.setStatus(msg('status.packageOpened', { createdAt: res.manifest.created_at ?? '' }, 'workspace'), 'done')
  } else if (drift) {
    ui.setStatus(msg('status.packageDrift', { count: drift }, 'workspace'), 'error')
  }
}
