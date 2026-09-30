import { i18n } from '@/i18n'

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
 * 一段文字里有几句——按**语种自己的**句末标点数（2026-09-29 用户硬性要求「一句话就能读懂」；Codex #742：只数「。」
 * 的话英文两句蒙混过关）：中文数「。！？」；英文数后面跟空白或到结尾的「. ! ?」——「…」是单独的省略号字符、
 * 「3.13」「python.org」的点后面不是空白，都不算句末。新加一个语种时在这里加它的规则，没有规则的语种直接抛错
 * （判不出就别假装判了）
 */
export function sentenceCount(text: string, lang: string): number {
  if (lang.startsWith('zh')) return (text.match(/[。！？]/g) ?? []).length
  if (lang.startsWith('en')) return (text.match(/[.!?](?=\s|$)/g) ?? []).length
  throw new Error(`sentenceCount: 没有 ${lang} 的句末规则`)
}

/**
 * 「一句话就能读懂」的两把尺子：主区域（收起的「详情 / 高级」不算）里的句子数——每个看得见的文字块各按语种数、再加总，
 * 不按子串找某一句；以及看得见的主按钮（`variant="primary"` 的近黑底白字）颗数
 */
export function visibleSentenceCount(root: Element, lang = i18n.language): number {
  return visibleBlocks(root)
    .map((b) => sentenceCount(b.text, lang))
    .reduce((a, b) => a + b, 0)
}

export function visiblePrimaryButtons(root: Element): number {
  return visibleElements(root).filter((el) => el.tagName === 'BUTTON' && el.className.includes('text-white')).length
}

/**
 * 「详情」展开后有没有重复（2026-09-29 用户截图：「不改动源码」「隔离环境」、下载各说了两遍）：把区域里每个文字块按
 * 句末标点切成句子，回重复出现的那些；另回几个「同一件事」的记号各出现了几次——句子措辞不同但说的是同一件事时，
 * 只比句子抓不到
 */
export function repeatedSentences(root: Element): string[] {
  const all = [...root.querySelectorAll('*')]
    .filter((el) => [...el.childNodes].some((n) => n.nodeType === Node.TEXT_NODE && (n.textContent ?? '').trim()))
    .flatMap((el) => (el.textContent ?? '').split(/[。；]/))
    .map((t) => t.trim())
    .filter(Boolean)
  return all.filter((t, i) => all.indexOf(t) !== i)
}

export function mentionCount(root: Element, needle: string): number {
  return (root.textContent ?? '').split(needle).length - 1
}
