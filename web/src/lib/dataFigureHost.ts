/**
 * 「从数据到图」store 的宿主：把项目（`projectStore.writable`）、刷新（`liveSync`）与「打开并运行」（`projectPreparationStore`）接给它。
 * 住在这里而不是 store 里，是为了不让 `dataFigureStore` ↔ `projectStore` / `liveSync` 成环（`importArchitecture` 门禁）；
 * 数据页（`DataPage`）一挂上就 import 本模块完成注册。
 */
import { configureDataFigureHost } from '@/store/dataFigureStore'
import { refreshProjectNow } from '@/store/liveSync'
import { scriptTarget, useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'

configureDataFigureHost({
  openDrawer: () => useUiStore.getState().setLeftTab('data'),
  openScript: async (script) => {
    // 项目先刷新到新脚本已经在的状态，再走素材库里「打开并运行」那条现有路径
    await refreshProjectNow().catch(() => undefined)
    await useProjectPreparationStore.getState().open(scriptTarget(script, { reuse: true }), { run: true })
  },
  projectWritable: () => useProjectStore.getState().project?.writable,
  activeCanvasId: () => useDocumentStore.getState().activeCanvasId ?? null,
})
