import { create } from 'zustand'
import type { Manifest } from '@/lib/api'

/** The bitmap actually displayed by each panel, paired with its draw's geometry. */
export interface MountedPng {
  key: string
  rev: number
  source: Manifest | null
  url: string | null
  manifest: Manifest | null
}

interface MountedPngState {
  byPanel: Record<string, MountedPng>
  show: (panelId: string, key: string, rev: number, source: Manifest | null, url: string | null) => void
  loaded: (panelId: string, frame: MountedPng) => void
  clear: (panelId: string) => void
}

export const useMountedPngStore = create<MountedPngState>((set) => ({
  byPanel: {},
  show: (panelId, key, rev, source, url) => set((s) => {
    const old = s.byPanel[panelId]
    if (old?.key === key && old.rev === rev && old.source === source && old.url === url) return s
    return { byPanel: { ...s.byPanel, [panelId]: { key, rev, source, url, manifest: null } } }
  }),
  loaded: (panelId, frame) => set((s) => {
    const expected = s.byPanel[panelId]
    if (!expected || expected.key !== frame.key || expected.rev !== frame.rev || expected.source !== frame.source || expected.url !== frame.url) return s
    if (expected.url === frame.url && expected.manifest === frame.manifest) return s
    return { byPanel: { ...s.byPanel, [panelId]: frame } }
  }),
  clear: (panelId) => set((s) => {
    if (!(panelId in s.byPanel)) return s
    const byPanel = { ...s.byPanel }
    delete byPanel[panelId]
    return { byPanel }
  }),
}))

export function pngManifestOf(
  original: Manifest | null,
  key: string,
  rev: number,
  source: Manifest | null,
  mounted: MountedPng | undefined,
): Manifest | null {
  if (!mounted) return original
  if (mounted.key !== key || mounted.rev !== rev || mounted.source !== source) return null
  return mounted.manifest
}
