/**
 * `frontend-state.json`：导出那一刻前端各个 store 的**结构摘要**（ADR 0016 §7）。
 *
 * 与 trace 的分工：trace 是「发生过什么」，快照是「现在是什么样」。#131 那类
 * 问题两者都需要——trace 说清了导致现状的那串操作，快照说清了用户此刻正
 * 盯着的状态（选中了几个元素、三个变体身份差在哪、有没有 preview 残留）。
 *
 * **它是读出来的，不是维护出来的**。诊断不持有任何影子状态——影子状态会
 * 漂移，而漂移的诊断比没有诊断更坏。所有字段都在这一刻现读业务 store。
 */
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import {
  exactPanelRender,
  panelDisplayView,
  panelRender,
  renderKeyOf,
  useRenderStore,
} from '@/store/renderStore'
import { getHistoryMode, previewSession } from '@/store/svgPreviewStore'
import type { CanvasObject, PanelObject } from '@/types/document'
import { documentDigest } from './digest'
import { safeGid, safeKey } from './sanitize'
import { fileHash, panelHash, previewHash, variantHash, variantHashOrNull } from './hash'
import { sessionElapsedMs } from './store'
import {
  SNAPSHOT_SCHEMA_VERSION,
  type FrontendDiagnosticSnapshot,
  type PanelSnapshot,
  type SelectionKind,
} from './types'

/** 面板载体类型。**不含面板名、文件名、stem**——那些是用户内容 */
function panelKindOf(panel: PanelObject): PanelSnapshot['kind'] {
  if (panel.fileKind === 'runtime') return 'runtime'
  if (panel.fileKind === 'raster') return 'image'
  if (panel.fileKind === 'pdf') return 'matplotlib'
  return 'unknown'
}

function selectionKindOf(gidCount: number, objectCount: number): SelectionKind {
  if (gidCount && objectCount) return 'mixed'
  if (gidCount) return 'element'
  if (objectCount) return 'object'
  return 'none'
}

/** 单个面板的三个变体身份 + 渲染态。几何写入安不安全全看这一行 */
export function panelSnapshot(panel: PanelObject): PanelSnapshot {
  const rs = useRenderStore.getState()
  const render = panelRender(rs, panel)
  const documentVariant = renderKeyOf(panel)
  const view = panelDisplayView(rs, panel)
  const displayVariant = view.sourceKey
  // **权威判据委托给 exactPanelRender**（ADR 0017），诊断不另立一份：
  // 报的必须就是护栏实际用的那个判据
  const exact = exactPanelRender(rs, panel)
  return {
    panel: panelHash(panel.id),
    file: fileHash(panel.fileId),
    kind: panelKindOf(panel),
    override_count: panel.overrides.length,
    document_variant: variantHash(documentVariant),
    display_variant: variantHashOrNull(displayVariant),
    authority_variant: exact ? variantHash(documentVariant) : null,
    // 画布上挂的那版就是文档这一版
    display_exact: displayVariant === documentVariant,
    // **几何权威是否精确**：量 bbox 的那份 manifest 就是文档这一版
    exact_manifest_available: !!exact,
    render_status: rs.byKey[documentVariant]?.status ?? 'idle',
    stale: !!rs.byKey[documentVariant]?.stale,
    element_count: render?.manifest?.elements.length ?? 0,
  }
}

const isPanel = (o: CanvasObject): o is PanelObject => o.type === 'panel'

/**
 * 当前前端状态的完整快照。纯读，无副作用，**不触发任何渲染**——
 * 导出诊断包绝不该让 matplotlib 多跑一次。
 */
export function buildFrontendDiagnosticSnapshot(): FrontendDiagnosticSnapshot {
  const doc = useDocumentStore.getState()
  const ui = useUiStore.getState()
  const selectedIds = useSelectionStore.getState().ids
  const panels = doc.doc.objects.filter(isPanel)
  const session = previewSession()

  return {
    schema_version: SNAPSHOT_SCHEMA_VERSION,
    session_ms: sessionElapsedMs(),
    document: {
      document_hash: documentDigest(doc.doc),
      object_count: doc.doc.objects.length,
      panel_count: panels.length,
      canvas_count: doc.canvases.length,
      history: {
        past: doc.past.length,
        future: doc.future.length,
        txn_open: doc.txn != null,
        // **只取内部 key**：UiMessage 的 values 里装着用户的文件名与属性值。
        // 再过一遍 safeKey——「只取 key」是调用点的自觉，safeKey 是判据
        txn_label_key: doc.txn ? safeKey(doc.txn.label.key) : null,
      },
    },
    selection: {
      active_panel: ui.elementPanelId ? panelHash(ui.elementPanelId) : null,
      selection_kind: selectionKindOf(ui.selectedGids.length, selectedIds.length),
      element_count: ui.selectedGids.length,
      // 技术 gid（axes_0.title）；sanitize 会把不符合 gid 形状的换成 hash
      element_gids: ui.selectedGids
        .slice(0, 24)
        .map(safeGid)
        .filter((g): g is string => g != null),
      object_count: selectedIds.length,
    },
    preview: {
      // 预览平面是单例：要么有一个在跑，要么没有。「有一个 settled 的还挂着」
      // 是正常的（等 reattach 认领），「有一个没 settled 的挂了很久」才是残留
      active_sessions: session && !session.settled ? 1 : 0,
      settled: session ? session.settled : null,
      history_mode: getHistoryMode(),
    },
    panels: panels.map(panelSnapshot),
  }
}

/** preview 会话的诊断身份（id 是进程内自增整数，仍走 hash 保持写法统一） */
export const previewSessionHash = (id: number): string => previewHash(id)
