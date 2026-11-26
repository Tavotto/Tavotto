import type { CanvasObject } from '@/types/document'
import { objectRotation } from '@/types/document'
import { boundsOf, visualBounds, type Rect } from './geometry'
import { remapGroupPath } from './groupTree'
import { newId } from './id'
import { rotateObjectAbout } from './transform'
import { remapLineIds } from '@/lib/pathText'

/**
 * 阵列（ADR 0185 M6）：把选区当一个整体，按网格或沿圆周复制几份。**纯函数**，结果只用已有字段（位置、旋转角），导出端没有新东西；
 * 同源靠参考实现对拍（`tests/golden/array_vectors.json`，`scripts/dev/gen_array_vectors.py`）。
 *
 * - 网格：`rows × cols`，第 (r, c) 份 = 原件平移 (c·(w + gapX), r·(h + gapY))；(0, 0) 是原件本身，不复制。w / h 是选区看得见的外接框。
 * - 环形：`count` 份（含原件）均匀排在圆周上，圆心在选区中心正下方 `radius` 处（原件在 12 点钟），第 i 份转过 i·360°/count；
 *   `rotate` 开着时每份绕自己的中心同样转过去（像钟面上的刻度），关着时只挪位置、方向不变。图（面板）只能整 90° 转：角度不是 90° 的倍数时只挪不转。
 */
export type ArraySpec =
  | { kind: 'grid'; rows: number; cols: number; gapX: number; gapY: number }
  | { kind: 'radial'; count: number; radius: number; rotate: boolean }

export const ARRAY_MAX = 400

/** 每一份相对原件的位移与转角（不含原件本身），按生成顺序 */
export function arrayOffsets(box: Rect, spec: ArraySpec): { dx: number; dy: number; deg: number }[] {
  const out: { dx: number; dy: number; deg: number }[] = []
  if (spec.kind === 'grid') {
    const rows = Math.max(1, Math.floor(spec.rows))
    const cols = Math.max(1, Math.floor(spec.cols))
    for (let r = 0; r < rows; r++)
      for (let c = 0; c < cols; c++) {
        if (!r && !c) continue
        out.push({ dx: c * (box.w + spec.gapX), dy: r * (box.h + spec.gapY), deg: 0 })
      }
    return out
  }
  const n = Math.max(1, Math.floor(spec.count))
  for (let i = 1; i < n; i++) {
    const a = (i * 2 * Math.PI) / n
    // 原件在圆心正上方：相对圆心 (0, −R)；转过 a（画布语义顺时针）后在 (R·sin a, −R·cos a)
    out.push({ dx: spec.radius * Math.sin(a), dy: spec.radius - spec.radius * Math.cos(a), deg: (i * 360) / n })
  }
  return out
}

/** 份数（含原件）；超过 `ARRAY_MAX` 的不做 */
export const arrayCount = (spec: ArraySpec): number =>
  spec.kind === 'grid' ? Math.max(1, Math.floor(spec.rows)) * Math.max(1, Math.floor(spec.cols)) : Math.max(1, Math.floor(spec.count))

/**
 * 生成副本：每一份是选区对象的整批克隆（新 id；组链整条换新、每份各一套，不和原件或别的份粘在一起），按位移 / 转角摆好。
 * 返回追加到文档末尾的对象（不含原件）。
 */
export function makeArray(objs: readonly CanvasObject[], spec: ArraySpec): CanvasObject[] {
  const box = boundsOf(objs.filter((o) => !o.hidden).map(visualBounds)) ?? boundsOf(objs.map(visualBounds))
  if (!box || !objs.length) return []
  const center: [number, number] = [box.x + box.w / 2, box.y + box.h / 2]
  const out: CanvasObject[] = []
  for (const off of arrayOffsets(box, spec)) {
    const groups = new Map<string, string>()
    const fresh = (g: string) => groups.get(g) ?? (groups.set(g, newId('g')), groups.get(g)!)
    const renamed = new Map<string, string>()
    const batch: CanvasObject[] = []
    for (const o of objs) {
      let copy = structuredClone(o) as CanvasObject
      if (spec.kind === 'radial' && spec.rotate && off.deg) {
        const canTurn = copy.type !== 'panel' || off.deg % 90 === 0
        if (canTurn) copy = rotateObjectAbout(copy, off.deg, center)
      }
      copy.id = newId(o.type[0])
      renamed.set(o.id, copy.id)
      copy.x += off.dx
      copy.y += off.dy
      remapGroupPath(copy, fresh)
      batch.push(copy)
    }
    remapLineIds(batch, renamed)
    out.push(...batch)
  }
  return out
}

/** 环形阵列转过之后对象的角度（给测试与说明用） */
export const turnedRotation = (o: CanvasObject, deg: number): number => objectRotation(o) + deg
