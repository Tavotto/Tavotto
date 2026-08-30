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
    // 1×1 画布：把**任何** CSS 颜色语法解析成 sRGB。
    // 只认 `rgb()/rgba()` 是个静默的盲点——Tailwind 的 `bg-ink/[.72]` 在现代
    // Chromium 里会以 `oklab(0.22 … / 0.72)` 的形态从 getComputedStyle 回来，
    // 正则匹配不上就被当成「这一层没有背景」，于是尺子继续往上找，最后拿一个
    // 与文字同色的祖先算出 1.00:1 —— 一条**假红**，而且它指向的那个元素其实
    // 对比度好得很。（实测：接入状态那条用例在 windows-exe-smoke 上第一次真跑
    // 起来就红在这三个角标上；同一份页面在 axe 扫过之后再量又是干净的——
    // 拿不拿得到 `rgb()` 取决于样式有没有被 flush 过，这种判据不能要。）
    const probe = document.createElement('canvas')
    probe.width = probe.height = 1
    const ctx = probe.getContext('2d', { willReadFrequently: true })
    const parse = (s: string): number[] | null => {
      const m = s.match(/^rgba?\(([^)]+)\)$/)
      if (m) {
        const p = m[1].split(/[\s,/]+/).filter(Boolean).map(Number)
        if (p.length >= 3 && !p.some((v, i) => i < 3 && Number.isNaN(v))) {
          return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]
        }
      }
      // `oklab()` —— 现代 Chromium 对带透明度的 Tailwind 颜色就发这个形态，
      // 而 canvas 的 `fillStyle` 认不出它（实测），所以这一支自己算。
      const ok = s.match(/^oklab\(([^)]+)\)$/)
      if (ok) {
        const p = ok[1].split(/[\s,/]+/).filter(Boolean).map((v) => parseFloat(v))
        if (p.length >= 3 && !p.slice(0, 3).some(Number.isNaN)) {
          const [L, A, B] = p
          const alpha = p.length > 3 && !Number.isNaN(p[3]) ? p[3] : 1
          const l = (L + 0.3963377774 * A + 0.2158037573 * B) ** 3
          const m = (L - 0.1055613458 * A - 0.0638541728 * B) ** 3
          const q = (L - 0.0894841775 * A - 1.291485548 * B) ** 3
          const lin = [
            4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * q,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * q,
            -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * q,
          ]
          const srgb = lin.map((v) => {
            const c = v <= 0.0031308 ? 12.92 * v : 1.055 * Math.abs(v) ** (1 / 2.4) - 0.055
            return Math.max(0, Math.min(255, Math.round(c * 255)))
          })
          return [srgb[0], srgb[1], srgb[2], alpha]
        }
      }
      if (!ctx || !s || s === 'none') return null
      // `fillStyle` 对认不出的字符串**不报错、不改值**，所以先塞一个哨兵再比对：
      // 没被改写就说明这个颜色浏览器自己也解不出，那才是真的 null。
      ctx.fillStyle = '#000000'
      ctx.fillStyle = s
      const sentinel = ctx.fillStyle
      ctx.fillStyle = '#ffffff'
      ctx.fillStyle = s
      if (ctx.fillStyle !== sentinel) return null
      ctx.globalCompositeOperation = 'copy'
      ctx.fillRect(0, 0, 1, 1)
      const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data
      return [r, g, b, a / 255]
    }
    const lum = (c: number[]) => {
      const f = c.slice(0, 3).map((v) => {
        const s = v / 255
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
      })
      return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]
    }
    /**
     * 文字背后**真正的**颜色：从元素自身往上收集每一层背景，直到（含）第一个
     * 不透明的，再从下往上叠回来。
     *
     * 以前这里只找第一个**不透明**背景，半透明的中间层直接跳过——于是
     * `bg-ink/[.72]`（白字 + 72% 墨色角标）会被当成「白底白字」算出 1.00:1，
     * 而它实际是 6.7:1。这不是保守，是**假红**：它指着一个对比度好得很的元素，
     * 逼人去修一件不存在的事（见 `simulated-input-shape-lies` 那一类）。
     *
     * 返回的 `node` 仍然是那个不透明层——`groupOpacity` 要用它当累乘的终点。
     */
    const opaqueBg = (el: HTMLElement): { color: number[]; node: HTMLElement | null } => {
      const layers: number[][] = []
      let n: HTMLElement | null = el
      let stop: HTMLElement | null = null
      while (n) {
        const c = parse(getComputedStyle(n).backgroundColor)
        if (c && c[3] > 0) {
          layers.push(c)
          if (c[3] >= 1) {
            stop = n
            break
          }
        }
        n = n.parentElement
      }
      // 最底下那层：找到了不透明的就用它，一直没找到就按白纸算（同旧行为）
      let base = stop ? layers.pop()! : [255, 255, 255, 1]
      for (let i = layers.length - 1; i >= 0; i--) {
        const c = layers[i]
        const a = c[3]
        base = [c[0] * a + base[0] * (1 - a), c[1] * a + base[1] * (1 - a), c[2] * a + base[2] * (1 - a), 1]
      }
      return { color: base, node: stop }
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
