/**
 * 图内多选对齐 / 分布的**离散动作**（issue #131）。
 *
 * 以前这件事是 `AlignSection` 直接干的：items 在 React render 阶段就算好，
 * 每个 `AlignEntry.write()` 闭包里封着**那一刻**的 anchor、bbox、panel、
 * manifest。点击可能发生在几百毫秒之后——期间用户改了字号、拖了一下、
 * 撤销过、或者新变体的渲染还没回来。用旧闭包提交等于拿一份过期几何写文档。
 *
 * 现在按钮只发意图，几何在**点击那一刻**从 store 现取：
 *
 *   1. 先收掉还开着的连续手势（否则这次 commit 会被并进上一条历史）；
 *   2. 从 documentStore 现取面板；
 *   3. 取**几何权威** manifest——`exactPanelRender`，拿不到就明确拒绝，
 *      绝不退回 `latest[fileId]`；
 *   4. 现取 selectedGids 与画布标注选区；
 *   5. 重算条目、重算落框、过滤 no-op、校验数值；
 *   6. 一次 standalone commit，最多触发一次权威渲染。
 *
 * 「拒绝」是刻意的：不排队、不延迟自动补做。悄悄在两秒后替用户执行一个
 * 位置操作，比当场说「正在同步」危险得多。
 */
import { layoutBoxes } from '@/lib/axesLayout'
import {
  alignEntries,
  annotationAlignEntries,
  isAnnotationEntry,
  panelFullRect,
  type MixedEntry,
} from '@/lib/elementGeom'
import type { AlignMode } from '@/lib/geometry'
import { msg } from '@/i18n'
import { applyMixedAlign } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { finishActiveGesture } from '@/store/gestureCoordinator'
import { exactPanelRender, useRenderStore } from '@/store/renderStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import { traceGeometry } from '@/lib/authorityTrace'
import type { PanelObject } from '@/types/document'

/** 拒绝的原因；调用方据此决定提示什么（都不写文档、不进历史、不渲染） */
export type AlignBlocked =
  /** 面板没了 / 不是面板 */
  | 'no-panel'
  /** 几何权威还没就位（新变体在渲染、脚本刚变过） */
  | 'syncing'
  /** 够格参与的几何目标不足两个 */
  | 'too-few'
  /** 算出来的框或落值不是有限数 —— 整批取消，不部分写入 */
  | 'invalid'
  /** 谁都不用动 */
  | 'noop'

export type AlignResult =
  | { ok: true; patches: number; moves: number }
  | { ok: false; reason: AlignBlocked }

/** 数字校验：非有限数一律当作算坏了 */
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const finiteBox = (b: readonly number[]): boolean => b.length === 4 && b.every(finite)

/**
 * 对当前选区执行一次对齐 / 分布。
 *
 * 返回值是给 UI 用的（提示什么、要不要置灰），**不抛异常**——这是一个按钮的
 * 点击处理器，任何一条失败路径都必须是「什么都没发生」而不是半个动作。
 */
