/** One atomic override keeps the selected primary and preferred fallback together. */
export interface FigureFontChoice {
  primary: string | string[] | null
  fallback: string
}

export function figureFontChoice(value: unknown): FigureFontChoice | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const v = value as Record<string, unknown>
  const primary = v.primary
  if (primary !== null && typeof primary !== 'string' && !(Array.isArray(primary) && primary.every((x) => typeof x === 'string'))) return null
  if (typeof v.fallback !== 'string' || !v.fallback) return null
  return { primary: primary as string | string[] | null, fallback: v.fallback }
}

export function changeFigureFont(value: unknown, part: 'primary' | 'fallback', next: string | null): unknown {
  const choice = figureFontChoice(value)
  const primary = choice?.primary ?? (typeof value === 'string' || (Array.isArray(value) && value.every((x) => typeof x === 'string')) ? value : null)
  const fallback = choice?.fallback ?? null
  const p = part === 'primary' ? next : primary
  const f = part === 'fallback' ? next : fallback
  return f ? { primary: p, fallback: f } : p
}

/** Primary value for existing single-font controls. */
export function figurePrimaryValue(value: unknown, original: unknown): unknown {
  const choice = figureFontChoice(value)
  if (!choice) return value
  return Array.isArray(choice.primary) ? choice.primary[0] : choice.primary ?? original
}
