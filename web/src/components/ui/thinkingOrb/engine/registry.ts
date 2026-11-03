// Vendored from thinking-orbs (MIT, (c) Jakub Antalik) @ de85557, src/engine/registry.ts — geometry only.
// See THIRD_PARTY_NOTICES.md and ADR 0164. The upstream canvas painters (MODE_DRAWS) are dropped:
// Tavotto paints with its own ink tokens in ../paint.ts.

import type { ModeFrame } from './types'
import { frameBraid } from './braid'
import { frameGlobe, frameRubik, frameWave } from './lattice'
import { frameMorph } from './morph'
import { frameOrbits } from './orbits'
import { frameRibbon } from './ribbon'
import { frameWeb } from './web'

export type ModeKey = 'orbits' | 'globe' | 'rubik' | 'wave' | 'web' | 'braid' | 'ribbon' | 'ring' | 'morph'

/** Mode key → pure geometry builder (no canvas, no theme). */
export const MODE_FRAMES: Record<ModeKey, ModeFrame> = {
  orbits: frameOrbits,
  globe: frameGlobe,
  rubik: frameRubik,
  wave: frameWave,
  web: frameWeb,
  braid: frameBraid,
  ribbon: frameRibbon,
  // ring shares ribbon's geometry — the `faceOn` profile flag switches it
  ring: frameRibbon,
  morph: frameMorph,
}
