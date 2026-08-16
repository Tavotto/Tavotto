import { create } from 'zustand'
import { newId } from '@/lib/id'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useSelectionStore } from '@/store/selectionStore'
import { useUiStore } from '@/store/uiStore'
import type { CanvasObject, LayoutGroup, PanelObject } from '@/types/document'
import { objectLabel } from '@/types/document'

/**
 * 对象剪贴板：⌘C/⌘V，走系统剪贴板（JSON + 魔数）。
 *
 * - 系统剪贴板意味着**跨标签页 / 跨布局文档**天然可用；
 * - 只有画布上有选中对象时 ⌘C 才写入剪贴板，绝不劫持普通文本复制；
 *   ⌘V 读到的不是本工具的负载时按无事发生处理（不吞别人的粘贴）；
 * - 粘贴保留相对位置、层级次序、成组关系与图内 overrides；
 * - 目标环境缺素材（跨图库 / 素材被删）时弹重新链接对话框，
 *   绝不静默生成空面板。
 */

import { CLIPBOARD_FORMAT, LEGACY_CLIPBOARD_FORMAT } from '@/lib/brand'

/** 写出用新魔数；读取同时接受旧魔数（换版本前复制的内容仍可粘贴） */
const MAGIC = CLIPBOARD_FORMAT
const MAGICS: string[] = [CLIPBOARD_FORMAT, LEGACY_CLIPBOARD_FORMAT]

interface ClipPayload {
  magic: string
  sourceDocId: string
  /** 复制发生时的画布；旧负载无此字段（单画布时代 doc 即 canvas） */
  sourceCanvasId?: string
  /** 数组序 = 原文档 z 序（底 → 顶） */
  objects: CanvasObject[]
  /** 完整包含在选区里的布局组约束 */
  layoutGroups: LayoutGroup[]
}

export async function copySelectedObjects(): Promise<boolean> {
  const ids = useSelectionStore.getState().ids
  if (!ids.length) return false
  const { doc, documentId, activeCanvasId } = useDocumentStore.getState()
  const objects = doc.objects.filter((o) => ids.includes(o.id))
  if (!objects.length) return false
  const carriedGroups = (doc.layoutGroups ?? []).filter((g) => {
    const members = doc.objects.filter((o) => o.groupId === g.id)
    return members.length >= 2 && members.every((o) => ids.includes(o.id))
  })
  const payload: ClipPayload = {
    magic: MAGIC,
    sourceDocId: documentId,
    sourceCanvasId: activeCanvasId,
    objects: structuredClone(objects),
    layoutGroups: structuredClone(carriedGroups),
  }
  try {
    await navigator.clipboard.writeText(JSON.stringify(payload))
  } catch {
    useUiStore.getState().setStatus('无法访问剪贴板（浏览器权限被拒）', 'error')
    return false
  }
  useUiStore
    .getState()
    .setStatus(
      objects.length === 1
        ? `已复制 ${objectLabel(objects[0])}`
        : `已复制 ${objects.length} 个对象（可粘贴到其他布局文档）`,
    )
  return true
}

export function parsePayload(text: string): ClipPayload | null {
  if (!MAGICS.some((m) => text.includes(m))) return null
  try {
    const data = JSON.parse(text)
    if (!MAGICS.includes(data?.magic) || !Array.isArray(data.objects)) return null
    return data as ClipPayload
  } catch {
    return null
  }
}

/* --------------------------- 缺失素材的重新链接 ----------------------------- */

export interface MissingAsset {
  fileId: string
  name: string
  /** 引用它的剪贴板面板数量 */
  count: number
  /** 用户选择：重新链接到哪个现有素材；undefined = 跳过 */
  relinkTo?: string
}

interface ClipboardState {
  /**
   * 等待用户处理缺失素材：
   * - paste：粘贴流程，确认后把剪贴板对象落进文档
   * - relink：项目包导入后就地重链接当前文档里的缺失面板
   */
  pending:
    | { mode: 'paste'; payload: ClipPayload; missing: MissingAsset[] }
    | { mode: 'relink'; missing: MissingAsset[] }
    | null
  setPending: (p: ClipboardState['pending']) => void
}

export const useClipboardStore = create<ClipboardState>((set) => ({
  pending: null,
  setPending: (pending) => set({ pending }),
}))

/** 项目包导入后：把当前文档里缺失素材的面板交给重链接对话框处理 */
export function requestRelinkMissing(): number {
  const { doc } = useDocumentStore.getState()
  const assets = useAssetStore.getState().byId
  const missingMap = new Map<string, MissingAsset>()
  for (const o of doc.objects) {
    if (o.type !== 'panel' || assets[o.fileId]) continue
    const cur = missingMap.get(o.fileId)
    if (cur) cur.count += 1
    else {
      missingMap.set(o.fileId, {
        fileId: o.fileId,
        name: o.name ?? o.fileId.split('/').pop() ?? o.fileId,
        count: 1,
      })
    }
  }
  if (missingMap.size) {
    useClipboardStore.getState().setPending({ mode: 'relink', missing: [...missingMap.values()] })
  }
  return missingMap.size
}

