/**
 * 「这一项为什么不能改」——guard 的 reason 从 manifest 走到眼睛的那一段。
 *
 * guard 的完整形态是 `detect → guard/hide → **unsupported reason** → issue → 修`。
 * 在此之前 reason 只到 manifest：`ManifestElement` 没声明这个字段，inspector 只按
 * `editable` 建 UI，于是多宿主色条的方向开关**就是消失了，没有任何解释**（#76）。
 * 比「点了把排版弄坏」好，但离承诺还差一步。
 *
 * 两条纪律：
 *   * **按 code 翻译，绝不透传英文**。`reason` 是稳定 code，界面负责措辞；
 *     不认识的 code 走一句通用兜底，而不是把 `multi_host_colorbar` 显示出来。
 *   * **占位而不是消失**。属性名照常显示、置灰，理由跟在下面——用户要看到的是
 *     「这一项在这里不适用」，不是「这一项不存在」。
 */
import type { ManifestElement } from '@/lib/api'
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

export function UnsupportedProps({ element }: { element: ManifestElement }) {
  const items = element.unsupported_props ?? []
  if (items.length === 0) return null
  return (
    <div className="mt-1.5 flex flex-col gap-1.5 border-t border-border pt-1.5">
      {items.map((item) => (
        <div key={item.prop} data-unsupported-prop={item.prop} className="flex flex-col gap-0.5">
          <span aria-disabled className="text-xs text-ink-faint">
            {propLabel(item.prop, element.role)}
          </span>
          <p className="text-xs leading-relaxed text-ink-3">
            {unsupportedReason(item.reason, item.detail)}
          </p>
        </div>
      ))}
    </div>
  )
}
