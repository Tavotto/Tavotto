import { create } from 'zustand'
import type { Manifest } from '@/lib/api'
import { exactPanelRender, useExactPanelRender, useRenderStore, type PanelRender } from '@/store/renderStore'
import type { PanelObject } from '@/types/document'

/**
 * 每个面板**此刻真正挂在 DOM 里**的那份内联 SVG（编辑态、矢量表示法时才有）。
 *
 * 为什么要单独记（#575 Codex 评审）：换一版 SVG 要先把它嵌的位图解码完
 * （`lib/useDecodedSvg`，最多 250ms），这段时间 store 里的权威渲染已经是新的，画面上
 * 挂的还是旧的。命中层、选中框与手柄若照新 manifest 走，用户点的是看得见的旧图、
 * 改的却是还没显示出来的几何。所以几何交互只在「权威那一版的 SVG 就是挂着的那份」
 * 时才生效（`useDisplayedExactManifest`），换图那几帧像权威缺席一样停摆。
 *
 * 记的是字符串、不是渲染键：不同变体可能渲出同一份 SVG——按键记的话字符串没变、
 * 挂载 effect 不重跑，门会一直关着。
 */
interface MountedSvgState {
  byPanel: Record<string, string>
  set: (panelId: string, svg: string | null) => void
}

export const useMountedSvgStore = create<MountedSvgState>((set) => ({
  byPanel: {},
  set: (panelId, svg) =>
    set((s) => {
      if (svg == null) {
        if (!(panelId in s.byPanel)) return s
        const byPanel = { ...s.byPanel }
        delete byPanel[panelId]
        return { byPanel }
      }
      if (s.byPanel[panelId] === svg) return s
      return { byPanel: { ...s.byPanel, [panelId]: svg } }
    }),
}))

/**
 * 几何写操作与命中用的 manifest：权威（`useExactPanelManifest` 同一判据），**且**这一版
 * 的 SVG 已经挂上画面。没记挂载（位图表示法、非编辑态、测试里不挂 PanelView）时不加门。
 */
export function useDisplayedExactManifest(panel: PanelObject | null | undefined): Manifest | null {
  const render = useExactPanelRender(panel)
  const mounted = useMountedSvgStore((s) => (panel ? s.byPanel[panel.id] : undefined))
  return displayedOf(render, mounted)
}

/** 同一判据的非 hook 版：键盘动作（方向键微调）在事件里现取 */
export function displayedExactManifest(panel: PanelObject): Manifest | null {
  return displayedOf(
    exactPanelRender(useRenderStore.getState(), panel),
    useMountedSvgStore.getState().byPanel[panel.id],
  )
}

function displayedOf(render: PanelRender | null, mounted: string | undefined): Manifest | null {
  if (!render?.manifest) return null
  if (mounted !== undefined && render.svg != null && render.svg !== mounted) return null
  return render.manifest
}
