import { useCallback, useEffect, useMemo, useRef } from 'react'
import type { EditableField, ManifestElement } from '@/lib/api'
import { msg, type UiMessage } from '@/i18n'
import { updateObjects } from '@/store/actions'
import { useDocumentStore } from '@/store/documentStore'
import { registerGesture } from '@/store/gestureCoordinator'
import { getHistoryMode } from '@/store/svgPreviewStore'
import {
  canvasFieldOf,
  coerceTypography,
  commonSupport,
  effectiveCanvasFamily,
  inheritedCanvasValue,
  propertyPathOf,
  readCanvasText,
  supportsTypography,
  writeCanvasText,
  type TypographyKind,
  type TypographyProp,
  type TypographyValue,
  type UnsupportedReason,
} from '@/lib/typography'
import type { PanelObject, TextObject } from '@/types/document'
// 「一轮」有多长**只有一个数**（原生取色对话框不保证发 blur，只能靠
// 「安静了这么久」判定一轮结束）。抄第二份的话，同一个动作在属性页与
// 画布标注上的撤销粒度会不一样，而两处都「看起来对」。
import { GESTURE_QUIET_MS } from './elementWrite'
import { useTextStyleAdapter } from './textStyleAdapter'
import type { OverrideState } from './textStyleModel'

/**
 * 排版控件的**唯一数据接口**（ADR 0032）。
 *
 * 控件那一侧看不到「这是图内标题还是画布标注」「这是一个还是三个」——两件
 * 事都由适配器吸收。于是「多选之后 B/I 退化成文字下拉」「标注面板没有字体
 * 这一行」这类分叉在结构上不可能再出现：它们本来就是**两份实现**的症状，
 * 而不是两份配置。
 *
 * 写入一律经各自的 document action（图内 `setOverride(s)`、画布
 * `updateObjects`）——**没有任何一条路径绕开 `documentStore.commit`**。
 */
export interface TypographyAdapter {
  /** 目标数量；> 1 时控件用批量文案 */
  count: number
  /** 目标涉及的对象类别（排序去重后）；property path 按它取 */
  kinds: TypographyKind[]
  /** 这条属性此刻的值域；`undefined` = 不可用，理由问 `unsupportedReason` */
  fieldOf: (prop: TypographyProp) => EditableField | undefined
  /** 这条属性的**当前值**（四档：uniform / mixed / inherit / unsupported） */
  valueOf: (prop: TypographyProp) => TypographyValue
  /** 连续型写入（数字 scrub、取色）：整轮压成一条历史 */
  write: (prop: TypographyProp, value: unknown, immediate?: boolean) => void
  /** 离散动作（B/I、下拉、开关）：当场收尾 */
  writeOnce: (prop: TypographyProp, value: unknown) => void
  beginGesture: () => void
  endGesture: () => void
  /** 已修改状态：没有 / 部分 / 全部（画布文字只有 none/all——它没有「脚本值」） */
  overrideStateOf: (prop: TypographyProp) => OverrideState
  reset: (prop: TypographyProp) => void
  /** 控件锚点 / 检查报的字段名——**同一份表**（`lib/typography.propertyPathOf`） */
  pathOf: (prop: TypographyProp) => string | null
  /** `fieldOf` 回 undefined 时说得出为什么 */
  unsupportedReason: (prop: TypographyProp) => UnsupportedReason | null
  /** 选项当前不可用（装不上的字体）——仍然显示名字 + warning，不悄悄换掉 */
  unavailableOptions: (prop: TypographyProp) => readonly string[]
}

/** 「没有不可用选项」的稳定空数组——每次新建会让 memo 白白失效 */
const EMPTY: readonly string[] = []

/**
 * 图内文字**批量**认的属性，按渲染顺序。
 *
 * 刻意**不含** `halign`：同一个 `left` 在图标题与 Y 轴标题上语义不同（一个
 * 沿图宽、一个沿轴），批量写会让元素意外移动。它不在这张表里，所以批量
 * 适配器**结构上**拿不到它——不是靠某个 if 记得挡住。
 */
