import { describe, expect, it } from 'vitest'
import { journalSources, loadCatalog, type JournalFieldSource } from './journals'
import { panelDisplayPath } from './journalPanelDisplay'

const oldPath = 'info.width_double_mm'
const correctPath = 'info.panel_max_width_aip_mm'
const source = journalSources('nature-chemistry', loadCatalog())[oldPath]
const withoutStage = structuredClone(source)
Reflect.deleteProperty(withoutStage, 'stage')

describe('panelDisplayPath', () => {
  it.each(['nature-chemistry', 'nature-energy', 'nature-materials', 'nature-medicine', 'nature-methods', 'nature-nanotechnology', 'nature-photonics', 'nature-physics'])('recognizes production evidence for %s without changing it', (journal) => {
    const current = journalSources(journal, loadCatalog())[oldPath]
    const before = structuredClone(current)
    expect(current.stage).toBe('production')
    expect(panelDisplayPath(journal, oldPath, current)).toBe(correctPath)
    expect(current).toEqual(before)
  })
  it.each([
    ['other journal', 'other-journal', oldPath, source],
    ['other path', 'nature-chemistry', 'widths_mm.double', source],
    ['wrong journal source', 'nature-materials', oldPath, source],
    ['different source', 'nature-chemistry', oldPath, { ...source, url: 'https://example.invalid/' }],
    ['different quote', 'nature-chemistry', oldPath, { ...source, quote: 'Double column width 180 mm' }],
    ['different value', 'nature-chemistry', oldPath, { ...source, value: 170 }],
    ['different qualifier', 'nature-chemistry', oldPath, { ...source, qualifier: 'min' as const }],
    ['submission stage', 'nature-chemistry', oldPath, { ...source, stage: 'submission' as const }],
    ['unknown stage', 'nature-chemistry', oldPath, { ...source, stage: 'unknown' as JournalFieldSource['stage'] }],
    ['missing stage', 'nature-chemistry', oldPath, withoutStage],
    ['any stage', 'nature-chemistry', oldPath, { ...source, stage: 'any' as const }],
    ['unset', 'nature-chemistry', oldPath, { ...source, unset: true }],
  ])('leaves %s unchanged', (_label, journal, path, source) => {
    expect(panelDisplayPath(journal, path, source)).toBe(path)
  })
})
