import { Fragment, useEffect, useRef, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ArrowRight, FileCodeCorner } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { PRODUCT_NAME } from '@/lib/brand'
import { dragRegionProps } from '@/lib/desktop'
import { DEMO_FIGURE } from '@/lib/onboarding/demoFigure'
import { leaveFromWelcome, runTutorialEntry } from '@/lib/onboarding/tutorial'
import { cn } from '@/lib/utils'
import { DEMO_EDITS_EMPTY } from '@/store/onboardingStore'
import { BrandMark } from '../ui/BrandMark'
import { Button } from '../ui/Button'
import { Card } from '../ui/Card'
import { DemoFigure } from './DemoFigure'
import { FigureStage } from './tutorialLayout'

/**
 * 欢迎页（不是步骤）：一句「直接在图上改 :)」+ 「脚本 → 图」的对照 + 两个出口。
 *
 * 左边的代码是内置示例 `fig1_kinetics.py` 里**逐字存在**的几行（`demoFigure.json` 的
 * `snippet`，`tests/test_tutorial_demo_figure.py` 逐行核对），组与组之间是省略线，不冒充连续代码；
 * 右边的图就是这份脚本画出来的那一张（同一份几何）。
 */
export function TutorialWelcome() {
  const { t } = useTranslation('dialogs')
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => heading.current?.focus({ preventScroll: true }), [])

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto" data-tutorial-view="welcome">
      {/* 窗框：macOS 的红绿灯叠在这一条上，空白处可拖动窗口 */}
      <div className="h-9 shrink-0" {...dragRegionProps('self')} />
      <main
        aria-labelledby="tutorial-welcome-title"
        className="mx-auto flex w-full max-w-[1060px] flex-1 flex-col items-center justify-center px-6 pb-8 text-center"
      >
        <div className="flex items-center gap-2.5">
          <BrandMark size={48} />
          <span className="type-display">
            {PRODUCT_NAME}
          </span>
        </div>
        <h1 ref={heading} id="tutorial-welcome-title" tabIndex={-1} className="type-hero mt-5 mb-2.5 outline-none">
          {t('onboarding.welcome.title')} <span className="whitespace-nowrap font-medium text-warn">{t('onboarding.welcome.smile')}</span>
        </h1>
        <p className="type-heading font-normal text-ink-3">{t('onboarding.welcome.subtitle')}</p>
        <ul
          aria-label={t('onboarding.welcome.featuresLabel')}
          className="mt-4 flex flex-wrap items-center justify-center text-lg text-ink-3"
        >
          {FEATURES.map((f, i) => (
            <li key={f} className={cn('px-4 whitespace-nowrap', i > 0 && 'border-l border-border-strong')}>
              {t(`onboarding.welcome.feature.${f}`)}
            </li>
          ))}
        </ul>

        <div
          role="group"
          aria-label={t('onboarding.welcome.demoLabel')}
          className="my-8 grid w-full max-w-[920px] grid-cols-[minmax(0,0.82fr)_44px_minmax(0,1fr)] items-center gap-5 pr-8 pl-3 [@media(max-height:720px)]:my-4"
        >
          <ScriptCard />
          <ArrowRight size={ICON_SIZE.lg} className="justify-self-center text-ink-faint" aria-hidden="true" />
          <FigureCard />
        </div>

        <div className="flex flex-col items-center gap-2">
          <Button
            variant="primary"
            size="lg"
            data-tutorial-begin=""
            className="h-12 min-w-[340px] gap-4 text-lg [@media(max-height:720px)]:h-11 [@media(max-height:720px)]:min-w-[300px]"
            onClick={() => void runTutorialEntry('picker')}
          >
            {t('onboarding.welcome.begin')}
            <ArrowRight size={ICON_SIZE.md} />
          </Button>
          <Button
            variant="ghost"
            size="lg"
            data-tutorial-open-workspace=""
            className="gap-3 text-ink-3"
            onClick={() => leaveFromWelcome()}
          >
            {t('onboarding.welcome.openWorkspace')}
            <ArrowRight size={ICON_SIZE.sm} />
          </Button>
        </div>
      </main>
    </div>
  )
}

/** 能在图上直接改的东西（原型的那一行；最后一项是「还有更多」） */
const FEATURES = ['legend', 'axes', 'colorbar', 'text', 'align', 'layout', 'more'] as const

/* -------------------------------- 脚本卡 -------------------------------- */

/** 极小的 Python 着色：关键字 / 字符串 / 数字 / 调用名。只为这几行示例，不是通用高亮器 */
function highlight(line: string): ReactNode[] {
  const out: ReactNode[] = []
  const re = /("[^"]*")|\b(import|as|from|def|return)\b|\b(\d+(?:\.\d+)?)\b|([A-Za-z_][\w.]*)(?=\()/g
  let last = 0
  for (const m of line.matchAll(re)) {
    if (m.index > last) out.push(line.slice(last, m.index))
    const cls = m[1]
      ? 'text-syntax-string'
      : m[2]
        ? 'text-syntax-keyword'
        : m[3]
          ? 'text-syntax-number'
          : 'text-syntax-function'
    out.push(
      <span key={m.index} className={cls}>
        {m[0]}
      </span>,
    )
    last = m.index + m[0].length
  }
  if (last < line.length) out.push(line.slice(last))
  return out
}

