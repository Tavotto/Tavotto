import { create } from 'zustand'
import { msg } from '@/i18n'
import { builderFaces, builderPlan, mergeFaces, type BuilderFace } from '@/lib/pathfinder'
import { loadVecops } from '@/lib/vecops/kernel'
import type { CanvasObject, PathObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { finishActiveGesture } from './gestureCoordinator'
import { useSelectionStore } from './selectionStore'
import { useUiStore } from './uiStore'

/**
 * 形状生成器（⇧M，ADR 0185 M3 第三批；M2 计划里那一项）的会话：
 * 进入时把选中的对象按「分割」切成区域放在这里（不碰文档），画布覆盖层（`canvas/ShapeBuilderOverlay`）画它们、
 * 悬停高亮；拖过几块 = 并成一块，按住 ⌥ 拖或点 = 删掉。退出（Esc / Enter / 选区或工具变了）时有改动才**一次 commit**：
 * 原对象删掉、剩下的每块区域一个路径对象（id / 样式 / 位置规则与路径查找器相同，`lib/pathfinder.builderPlan`）。
 * 一次也没动就退出 = 文档不变、不进历史。
 */

export interface ShapeBuilderState {
  active: boolean
  /** 原对象（进入那一刻的样子、换成路径之后；样式与 id 的来源） */
  sources: PathObject[]
  /** 进入那一刻文档里的原对象本身（引用）：文档在别处被改了（撤销等），引用就不一样了 */
  originals: CanvasObject[]
  faces: BuilderFace[]
  /** 正在拖的这一下：模式与拖过的区域下标 */
  gesture: { mode: 'merge' | 'delete'; picked: number[] } | null
  hover: number | null
  changed: boolean
}

const IDLE: ShapeBuilderState = { active: false, sources: [], originals: [], faces: [], gesture: null, hover: null, changed: false }

export const useShapeBuilderStore = create<ShapeBuilderState>(() => ({ ...IDLE }))

const say = (key: string) => useUiStore.getState().setStatus(msg(`status.${key}`, undefined, 'workspace'), 'info')

export const shapeBuilderActive = () => useShapeBuilderStore.getState().active

/** 进入：选中两个以上能参与的对象（与路径查找器同一判据）。内核加载不了 = 说一句 */
export async function enterShapeBuilder(): Promise<boolean> {
  if (shapeBuilderActive()) return true
  finishActiveGesture()
  const k = await loadVecops()
  if (!k) {
    say('pathfinderUnavailable')
    return false
  }
  const ids = [...useSelectionStore.getState().ids]
  const r = builderFaces(k, useDocumentStore.getState().doc.objects, ids)
  if (!r) {
    say('pathfinderEmpty')
    return false
  }
  const want = new Set(r.sources.map((o) => o.id))
  const originals = useDocumentStore.getState().doc.objects.filter((o) => want.has(o.id))
  useShapeBuilderStore.setState({ active: true, sources: r.sources, originals, faces: r.faces, gesture: null, hover: null, changed: false })
  return true
}

export function setBuilderHover(i: number | null) {
  if (useShapeBuilderStore.getState().hover !== i) useShapeBuilderStore.setState({ hover: i })
}

/** 按在一块区域上：开始这一下（⌥ = 删除模式） */
export function beginBuilderGesture(i: number, alt: boolean) {
  useShapeBuilderStore.setState({ gesture: { mode: alt ? 'delete' : 'merge', picked: [i] } })
}

/** 拖着经过一块区域 */
export function extendBuilderGesture(i: number) {
  const g = useShapeBuilderStore.getState().gesture
  if (g && !g.picked.includes(i)) useShapeBuilderStore.setState({ gesture: { ...g, picked: [...g.picked, i] } })
}

/** 松手：合并拖过的区域（只有一块 = 不变），或删掉它们。合并用 vecops 内核（已经在进入时加载过） */
export async function endBuilderGesture(): Promise<void> {
  const st = useShapeBuilderStore.getState()
  const g = st.gesture
  if (!g) return
  useShapeBuilderStore.setState({ gesture: null })
  const picked = new Set(g.picked)
  if (g.mode === 'delete') {
    useShapeBuilderStore.setState({ faces: st.faces.filter((_, i) => !picked.has(i)), changed: true, hover: null })
    return
  }
  if (picked.size < 2) return
  const k = await loadVecops()
  if (!k) return
  const merged = mergeFaces(k, g.picked.map((i) => st.faces[i]))
  if (!merged) return
  const rest = st.faces.filter((_, i) => !picked.has(i))
  // 并出来的那块放在被并的区域里最前面那一位，其余次序不变
  const at = Math.min(...g.picked)
  rest.splice(at, 0, merged)
  useShapeBuilderStore.setState({ faces: rest, changed: true, hover: null })
}

/** 不落地地退出（原对象在别处被改了：撤销、别的命令——拿旧样子去落地会把那次改动吞掉） */
export function cancelShapeBuilder(): void {
  if (useShapeBuilderStore.getState().active) useShapeBuilderStore.setState({ ...IDLE })
}

/** 退出：有改动就一次 commit，选中结果；没改动就原样 */
export function finishShapeBuilder(): void {
  const st = useShapeBuilderStore.getState()
  if (!st.active) return
  useShapeBuilderStore.setState({ ...IDLE })
  if (!st.changed) return
  const doc = useDocumentStore.getState()
  const plan = builderPlan(doc.doc.objects, st.sources, st.faces)
  if (!plan) return
  const remove = new Set(plan.remove)
  doc.commit(msg('history.shapeBuilder', undefined, 'workspace'), (d) => {
    d.objects = d.objects.filter((o) => !remove.has(o.id))
    d.objects.splice(plan.at, 0, ...plan.add)
  })
  useSelectionStore.getState().set(plan.add.map((o) => o.id))
}
