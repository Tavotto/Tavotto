/**
 * 主页新手版三步卡片里的示意图：脚本文件 → 认出来的几张图 → 排版画布。
 *
 * 画的是**产品流程**，不是图标（图标集里没有「一叠图表」「一扇排版窗口」这种东西，
 * 换成图标会丢掉「几张图被摆进同一张画布」这层意思），所以按个数进
 * `iconography.test.tsx` 的内联 svg 豁免表。克制、灰阶：全部颜色取 token，
 * 不嵌位图、不画第三方标志（Python 文件用「.py」字样表示）。纯装饰，`aria-hidden`——
 * 卡片自己的标题与说明已经把这一步说清了。
 */

const C = {
  paper: 'var(--color-surface)',
  well: 'var(--color-surface-2)',
  edge: 'var(--color-border-strong)',
  hair: 'var(--color-border)',
  ink: 'var(--color-ink)',
  ink2: 'var(--color-ink-2)',
  faint: 'var(--color-ink-faint)',
  sel: 'var(--color-selected)',
}

const VIEW = '0 0 240 120'

/** 一张脚本文件 + 光标 */
export function ScriptFileArt() {
  return (
    <svg viewBox={VIEW} className="h-full w-full" aria-hidden>
      <path d="M96 18h36l14 14v72a3 3 0 0 1-3 3H96a3 3 0 0 1-3-3V21a3 3 0 0 1 3-3z" fill={C.paper} stroke={C.edge} />
      <path d="M132 18v11a3 3 0 0 0 3 3h11" fill="none" stroke={C.edge} />
      <text x="119.5" y="56" textAnchor="middle" fontSize="15" fontWeight="500" fill={C.ink} fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace">
        .py
      </text>
      <rect x="102" y="68" width="34" height="3" rx="1.5" fill={C.hair} />
      <rect x="102" y="76" width="26" height="3" rx="1.5" fill={C.hair} />
      <rect x="102" y="84" width="30" height="3" rx="1.5" fill={C.hair} />
      <path
        d="M140 84l0 22 5.5-5.2 4 8.6 3.6-1.7-4-8.4 7.6-.2z"
        fill={C.ink}
        stroke={C.paper}
        strokeWidth="1.2"
        strokeLinejoin="round"
      />
    </svg>
  )
}

/** 从脚本里认出来的几张图：叠在一起的三张 */
export function FoundFiguresArt() {
  return (
    <svg viewBox={VIEW} className="h-full w-full" aria-hidden>
      {/* 最后面：热图 */}
      <rect x="138" y="12" width="78" height="56" rx="3" fill={C.paper} stroke={C.edge} />
      {[0, 1, 2, 3].map((r) =>
        [0, 1, 2, 3, 4].map((c) => (
          <rect
            key={`${r}-${c}`}
            x={160 + c * 9}
            y={20 + r * 9}
            width="8"
            height="8"
            fill={C.ink2}
            opacity={0.12 + ((r * 5 + c * 3) % 7) * 0.07}
          />
        )),
      )}
      {/* 中间：散点 + 拟合线 */}
      <rect x="104" y="34" width="84" height="62" rx="3" fill={C.paper} stroke={C.edge} />
      <path d="M112 88L180 44" stroke={C.ink2} strokeWidth="1.2" strokeDasharray="3 2" />
      {[
        [118, 82], [124, 80], [128, 76], [133, 77], [137, 70], [142, 72], [146, 66],
        [150, 64], [155, 62], [158, 57], [163, 56], [167, 52], [172, 50], [176, 47],
      ].map(([x, y]) => (
        <circle key={`${x}-${y}`} cx={x} cy={y} r="1.8" fill={C.ink2} />
      ))}
      {/* 最前面：两条曲线 */}
      <rect x="28" y="46" width="96" height="64" rx="3" fill={C.paper} stroke={C.edge} />
      <path d="M38 52v50h80" fill="none" stroke={C.faint} strokeWidth="1" />
      <path d="M40 100C48 70 62 60 78 57S108 54 116 54" fill="none" stroke={C.ink} strokeWidth="1.4" />
      <path d="M40 101C54 86 72 76 90 71S110 66 116 65" fill="none" stroke={C.ink2} strokeWidth="1.2" strokeDasharray="3 2" />
    </svg>
  )
}

/** 排版画布：一扇窗，左栏 + 网格画布 + 右栏，两张图摆在画布上 */
export function LayoutCanvasArt() {
  return (
    <svg viewBox={VIEW} className="h-full w-full" aria-hidden>
      <rect x="14" y="8" width="212" height="106" rx="4" fill={C.paper} stroke={C.edge} />
      <path d="M14 20h212" stroke={C.hair} />
      <circle cx="22" cy="14" r="1.8" fill={C.faint} />
      <circle cx="28" cy="14" r="1.8" fill={C.faint} />
      <circle cx="34" cy="14" r="1.8" fill={C.faint} />
      <rect x="78" y="12" width="84" height="4" rx="2" fill={C.hair} />
      {/* 左栏 */}
      <path d="M52 20v94" stroke={C.hair} />
      {[30, 42, 54].map((y) => (
        <g key={y}>
          <rect x="20" y={y} width="6" height="6" rx="1" fill={C.sel} stroke={C.edge} strokeWidth="0.6" />
          <rect x="29" y={y + 1.5} width="17" height="3" rx="1.5" fill={C.hair} />
        </g>
      ))}
      {/* 右栏 */}
      <path d="M198 20v94" stroke={C.hair} />
      {[28, 38, 48].map((y) => (
        <rect key={y} x="204" y={y} width="16" height="4" rx="2" fill={C.hair} />
      ))}
      {/* 画布网格 */}
      <rect x="58" y="26" width="134" height="82" fill={C.well} />
      {[72, 86, 100, 114, 128, 142, 156, 170, 184].map((x) => (
        <path key={`v${x}`} d={`M${x} 26v82`} stroke={C.hair} strokeWidth="0.6" />
      ))}
      {[38, 52, 66, 80, 94].map((y) => (
        <path key={`h${y}`} d={`M58 ${y}h134`} stroke={C.hair} strokeWidth="0.6" />
      ))}
      {/* 摆上去的两张图 */}
      <rect x="66" y="42" width="54" height="42" rx="2" fill={C.paper} stroke={C.edge} />
      <path d="M71 80C76 58 86 52 96 50S112 49 116 49" fill="none" stroke={C.ink} strokeWidth="1.2" />
      <path d="M71 80C80 70 92 64 104 61S114 59 116 59" fill="none" stroke={C.ink2} strokeWidth="1" strokeDasharray="2.5 1.8" />
      <rect x="128" y="42" width="54" height="42" rx="2" fill={C.paper} stroke={C.edge} />
      <path d="M133 78L178 48" stroke={C.ink2} strokeWidth="1" strokeDasharray="2.5 1.8" />
      {[
        [137, 75], [142, 73], [146, 68], [151, 69], [155, 63], [160, 62], [164, 58], [169, 55], [173, 52],
      ].map(([x, y]) => (
        <circle key={`${x}-${y}`} cx={x} cy={y} r="1.4" fill={C.ink2} />
      ))}
    </svg>
  )
}
