import { useEffect } from 'react'
import { useDataFigureStore } from '@/store/dataFigureStore'
import { useTafFlagStore } from '@/store/tafFlagStore'

/**
 * 实验开关开着、且这个项目里有数据文件：给「数据」轨道项和空画布里「用数据画一张」共用的判据。
 * 开关关着时什么都不读、不请求（清单由开关开着的第一个使用者读一次）。
 */
export function useHasDataFiles(): boolean {
  const taf = useTafFlagStore((s) => s.enabled)
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
