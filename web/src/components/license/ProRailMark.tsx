import { ICON_DEFS, type IconPathSpec } from '@/components/ui/icons/defs'
import { FacetStar } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'

const d = (p: IconPathSpec) => (typeof p === 'string' ? p : p.d)
/** 切面星的外形与对角两片暗切面直接取图标集那一份（`defs.ts` 的 `FacetStar`），不另画一遍 */
const OUTLINE = d(ICON_DEFS.FacetStar.s![0])
const DARK = (ICON_DEFS.FacetStar.f ?? []).map(d)
/** 另外两片亮切面（与暗切面成风车；坐标与 `defs.ts` 同一套 24 网格） */
const LIGHT = ['M12 2.25L14.25 9.75L12 12z', 'M12 21.75L9.75 14.25L12 12z']

/**
 * 左轨 Pro 入口的徽记：没激活 = 图标集里的单色切面星；这台电脑激活了 Pro = 同一颗星换成钛色的明暗两阶切面
 * （与创始铭牌同一母题；维护者 2026-11-26 定。颜色是 `--color-pro-ti-*` 三个令牌，浅色主题暖调深钛灰、深色主题镜面银）。
 * 只有明暗两阶、不用渐变：16 px 上渐变边缘发虚（agy 审核意见）。
 */
/** 只在左轨用，尺寸固定是左轨那一档（`ICON_SIZE.md`） */
export function ProRailMark({ active }: { active: boolean }) {
  const size = ICON_SIZE.md
  if (!active) return <FacetStar size={ICON_SIZE.md} />
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" data-pro-mark="titanium">
      {LIGHT.map((p) => (
        <path key={p} d={p} fill="var(--color-pro-ti-light)" stroke="var(--color-pro-ti-light)" strokeWidth={0.5} strokeLinejoin="round" />
      ))}
      {DARK.map((p) => (
        <path key={p} d={p} fill="var(--color-pro-ti-dark)" stroke="var(--color-pro-ti-dark)" strokeWidth={0.5} strokeLinejoin="round" />
      ))}
      <path d={OUTLINE} fill="none" stroke="var(--color-pro-ti-edge)" strokeWidth={1} strokeLinejoin="round" />
    </svg>
  )
}
