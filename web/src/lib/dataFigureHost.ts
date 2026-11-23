/**
 * 「从数据到图」store 的宿主：把项目（`projectStore.writable`）、刷新（`liveSync`）与「打开并运行」（`projectPreparationStore`）接给它。
 * 住在这里而不是 store 里，是为了不让 `dataFigureStore` ↔ `projectStore` / `liveSync` 成环（`importArchitecture` 门禁）；
 * 数据页（`DataPage`）一挂上就 import 本模块完成注册。
 */
import { markAutoShown } from '@/lib/guideCardSeen'
import { armBindingInherit } from '@/lib/dataFigureInherit'
import { currentProjectId } from '@/lib/session'
import { resolveDocumentSpec } from '@/lib/specBinding'
import { configureDataBatchHost } from '@/store/dataBatchStore'
import { configureDataFigureHost } from '@/store/dataFigureStore'
import { refreshProjectNow } from '@/store/liveSync'
import { useProjectScanStore } from '@/store/projectScanStore'
import { scriptTarget, useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useScriptRunStore } from '@/store/scriptRunStore'
import { useDocumentStore } from '@/store/documentStore'
import { useProfileStore } from '@/store/profileStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'
import { useWorkspaceStore } from '@/store/workspace'
import type { DocumentProfile } from '@/types/document'

/** 绑定 → 要发给后端的那份规范（快照优先 / 跟随全局 / 期刊覆盖并入，取舍只在 `resolveDocumentSpec`，后端不重做） */
const resolveSpec = (binding: DocumentProfile): Record<string, unknown> =>
  resolveDocumentSpec(binding, useProfileStore.getState().catalog()).profile as unknown as Record<string, unknown>

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
  canvasBinding: () => {
    const p = useDocumentStore.getState().doc.profile
    return p ? structuredClone(p) : null
  },
  resolveSpec,
  armInherit: armBindingInherit,
})

// 批量出图（一次选多份、各画一张）：只刷新素材清单、不打开；全部画成后左栏切到素材抽屉
configureDataBatchHost({
  refreshProject: () => refreshProjectNow(),
  runScript: (script) => useScriptRunStore.getState().run(script),
  showAssets: () => useUiStore.getState().setLeftTab('assets'),
  showData: () => useUiStore.getState().setLeftTab('data'),
  // 项目刷新会重扫，新脚本（还没运行过、没登记）会被当成「发现绘图脚本」自动弹准备卡；单份流程有「打开并运行」的条目顶着，批量没有。
  // 复用「每个项目只自动弹一次」的本机标记（`lib/guideCardSeen`）：标上之后这个项目不再自动弹，已经开着的卡与手动打开不受影响
  quietGuideCard: () => {
    const scan = useProjectScanStore.getState().scan
    if (scan) markAutoShown(scan.project_id)
  },
  projectWritable: () => useProjectStore.getState().project?.writable,
  canvasBinding: () => {
    const p = useDocumentStore.getState().doc.profile
    return p ? structuredClone(p) : null
  },
  resolveSpec,
})
