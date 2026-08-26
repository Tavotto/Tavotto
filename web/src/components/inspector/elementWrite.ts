import { useCallback, useEffect, useRef } from 'react'
import { beginElementPreview, commitElementPreview } from '@/canvas/elementPreview'
import { flushRender } from '@/hooks/useEngineSync'
import type { EditableField, ManifestElement } from '@/lib/api'
import { canPreviewStyle } from '@/lib/svgStyle'
import { msg, t, type UiMessage } from '@/i18n'
import { setOverride } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { registerGesture } from '@/store/gestureCoordinator'
import { getHistoryMode, previewStyle } from '@/store/svgPreviewStore'
import type { PanelObject } from '@/types/document'
import { propLabel } from './roles/registry'

/**
 * 原生取色对话框（`<input type="color">`）不保证发 blur：连续拖着选色时，
 * 只能靠「安静了这么久」判定这一轮结束。取值刻意大于渲染防抖（300ms），
 * 免得刚停手就被当成两轮。
 */
const GESTURE_QUIET_MS = 450

/**
 * 一次「连续调整」。**历史与渲染在这里彻底分开**：
 *
 *   历史：gesture 模式下整轮压成一条事务（一次 scrub / 一轮取色 = 一条撤销）；
 *         granular 模式下不开事务，每个语义变化各自 commit 成一条。
 *         **两种模式都经过 documentStore.commit**，一条都不会丢。
 *   渲染：不管哪种模式，能局部预览的字段整轮都不麻烦 matplotlib
 *         （setOverride 走 render:'none'），收尾时 flushRender 定稿一次。
 *
 * 收尾必须可靠：组件卸载（切走选中元素、关掉属性页、右键弹层关掉）也要收
 * ——否则事务悬着、占位的 wantPatches 还挡着同步器，用户会等来一张永远不
 * 出现的定稿图。
 *
 * `start(label)` 允许每次带自己的历史标题：工具条一个手势里可能先点加粗
 * 再改颜色，标题跟着第一个动作走比钉死一个泛称诚实。
 */
export function useFieldGesture(panel: PanelObject, defaultLabel: UiMessage | string) {
  const open = useRef(false)
  const timer = useRef<number | undefined>(undefined)
  /** gestureCoordinator 的注销函数——收尾时必须一起解掉，否则登记表挂着野引用 */
  const unregister = useRef<(() => void) | null>(null)
  const panelId = panel.id
  const labelRef = useRef(defaultLabel)
  labelRef.current = defaultLabel

  const end = useCallback(() => {
    window.clearTimeout(timer.current)
    timer.current = undefined
    unregister.current?.()
    unregister.current = null
    if (!open.current) return
    open.current = false
    if (getHistoryMode() === 'gesture') useDocumentStore.getState().endTxn()
    commitElementPreview(panelId)
    flushRender(panelId)
  }, [panelId])

  const start = useCallback(
    (label?: unknown) => {
      window.clearTimeout(timer.current)
      if (open.current) return
      open.current = true
      const p = useDocumentStore.getState().doc.objects.find((o) => o.id === panelId)
      if (p?.type !== 'panel') return
      // 参数按 string 校验而不是直接用：这个函数同时被当成 onFocus /
      // onScrubStart 的处理器传下去，那时第一个实参是事件对象。
      // 传进来的字符串已经是当前语言的成品文案，包成 literal 描述符即可。
      const raw = typeof label === 'string' ? label : labelRef.current
      const title: UiMessage = typeof raw === 'string' ? { key: 'literal', ns: 'common', values: { text: raw } } : raw
      // granular：不开事务，每个变化各成一条历史；渲染照样推迟到 end()
      if (getHistoryMode() === 'gesture') useDocumentStore.getState().beginTxn(title)
      // 登记给 gestureCoordinator：别处的离散动作（对齐、撤销、版本恢复）
      // 点下去时会先喊一声 finishActiveGesture()，走的正是这里的 end()
      // ——事务、安静计时器、预览会话、定稿渲染一次收干净
      unregister.current = registerGesture(end)
      beginElementPreview(p)
    },
    [panelId, end],
  )

  /** 心跳：每次值变化都续一次「安静计时」，超时就当这一轮结束 */
  const touch = useCallback(() => {
    if (!open.current) return
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(end, GESTURE_QUIET_MS)
  }, [end])

  useEffect(() => () => end(), [end])

  return { start, end, touch, isOpen: () => open.current }
}

export interface ElementWriter {
  /** 当前值：用户改过的 override 优先于渲染时的初值 */
  read: (prop: string) => unknown
  /** manifest 里有没有这条属性——没有就不该画出对应控件 */
  fieldOf: (prop: string) => EditableField | undefined
  has: (prop: string) => boolean
  /** 连续型写入（数字 scrub、取色）：整轮压成一条历史 + 一次定稿渲染 */
  write: (prop: string, value: unknown, immediate?: boolean) => void
  /** 离散动作（开关、下拉、点一下的按钮）：当场收尾 */
  writeOnce: (prop: string, value: unknown) => void
  /** 这一轮结束（输入框失焦 / 取色盘关掉） */
  endGesture: () => void
  beginGesture: (label?: string) => void
}

/** 图内属性写入的默认历史标题 */
const defaultGestureLabel = (): UiMessage => msg('element.editElement', undefined, 'inspector')

/**
 * 图内元素的属性写入器：把「预览 + 事务 + 渲染时机」这一套收在一处，
 * 属性页的工具条与右键弹层共用同一份，两边行为不会飘。
 *
 * 能局部预览的字段先把新样子贴到 SVG 上（rAF 合并），**这一轮完全不发
 * 后端**；预览没生效（不在能力表里 / gid 在 SVG 里查不到）就原路走后端。
 */
export function useElementWriter(panel: PanelObject, element: ManifestElement): ElementWriter {
  const gesture = useFieldGesture(panel, defaultGestureLabel())
  const gid = element.gid
  const role = element.role

  const fieldOf = (prop: string) => element.editable.find((f) => f.prop === prop)

  const read = (prop: string) => {
    const ov = panel.overrides.find((o) => o.gid === gid && o.prop === prop)
    return ov ? ov.value : fieldOf(prop)?.value
  }

  const write = (prop: string, value: unknown, immediate = false) => {
    const previewable = canPreviewStyle(role, prop)
    if (previewable && !gesture.isOpen()) {
      gesture.start(t('element.editProp', { ns: 'inspector', label: propLabel(prop, role) }))
    }
    const previewed = previewable && previewStyle(gid, role, prop, value)
    setOverride(panel.id, gid, prop, value, previewed ? 'none' : immediate)
    gesture.touch()
  }

  const writeOnce = (prop: string, value: unknown) => {
    write(prop, value, true)
    gesture.end()
  }

  return {
    read,
    fieldOf,
    has: (prop) => !!fieldOf(prop),
    write,
    writeOnce,
    endGesture: gesture.end,
    beginGesture: gesture.start,
  }
}
