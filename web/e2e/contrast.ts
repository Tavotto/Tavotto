import type { Page } from '@playwright/test'

/**
 * 自己算 WCAG 文字对比度，返回不达标的元素描述。
 *
 * **为什么不能只靠 axe**（issue #130）：axe 判不出被其它元素覆盖的文字的背景色，
 * 会把该节点丢进 `results.incomplete` 而**不是** `violations`。我们「整行可点」
 * 的写法（`absolute inset-0` 的按钮盖在行内容上）恰好每次都触发这条路径：
 *
 *     violations: []
 *     incomplete: ["aria-hidden-focus/serious/8", "color-contrast/serious/8"]
 *       → "Element's background color could not be determined
 *          because it is overlapped by another element"
 *
 * 也就是说：凡是用了行覆盖层的地方，「axe 无 serious 违规」为绿**不代表对比度
 * 达标，而是 axe 根本没测**。实证：把 `--color-warn` 改成明显不达标的 `#e8c98f`，
 * 只看 violations 的检查照样绿。
 *
 * 背景色在这里是确定的：向上找第一个不透明祖先即可，不必猜覆盖层；半透明前景
 * 先与背景合成再算比值，否则算出来偏乐观。
 */
export async function lowContrastNodes(page: Page, root = 'body'): Promise<string[]> {
  return page.evaluate((rootSelector) => {
    const parse = (s: string): number[] | null => {
      const m = s.match(/rgba?\(([^)]+)\)/)
      if (!m) return null
      const p = m[1].split(/[\s,/]+/).filter(Boolean).map(Number)
      if (p.length < 3 || p.some((v, i) => i < 3 && Number.isNaN(v))) return null
      return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]
    }
    const lum = (c: number[]) => {
      const f = c.slice(0, 3).map((v) => {
        const s = v / 255
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
      })
      return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]
    }
    const opaqueBg = (el: HTMLElement): number[] => {
      let n: HTMLElement | null = el
      while (n) {
        const c = parse(getComputedStyle(n).backgroundColor)
        if (c && c[3] === 1) return c
        n = n.parentElement
      }
      return [255, 255, 255, 1]
    }
    const scope = document.querySelector(rootSelector)
    if (!scope) return [`NO ROOT: ${rootSelector}`]
    const out: string[] = []
    for (const el of Array.from(scope.querySelectorAll('*'))) {
      const e = el as HTMLElement
      // 只看自己直接持有文字的元素，避免把容器算成它子孙的颜色
      const text = Array.from(e.childNodes)
        .filter((n) => n.nodeType === 3)
        .map((n) => n.textContent ?? '')
        .join('')
        .trim()
      if (!text) continue
      const cs = getComputedStyle(e)
      if (cs.visibility === 'hidden' || cs.display === 'none') continue
      if (!e.getClientRects().length) continue
      const fgRaw = parse(cs.color)
      if (!fgRaw) continue
      const bg = opaqueBg(e)
      const a = fgRaw[3]
      const fg = [0, 1, 2].map((i) => fgRaw[i] * a + bg[i] * (1 - a))
      const size = parseFloat(cs.fontSize)
      const large = size >= 24 || (size >= 18.66 && Number(cs.fontWeight) >= 700)
      const need = large ? 3 : 4.5
      const l1 = lum(fg)
      const l2 = lum(bg)
      const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05)
      if (ratio < need) {
        out.push(
          `${e.tagName}.${String(e.className).slice(0, 40)} "${text.slice(0, 16)}" ` +
            `${ratio.toFixed(2)}:1 < ${need}`,
        )
      }
    }
    return out
  }, root)
}
