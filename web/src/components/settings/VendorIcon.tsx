import { Link2 } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { IconFrame } from './IconFrame'
import { VENDOR_GLYPHS } from './vendorIcons'

/** 通用图标（取别名：尺寸来自调用方的 glyphSize，而不是在这里写死的档位） */
const Generic = Link2

/**
 * 第三方模型服务（厂商）的图标。
 *
 * 与 `AgentIcon` 同一套外框与尺寸参数，规矩也同：**只用本地内联矢量，不加载任何远程图片**。
 * 图形数据在 `vendorIcons.ts`（lobe-icons，MIT；出处见 `THIRD_PARTY_NOTICES.md`），全部单色、
 * 跟随 currentColor——不保留品牌色，是因为暗色模式下深色标志（Vercel、OpenAI…）会直接消失。
 *
 * `iconKey` 来自后端厂商目录（`engine/ai_catalog.json` 的 `icon_key`，已保存的接口也带它）；
 * 表外的 key（Synthetic 等没有可靠图形的厂商、自定义接口）落到通用图标，**不是空白**。
 * 名字永远在旁边，图标只是辅助指认，所以这里不带可访问文本。
 */
export function VendorIcon({
  iconKey,
  size = 36,
  glyphSize = ICON_SIZE.lg,
  className,
}: {
  iconKey?: string | null
  /** 外框边长 */
  size?: number
  /** 图形本身的边长 */
  glyphSize?: number
  className?: string
}) {
  const paths = iconKey ? VENDOR_GLYPHS[iconKey] : undefined
  return (
    <IconFrame size={size} className={className}>
      {paths ? (
        <svg
          width={glyphSize}
          height={glyphSize}
          viewBox="0 0 24 24"
          fill="currentColor"
          fillRule="evenodd"
          data-vendor-icon={iconKey}
          aria-hidden
        >
          {paths.map((p, i) => (
            <path key={i} d={p.d} fillOpacity={p.o} />
          ))}
        </svg>
      ) : (
        <Generic size={glyphSize} data-vendor-icon="generic" aria-hidden />
      )}
    </IconFrame>
  )
}
