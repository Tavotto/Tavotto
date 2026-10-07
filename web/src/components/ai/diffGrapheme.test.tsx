import { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, expect, it, vi } from 'vitest'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { DiffView } from './DiffView'
import { parseUnifiedDiff, type LineRow } from './transcriptModel'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
const segmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' })
const diff = (x: string, y: string) => `@@ -1 +1 @@\n-${x}\n+${y}\n`
const boundaries = (text: string) => [text.length, ...Array.from(segmenter.segment(text), g => g.index)]
const regionalEdits = [
  ['f(🇦🇧🇨🇩)', 'f(🇽🇦🇧🇨🇩)'],
  ['f(🇽🇦🇧🇨🇩)', 'f(🇦🇧🇨🇩)'],
]

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  vi.resetModules()
})

// Adding or removing one regional indicator changes the pairing of the unchanged suffix.
// Check boundaries in both whole original lines, not in a resegmented suffix.
it.each(regionalEdits)('keeps both highlight offsets on grapheme boundaries: %s → %s', (x, y) => {
  const rows = parseUnifiedDiff(diff(x, y)) as LineRow[]
  expect(rows.map(row => row.kind)).toEqual(['del', 'add'])
  for (const row of rows) {
    expect(row.pair).toEqual([2, row.text.length - 1])
    for (const offset of row.pair!) expect(boundaries(row.text)).toContain(offset)
  }
})

it.each(regionalEdits)('keeps DiffView text-node boundaries and content intact: %s → %s', async (x, y) => {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  try {
    await act(async () => root.render(<TooltipProvider><DiffView diff={diff(x, y)} /></TooltipProvider>))
    for (const [kind, text] of [['del', x], ['add', y]]) {
      const words = host.querySelectorAll(`[data-diff-row="${kind}"] [data-diff-word]`)
      expect(words).toHaveLength(1)
      const body = words[0].parentElement!
      expect(body.textContent).toBe(text)
      let offset = 0
      for (const node of body.childNodes) {
        offset += node.textContent!.length
        expect(boundaries(text), `DOM boundary ${offset}`).toContain(offset)
      }
    }
  } finally {
    await act(async () => root.unmount())
    host.remove()
  }
})

it.each([
  ['x = 𝛼 + 1', 'x = 𝛽 + 1'],
  ['label="😀 ok"', 'label="🙀 ok"'],
  ['a = "𝐀"', 'a = "🐀"'],
  ['f(👨‍👩‍👧)', 'f(👨‍👩‍👦)'],
])('without Intl.Segmenter, still preserves surrogate pairs: %s → %s', async (x, y) => {
  const segment = vi.spyOn(Intl.Segmenter.prototype, 'segment')
  vi.stubGlobal('Intl', Object.create(Intl, { Segmenter: { value: undefined } }))
  const { parseUnifiedDiff: fallbackParse } = await import('./transcriptModel')
  const rows = fallbackParse(diff(x, y)) as LineRow[]
  expect(segment).not.toHaveBeenCalled()
  expect(rows.map(row => row.text)).toEqual([x, y])
  for (const row of rows) {
    expect(row.pair).toBeDefined()
    const offsets = [0]
    for (const codepoint of row.text) offsets.push(offsets.at(-1)! + codepoint.length)
    for (const offset of row.pair!) expect(offsets).toContain(offset)
  }
})

it('does not segment lines with no shared prefix or suffix', () => {
  const segment = vi.spyOn(Intl.Segmenter.prototype, 'segment')
  for (const [x, y] of [['abc', 'XYZ'], ['', '😀'], ['😀', '']]) {
    const rows = parseUnifiedDiff(diff(x, y)) as LineRow[]
    expect(rows.map(row => row.text)).toEqual([x, y])
    expect(rows.map(row => row.pair)).toEqual([undefined, undefined])
  }
  expect(segment).not.toHaveBeenCalled()
})
