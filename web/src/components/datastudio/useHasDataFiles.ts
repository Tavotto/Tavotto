import { useEffect } from 'react'
import { useDataFigureStore } from '@/store/dataFigureStore'
import { useTafFlagStore } from '@/store/tafFlagStore'

/**
 * 实验开关开着、且这个项目里有数据文件：给「数据」轨道项和空画布里「用数据画一张」共用的判据。
 * 开关关着时什么都不读、不请求（清单由开关开着的第一个使用者读一次）。
 */
export function useHasDataFiles(): boolean {
  const taf = useTafFlagStore((s) => s.enabled)
  // 开关的权威值只有后端知道：后端已有打开的项目时直接进工作区、不挂首页，首页那一处刷新就走不到——
  // 工作区这边的使用者（左轨、空画布）挂上时也问一次（`refresh` 幂等：问过 / 在问就不再问；读不到当没变）
  useEffect(() => {
    void useTafFlagStore.getState().refresh()
  }, [])
  const status = useDataFigureStore((s) => s.files.status)
  const has = useDataFigureStore((s) => {
    const f = s.files
    return (f.status === 'ready' || f.status === 'error' || f.status === 'loading') && (f.entries?.length ?? 0) > 0
  })
  useEffect(() => {
    if (taf && status === 'idle') void useDataFigureStore.getState().loadFiles()
  }, [taf, status])
  return taf && has
}
