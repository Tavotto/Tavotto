import { useAssetStore } from '@/store/assetStore'
import type { PanelInfo } from '@/lib/api'

/** 素材 id 是图库相对路径（可能带子目录）；stem = 去目录去扩展名（**文件名** stem，不是「哪张图」）。 */
export function stemOf(fileId: string): string {
  const base = fileId.split(/[/\\]/).pop() ?? fileId
  const dot = base.lastIndexOf('.')
  return dot > 0 ? base.slice(0, dot) : base
}

/**
 * 磁盘面板属于哪张图：后端按注册表给的 `stem`（`Registry.artifact_stem`，#1251），没有就是文件名 stem
 * （老项目 / 老后端）。凡主语是「这张图」的匹配（交接 `?open=`、服务端事件里的 stems、重建为可编辑图……）
 * 都走这里，不再各自 `stemOf(fileId)`；主语是「文件名」的（导出文件名、写回目标文件名）才用 `stemOf`。
 */
export function panelStemOf(p: Pick<PanelInfo, 'id' | 'stem'>): string {
  return p.stem ?? stemOf(p.id)
}

/** 文档对象上只有 fileId：从素材清单（`/api/panels`）里查出 `stem`，查不到退回文件名 stem。 */
export function panelStemOfFileId(fileId: string): string {
  const info = useAssetStore.getState().byId[fileId]
  return info ? panelStemOf(info) : stemOf(fileId)
}
