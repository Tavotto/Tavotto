import { t as translate } from '@/i18n'

const st = (key: string, values?: Record<string, unknown>) =>
  translate(`settings.${key}`, { ns: 'dialogs', ...(values ?? {}) })

/**
 * 「拖动时一同移动关联对象」的前后示意（审计 T39）。
 *
 * 这个开关讲的是**空间关系**：挪走子图之后，手动摆过位置的标题 / 图例是跟着
 * 走还是留在原地。改动前它靠一段两行的文字解释（还带着 figure、twinx 这些
 * 实现词），而空间关系正是文字最讲不清、图一眼就够的那类事。
 *
 * **静态 SVG，没有动画**：`prefers-reduced-motion` 下不需要另给一个版本，
 * 而且它随开关状态重画——用户看到的是「我这一档会怎么样」，不是一段说明。
 *
 * 尺寸固定 152×40，`shrink-0`：它跟在开关后面，不参与那一行的伸缩。
 */
export function CompanionDiagram({ on }: { on: boolean }) {
  return (
    <svg
      width="152"
      height="40"
      viewBox="0 0 152 40"
      role="img"
      aria-label={st(on ? 'canvas.diagramOn' : 'canvas.diagramOff')}
      className="shrink-0 text-ink-3"
    >
      {/* 拖动前：子图 + 上面的标题 + 右边的图例 */}
      <Frame />
      {/* 箭头 */}
      <g stroke="currentColor" strokeWidth="1" fill="none" opacity=".7">
        <path d="M52 24 h11" />
        <path d="M60 21 l3.5 3 -3.5 3" />
      </g>
      {/* 拖动后：子图挪了 (+10, +4)。开着 → 标题与图例跟着挪（accent 高亮）；
          关着 → 它们留在原地（淡出，表示被落下了） */}
      <g transform="translate(70 0)">
        <Frame moved companions={on ? 'moved' : 'left-behind'} />
      </g>
    </svg>
  )
}

/**
 * 一帧：子图方框 + 标题横条 + 图例小框。
 * `moved` 把子图挪一格；`companions` 决定标题与图例挪不挪、画成什么样。
 */
function Frame({
  moved = false,
  companions = 'moved',
}: {
  moved?: boolean
  companions?: 'moved' | 'left-behind'
}) {
  const dx = moved ? 10 : 0
  const dy = moved ? 4 : 0
  const cdx = companions === 'moved' ? dx : 0
  const cdy = companions === 'moved' ? dy : 0
  const highlight = moved && companions === 'moved'
  return (
    <>
      {/* 子图 */}
      <rect
        x={4 + dx}
        y={14 + dy}
        width="26"
        height="19"
        rx="2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1"
      />
      <g
        className={highlight ? 'text-accent' : undefined}
        opacity={moved && companions === 'left-behind' ? 0.4 : 1}
      >
        {/* 标题 */}
        <rect x={8 + cdx} y={8 + cdy} width="15" height="2.5" rx="1.25" fill="currentColor" />
        {/* 图例 */}
        <rect
          x={34 + cdx}
          y={17 + cdy}
          width="11"
          height="8"
          rx="1.5"
          fill="none"
          stroke="currentColor"
          strokeWidth="1"
        />
      </g>
    </>
  )
}
