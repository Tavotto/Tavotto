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
    /** 向上找第一个不透明背景，连同「它是谁」一起返回（算 opacity 要用） */
    const opaqueBg = (el: HTMLElement): { color: number[]; node: HTMLElement | null } => {
      let n: HTMLElement | null = el
      while (n) {
        const c = parse(getComputedStyle(n).backgroundColor)
        if (c && c[3] === 1) return { color: c, node: n }
        n = n.parentElement
      }
      return { color: [255, 255, 255, 1], node: null }
    }
    /**
     * 从文字元素累乘到背景那一层的 **CSS `opacity`**。
     *
     * `getComputedStyle(e).color` **不含**祖先的 group opacity——本仓库大量使用
     * `opacity-60` / `disabled:opacity-35` 这类淡化态，只看 color 的 alpha 会把
     * 它们全都当成不透明，算出来的比值偏乐观。而被覆盖层遮住的节点 axe 本来就
     * 只放进 incomplete，两边一起放行就等于没测（Codex 在 PR #167 上指出）。
     *
     * 累乘到**背景那一层为止（含）**：背景自己也淡化时，把它的 opacity 记在前景
     * 上是保守方向——算出来的比值只会更差，不会更好。门禁宁可偏严。
     */
    const groupOpacity = (el: HTMLElement, stop: HTMLElement | null): number => {
      let n: HTMLElement | null = el
      let a = 1
      while (n) {
        const o = parseFloat(getComputedStyle(n).opacity)
        if (!Number.isNaN(o)) a *= o
        if (n === stop) break
        n = n.parentElement
      }
      return a
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
      // **禁用态不在 WCAG 1.4.3 的范围内**（"Incidental: text that is part of an
      // inactive user interface component"），axe 的 color-contrast 同样跳过。
      // 本仓库的禁用态就是靠 `disabled:opacity-35` 做的——把 opacity 计入之后
      // 不排除它们，每一个灰掉的按钮都会变成一条假红，而误报比漏报更糟：它逼人
      // 去「修」一件标准上根本不要求的事。
      if (e.closest('[disabled], [aria-disabled="true"], fieldset[disabled]')) continue
      const fgRaw = parse(cs.color)
      if (!fgRaw) continue
      const { color: bg, node: bgNode } = opaqueBg(e)
      // 半透明前景先与背景合成，否则算出来的比值偏乐观。alpha 有两个来源：
      // 颜色自己的 alpha，以及**从这里累乘到背景那一层的 CSS opacity**。
      const a = fgRaw[3] * groupOpacity(e, bgNode)
      if (a <= 0.01) continue      // 整个透明：看不见的东西不谈对比度
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
            `${ratio.toFixed(2)}:1 < ${need}` +
            (a < 0.999 ? ` (有效 alpha ${a.toFixed(2)})` : ''),
        )
      }
    }
    return out
  }, root)
}
