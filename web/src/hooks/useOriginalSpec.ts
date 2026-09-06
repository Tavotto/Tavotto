/**
 * 原图规格（ADR 0028）的 React 绑定：**订阅它依赖的每一份状态**。
 *
 * `getOriginalOutputSpec()` 读四个 store 的当前快照——文档（第 ② 档
 * `nativeW/nativeH`）、渲染态（第 ① 档 manifest `size_mm`）、素材清单与
 * runtime 清单（第 ③ 档）。组件里 `useMemo(() => …, [figureId])` 只挂 id 的话，
 * 渲染回来（第 ① 档到位）与图幅同步进文档（`useEngineSync` 改第 ② 档）都不会
 * 让它重算：导出对话框于是停在它打开那一刻的旧规格，而快速编辑条上是新的
 * ——审计 T33 里一个说 75.3 × 58.7、一个说 80 × 57.6，差的就是这一次重算。
 *
 * 这里把「什么时候重算」收成一处：两个界面都从同一个 hook 拿，就不可能再各挂
 * 各的依赖。四个 store 只当**信号**用（入参仍由 `getOriginalOutputSpec` 自己读），
 * 所以 linter 看不见的那层依赖要显式列出来。
 */
import { useMemo } from 'react'
import { originalAvailability, type OriginalAvailability } from '@/lib/exportRequest'
import { getOriginalOutputSpec, type OriginalOutputSpec } from '@/lib/originalSpec'
import { useAssetStore } from '@/store/assetStore'
import { useDocumentStore } from '@/store/documentStore'
import { useRenderStore } from '@/store/renderStore'
import { useRuntimeAssetStore } from '@/store/runtimeAssetStore'

/** 规格会变的每一个信号。返回值只用于 memo 依赖，不当入参。 */
function useSpecSignals() {
  const objects = useDocumentStore((s) => s.doc.objects)
  const canvases = useDocumentStore((s) => s.canvases)
  const byKey = useRenderStore((s) => s.byKey)
  const assets = useAssetStore((s) => s.byId)
  const runtime = useRuntimeAssetStore((s) => s.assets)
  return [objects, canvases, byKey, assets, runtime] as const
}

/** 这张图的原图规格；文档与清单都不认识它时为 `null` */
export function useOriginalSpec(figureId: string | null): OriginalOutputSpec | null {
  const signals = useSpecSignals()
  return useMemo(
    () => (figureId ? getOriginalOutputSpec(figureId) : null),
    // signals 是**触发重算的信号**，不是入参：`getOriginalOutputSpec()` 读的是
    // store 的当前快照，linter 看不见那一层
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [figureId, ...signals],
  )
}

/** 「按原图导出」现在能不能用 + 它的规格（`lib/exportRequest.originalAvailability`） */
export function useOriginalAvailability(figureId: string | null): OriginalAvailability {
  const signals = useSpecSignals()
  return useMemo(
    () => originalAvailability(figureId),
    // 同上：信号，不是入参
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [figureId, ...signals],
  )
}
