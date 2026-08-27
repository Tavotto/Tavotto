/**
 * 「这一项为什么不能改」——guard 的 reason 从 manifest 走到眼睛的那一段。
 *
 * guard 的完整形态是 `detect → guard/hide → **unsupported reason** → issue → 修`。
 * 在此之前 reason 只到 manifest：`ManifestElement` 没声明这个字段，inspector 只按
 * `editable` 建 UI，于是多宿主色条的方向开关**就是消失了，没有任何解释**（#76）。
 * 比「点了把排版弄坏」好，但离承诺还差一步。
 *
 * 三条纪律：
 *   * **按 code 翻译，绝不透传英文**。`reason` 是稳定 code，界面负责措辞；
 *     不认识的 code 走一句通用兜底，而不是把 `multi_host_colorbar` 显示出来。
 *   * **占位而不是消失**。属性名照常显示、置灰，理由跟在下面——用户要看到的是
 *     「这一项在这里不适用」，不是「这一项不存在」。
 *   * **多选也要说**。多选时 inspector 走的是批量分支，单元素表单整个让位；
 *     那条路上不渲染理由的话，「开关凭空消失」会原样复发（Codex 在 PR #160 上
 *     指出）。所以本组件收的是**一组**元素，按 (prop, reason) 归并。
 */
import type { ManifestElement, UnsupportedProp } from '@/lib/api'
import { t as translate } from '@/i18n'

import { propLabel } from './roles/registry'

const ins = (key: string, values?: Record<string, unknown>) =>
  translate(key, { ns: 'inspector', ...values })

/** reason code → 本地化文案。不认识的 code 落到通用兜底，不显示 code 本身。 */
export function unsupportedReason(reason: string, detail?: Record<string, unknown>): string {
  const text = translate(`unsupported.${reason}`, {
    ns: 'inspector',
    defaultValue: '',
    ...detail,
  })
  return text || ins('unsupported.unknown')
}

interface MergedUnsupported extends UnsupportedProp {
  role: string
  /** 选区里有几个元素带着这一条 */
  count: number
}

/** 把一组元素的 `unsupported_props` 按 (prop, reason) 归并。 */
export function mergeUnsupported(elements: ManifestElement[]): MergedUnsupported[] {
  const out = new Map<string, MergedUnsupported>()
  for (const el of elements) {
    for (const item of el.unsupported_props ?? []) {
      const key = `${item.prop}|${item.reason}`
      const hit = out.get(key)
      if (hit) hit.count += 1
      else out.set(key, { ...item, role: el.role, count: 1 })
    }
  }
  return [...out.values()]
}

export function UnsupportedProps({ elements }: { elements: ManifestElement[] }) {
  const items = mergeUnsupported(elements)
  if (items.length === 0) return null
  return (
    <div className="mt-1.5 flex flex-col gap-1.5 border-t border-border pt-1.5">
      {items.map((item) => (
        <div
          key={`${item.prop}|${item.reason}`}
          data-unsupported-prop={item.prop}
          className="flex flex-col gap-0.5"
        >
          <span aria-disabled className="text-xs text-ink-faint">
            {propLabel(item.prop, item.role)}
            {/* 只有部分元素受影响时才说数量——全都受影响时那句话是噪音 */}
            {elements.length > 1 && item.count < elements.length && (
              <span className="ml-1">
                {ins('unsupported.partial', { count: item.count, total: elements.length })}
              </span>
            )}
          </span>
          <p className="text-xs leading-relaxed text-ink-3">
            {unsupportedReason(item.reason, item.detail)}
          </p>
        </div>
      ))}
    </div>
  )
}
