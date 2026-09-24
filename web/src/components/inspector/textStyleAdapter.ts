import { useMemo } from 'react'
import type { EditableField, ManifestElement } from '@/lib/api'
import { msg, t as translate, type UiMessage } from '@/i18n'
import { pageValueOf } from '@/lib/stylePanelModel'
import { pageField, pagePtLens } from '@/lib/stylePresets'
import { canPreviewStyle } from '@/lib/svgStyle'
import { clearOverride, clearOverrides, setOverride, setOverrides } from '@/store/actions'
import { previewStyle } from '@/store/svgPreviewStore'
import type { PanelObject } from '@/types/document'
import { useFieldGesture } from './elementWrite'
import { propLabel } from './roles/registry'
import {
  commonTextFields,
  currentOf,
  overrideStateOf,
  readAcross,
  TEXT_STYLE_PROPS,
  type ControlValue,
  type OverrideState,
} from './textStyleModel'

/**
 * 文字样式控件的数据接口。
 *
 * **单选与多选走同一个实现**（`useTextStyleAdapter` 接的就是一个数组），
 * 所以「多选了第二个对象，B/I 就退化成枚举下拉」这类分叉在结构上不可能
 * 再出现——控件那边根本看不到「这是一个还是三个」。
 *
 * **以 pt 计的量（`stylePresets.PAGE_PT_PROPS`）进出都是页面上的值**：`fieldOf` / `valueOf`
 * 给的、`write` 收的都是读者量到的 pt，换回脚本值只在写 override 之前那一处。
 */
export interface TextStyleAdapter {
  /** 目标数量；> 1 时控件用批量文案（历史标题、恢复按钮措辞） */
  count: number
  /** 交集后的字段；`undefined` = 至少一个目标不支持，控件整条不渲染 */
  fieldOf: (prop: string) => EditableField | undefined
  /** 当前值的三态（uniform / mixed / unavailable） */
  valueOf: (prop: string) => ControlValue
  /** 连续型写入（数字 scrub、取色）：整轮压成一条历史 + 一次定稿渲染 */
  write: (prop: string, value: unknown, immediate?: boolean) => void
  /** 离散动作（B/I、下拉、开关）：当场收尾 */
  writeOnce: (prop: string, value: unknown) => void
  beginGesture: () => void
  endGesture: () => void
  overrideStateOf: (prop: string) => OverrideState
  /** 恢复到脚本值：只清**这一组目标**上这条属性的 override */
  reset: (prop: string) => void
}

/**
 * 图内文字样式写入器。一个或多个目标一视同仁。
 *
 * 写入纪律沿用既有批量行：
 *   * 每个目标各自判断能不能局部预览——同时选中标题与刻度组时，标题照样
 *     抢先显示，刻度组安静地等后端；
 *   * **只要有一个目标没预览成功就照旧走后端**，宁可多渲染一次，也不能让
 *     画布上一半元素显示新值、另一半停在旧值；
 *   * 一次点击 = 一条历史（`setOverrides` 一次 commit），撤销一次全组回滚。
 */
export function useTextStyleAdapter(
  panel: PanelObject,
  elements: ManifestElement[],
  props: readonly string[] = TEXT_STYLE_PROPS,
): TextStyleAdapter {
  const gesture = useFieldGesture(panel, defaultLabel(elements.length))
  const fields = useMemo(() => commonTextFields(elements, props), [elements, props])
  const count = elements.length
  // 以 pt 计的量**进出都是页面上的值**（`stylePresets.pagePtLens`）：控件看到的、校验的、
  // 用户敲进去的都是读者量到的那个数；只有落进 override 与局部预览之前换回脚本值
  // ——预览贴在按脚本坐标系画的 SVG 上，它与 override 必须是同一个数
  const lens = pagePtLens(panel)
  const scale = lens.scale
  const pageFields = useMemo(() => {
    const out = new Map<string, EditableField>()
    for (const [prop, f] of fields) out.set(prop, pageField(f, scale))
    return out
  }, [fields, scale])

  const historyLabel = (prop: string): UiMessage => {
    const label = propLabel(prop, elements[0]?.role ?? '')
    return count > 1
      ? msg('element.batchEdit', { label }, 'inspector')
      : msg('element.editProp', { label }, 'inspector')
  }

  const write = (prop: string, pageValue: unknown, immediate = false) => {
    if (!fields.has(prop) || !count) return
    const value = lens.toScript(prop, pageValue)
    const previewables = elements.filter((e) => canPreviewStyle(e.role, prop))
    if (previewables.length && !gesture.isOpen()) {
      gesture.start(
        translate('element.editProp', {
          ns: 'inspector',
          label: propLabel(prop, elements[0].role),
        }),
      )
    }
    let previewed = previewables.length === count
    for (const e of previewables) {
      if (!previewStyle(e.gid, e.role, prop, value)) previewed = false
    }
    const policy = previewed ? ('none' as const) : immediate
    if (count === 1) {
      // 单目标保持既有历史标题（hist('setProp')），契约测试认的是那一条
      setOverride(panel.id, elements[0].gid, prop, value, policy)
    } else {
      setOverrides(
        panel.id,
        historyLabel(prop),
        elements.map((e) => ({ gid: e.gid, prop, value })),
        policy,
      )
    }
    gesture.touch()
  }

  return {
    count,
    fieldOf: (prop) => pageFields.get(prop),
    // 四档照旧在脚本值上判（mixed 不因换算后碰巧相等而被压扁），只有 uniform 的那个数换到页面上
    valueOf: (prop) => pageValueOf(readAcross(panel, elements, prop, fields), prop, scale),
    write,
    writeOnce: (prop, value) => {
      write(prop, value, true)
      gesture.end()
    },
    beginGesture: gesture.start,
    endGesture: gesture.end,
    overrideStateOf: (prop) => overrideStateOf(panel, elements, prop),
    reset: (prop) => {
      const hit = elements.filter((e) =>
        panel.overrides.some((o) => o.gid === e.gid && o.prop === prop),
      )
      if (!hit.length) return
      if (hit.length === 1 && count === 1) {
        clearOverride(panel.id, hit[0].gid, prop)
        return
      }
      clearOverrides(
        panel.id,
        msg('element.resetProp', { label: propLabel(prop, elements[0].role) }, 'inspector'),
        hit.map((e) => ({ gid: e.gid, prop })),
      )
    },
  }
}

const defaultLabel = (count: number): UiMessage =>
  count > 1
    ? msg('element.batchEditGeneric', undefined, 'inspector')
    : msg('element.editElement', undefined, 'inspector')

/** 单元素读值（供不走 adapter 的老路径复用，口径与 adapter 一致） */
export { currentOf }
