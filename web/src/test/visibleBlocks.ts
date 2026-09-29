/**
 * 默认可见的文字块（按**元素**数，不按子串）：自己直接带文字的元素算一块；收起的 `<details>` 里只有 `<summary>`
 * 可见，`sr-only` 不可见。jsdom 没有布局，「可见」只能按这两条结构事实判——它们正是这张卡控制可见性的全部手段
 */
export function visibleBlocks(root: Element): { tag: string; text: string }[] {
  const out: { tag: string; text: string }[] = []
  const walk = (el: Element) => {
    if (el.classList.contains('sr-only')) return
    const own = [...el.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && (n.textContent ?? '').trim())
    if (own) out.push({ tag: el.tagName.toLowerCase(), text: (el.textContent ?? '').trim() })
    const kids = [...el.children]
    if (el.tagName === 'DETAILS' && !(el as HTMLDetailsElement).open) {
      kids.filter((k) => k.tagName === 'SUMMARY').forEach(walk)
      return
    }
    kids.forEach(walk)
  }
  walk(root)
  return out
}