export const FIGURE_TEXT_BATCH_PROPS: readonly TypographyProp[] = [
  'fontFamily',
  'sizePt',
  'weight',
  'style',
  'color',
]

/** 单选图内文字额外多一条水平对齐。 */
export const FIGURE_TEXT_SINGLE_PROPS: readonly TypographyProp[] = [
  ...FIGURE_TEXT_BATCH_PROPS,
  'halign',
]

/** 画布文字在高频区显示的属性（行距 / 旋转在「更多」里，各自的控件另有安排）。 */
export const CANVAS_TEXT_PRIMARY_PROPS: readonly TypographyProp[] = [
  'fontFamily',
  'sizePt',
  'weight',
  'style',
  'color',
  'halign',
]

/* ------------------------------ 图内文字 ---------------------------------- */

/** 规范属性名 → manifest 的 prop 名（表在 `lib/typography`，这里只做投影） */
const FIGURE_PROP: Record<TypographyProp, string | null> = {
  fontFamily: 'fontfamily',
  sizePt: 'fontsize',
  weight: 'weight',
  style: 'style',
  color: 'color',
  halign: 'ha',
  valign: 'va',
  lineHeight: null,
  rotationDeg: 'rotation',
  // 图内文字的上下标是 matplotlib 的 `$…$`（`mathTextModeOf` 的另一档），
  // 不是我们自己的合成管线——引擎不认这条 prop，所以是 null 而不是某个名字。
  interpretation: null,
}

/**
 * 图内文字（matplotlib `Text`）的排版适配器。
 *
 * 能力仍由 manifest 说了算：`SUPPORT` 表只回答「值不值得问引擎」，引擎没发
 * `fontsize` 的图例照样没有字号行。两层缺一不可——只有静态表会摆出点了没
 * 反应的控件，只有 manifest 则说不出「这类对象本来就没有这条属性」。
 */
export function useFigureTypography(
  panel: PanelObject,
  elements: ManifestElement[],
  props: readonly TypographyProp[],
): TypographyAdapter {
  const manifestProps = useMemo(
    () => props.map((p) => FIGURE_PROP[p]).filter((v): v is string => !!v),
    [props],
  )
  const inner = useTextStyleAdapter(panel, elements, manifestProps)
  const allowed = useMemo(() => new Set(props), [props])

  const mp = (prop: TypographyProp): string | null =>
    allowed.has(prop) ? FIGURE_PROP[prop] : null

  return {
    count: inner.count,
    kinds: ['figureText'],
    fieldOf: (prop) => {
      const m = mp(prop)
      return m ? inner.fieldOf(m) : undefined
    },
    valueOf: (prop) => {
      const m = mp(prop)
      if (!m) {
        return {
          kind: 'unsupported',
          reason: supportsTypography('figureText', prop) ? 'not_in_manifest' : 'kind_unsupported',
        }
      }
      const v = inner.valueOf(m)
      if (v.kind === 'unavailable') return { kind: 'unsupported', reason: 'not_in_manifest' }
      return v
    },
    write: (prop, value, immediate) => {
      const m = mp(prop)
      if (!m) return
      const ok = coerceTypography(prop, value, inner.fieldOf(m))
      if (!ok.ok) return
      inner.write(m, ok.value, immediate)
    },
    writeOnce: (prop, value) => {
      const m = mp(prop)
      if (!m) return
      const ok = coerceTypography(prop, value, inner.fieldOf(m))
      if (!ok.ok) return
      inner.writeOnce(m, ok.value)
    },
    beginGesture: inner.beginGesture,
    endGesture: inner.endGesture,
    overrideStateOf: (prop) => {
      const m = mp(prop)
      return m ? inner.overrideStateOf(m) : 'none'
    },
    reset: (prop) => {
      const m = mp(prop)
      if (m) inner.reset(m)
    },
    pathOf: (prop) => propertyPathOf('figureText', prop),
    unsupportedReason: (prop) => {
      const m = mp(prop)
      if (m && inner.fieldOf(m)) return null
      return supportsTypography('figureText', prop) ? 'not_in_manifest' : 'kind_unsupported'
    },
    unavailableOptions: (prop) => {
      const m = mp(prop)
      if (!m) return EMPTY
      return inner.fieldOf(m)?.options_unavailable ?? EMPTY
    },
  }
}