/** 就地重链接：把文档里引用缺失素材的面板换到所选素材（一条历史）。 */
export function materializeRelink(resolved: MissingAsset[]): void {
  const assets = useAssetStore.getState().byId
  const relink = resolved.filter((m) => m.relinkTo && assets[m.relinkTo])
  if (!relink.length) {
    useUiStore.getState().setStatus('未重链接任何面板；缺失素材的面板保持原样')
    return
  }
  const byFileId = new Map(relink.map((m) => [m.fileId, assets[m.relinkTo!]]))
  useDocumentStore.getState().commit(`重新链接 ${relink.length} 个素材`, (d) => {
    for (const o of d.objects) {
      if (o.type !== 'panel') continue
      const info = byFileId.get(o.fileId)
      if (!info) continue
      o.fileId = info.id
      o.fileKind = info.kind
      o.nativeW = info.native_w_mm
      o.nativeH = info.native_h_mm
      o.pxW = info.px_w
      o.script = info.script ?? null
      o.cost = info.cost
      o.name = info.name
      o.overrides = info.baked_overrides ? structuredClone(info.baked_overrides) : []
    }
  })
  useUiStore.getState().setStatus(`已重新链接 ${relink.length} 个素材（⌘Z 可撤销）`)
}

/** ⌘V 入口。返回 true = 消费了这次粘贴（是本工具的对象负载）。 */
export async function pasteObjects(): Promise<boolean> {
  let text = ''
  try {
    text = await navigator.clipboard.readText()
  } catch {
    return false // 权限被拒：不打扰（可能根本不是我们的粘贴）
  }
  const payload = parsePayload(text)
  if (!payload) return false

  const assets = useAssetStore.getState().byId
  const missingMap = new Map<string, MissingAsset>()
  for (const o of payload.objects) {
    if (o.type !== 'panel') continue
    const p = o as PanelObject
    if (assets[p.fileId]) continue
    const cur = missingMap.get(p.fileId)
    if (cur) cur.count += 1
    else {
      missingMap.set(p.fileId, {
        fileId: p.fileId,
        name: p.name ?? p.fileId.split('/').pop() ?? p.fileId,
        count: 1,
      })
    }
  }

  if (missingMap.size) {
    useClipboardStore.getState().setPending({
      mode: 'paste',
      payload,
      missing: [...missingMap.values()],
    })
    return true
  }
  materializePaste(payload, [])
  return true
}

/** 真正落盘：一条历史记录。resolved 为缺失素材的处置结果。 */
export function materializePaste(payload: ClipPayload, resolved: MissingAsset[]): void {
  const { doc, documentId, activeCanvasId, commit } = useDocumentStore.getState()
  const assets = useAssetStore.getState().byId
  // 「原位 +4mm 错开」只对**同一张画布**成立；跨画布粘贴保持原坐标。
  // 旧负载没有 sourceCanvasId（单画布时代），同文档即同画布。
  const sameDoc =
    payload.sourceDocId === documentId &&
    (payload.sourceCanvasId == null || payload.sourceCanvasId === activeCanvasId)
  const relink = new Map(resolved.map((m) => [m.fileId, m.relinkTo]))

  const idMap = new Map<string, string>()
  const groupMap = new Map<string, string>()
  const clones: CanvasObject[] = []
  let skipped = 0

  for (const src of payload.objects) {
    if (src.type === 'panel') {
      const missing = relink.has(src.fileId) ? relink.get(src.fileId) : undefined
      if (relink.has(src.fileId) && !missing) {
        skipped += 1
        continue // 用户选择跳过：不生成空面板
      }
    }
    const copy = structuredClone(src)
    const nid = newId(src.type[0])
    idMap.set(src.id, nid)
    copy.id = nid
    if (copy.groupId) {
      const next = groupMap.get(copy.groupId) ?? newId('g')
      groupMap.set(copy.groupId, next)
      copy.groupId = next
    }
    if (copy.type === 'panel') {
      const target = relink.get(copy.fileId)
      if (target) {
        const info = assets[target]
        if (info) {
          copy.fileId = info.id
          copy.fileKind = info.kind
          copy.nativeW = info.native_w_mm
          copy.nativeH = info.native_h_mm
          copy.pxW = info.px_w
          copy.script = info.script ?? null
          copy.cost = info.cost
          copy.name = info.name
          // overrides 绑定在原脚本元素上，跨素材不可靠 → 换链接即清空
          copy.overrides = info.baked_overrides ? structuredClone(info.baked_overrides) : []
        }
      }
    }
    if (sameDoc) {
      copy.x += 4
      copy.y += 4
    } else {
      // 跨文档：保持原坐标，越界的钳回页面内
      copy.x = Math.min(Math.max(copy.x, -copy.w * 0.9), doc.page.w - copy.w * 0.1)
      copy.y = Math.min(Math.max(copy.y, -copy.h * 0.9), doc.page.h - copy.h * 0.1)
    }
    clones.push(copy)
  }

  if (!clones.length) {
    useUiStore.getState().setStatus('没有可粘贴的对象（缺失素材均被跳过）', 'error')
    return
  }

  const carriedGroups = payload.layoutGroups
    .filter((g) => groupMap.has(g.id))
    .map((g) => ({
      ...structuredClone(g),
      id: groupMap.get(g.id)!,
      order: g.order.map((oid) => idMap.get(oid)).filter((x): x is string => !!x),
    }))

  commit(`粘贴 ${clones.length} 个对象`, (d) => {
    d.objects.push(...clones)
    if (carriedGroups.length) {
      d.layoutGroups = [...(d.layoutGroups ?? []), ...carriedGroups]
    }
  })
  useSelectionStore.getState().set(clones.map((o) => o.id))
  useUiStore
    .getState()
    .setStatus(
      `已粘贴 ${clones.length} 个对象` +
        (skipped ? `（跳过 ${skipped} 个缺失素材的面板）` : '') +
        '（⌘Z 可撤销）',
    )
}
