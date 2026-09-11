import { t as translate } from '@/i18n'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 「拖动时一同移动关联对象」的关系示意（审计 T39）。
 *
 * 这个开关讲的是**空间关系**：手动摆过位置的标题 / 图例是不是跟子图绑在一起。
 * 图里只画一帧：子图方框、标题横条、图例小框都是中性色，唯独把它们连到子图上的
 * 两条「关联」短线用 Tavotto 蓝——对象保持中性，只有关系被强调。
 *
 * 关掉时不画「被落下」的流程图，而是让标题 / 图例降低存在感、关联线变虚淡出：
 * 用户看到的是「这一档关联还在不在」，不是一段说明。
 *
 * 只有透明度过渡，走 --duration-fast；`prefers-reduced-motion` 由全局规则接管。
 * Session 6 起它坐在 `SettingRow` 的 `illustration` 槽里（标题列、说明下方），
 * 是说明的图示，不再跟在开关旁边；无背景、无边框，72×32 的原尺寸。
 */
export function CompanionDiagram({ on }: { on: boolean }) {
  return (
    <span className="inline-flex shrink-0">
      <svg
        width="72"
        height="32"
        viewBox="0 0 72 32"
        fill="none"
        role="img"
        aria-label={st(on ? 'canvas.diagramOn' : 'canvas.diagramOff')}
        className="overflow-visible text-ink-3"
      >
        {/* 子图主体 */}
        <rect x="15" y="10" width="28" height="18" rx="2.5" stroke="currentColor" strokeWidth="1.25" />
        {/* 关联对象：标题横条 + 图例小框。关着 → 降低存在感 */}
        <g
          className="transition-opacity duration-(--duration-fast)"
          opacity={on ? 1 : 0.42}
          stroke="currentColor"
          strokeLinecap="round"
        >
          <path d="M20 6.5H31" strokeWidth="2" />
          <rect x="51" y="13" width="9" height="7" rx="1.5" strokeWidth="1.25" />
        </g>
        {/* 关联线：唯一用品牌蓝的地方。关着 → 变虚并淡出 */}
        <g
          className="text-ink transition-opacity duration-(--duration-fast)"
          opacity={on ? 1 : 0.14}
          stroke="currentColor"
          strokeWidth="1.25"
          strokeLinecap="round"
          strokeDasharray={on ? undefined : '2 2'}
        >
          <path d="M43 16.5H51" />
          <path d="M25.5 8.5V10" />
        </g>
      </svg>
    </span>
  )
}
