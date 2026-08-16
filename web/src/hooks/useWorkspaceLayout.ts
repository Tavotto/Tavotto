import { useEffect } from 'react'
import { useUiStore, type WorkspaceLayout } from '@/store/uiStore'

/**
 * 工作区断点。画布是主角，窄下来时先让侧栏让路，而不是压缩画布：
 * - ≥1440 左右可同时钉住；
 * - 1024–1439 左右互斥，同时只留一侧（1280×720 下画布仍 ≥760px）；
 * - <1024 侧栏改成盖在画布上的抽屉，画布宽度完全不受影响。
 */
const WIDE = 1440
const MEDIUM = 1024

export const layoutFor = (w: number): WorkspaceLayout =>
  w >= WIDE ? 'wide' : w >= MEDIUM ? 'medium' : 'narrow'

export function useWorkspaceLayout(): WorkspaceLayout {
  const layout = useUiStore((s) => s.layout)

  useEffect(() => {
    const apply = () => useUiStore.getState().setLayout(layoutFor(window.innerWidth))
    apply()
    window.addEventListener('resize', apply)
    return () => window.removeEventListener('resize', apply)
  }, [])

  return layout
}
