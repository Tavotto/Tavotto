import type { JournalFieldSource } from './journals'

// These historical bindings label a figure-panel limit as a double-column width.
// Correct only the matching stored evidence; never rewrite the project snapshot.
const urls: Record<string, string> = {
  'nature-chemistry': 'https://www.nature.com/nchem/submission-guidelines/aip-and-formatting',
  'nature-energy': 'https://www.nature.com/nenergy/submission-guidelines/aip-and-formatting',
  'nature-materials': 'https://www.nature.com/nmat/submission-guidelines/aip-and-formatting',
  'nature-medicine': 'https://www.nature.com/nm/submission-guidelines/aip-and-formatting',
  'nature-methods': 'https://www.nature.com/nmeth/submission-guidelines/aip-and-formatting',
  'nature-nanotechnology': 'https://www.nature.com/nnano/submission-guidelines/aip-and-formatting',
  'nature-photonics': 'https://www.nature.com/nphoton/submission-guidelines/aip-and-formatting',
  'nature-physics': 'https://www.nature.com/nphys/submission-guidelines/aip-and-formatting',
}
const quote = 'Figure panels should be prepared at a minimum resolution of 300 dpi and saved at a maximum width of 180 mm.'

export function panelDisplayPath(journal: string, path: string, source: JournalFieldSource): string {
  if (
    path === 'info.width_double_mm' && urls[journal] === source.url &&
    source.quote === quote && source.value === 180 && source.qualifier === 'max' && source.stage === 'production' &&
    source.rule_scope === 'journal' && source.source_scope === 'journal' &&
    source.affects === 'info' && !source.unset
  ) return 'info.panel_max_width_aip_mm'
  return path
}