/* ------------------------------ 画布文字 ---------------------------------- */

/**
 * 一次「连续调整」（画布对象版）。
 *
 * 与图内那条路（`elementWrite.useFieldGesture`）的区别只有一个：画布对象
 * **没有预览平面**，画布上的 `TextView` 直接读文档，改完就是新样子。所以
 * 这里只管事务边界，不碰渲染。
 *
 * 收尾必须可靠：安静计时器到点、组件卸载、别处的离散动作喊
 * `finishActiveGesture()` 三条路都收得干净——事务悬着的话，下一次编辑会被
 * 静默并进上一条历史，用户看到的是「撤销一次退了两步」。
 */
function useObjectGesture() {
  const open = useRef(false)
  const timer = useRef<number | undefined>(undefined)
  const unregister = useRef<(() => void) | null>(null)

  const end = useCallback(() => {
    window.clearTimeout(timer.current)
    timer.current = undefined
    unregister.current?.()
    unregister.current = null
    if (!open.current) return
    open.current = false
    if (getHistoryMode() === 'gesture') useDocumentStore.getState().endTxn()
  }, [])

  /**
   * `label` 每轮各带各的：一个手势里先改字号再改颜色时，历史标题跟着**第一个
   * 动作**走，比钉死一句「编辑文字」诚实。
   */
  const start = useCallback(
    (label: UiMessage = hist('editText')) => {
      window.clearTimeout(timer.current)
      if (open.current) return
      open.current = true
      if (getHistoryMode() === 'gesture') useDocumentStore.getState().beginTxn(label)
      unregister.current = registerGesture(end)
    },
    [end],
  )

  const touch = useCallback(() => {
    if (!open.current) return
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(end, GESTURE_QUIET_MS)
  }, [end])

  useEffect(() => () => end(), [end])

  return { start, end, touch, isOpen: () => open.current }
}

const hist = (key: string): UiMessage => msg(`history.${key}`, undefined, 'inspector')

/**
 * 画布文字里**可以「没设过」**的那几条（可选字段）。
 *
 * 其余几条（字号 / 字重 / 颜色 / 水平对齐）是 `TextObject` 的必填字段：
 * 它们没有「继承」这一档，也就没有「恢复」这个动作——`bold: false` 与
 * 「没设过加粗」在磁盘上根本分不开。
 */
const CANVAS_INHERITABLE = new Set<TypographyProp>([
  'fontFamily',
  'style',
  'lineHeight',
  'rotationDeg',
  'interpretation',
])

/** 规范属性名 → 历史标题。**改哪一条就说哪一条**，不共用一句「编辑文字」。 */
const CANVAS_HISTORY: Record<TypographyProp, string> = {
  fontFamily: 'setFontFamily',
  sizePt: 'setFontSize',
  weight: 'toggleBold',
  style: 'toggleItalic',
  color: 'setTextColor',
  halign: 'setAlign',
  valign: 'setAlign',
  lineHeight: 'setLineHeight',
  rotationDeg: 'rotateObject',
  interpretation: 'setInterpretation',
}

/**
 * 画布文字（标注 / 自由文字）的排版适配器。
 *
 * `overrideStateOf` 在这一侧只有 `none` / `all`：画布文字没有「脚本值」这个
 * 概念，能说的只是「设过没有」。`reset` 因此是**删字段回到继承**，不是
 * 「写一个默认值进去」——后者会把 inherit 悄悄变成一次显式设置。
 */
