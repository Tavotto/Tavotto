/**
 * 把一个项目包文件（`.tavotto` / `.magplot` / zip）作为新文档打开：顶栏「导入项目包…」与画布拖放 / 粘贴共用这一条
 * （ADR 0176：拖进画布的项目包走现有的导入流程，不另起一套）。
 *
 * 后端检视（不写入图库）→ 作为新文档打开；有缺失素材时接到统一的重新链接对话框，绝不静默出空面板。
 * 成功的那句话在这里说；**失败原样抛出**，由调用方决定怎么说（顶栏说原因，画布拖放给「发送问题反馈」）。
 */
import { msg } from '@/i18n'
import { ApiError, openPackage } from '@/lib/api'
import { PACKAGE_EXT } from '@/lib/brand'
import { requestRelinkMissing } from '@/lib/clipboard'
import { openLayoutDocument } from '@/store/actions'
import { useProjectPackageInstallStore } from '@/store/projectPackageInstallStore'
import { useUiStore } from '@/store/uiStore'

/**
 * 唯一入口：后端按包内容（`layout.json` 的 schema + 清单的 `document_format`）
 * 分流，前端只看响应里的 `document_format`——不让用户选格式（#1921）。
 */
export async function openPackageFile(file: File): Promise<void> {
  const ui = useUiStore.getState()
  const res = await openPackage(file)
  if (res.document_format === 'v4') {
    // 计算图表便携包：交给检视 / 安装流程，不在这里当排版打开。
    await useProjectPackageInstallStore.getState().inspect(file)
    return
  }
  await openLayoutDocument(res.doc)
  const missing = requestRelinkMissing()
  const drift = res.drifted.length
  if (!missing && !drift) {
    ui.setStatus(msg('status.packageOpened', { createdAt: res.manifest.created_at ?? '' }, 'workspace'), 'done')
  } else if (drift) {
    ui.setStatus(msg('status.packageDrift', { count: drift }, 'workspace'), 'error')
  }
}

/** 打开文件选择器，把选中的包交给唯一入口；`.magplot` 是 0.7 时代的同结构包。 */
export function pickPackageFile(onFile: (file: File) => void = (file) => void openPackageFile(file)): void {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = `${PACKAGE_EXT},.magplot,.zip,application/zip`
  input.onchange = () => {
    const file = input.files?.[0]
    if (file) onFile(file)
  }
  input.click()
}

/** 包来自更新的 Tavotto（排版 schema 比本版本新）：调用方给「检查更新」，不说成坏包（#1921） */
export function isPackageTooNew(e: unknown): boolean {
  return e instanceof ApiError && e.body?.code === 'package_schema_too_new'
}
