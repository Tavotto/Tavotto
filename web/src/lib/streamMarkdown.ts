/**
 * 流式回答的「逐词显影」——`components/ai/Markdown` 在 streaming 时挂上的 rehype 插件。
 *
 * ChatGPT / Claude 那种「新到的字轻轻浮现、旧的字纹丝不动」靠的不是逐字动画，而是
 * **每个词一个元素、只在挂载那一帧播一次淡入**。这里在 hast（markdown → HTML 的中间树）
 * 上把每个文字节点切成词，每个词包一层 `<span class="animate-stream-in">`：
 *
 * - react-markdown 给元素的 key 是「标签名 + 在父节点里的序号」（hast-util-to-jsx-runtime），
 *   文字只会往后追加，所以第 i 个 span 始终是第 i 个词——旧词的 DOM 不重建、不重播，
 *   新词挂上来才播那一次淡入；正在长的最后一个词只是原地改文字。半截 markdown 被补全
 *   （`*强调*` 闭合、`- ` 变成列表）时那一段会重排一次、重播一次淡入——那是 ChatGPT 也有的
 *   一次轻闪，不是错误。
 * - 切词用 `Intl.Segmenter`（中文没有空格，按空白切会把整段中文变成一个词，永远不播；
 *   Segmenter 按词典切成「正在 | 改写 | 脚本」）；标点贴到前一个词上，不单独成一个会闪的片段；
 *   没有 Segmenter 的引擎退回按空白切。
 * - 终稿到达（streaming=false）时不再挂这个插件，span 全部消失、文字合并——视觉零变化，
 *   因为那时每个词早已完全不透明。
 *
 * 只动 opacity（`--animate-stream-in`），不位移：正在阅读的文字一动就是抖。
 * `prefers-reduced-motion` 由 index.css 的全局兜底照顾（CSS 动画，不经 JS）。
 */

/** hast 的最小结构（`@types/hast` 不是本包的直接依赖，按结构写就够了） */
interface HastText {
  type: 'text'
  value: string
}
interface HastElement {
  type: 'element'
  tagName: string
  properties?: Record<string, unknown>
  children: HastNode[]
}
interface HastParent {
  type: string
  children: HastNode[]
}
type HastNode = HastText | HastElement | HastParent | { type: string }

/** 每个词外面那层 span 的类名——就是 index.css 里 `--animate-stream-in` 生成的工具类 */
export const STREAM_WORD_CLASS = 'animate-stream-in'

const segmenter: Intl.Segmenter | null =
  typeof Intl !== 'undefined' && typeof Intl.Segmenter === 'function'
    ? new Intl.Segmenter(undefined, { granularity: 'word' })
    : null

const isSpace = (s: string) => /^\s+$/.test(s)

export interface WordPiece {
  text: string
  /** 纯空白：原样留作文字节点，不包 span */
  space: boolean
}

/**
 * 切成「词」与「空白」交替的片段。
 * 中文按 Intl 的词典切；标点 / 符号（非 wordLike）贴到前一个词后面；
 * 没有 Segmenter 的引擎退回按空白切（中文那时整段是一个片段，只是不播动画，不损失内容）。
 */
export function segmentWords(text: string): WordPiece[] {
  const out: WordPiece[] = []
  if (!segmenter) {
    for (const part of text.split(/(\s+)/)) {
      if (part) out.push({ text: part, space: isSpace(part) })
    }
    return out
  }
  for (const seg of segmenter.segment(text)) {
    const s = seg.segment
    if (isSpace(s)) {
      out.push({ text: s, space: true })
      continue
    }
    const last = out.at(-1)
    if (!seg.isWordLike && last && !last.space) last.text += s
    else out.push({ text: s, space: false })
  }
  return out
}

function wrapWords(node: HastParent): void {
  const next: HastNode[] = []
  for (const child of node.children) {
    if (child.type === 'text') {
      for (const { text, space } of segmentWords((child as HastText).value)) {
        next.push(
          space
            ? { type: 'text', value: text }
            : {
                type: 'element',
                tagName: 'span',
                properties: { className: [STREAM_WORD_CLASS] },
                children: [{ type: 'text', value: text }],
              },
        )
      }
      continue
    }
    if ('children' in child) wrapWords(child as HastParent)
    next.push(child)
  }
  node.children = next
}

/** rehype 插件：树里每个文字节点按词切开、逐词包 span */
export function rehypeStreamWords() {
  return (tree: HastParent) => {
    wrapWords(tree)
  }
}
