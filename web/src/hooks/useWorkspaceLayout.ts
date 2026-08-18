import { useEffect } from 'react'
import { layoutFor, useUiStore, type WorkspaceLayout } from '@/store/uiStore'

/**
 * 断点与 layoutFor 的唯一出处在 uiStore —— 开机读 persisted 偏好时就要按
 * 当前窗口宽度裁一次（窄屏不让右栏盖住画布），放这边会和 store 成环。
 */
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
