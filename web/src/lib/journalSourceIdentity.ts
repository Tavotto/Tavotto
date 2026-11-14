import type { JournalFieldSource } from '@/lib/journals'

/** Temporary source-identity warning; never changes a stored snapshot or preflight. */
function isAdultGuide(value: unknown): boolean {
  if (typeof value !== 'string') return false
  try {
    const url = new URL(value)
    if (url.protocol !== 'https:' || url.hostname !== 'www.tandfonline.com' || url.port !== '' ||
        url.username !== '' || url.password !== '' || url.pathname !== '/action/authorSubmission') return false
    return url.search === '?show=instructions&journalCode=hapn21' ||
      url.search === '?journalCode=hapn21&show=instructions'
  } catch {
    return false
  }
}

export function hasChildAdultSource(
  journalId: string | undefined,
  sources: Record<string, JournalFieldSource> | undefined,
): boolean {
  if (journalId !== 'applied-neuropsychology-child' || !sources) return false
  return Object.values(sources).some((source) => source && (isAdultGuide(source.url) || isAdultGuide(source.link)))
}
