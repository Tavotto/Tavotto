/**
 * 「rev16 之前」的 Nature 八刊：种子里曾带 `info.width_double_mm`（原句是分图最大宽度 180 mm，不是双栏宽度；
 * rev16 起从种子删掉）。**已按这些期刊存盘的旧项目**绑定里还留着这一项，界面按「历史 AIP 建议」显示
 * （`journalPanelDisplay`），这些用例要的就是这份旧形状——所以在现行种子上把那一条原样加回来，而不是依赖种子。
 */
import { loadCatalog, type JournalCatalog } from '@/lib/journals'
import seed from '@journals'
import rules from './legacyNatureWidthRules.json'

export const LEGACY_NATURE_IDS = Object.keys(rules)

export function legacyNatureCatalog(): JournalCatalog {
  const raw = structuredClone(seed) as unknown as { journals: Record<string, { rules: Record<string, unknown> }> }
  for (const [id, rule] of Object.entries(rules)) raw.journals[id].rules['info.width_double_mm'] = structuredClone(rule)
  return loadCatalog(raw)
}
