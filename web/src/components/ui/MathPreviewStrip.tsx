import { useEffect, useState } from 'react'
import { t as translate } from '@/i18n'
import type { RichCodec } from '@/lib/richEditor'
import { mathPathData, useMathBox } from '@/lib/mathtext'

/** 光标所在行最多预览几个公式（多了只看前几个：预览条是核对用的，不是排版） */
export const PREVIEW_MAX = 4
/** 停手多久再去排：源码框每个键都改字符串，不能每键一次请求 */
export const PREVIEW_DEBOUNCE_MS = 250

/**
 * 源码里光标所在行的公式（去重、最多 `PREVIEW_MAX` 个）。
 *
 * 取「光标那一行」：图内文字的 `$…$` 本来就按行判（matplotlib 逐行），一行一个单位；
 * 标注的公式也几乎都在一行里。整段里公式很多时只看光标处，预览条才不会长成一面墙。
 * 读法与源码框同步文档时用的同一个（`reparse ?? parse`），所以预览的就是切回去会看到的那些公式。
 */
export function previewFormulas(source: string, caret: number, codec: RichCodec): string[] {
  const at = Math.max(0, Math.min(caret, source.length))
  const line = source.split('\n')[source.slice(0, at).split('\n').length - 1] ?? ''
  const out: string[] = []
  for (const r of (codec.reparse ?? codec.parse)(line)) {
    if (r.math && r.text.trim() && !out.includes(r.text)) out.push(r.text)
    if (out.length >= PREVIEW_MAX) break
  }
  return out
}

const tx = (key: string) => translate(`richText.${key}`, { ns: 'inspector' })

/** 排不出来的原因：环境问题说人话，语法错误直接用 matplotlib 的原话（它最准） */
const reasonOf = (error: string | undefined) =>
  error === 'no_runtime' ? tx('previewNoRuntime') : (error ?? '')

function Item({ src, family, sizePx }: { src: string; family: string; sizePx: number }) {
  const box = useMathBox(src, family)
  // 还没排好：先不画（旧的一条留着直到新的到，不闪成源码）
  if (!box) return null
  if (!box.ok || box.width === undefined) {
    return (
      <span data-math-preview-item="error" className="inline-flex min-w-0 items-baseline gap-1.5 text-xs">
        <span
          className="font-mono text-ink"
          style={{ textDecoration: 'underline wavy', textDecorationColor: 'var(--color-danger)' }}
        >
          {`$${src}$`}
        </span>
        <span className="min-w-0 break-words text-danger">{reasonOf(box.error)}</span>
      </span>
    )
  }
  const asc = box.ascent ?? 0
  const desc = box.descent ?? 0
  return (
    <svg
      data-math-preview-item="ok"
      role="img"
      aria-label={src}
      viewBox={`0 ${-asc} ${box.width} ${asc + desc}`}
      width={box.width * sizePx}
      height={(asc + desc) * sizePx}
      className="shrink-0"
      overflow="visible"
    >
      <path d={mathPathData(box)} fill="currentColor" />
    </svg>
  )
}

/**
 * LaTeX 源码模式下方的公式预览条（ADR 0152 后续）：只画公式，没有公式就整条不出现。
 * 停手 `PREVIEW_DEBOUNCE_MS` 后才换内容；第一次显示（刚切到源码模式）不等。
 */
export function MathPreviewStrip({
  source,
  caret,
  codec,
  family,
  sizePx,
  label,
}: {
  source: string
  caret: number
  codec: RichCodec
  family: string
  sizePx: number
  label: string
}) {
  const [shown, setShown] = useState(() => previewFormulas(source, caret, codec))
  useEffect(() => {
    const next = previewFormulas(source, caret, codec)
    const timer = setTimeout(
      () => setShown((cur) => (cur.length === next.length && cur.every((s, i) => s === next[i]) ? cur : next)),
      PREVIEW_DEBOUNCE_MS,
    )
    return () => clearTimeout(timer)
  }, [source, caret, codec])
  if (shown.length === 0) return null
  return (
    <div
      role="group"
      aria-label={label}
      data-math-preview
      className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border pt-1.5 text-ink"
    >
      {shown.map((src) => (
        <Item key={src} src={src} family={family} sizePx={sizePx} />
      ))}
    </div>
  )
}
