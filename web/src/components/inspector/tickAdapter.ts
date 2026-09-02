import type { Manifest, ManifestElement } from '@/lib/api'
import { msg, t as translate } from '@/i18n'
import { canPreviewStyle } from '@/lib/svgStyle'
import { clearOverride, setOverride } from '@/store/actions'
import { previewStyle } from '@/store/svgPreviewStore'
import type { PanelObject } from '@/types/document'
import { useFieldGesture } from './elementWrite'
import { propLabel } from './roles/registry'
import type { TickAxisAdapter } from './controls/TickTaskCard'
import type { AxisTickState, TickDirection } from './controls/TickAndSpineDiagram'

/**
 * 刻度写入面的组装。
 *
 * 宿主映射（唯一的一份）：子图 `axes_0` 的 X 刻度住在 `axes_0.xticks`、
 * Y 刻度住在 `axes_0.yticks`。四边开关（`ticks_top` 等）住在 **axes 自己**
 * 身上，方向 / 长度 / 宽度 / 次刻度住在 **ticks 元素**身上——两组字段分属
 * 两个 manifest 元素，这就是「刻度这件事被拆散」的根因。界面把它们并到
 * 一张卡上，写入仍各回各的元素，一个字节的协议都没动。
 */

/** 子图 gid → 该轴的刻度元素 */
export function tickElementOf(
  manifest: Manifest | null | undefined,
  axesGid: string,
  axis: 'x' | 'y',
): ManifestElement | undefined {
  return manifest?.elements.find((e) => e.gid === `${axesGid}.${axis}ticks` && e.role === 'ticks')
}

/** 刻度元素 gid → 它属于哪个轴、宿主子图是谁 */
export function tickHostOf(gid: string): { axesGid: string; axis: 'x' | 'y' | 'z' } | null {
  const m = gid.match(/^(.*)\.([xyz])ticks$/)
  return m ? { axesGid: m[1], axis: m[2] as 'x' | 'y' | 'z' } : null
}

/**
 * 一个轴的刻度写入器。`element` 为空（引擎没发这个轴的刻度元素）时回 null，
 * 但**hook 调用次数不变**——调用方可以无条件为 X / Y 各调一次。
 */
export function useTickAxisAdapter(
  panel: PanelObject,
  element: ManifestElement | undefined,
  axis: 'x' | 'y',
): TickAxisAdapter | null {
  const gesture = useFieldGesture(panel, msg('element.editElement', undefined, 'inspector'))
  if (!element) return null
  const gid = element.gid
  const role = element.role
  const fieldOf = (prop: string) => element.editable.find((f) => f.prop === prop)
  const read = (prop: string) => {
    const ov = panel.overrides.find((o) => o.gid === gid && o.prop === prop)
    return ov ? ov.value : fieldOf(prop)?.value
  }
  const write = (prop: string, value: unknown, immediate = false) => {
    // 刻度是 manifest 的伪元素，gid 在 SVG 里不存在——预览必然失败，
    // 这里照旧问一次 canPreviewStyle 而不是写死 false：能力表是唯一权威，
    // 哪天引擎给刻度发了真 gid，这条路自动就通了
    const previewable = canPreviewStyle(role, prop)
    if (previewable && !gesture.isOpen()) {
      gesture.start(
        translate('element.editProp', { ns: 'inspector', label: propLabel(prop, role) }),
      )
    }
    const previewed = previewable && previewStyle(gid, role, prop, value)
    setOverride(panel.id, gid, prop, value, previewed ? 'none' : immediate)
    gesture.touch()
  }
  return {
    axis,
    gid,
    has: (prop) => !!fieldOf(prop),
    fieldOf,
    read,
    write: (prop, value) => write(prop, value),
    writeOnce: (prop, value) => {
      write(prop, value, true)
      gesture.end()
    },
    beginGesture: gesture.start,
    endGesture: gesture.end,
    isOverridden: (prop) => panel.overrides.some((o) => o.gid === gid && o.prop === prop),
    reset: (prop) => clearOverride(panel.id, gid, prop),
  }
}

const DIRECTIONS = new Set<TickDirection>(['in', 'out', 'inout'])

/**
 * 状态图要画成什么样。**读的是真实字段**，不是画死的「朝外」——
 * 引擎没给 direction（3D 轴）时才回落到 matplotlib 默认 out。
 */
export function axisTickState(adapter: TickAxisAdapter | null): AxisTickState {
  if (!adapter) return { direction: 'out', minor: false }
  const raw = adapter.read('direction')
  const direction = DIRECTIONS.has(raw as TickDirection) ? (raw as TickDirection) : 'out'
  return { direction, minor: adapter.read('minor_visible') === true }
}
