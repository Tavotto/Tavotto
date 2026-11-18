/**
 * 「从数据到图」store 的宿主：把项目（`projectStore.writable`）、刷新（`liveSync`）与「打开并运行」（`projectPreparationStore`）接给它。
 * 住在这里而不是 store 里，是为了不让 `dataFigureStore` ↔ `projectStore` / `liveSync` 成环（`importArchitecture` 门禁）；
 * 数据页（`DataPage`）一挂上就 import 本模块完成注册。
 */
import { currentProjectId } from '@/lib/session'
import { configureDataFigureHost } from '@/store/dataFigureStore'
import { refreshProjectNow } from '@/store/liveSync'
import { scriptTarget, useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspace'

configureDataFigureHost({
  openDrawer: () => useUiStore.getState().setLeftTab('data'),
  openScript: async (script, still) => {
    // 脚本路径是 A 项目里的相对路径：刷新挂起期间用户切到别的项目，就整个放弃（不打开、不提示）。
    // 发起那一刻的项目标识 + store 的项目代际（`still`，`clear()` 换代）两道都核，`open()` 只认「此刻的项目」，不能等它之后再拦
    const pj = currentProjectId()
    const same = () => still() && currentProjectId() === pj
    // 项目先刷新到新脚本已经在的状态，再走素材库里「打开并运行」那条现有路径
    await refreshProjectNow().catch(() => undefined)
    if (!same()) return
    await useProjectPreparationStore.getState().open(scriptTarget(script, { reuse: true }), { run: true })
  },
  projectWritable: () => useProjectStore.getState().project?.writable,
  activeCanvasId: () => useDocumentStore.getState().activeCanvasId ?? null,
  notify: (message) => useUiStore.getState().setStatus(message, 'info'),
  dismissAddedNote: () => useWorkspaceStore.setState({ addedForEdit: null }),
})
