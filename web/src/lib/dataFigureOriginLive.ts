/**
 * 「图没被改过」的现场读法：把 `lib/dataFigureOrigin.figureUntouched` 要的事实从文档 / 素材 store 里取出来。
 * 属性页的那一行（渲染时）与换图前的复核（等了脚本跑完之后）读的是同一份，所以放一处。
 */
import { figureUntouched, type FigureOrigin } from '@/lib/dataFigureOrigin'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'
import type { PanelObject } from '@/types/document'

/** 这块面板此刻是不是还没被改过（读 store 的现值；不知道的一律 false） */
export function panelUntouchedNow(panel: PanelObject, origin: FigureOrigin): boolean {
  const rt = (useRuntimeAssetStore.getState().assets ?? []).find((a) => a.id === panel.fileId)
  const bakedCount = useAssetStore.getState().byId[panel.fileId]?.baked_overrides?.length ?? 0
  return figureUntouched(useDocumentStore.getState().doc, panel, origin, { runtime: rt, bakedCount })
}
