import { memo } from 'react'
import { useInteractionStore } from '@/store/interactionStore'
import { useSelectionStore } from '@/store/selectionStore'
import { enterElementEdit, groupMates } from '@/store/actions'
import { useUiStore } from '@/store/uiStore'
import { mmToWorld } from '@/store/viewportStore'
import type { CanvasObject } from '@/types/document'
import { objectRotation, panelRotation } from '@/types/document'
import { startMoveDrag } from './interactions'
import { openQuickEdit } from './quickEditStore'
import { ArrowView } from './ArrowView'
import { PanelView } from './PanelView'
import { ShapeView } from './ShapeView'
import { TextView } from './TextView'

/** 世界层里的单个对象：只负责定位与命中，选择框/手柄交给屏幕层的 OverlaySvg */
export const ObjectView = memo(function ObjectView({ obj }: { obj: CanvasObject }) {
  const editing = useUiStore((s) => s.editingTextId === obj.id)
  const cropping = useUiStore((s) => s.cropTargetId === obj.id)

  if (obj.hidden) return null

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0 || editing || cropping) return
    e.stopPropagation()

    // 图内编辑时点了别的对象 = 回到画布层工作：退出编辑态，
    // 属性页才能跟随点中的对象（正在编辑的面板自己被命中层拦截，到不了这里）
    const ui = useUiStore.getState()
    if (ui.elementPanelId && ui.elementPanelId !== obj.id) ui.setElementPanel(null)

    const sel = useSelectionStore.getState()
    // 成组的对象点谁都是整组：选择、移动、属性都对整组生效
    const mates = groupMates(obj.id)
    if (e.shiftKey) {
      if (sel.ids.includes(obj.id)) {
        sel.set(sel.ids.filter((id) => !mates.includes(id)))
        return
      }
      sel.set([...sel.ids, ...mates.filter((id) => !sel.ids.includes(id))])
    } else if (!mates.every((id) => sel.ids.includes(id))) {
      sel.set(mates)
    } else if (sel.ids.at(-1) !== obj.id) {
      // 已在选区内：把它提为主选，供对齐 / 等宽等高作基准
      sel.set([...sel.ids.filter((id) => id !== obj.id), obj.id])
    }
    startMoveDrag(e, obj.id)
  }

  /** 右键：先保证它在选区里（菜单里的复制/层级/删除都作用于选区），再弹快捷菜单 */
  const onContextMenu = (e: React.MouseEvent) => {
    if (editing || cropping) return
    e.preventDefault()
    e.stopPropagation()
    const sel = useSelectionStore.getState()
    if (!sel.ids.includes(obj.id)) sel.set(groupMates(obj.id))
    openQuickEdit({ kind: 'object', id: obj.id }, e)
  }

  const onDoubleClick = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (obj.type === 'text') useUiStore.getState().setEditingText(obj.id)
    else if (obj.type === 'panel') {
      // 可参数化面板双击进图内编辑，普通面板双击进裁剪
      // （旋转过的面板裁剪框方向会与画布对不上，先不进裁剪态）
      if (obj.script) enterElementEdit(obj.id)
      else if (!panelRotation(obj)) useUiStore.getState().setCropTarget(obj.id)
    }
  }

  return (
    <div
      data-object-id={obj.id}
      onPointerDown={onPointerDown}
      onContextMenu={onContextMenu}
      onDoubleClick={onDoubleClick}
      onPointerEnter={() => {
        if (useInteractionStore.getState().kind === 'none') {
          useInteractionStore.getState().setHover(obj.id)
        }
      }}
      onPointerLeave={() => {
        if (useInteractionStore.getState().hoverId === obj.id) {
          useInteractionStore.getState().setHover(null)
        }
      }}
      className="absolute"
      style={{
        left: mmToWorld(obj.x),
        top: mmToWorld(obj.y),
        width: mmToWorld(obj.w),
        height: mmToWorld(obj.h),
        // 任意角度旋转（text/arrow/shape）：绕中心，包围盒字段保持未旋转值
        transform: objectRotation(obj) ? `rotate(${objectRotation(obj)}deg)` : undefined,
        // 不写 'auto'：绘制工具激活时世界层整体设为 none，靠继承让对象一起失去命中
        pointerEvents: obj.locked ? 'none' : undefined,
        cursor: editing ? 'text' : 'default',
      }}
    >
      {obj.type === 'panel' && <PanelView obj={obj} />}
      {obj.type === 'text' && <TextView obj={obj} />}
      {obj.type === 'arrow' && <ArrowView obj={obj} />}
      {obj.type === 'shape' && <ShapeView obj={obj} />}
    </div>
  )
})