export function useCanvasTypography(objs: TextObject[]): TypographyAdapter {
  const gesture = useObjectGesture()
  const ids = useMemo(() => objs.map((o) => o.id), [objs])
  const count = objs.length

  const supported = useMemo(() => commonSupport(count ? ['canvasText'] : []), [count])

  const valueOf = (prop: TypographyProp): TypographyValue => {
    if (!supported.has(prop)) {
      return {
        kind: 'unsupported',
        reason: supportsTypography('canvasText', prop) ? 'not_in_manifest' : 'kind_unsupported',
      }
    }
    const raw = objs.map((o) => readCanvasText(o, prop))
    const eff = raw.map((v, i) =>
      v === undefined
        ? prop === 'fontFamily'
          ? effectiveCanvasFamily(objs[i])
          : inheritedCanvasValue(prop)
        : v,
    )
    const first = JSON.stringify(eff[0] ?? null)
    if (eff.some((v) => JSON.stringify(v ?? null) !== first)) return { kind: 'mixed' }
    // 全体都没设过 = inherit（显示继承来的那个值，但标记它不是自己的）
    if (raw.every((v) => v === undefined)) return { kind: 'inherit', value: eff[0] }
    return { kind: 'uniform', value: eff[0] }
  }

  const apply = (prop: TypographyProp, value: unknown, keepOpen: boolean) => {
    if (!supported.has(prop) || !count) return
    // **invalid 输入一个字都不写**：不开事务、不 commit、不进历史
    const ok = coerceTypography(prop, value, canvasFieldOf(prop))
    if (!ok.ok) return
    if (keepOpen && !gesture.isOpen()) gesture.start(hist(CANVAS_HISTORY[prop]))
    updateObjects(ids, hist(CANVAS_HISTORY[prop]), (o) => {
      if (o.type === 'text') writeCanvasText(o, prop, ok.value)
    })
    if (keepOpen) gesture.touch()
  }

  return {
    count,
    kinds: ['canvasText'],
    fieldOf: (prop) => {
      if (!supported.has(prop)) return undefined
      const base = canvasFieldOf(prop)
      if (!base) return undefined
      // `value` 是**第一个目标此刻的值**（与图内那侧同一口径：那边的合并字段
      // 也带着 `fields[0].value`）。控件在 mixed 时拿它当色块的显示值——
      // 不带的话会退回一个谁都不是的硬编码黑，而控件里的注释说的是
      // 「取第一个目标的真实颜色」。
      const first = objs.length ? readCanvasText(objs[0], prop) : undefined
      return { ...base, value: first ?? inheritedCanvasValue(prop) ?? null }
    },
    valueOf,
    write: (prop, value) => apply(prop, value, true),
    writeOnce: (prop, value) => {
      apply(prop, value, true)
      gesture.end()
    },
    beginGesture: () => gesture.start(),
    endGesture: gesture.end,
    overrideStateOf: (prop) => {
      // **只有能「没设过」的那几条才谈得上「改过」。** 字号 / 颜色 / 对齐是
      // `TextObject` 的必填字段，磁盘上永远有值——拿「有没有值」当判据的话
      // 每一条都会永远挂着「已修改」的点和一颗按了没反应的恢复按钮。
      if (!CANVAS_INHERITABLE.has(prop)) return 'none'
      const set = objs.filter((o) => readCanvasText(o, prop) !== undefined)
      if (!set.length) return 'none'
      return set.length === count ? 'all' : 'some'
    },
    reset: (prop) => {
      if (!CANVAS_INHERITABLE.has(prop)) return
      gesture.end()
      updateObjects(ids, hist('resetTextProp'), (o) => {
        if (o.type !== 'text') return
        // 「恢复」= 删掉这个字段回到继承。写一个等于默认值的显式值会让
        // 「没设过」变成「设过、正好一样」——下一次默认值改了它就不跟了。
        if (prop === 'fontFamily') delete o.fontFamily
        else if (prop === 'style') delete o.italic
        else if (prop === 'lineHeight') delete o.lineHeight
        else if (prop === 'rotationDeg') delete o.rotationDeg
      })
    },
    pathOf: (prop) => propertyPathOf('canvasText', prop),
    unsupportedReason: (prop) =>
      supported.has(prop)
        ? null
        : supportsTypography('canvasText', prop)
          ? 'not_in_manifest'
          : 'kind_unsupported',
    unavailableOptions: () => EMPTY,
  }
}
