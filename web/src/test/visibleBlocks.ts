/**
 * 默认可见的文字块（按**元素**数，不按子串）：自己直接带文字的元素算一块；收起的 `<details>` 里只有 `<summary>`
 * 可见，`sr-only` 不可见。jsdom 没有布局，「可见」只能按这两条结构事实判——它们正是这张卡控制可见性的全部手段
 */
function visibleElements(root: Element): Element[] {
  const out: Element[] = []
  const walk = (el: Element) => {
    if (el.classList.contains('sr-only')) return
    out.push(el)
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

export function visibleBlocks(root: Element): { tag: string; text: string }[] {
  return visibleElements(root)
    .filter((el) => [...el.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && (n.textContent ?? '').trim()))
    .map((el) => ({ tag: el.tagName.toLowerCase(), text: (el.textContent ?? '').trim() }))
}

/**
 * 「一句话就能读懂」（2026-09-29 用户硬性要求）的两把尺子：主区域（收起的「详情 / 高级」不算）里的句子数——按中文
 * 句末标点「。」数，不按子串找某一句；以及看得见的主按钮（`variant="primary"` 的近黑底白字）颗数
 */
export function visibleSentenceCount(root: Element): number {
  return visibleBlocks(root)
    .map((b) => (b.text.match(/。/g) ?? []).length)
    .reduce((a, b) => a + b, 0)
}

export function visiblePrimaryButtons(root: Element): number {
  return visibleElements(root).filter((el) => el.tagName === 'BUTTON' && el.className.includes('text-white')).length
}
