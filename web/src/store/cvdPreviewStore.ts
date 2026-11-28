import { create } from 'zustand'
import type { Vision } from '@/lib/palette/cvd'

/**
 * 画布的色觉预览（ADR 0210 §一：只改显示，不写文档、不进历史、不进导出）。
 *
 * 不持久化：换项目、刷新页面都回到正常色觉——它是「看一眼」，不是一个设置。
 * 显示由 `canvas/CvdPreviewFilter` 在世界层上挂一个 SVG 滤镜完成。
 */
interface CvdPreviewState {
  vision: Vision
  setVision: (v: Vision) => void
}

export const useCvdPreviewStore = create<CvdPreviewState>((set) => ({
  vision: 'normal',
  setVision: (vision) => set({ vision }),
}))

/** 色觉预览滤镜的元素 id（`CvdPreviewFilter` 定义、世界层引用，两边用同一个） */
export const cvdFilterId = (vision: Vision): string => `cvd-preview-${vision}`

/** 世界层 `style.filter` 用的引用；正常色觉 = 不挂滤镜 */
export const cvdFilterRef = (vision: Vision): string | undefined =>
  vision === 'normal' ? undefined : `url(#${cvdFilterId(vision)})`