export function alignSelectedPanelElements(panelId: string, mode: AlignMode): AlignResult {
  // 1. 先把还开着的那一轮连续编辑收掉：事务、安静计时器、预览会话、定稿渲染
  //    一次收干净。不收的话下面这次 commit 会被静默并进上一条历史。
  finishActiveGesture()

  // 2. 面板从文档现取，不信任何调用方手里那份
  const panel = useDocumentStore
    .getState()
    .doc.objects.find((o) => o.id === panelId)
  if (panel?.type !== 'panel') return blocked(panelId, mode, 'no-panel')

  // 3. 几何权威。拿不到就到此为止——这一步是 issue #131 的闸门本身
  const exact = exactPanelRender(useRenderStore.getState(), panel)
  if (!exact?.manifest) return blocked(panelId, mode, 'syncing')
  const manifest = exact.manifest

  // 4. 选区现取（组件那一轮之后用户完全可能又加选/减选过）
  const gids = useUiStore.getState().selectedGids
  const selIds = useSelectionStore.getState().ids
  const objects = useDocumentStore.getState().doc.objects
  const annotations = objects.filter(
    (o) =>
      selIds.includes(o.id) &&
      (o.type === 'text' || o.type === 'arrow' || o.type === 'shape'),
  )

  // 5. 重算条目
  const items: MixedEntry[] = [
    ...alignEntries(panel, manifest, gids),
    ...annotationAlignEntries(panel, annotations),
  ]
  if (items.length < 2) return blocked(panelId, mode, 'too-few')
  // 进来的框必须先是有限数，否则 min/max 会把 NaN 传染给整批
  if (!items.every((it) => finiteBox(it.box))) return blocked(panelId, mode, 'invalid')

  const boxes = layoutBoxes(items, mode)
  if (!boxes.size) return blocked(panelId, mode, 'noop')

  // 6. 落成补丁。**任何一条不合法就整批取消**：一次多选对齐要么全部基于
  //    同一份精确 manifest 成立，要么一条都不写。
  const full = panelFullRect(panel)
  const patches: { gid: string; prop: string; value: unknown }[] = []
  const moves: { id: string; x: number; y: number }[] = []
  for (const it of items) {
    const next = boxes.get(it.key)
    if (!next) continue // layoutBoxes 已经把 no-op 摘掉了
    if (!finiteBox(next)) return blocked(panelId, mode, 'invalid')
    if (isAnnotationEntry(it)) {
      const x = full.x + next[0] * full.w
      const y = full.y + next[1] * full.h
      if (!finite(x) || !finite(y)) return blocked(panelId, mode, 'invalid')
      const obj = objects.find((o) => o.id === it.objectId)
      // 对象在这一刻已经不在了（别处删掉）：整批取消，不做部分成功
      if (!obj) return blocked(panelId, mode, 'invalid')
      moves.push({ id: it.objectId, x, y })
    } else {
      const patch = it.write(next)
      const v = patch.value
      if (!Array.isArray(v) || !v.length || !v.every(finite)) {
        return blocked(panelId, mode, 'invalid')
      }
      // gid 必须还在权威 manifest 里——写一条指不到东西的 override 等于
      // 当场制造一个孤儿
      if (!manifest.elements.some((e) => e.gid === patch.gid || e.gid === it.key)) {
        return blocked(panelId, mode, 'invalid')
      }
      patches.push(patch)
    }
  }

  // 值与现有 override 逐字相同的不重写：override 数组顺序也是变体键的一部分，
  // 白写一条等于换一个键 = 一次完全没必要的重渲染
  const fresh = patches.filter((p) => {
    const cur = panel.overrides.find((o) => o.gid === p.gid && o.prop === p.prop)
    return !cur || JSON.stringify(cur.value) !== JSON.stringify(p.value)
  })
  if (!fresh.length && !moves.length) return blocked(panelId, mode, 'noop')

  traceGeometry('align.commit', {
    panelId,
    mode,
    authorityKey: exact.lastPatches,
    patches: fresh.map((p) => `${p.gid}:${p.prop}`),
    moves: moves.length,
  })
  applyMixedAlign(panelId, msg(`alignMode.${mode}`, undefined, 'inspector'), fresh, moves)
  return { ok: true, patches: fresh.length, moves: moves.length }
}

function blocked(panelId: string, mode: AlignMode, reason: AlignBlocked): AlignResult {
  traceGeometry('align.blocked', { panelId, mode, reason })
  return { ok: false, reason }
}

/**
 * 对齐按钮此刻能不能点。与 `alignSelectedPanelElements` 共用同一个权威判据
 * ——两份判据迟早分叉，而分叉的表现正是「按钮亮着、点下去什么都不发生」。
 */
export function alignAuthorityReady(panel: PanelObject | null | undefined): boolean {
  if (!panel) return false
  return !!exactPanelRender(useRenderStore.getState(), panel)
}
