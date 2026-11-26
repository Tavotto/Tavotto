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
import { configureDataFigureHost, reserveBackendSession } from '@/store/dataFigureStore'
import { configureFigureOriginHost, useFigureOriginStore } from '@/store/figureOriginStore'
import { panelOfScript, panelUntouchedNow } from '@/lib/dataFigureOriginLive'
import { normScript } from '@/lib/dataFigureOrigin'
import { enterElementEdit, replaceRuntimeFigure } from '@/store/actions'
import { useSelectionStore } from '@/store/selectionStore'
import { selectErrorBarWhenDrawn } from '@/store/figureSwitchSelect'
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

// 点选误差线换 SD / SEM / 95% CI（`figureOriginStore`）：新脚本落盘之后，在同一块面板上把图原位换成它画的
const quietGuideCard = () => {
  // 项目刷新会重扫，没运行过的新脚本会被当成「发现绘图脚本」弹准备卡；这个脚本是我们自己生成、自己运行的（同批量出图的做法）
  const scan = useProjectScanStore.getState().scan
  if (scan) markAutoShown(scan.project_id)
}
configureFigureOriginHost({
  reserveSession: reserveBackendSession,
  notify: (message) => useUiStore.getState().setStatus(message, 'info'),
  replaceFigure: async ({ oldScript, newScript, origin, still }) => {
    const pj = currentProjectId()
    const same = () => still() && currentProjectId() === pj
    // 换的是用户此刻在编辑的那一块（激活画布上、画旧脚本的面板）；等脚本跑的这段时间里他换了画布 / 删了它 / 改了它，一律不换
    const canvasId = useDocumentStore.getState().activeCanvasId
    const preferGid = useUiStore.getState().selectedGids[0] ?? null
    const target = panelOfScript(oldScript, useWorkspaceStore.getState().activePanelId)
    if (!target || !panelUntouchedNow(target, origin)) return null
    quietGuideCard()
    await refreshProjectNow().catch(() => undefined)
    if (!same()) return null
    await useScriptRunStore.getState().run(newScript)
    if (!same()) return null
    const run = useScriptRunStore.getState().byScript[newScript]
    const desc = run?.phase === 'captured_one' ? run.descriptors[0] : undefined
    if (!desc || normScript(desc.script) !== normScript(newScript)) return null
    // 复核：还是那张画布、还是那一块面板、图仍然没被改过
    const panel = panelOfScript(oldScript, target.id)
    if (!panel || panel.id !== target.id || useDocumentStore.getState().activeCanvasId !== canvasId || !panelUntouchedNow(panel, origin)) return null
    if (!replaceRuntimeFigure(panel.id, desc)) return null
    // 回到图内编辑（与「打开并运行」之后落在的状态一样）：选中这块面板
    useSelectionStore.getState().set([panel.id])
    enterElementEdit(panel.id)
    // 新图的 manifest 到了就选回误差棒（属性页停在误差棒页，那一行显示新类型）
    selectErrorBarWhenDrawn(panel.id, preferGid)
    return { fingerprint: desc.source_fingerprint }
  },
})

// 面板第一次出现在文档里：记下它的来源指纹（之后「脚本是否被改过」都对着它比）。只在有还没记到指纹的来历时才扫文档
const bindOriginFingerprints = () => {
  const reg = useFigureOriginStore.getState()
  const need = new Set(reg.unbound())
  if (!need.size) return
  const s = useDocumentStore.getState()
  const objects = [...s.doc.objects, ...s.canvases.filter((c) => c.id !== s.activeCanvasId).flatMap((c) => c.objects)]
  for (const o of objects) {
    if (o.type !== 'panel' || o.fileKind !== 'runtime' || !o.script || !o.source?.fingerprint) continue
    const key = normScript(o.script)
    if (need.has(key)) reg.bindFingerprint(key, o.source.fingerprint)
  }
}
useDocumentStore.subscribe(bindOriginFingerprints)
useFigureOriginStore.subscribe(bindOriginFingerprints)
