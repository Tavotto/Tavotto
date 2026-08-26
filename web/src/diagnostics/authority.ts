/**
 * 几何权威不变式（ADR 0016 §6）——**诊断与运行时护栏共用同一份判据**。
 *
 * 一个面板身上同时存在三个变体身份：
 *
 *   document variant   文档说它现在是什么          renderKeyOf(panel)
 *   display variant    画布上此刻挂的那版 SVG      activeRenderKey(...)
 *   authority variant  **量 bbox 的那份 manifest 来自哪版**  manifestSourceKey(...)
 *
 * 三者在 render pending 期间不一致是**合法**的常态（不退回上一版的话，每敲
 * 一个字画布都会闪回磁盘原图）。真正危险的只有一种组合：**在
 * authority ≠ document 的时刻执行几何写入**——量的是 A 的坐标，写进的是 B 的
 * 文档。issue #131 的「布局丢失、错乱、撤销回不到正确位置」就是这个形状：
 * 错误的坐标**进了历史**，所以撤销回到的是「错乱之前」而不是用户以为的上一步。
 *
 * 所以这里不只记一笔，它**当场拒绝这次写入**。诊断告诉我们「为什么错」，
 * 护栏让这一类根本不再伤到用户。
 */
import { manifestSourceKey, renderKeyOf, useRenderStore, activeRenderKey } from '@/store/renderStore'
import { useDocumentStore } from '@/store/documentStore'
import type { PanelObject } from '@/types/document'
import { fileHash, panelHash, variantHash, variantHashOrNull } from './hash'
import { recordDiagnosticEvent, recordIfChanged } from './store'
import type { InvariantKind } from './types'

/** 某个面板此刻的三个变体身份。全部是**原始键**，进事件前才 hash */
export interface AuthorityView {
  panelId: string
  documentVariant: string
  displayVariant: string | null
  authorityVariant: string | null
  /** authority === document：几何写入此刻是否安全 */
  exact: boolean
}

/** 现读一个面板的三个身份。纯读，不触发渲染 */
export function readAuthority(panel: PanelObject): AuthorityView {
  const rs = useRenderStore.getState()
  const documentVariant = renderKeyOf(panel)
  const authorityVariant = manifestSourceKey(rs, panel)
  const hasSvg = !!rs.byKey[documentVariant]?.svg || !!rs.latest[panel.fileId]
  return {
    panelId: panel.id,
    documentVariant,
    displayVariant: hasSvg ? activeRenderKey(rs, panel) : null,
    authorityVariant,
    exact: authorityVariant === documentVariant,
  }
}

/** 按 id 现取面板再读——调用点闭包里那份 panel 可能已经是上一帧的了 */
export function readAuthorityById(panelId: string): AuthorityView | null {
  const panel = useDocumentStore.getState().doc.objects.find((o) => o.id === panelId)
  if (panel?.type !== 'panel') return null
  return readAuthority(panel)
}

/** 三个身份 → 事件字段（一律 hash） */
export function authorityFields(view: AuthorityView): {
  document_variant: string
  display_variant: string | null
  authority_variant: string | null
} {
  return {
    document_variant: variantHash(view.documentVariant),
    display_variant: variantHashOrNull(view.displayVariant),
    authority_variant: variantHashOrNull(view.authorityVariant),
  }
}

/* -------------------------------------------------------------------------- */

/** 记一条不变式违反。**只记，不决定**——是否阻断由调用方按自己的语义定 */
export function recordInvariantViolation(
  kind: InvariantKind,
  operation: string,
  view: AuthorityView | null,
): void {
  recordDiagnosticEvent({
    type: 'invariant.violation',
    kind,
    operation,
    panel: view ? panelHash(view.panelId) : null,
    document_variant: view ? variantHash(view.documentVariant) : null,
    display_variant: view ? variantHashOrNull(view.displayVariant) : null,
    authority_variant: view ? variantHashOrNull(view.authorityVariant) : null,
  })
  // 开发构建当场喊出来：这一类上一次是靠用户报 issue 才发现的。
  // **生产不崩**——诊断护栏自己把编辑弄挂，比它要防的 bug 还糟。
  if (import.meta.env?.DEV) {
    console.error(
      `[几何不变式] ${operation}：${kind}`,
      view
        ? {
            document: variantHash(view.documentVariant),
            display: variantHashOrNull(view.displayVariant),
            authority: variantHashOrNull(view.authorityVariant),
          }
        : null,
    )
  }
}

export interface AuthorityCheck {
  /** 内部操作键（align.left / scale.group），不是文案 */
  operation: string
  panelId: string
  /**
   * **测量那一刻**的权威键，由调用方在算 bbox 的同一个渲染周期里捕获后传进来。
   *
   * 绝不能在这里现推导一次。要问的是「我量的那份几何来自哪一版」，不是
   * 「此刻的 manifest 来自哪一版」；两者之间隔着用户从看到界面到按下按钮
   * 的那段时间，现推导会让 TOCTOU 窗口内的不一致刚好检查不出来——而那个
   * 窗口正是 bug 发生的地方。
   */
  authority: string | null
}

export interface AuthorityVerdict {
  ok: boolean
  view: AuthorityView | null
  reason: 'panel_missing' | 'no_manifest' | 'authority_stale' | null
}

/**
 * 几何写入的前置不变式。**通过才允许写**。
 *
 * 阻断范围刻意收窄（ADR 0016 §6 的表）：输入是「别的元素的 manifest bbox」
 * 的操作（对齐 / 分布 / 等宽等高 / 成组缩放）走这条；输入是「用户对着看得见
 * 的像素拖指针」的操作只记录不阻断——那类操作与所见自洽，中途弹一个拒绝
 * 反而是伤害。
 */
export function verifyGeometryAuthority(check: AuthorityCheck): AuthorityVerdict {
  const view = readAuthorityById(check.panelId)
  if (!view) {
    recordInvariantViolation('geometry_authority_mismatch', check.operation, null)
    return { ok: false, view: null, reason: 'panel_missing' }
  }
  if (check.authority == null) {
    recordInvariantViolation('geometry_authority_mismatch', check.operation, view)
    return { ok: false, view, reason: 'no_manifest' }
  }
  if (check.authority !== view.documentVariant) {
    recordInvariantViolation('geometry_authority_mismatch', check.operation, view)
    return { ok: false, view, reason: 'authority_stale' }
  }
  return { ok: true, view, reason: null }
}

/* -------------------------------------------------------------------------- */

/**
 * 面板显示态采样。由渲染同步那一轮驱动（真状态变化才跑），载荷没变就不记
 * ——`display.source_changed` 每轮都算得出来，但绝大多数轮次三个身份一个字
 * 都没动，全记下来会把环里真正有用的事件挤掉。
 */
export function sampleDisplayState(panel: PanelObject): void {
  const rs = useRenderStore.getState()
  const view = readAuthority(panel)
  const own = rs.byKey[view.documentVariant]
  recordIfChanged(`display:${panel.id}`, {
    type: 'display.source_changed',
    panel: panelHash(panel.id),
    // fileId 可能就是一条路径——只进 hash，绝不原样进 trace
    file: fileHash(panel.fileId),
    ...authorityFields(view),
    exact: view.exact,
    render_status: own?.status ?? 'idle',
    stale: !!own?.stale,
  })
  recordIfChanged(`authority:${panel.id}`, {
    type: view.authorityVariant ? 'authority.ready' : 'authority.unavailable',
    panel: panelHash(panel.id),
    document_variant: variantHash(view.documentVariant),
    authority_variant: variantHashOrNull(view.authorityVariant),
  })
}