function ScriptCard() {
  const { t } = useTranslation('dialogs')
  return (
    <Card
      padding="none"
      className="-rotate-[4deg] rounded-panel p-4 text-left [@media(max-height:720px)]:p-3"
      data-tutorial-script-card=""
    >
      <div className="mb-3 flex items-center gap-3 [@media(max-height:720px)]:mb-1.5">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-md bg-surface-2 text-ink-2">
          <FileCodeCorner size={ICON_SIZE.lg} />
        </span>
        <span className="font-mono text-lg font-medium text-ink">{DEMO_FIGURE.script}</span>
      </div>
      <pre
        aria-label={t('onboarding.welcome.codeLabel', { name: DEMO_FIGURE.script })}
        className="overflow-hidden rounded-md bg-surface-2 px-2.5 py-2 font-mono text-sm leading-[1.65] text-ink-3 [@media(max-height:720px)]:text-xs"
      >
        <code>
          {DEMO_FIGURE.snippet.map((group, gi) => (
            <Fragment key={gi}>
              {gi > 0 && <span aria-hidden="true" className="my-1 block border-t border-dashed border-border-strong" />}
              {group.map((line) => (
                <span key={line} className="block whitespace-pre">
                  {highlight(line)}
                </span>
              ))}
            </Fragment>
          ))}
        </code>
      </pre>
    </Card>
  )
}

/* -------------------------------- 图卡 --------------------------------- */

/**
 * 右边的图 + 三枚注释：「修改文字」指标题、「调整图例」指图例、「拖动位置」指横轴标签。
 * 落点按图的几何（pt）× 舞台此刻的缩放算，窗口变了跟着走，不写死像素。
 */
function FigureCard() {
  const { t } = useTranslation('dialogs')
  const F = DEMO_FIGURE
  const [lx, ly, lw, lh] = F.legend.box
  const [xx, xy, xw, xh] = F.xlabel.box
  const anchors = [
    { key: 'title', at: [F.title.anchor[0] + 40, F.title.anchor[1] - 6], side: 'above' as const },
    { key: 'legend', at: [lx + lw, ly + lh / 2], side: 'right' as const },
    { key: 'position', at: [xx + xw, xy + xh / 2], side: 'below' as const },
  ]
  return (
    <Card
      padding="none"
      className="relative h-[clamp(210px,33vh,310px)] min-h-0 rounded-panel p-4 [@media(max-height:720px)]:h-[196px] [@media(max-height:720px)]:p-2.5"
    >
      <FigureStage
        className="h-full w-full"
        overlay={
          <div aria-hidden="true" className="pointer-events-none absolute inset-0">
            {anchors.map((a) => (
              <Callout key={a.key} at={a.at as [number, number]} side={a.side}>
                {t(`onboarding.welcome.callout.${a.key}`)}
              </Callout>
            ))}
          </div>
        }
      >
        <DemoFigure demo={DEMO_EDITS_EMPTY} label={t('onboarding.welcome.figureLabel')} className="block h-full w-full" />
      </FigureStage>
    </Card>
  )
}

/** 一枚注释：圆点落在锚点上，一条细线连到注释签。坐标是图的 pt，用舞台宽度换算 */
function Callout({ at, side, children }: { at: [number, number]; side: 'above' | 'right' | 'below'; children: ReactNode }) {
  const F = DEMO_FIGURE
  // 百分比定位：锚点 = 图幅里的比例位置，舞台怎么缩放都对得上
  const left = `${(at[0] / F.figure.width) * 100}%`
  const top = `${(at[1] / F.figure.height) * 100}%`
  const line = { above: 'h-6 w-px -translate-y-full', right: 'h-px w-7', below: 'h-5 w-px' }[side]
  const chip = {
    above: 'bottom-6 left-0 -translate-x-1/3 -translate-y-px',
    right: 'top-0 left-7 -translate-y-1/2',
    below: 'top-5 left-0 -translate-x-1/2',
  }[side]
  return (
    <div className="absolute" style={{ left, top }}>
      <span className="absolute -top-[3px] -left-[3px] h-1.5 w-1.5 rounded-full bg-accent" />
      <span className={cn('absolute left-0 top-0 bg-accent/60', line, side === 'right' && '-translate-y-px')} />
      <span
        className={cn(
          'absolute rounded-md border border-info-border bg-accent-subtle px-3 py-1.5 text-base font-medium whitespace-nowrap text-info-content shadow-pop',
          chip,
        )}
      >
        {children}
      </span>
    </div>
  )
}
