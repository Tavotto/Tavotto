import { visiblePrepSurface } from '@/lib/prepSurface'
import { useOnboardingStore } from '@/store/onboardingStore'
import { useProjectPreparationStore } from '@/store/projectPreparationStore'
import { useProjectStore } from '@/store/projectStore'
import { useUiStore } from '@/store/uiStore'

/** 订阅版：此刻可见的准备面（见 `lib/prepSurface`）与「整个被藏起来」的原因（教程 / 引导） */
export function usePrepSurface() {
  const focus = useProjectPreparationStore((s) => s.focus)
  const entries = useProjectPreparationStore((s) => s.entries)
  const guideCard = useUiStore((s) => s.guideCard)
  const hidden =
    useProjectStore((s) => s.project?.tutorial === true) || useOnboardingStore((s) => s.status === 'active')
  return { hidden, surface: visiblePrepSurface({ focus, entries, guideCard, hidden }) }
}
