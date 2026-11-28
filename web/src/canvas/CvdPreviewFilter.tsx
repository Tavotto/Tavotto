import { cvdFilterValues, type Vision } from '@/lib/palette/cvd'
import { cvdFilterId } from '@/store/cvdPreviewStore'

/**
 * 色觉预览的 SVG 滤镜定义（ADR 0210）：只在预览开着时挂载，0×0、不占位、不可见。
 * 世界层（页面与图）整体过这个滤镜；选框、标尺这些界面层不在世界层里，不受影响。
 */
export function CvdPreviewFilter({ vision }: { vision: Exclude<Vision, 'normal'> }) {
  return (
    <svg aria-hidden data-cvd-preview={vision} width={0} height={0} className="pointer-events-none absolute">
      <filter id={cvdFilterId(vision)} colorInterpolationFilters="linearRGB">
        <feColorMatrix type="matrix" values={cvdFilterValues(vision)} />
      </filter>
    </svg>
  )
}
