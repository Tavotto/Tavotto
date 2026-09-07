import type { Page } from '@playwright/test'

/**
 * 一个根节点底下**每一个**把布局撑破的元素（真布局才量得出来，jsdom 量不了）。
 *
 * ## 为什么逐个元素扫，而不是只量最外面两层
 *
 * 中间任何一个可滚 / 可裁切的容器都会把里面的溢出吸收掉，外面两层永远是干净的
 * ——往行里塞一个 900px 不收缩的元素，只量两层的写法照样绿（变异验过）。所以
 * 这里逐个元素扫，只认 `overflow-x: visible` 的那些：裁切（`truncate` 就是
 * hidden + 省略号）与有意可滚的容器本来就不该算撑破。
 *
 * ## 为什么只量 HTML 元素（含最外层的 `<svg>` 自己——它也在 SVG 命名空间里）
 *
 * SVG 里的 `scrollWidth` / `clientWidth` 量的**不是「这个盒子在页面上有多宽」**：
 * 设置 → 样式页的示意图声明 `viewBox="0 0 200 128"`，里面那条 `rotate(-90)` 的
 * 轴标题报 `sw=66 cw=21`——66 是没转之前的字宽、21 是转完的外接盒，两个数说的是
 * 同一段字的两种量法，谁都不是页面上的宽度；而外层 `<svg>` 按 viewBox 缩放，
 * 页面上一个像素都没溢出。主语错了的判据只会产出假红。
 *
 * 跳过它们**不会漏掉真的溢出**：一张真的画得太宽的图会让**装着它的那个 HTML
 * 容器** scrollWidth 超出 clientWidth，在这里照样报出来（`settings-shell.spec.ts`
 * 反证跑过：把示意图撑到 2000px，用例当场红）。
 *
 * ## 这里为什么是一份而不是三份
 *
 * 这把尺子原先在 `coding-agents` / `settings-shell` / `ux-consistency` 各抄了
 * 一遍，命名空间这一条只补进了其中一份——另外两份带着同一个盲区活了下来，直到
 * 2026-09-07 有人把挡在前面的那条断言修好，`ux-consistency` 才第一次真的走到
 * 这一行并当场红（#299）。共享判据修一处不算修完，所以现在只有这一份。
 */
export async function horizontalOffenders(page: Page, rootSel: string): Promise<string[]> {
  return page.evaluate((sel) => {
    const rootEl = document.querySelector(sel)
    if (!rootEl) return ['NO ROOT: ' + sel]
    const HTML_NS = 'http://www.w3.org/1999/xhtml'
    const out: string[] = []
    for (const el of [document.body, rootEl, ...Array.from(rootEl.querySelectorAll('*'))]) {
      if (el.namespaceURI !== HTML_NS) continue
      const e = el as HTMLElement
      if (getComputedStyle(e).overflowX !== 'visible') continue
      if (e.scrollWidth > e.clientWidth + 1) {
        out.push(
          `${e.tagName}.${String(e.className).slice(0, 60)} sw=${e.scrollWidth} cw=${e.clientWidth}`,
        )
      }
    }
    return out
  }, rootSel)
}
