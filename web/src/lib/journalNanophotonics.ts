import type { JournalFieldSource } from '@/lib/journals'

const LEGACY_JOURNAL = 'nanophotonics'
const LEGACY_SOURCE = 'https://www.degruyterbrill.com/journal/key/nanoph/html'
export const NANOPHOTONICS_GUIDE = 'https://onlinelibrary.wiley.com/page/journal/21928614/homepage/author-guidelines'
export type NanophotonicsSourceState = 'legacy' | 'unknown' | null

/** Classify only the exact journal; a different source is not evidence of safety. */
export function nanophotonicsSourceState(
  journalId: string | undefined,
  sources: Record<string, JournalFieldSource> | undefined,
): NanophotonicsSourceState {
  if (journalId !== LEGACY_JOURNAL) return null
  if (!sources || typeof sources !== 'object' || Array.isArray(sources)) return 'unknown'
  const source = sources.min_raster_dpi
  if (!source || typeof source !== 'object' || Array.isArray(source)) return 'unknown'
  if (typeof source.url !== 'string' || !source.url.trim()) return 'unknown'
  try {
    const parsed = new URL(source.url)
    if (parsed.protocol !== 'https:' || parsed.username || parsed.password) return 'unknown'
  } catch { return 'unknown' }
  return source.url === LEGACY_SOURCE ? 'legacy' : null
}
