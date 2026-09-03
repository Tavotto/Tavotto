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
 * 背景色按**绘制顺序**取：问浏览器自己的命中测试要「这一点上自上而下的整叠元素」
 * （`paintStackBelow`），再从元素自己往下叠到第一个不透明层为止（`opaqueBg`）。
 * 绘制顺序不等于 DOM 顺序（z-index / 层叠上下文），所以不自己排——浏览器已经
 * 实现了整套规则。量不出来时报**判不准**，不编一个自信的数。
 * 画在**上面**的覆盖层一律不算——
 * 模态遮罩确实会把背后的文字压暗，但那是被遮住的 inert 内容，不在 WCAG 1.4.3
 * 的范围内；把它算进来，同一批节点就会在「有没有模态」之间给出两个结论。
 * 半透明前景先与背景合成再算比值，否则算出来偏乐观。
 */
export async function lowContrastNodes(page: Page, root = 'body'): Promise<string[]> {
  // **先等动效落定**（issue #210）：对比度是**稳定态**的属性，淡入淡出中间那一
  // 帧不是。本仓库的气泡/抽屉走 CSS 淡入淡出动画与 WAAPI，
  // 扫描撞进去就会把有效 alpha 量成 0.12、0.42 这种中间值，报出一条下一帧就消失
  // 的假红——两次扫描因此给出不同结论（实测：接入状态那条用例先 focus 轨道按钮，
  // 气泡正在淡出时被量到 `1.27:1（有效 alpha 0.12）`）。
  // 只等**有限次**的动画：脉冲类 `iterations: Infinity` 的动画永远不结束。
  await page.evaluate(async () => {
    if (typeof document.getAnimations !== 'function') return
    const finite = document
      .getAnimations()
      .filter((a) => a.playState === 'running' && a.effect?.getTiming().iterations !== Infinity)
    await Promise.race([
      Promise.allSettled(finite.map((a) => a.finished)),
      new Promise((r) => setTimeout(r, 2000)),
    ])
  })
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
     * 采样点上**绘制顺序位于该元素之下**的每一层，自上而下。
     *
     * 为什么不能只走 DOM 祖先链（issue #210）：祖先链看不见「画在下面的兄弟
     * 层」。空画布提示就是这么被诬告的——它绝对定位在纸面中心，视觉上坐在
     * **白纸**（`[data-page-sheet]`，`background: #ffffff`）上，而白纸是它的
     * **兄弟子树**，不是祖先；尺子一路往上只找到画布灰 `--color-canvas`
     * (#eaeae6)，`#6b6b64` 在它上面正好 4.45:1，于是报了一条差 0.05 的假红。
     * 实测：那一点的真实渲染像素是 rgb(255,255,255)，元素截图里白底占 68%，
     * 真实比值 5.37:1 —— 达标。
     *
     * **权威是浏览器自己的命中测试**（`document.elementsFromPoint`，返回该点上
     * 自上而下的整叠元素），不是 DOM 顺序。第一版这里按几何自己排「元素自己 →
     * 前序兄弟 → 父元素」，等于**把 DOM 顺序当成绘制顺序** —— 而
     * `LeftPanel` 在 narrow 档给抽屉的是 `absolute inset-y-0 z-30`：它在 DOM 里
     * 排在画布**之前**，却画在画布**之上**（实测 900px 视口下 `position:absolute`
     * / `z-index:30`）。量画布文字时那一版会停在抽屉那层不透明白底上，把一个根本
     * 不在文字背后的颜色当成背景。z-index、`position`、`transform`/`opacity`/
     * `filter`/`isolation` 造出的层叠上下文……自己实现一遍是整套 CSS 绘制顺序，
     * 做一半比不做更危险（它会让人更信它）。浏览器已经实现了，直接问它。
     *
     * 命中测试的两个已知障碍都当场解掉，不留「有模态就退化」的暗门：
     * ① `pointer-events` —— 模态打开时 Radix 会把 `body` 设成
     *    `pointer-events:none`，同一点上整叠从 13 个塌成 2 个（只剩遮罩和
     *    `<html>`）。扫描期间临时注入 `*{pointer-events:auto !important}`
     *    盖住它（作者 `!important` 压得过行内样式），扫完就摘掉；
     * ② 滚出视口 —— 先按元素每个 client rect 的可见中心试，再
     *    `scrollIntoView` 重试一次，扫完把所有滚动位置还原。
     *
     * 还是命中不到就返回 `null`：那说明它在**任何一个采样点上都没有被画出来**
     * （实测这一档几乎全是折叠 `<details>` 里的 `dt`/`dd`——`getClientRects()`
     * 有值，但根本没画）。不给它编一个背景色，交给调用处当「不在范围内」处理，
     * 与 `display:none` 同理。
     *
     * **两条已知盲区，别让它们悄悄成立：**
     * ① 采样点是**元素框内的一点**，不是字形像素。命中测试按盒子算，所以点落在
     *    字与字之间的空隙上照样命中元素自己（实测：`letter-spacing:14px` 的窄
     *    行内元素与紧挨 inline-block 的行内元素都正常报出 1.61:1）。剩下的盲区是
     *    **同一个框内背景不均匀**——半覆盖的兄弟层、`background-image`、渐变——
     *    这时量到的是采样点那一处的背景，不是每个字背后的。
     * ② `scrollIntoView` 会触发页面自己的 scroll 监听。注入的 `<style>` 挂在
     *    `document.head`，而本仓库唯一的 `MutationObserver`（`focusRescue`）只
     *    `observe(document.body, {childList, subtree})`，看不到它；滚动位置扫完
     *    逐个还原（实测扫描前后 `<style>` 数与 `body` 的行内样式都不变）。
     */
    const paintStackBelow = (el: HTMLElement): HTMLElement[] | null => {
      const at = (): HTMLElement[] | null => {
        for (const r of Array.from(el.getClientRects())) {
          const x0 = Math.max(0, r.left)
          const x1 = Math.min(window.innerWidth, r.right)
          const y0 = Math.max(0, r.top)
          const y1 = Math.min(window.innerHeight, r.bottom)
          if (!(x1 > x0 && y1 > y0)) continue
          const stack = document.elementsFromPoint((x0 + x1) / 2, (y0 + y1) / 2)
          const i = stack.indexOf(el)
          // 从 el 自己往下切：画在**上面**的覆盖层一律不算（见函数头注释）
          if (i >= 0) return stack.slice(i) as HTMLElement[]
        }
        return null
      }
      const direct = at()
      if (direct) return direct
      el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' as ScrollBehavior })
      return at()
    }
    /**
     * 文字背后**真正的**颜色：按绘制顺序从元素自身往下收集每一层背景，直到
     * （含）第一个不透明的，再从下往上叠回来。
     *
     * 以前这里只找第一个**不透明**背景，半透明的中间层直接跳过——于是
     * `bg-ink/[.72]`（白字 + 72% 墨色角标）会被当成「白底白字」算出 1.00:1，
     * 而它实际是 6.7:1。这不是保守，是**假红**：它指着一个对比度好得很的元素，
     * 逼人去修一件不存在的事（见 `simulated-input-shape-lies` 那一类）。
     *
     * 返回的 `node` 仍然是那个不透明层——`groupOpacity` 要用它当累乘的终点。
     */
    const opaqueBg = (
      el: HTMLElement,
    ): { color: number[]; node: HTMLElement } | 'not-painted' | 'no-opaque-layer' => {
      const stack = paintStackBelow(el)
      if (!stack) return 'not-painted'
      const layers: number[][] = []
      let stop: HTMLElement | null = null
      for (const n of stack) {
        const c = parse(getComputedStyle(n).backgroundColor)
        if (c && c[3] > 0) {
          layers.push(c)
          if (c[3] >= 1) {
            stop = n
            break
          }
        }
      }
      // 叠到底都没有不透明层：**「不知道」是独立一档**。以前这里按白纸算，那是
      // 把「判不准」并进了一个相邻取值——算出来的比值看着跟真的一样自信。
      if (!stop) return 'no-opaque-layer'
      let base = layers.pop()!
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
     *
     * 背景层现在可能是个**兄弟**（见 `paintStackBelow`），那就累乘到「第一个
     * 同时含住它俩的祖先」为止：再往上的 opacity 对前景和背景同等生效，进不了
     * 比值。`Node.contains` 认自己，所以背景层是祖先时这条判据退化成 `n === stop`。
     */
    const groupOpacity = (el: HTMLElement, stop: HTMLElement | null): number => {
      let n: HTMLElement | null = el
      let a = 1
      while (n) {
        const o = parseFloat(getComputedStyle(n).opacity)
        if (!Number.isNaN(o)) a *= o
        if (stop && n.contains(stop)) break
        n = n.parentElement
      }
      return a
    }
    const scope = document.querySelector(rootSelector)
    if (!scope) return [`NO ROOT: ${rootSelector}`]
    // 命中测试要在「pointer-events 全开」下做，且允许把元素滚进视口——两样都是
    // 临时的，`finally` 里原样还原（同一次同步 evaluate 内完成，页面不留痕迹）。
    const peOverride = document.createElement('style')
    peOverride.textContent = '*{pointer-events:auto !important}'
    document.head.appendChild(peOverride)
    const savedScroll: [Element, number, number][] = []
    for (const n of Array.from(document.querySelectorAll('*'))) {
      if (n.scrollTop || n.scrollLeft) savedScroll.push([n, n.scrollTop, n.scrollLeft])
    }
    const savedPage: [number, number] = [window.scrollX, window.scrollY]
    const out: string[] = []
    try {
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
        const measured = opaqueBg(e)
        // 「这一刻它根本没被画出来」与 `display:none` 同理，不在对比度的范围内。
        if (measured === 'not-painted') continue
        // 「判不准」是**独立一档**，不许并进任何一个数：报出来，让人去看，而不是
        // 给一个自信的错数（`unknown-is-its-own-value`）。
        if (measured === 'no-opaque-layer') {
          out.push(
            `${e.tagName}.${String(e.className).slice(0, 40)} "${text.slice(0, 16)}" ` +
              `判不准（绘制顺序上叠到底也没有不透明层，量不出背景色）`,
          )
          continue
        }
        const { color: bg, node: bgNode } = measured
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
    } finally {
      for (const [n, top, left] of savedScroll) {
        n.scrollTop = top
        n.scrollLeft = left
      }
      window.scrollTo(savedPage[0], savedPage[1])
      peOverride.remove()
    }
    return out
  }, root)
}
