import { msg } from '@/i18n'

/** A source snapshot limitation never changes the successful layout save result. */
export function svgSnapshotWarning(name: string, sources: readonly string[] | undefined) {
  return sources?.length
    ? msg('save.svgSnapshotWarning', { name, sources: sources.join(', ') }, 'workspace')
    : null
}
