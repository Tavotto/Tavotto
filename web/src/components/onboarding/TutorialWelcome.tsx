import { Fragment, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
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
import { CALLOUT_LEAD, estimateTextBox, placeCallouts } from './tutorialGeometry'
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
        className="mx-auto flex w-full max-w-[1060px] flex-1 flex-col items-center justify-center px-6 pb-9 text-center"
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
          className="my-8 grid w-full max-w-[1012px] grid-cols-[minmax(0,0.7fr)_44px_minmax(0,1fr)] items-center gap-5 pl-3 [@media(max-height:720px)]:my-4"
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

/** 气泡槽的宽度（px，卡片里图框右边留给「引线 + 气泡」的地方）：够英文「Adjust the legend」放下 */
const BUBBLE_SLOT = 184
/** 气泡宽度上限（给碰撞计算用；真实宽度由文字撑开，不超过它） */
const BUBBLE_MAX_W = BUBBLE_SLOT - CALLOUT_LEAD

/**
 * 右边的图 + 三枚注释：「修改文字」指标题、「调整图例」指图例、「拖动位置」指横轴标签。
 * 气泡排在图右侧的专用槽里（卡片内），引线从被指元素右边缘外侧水平拉过去；落点由
 * `placeCallouts` 按元素 bbox × 舞台缩放现算并做避让，窗口变了跟着走，不写死像素。
 */
function FigureCard() {
  const { t } = useTranslation('dialogs')
  return (
    <Card
      padding="none"
      className="relative flex h-[clamp(210px,33vh,310px)] min-h-0 items-center rounded-panel p-4 [@media(max-height:720px)]:h-[196px] [@media(max-height:720px)]:p-2.5"
    >
      <FigureStage
        className="h-full min-w-0 flex-1"
        overlay={(size) => <Callouts size={size} />}
      >
        <DemoFigure demo={DEMO_EDITS_EMPTY} label={t('onboarding.welcome.figureLabel')} className="block h-full w-full" />
      </FigureStage>
      {/* 气泡槽：图框右边这一块只放「引线 + 气泡」，图自己不会被挤进去 */}
      <div aria-hidden="true" className="h-full shrink-0" style={{ width: BUBBLE_SLOT }} />
    </Card>
  )
}

function Callouts({ size }: { size: { w: number; h: number } }) {
  const { t } = useTranslation('dialogs')
  const F = DEMO_FIGURE
  const root = useRef<HTMLDivElement>(null)
  // 标题文字此刻的外框（pt）：量得到按真实字形，量不到（jsdom）按字数估
  const [titleBox, setTitleBox] = useState(() => estimateTextBox(F.title.text, F.title.sizePt, F.title.anchor))
  useLayoutEffect(() => {
    try {
      const b = root.current?.parentElement?.querySelector<SVGTextElement>('[data-demo-title] text')?.getBBox()
      if (b && b.width > 0) setTitleBox([b.x, b.y, b.width, b.height])
    } catch {
      /* 量不到就用估值 */
    }
  }, [size.w])
  const placed = placeCallouts({
    figure: F.figure,
    stage: size,
    bubbleW: BUBBLE_MAX_W,
    targets: [
      { key: 'title', box: titleBox },
      { key: 'legend', box: F.legend.box as [number, number, number, number] },
      { key: 'position', box: F.xlabel.box as [number, number, number, number] },
    ],
  })
  return (
    <div ref={root} aria-hidden="true" data-welcome-callouts="" className="pointer-events-none absolute inset-0">
      {placed.map((p) => {
        // 引线：从锚点到气泡左沿中点的一根细线（div 旋转，不手写内联 svg——图标只有一套）
        const dx = p.bubble.x - p.anchor.x
        const dy = p.bubble.y + p.bubble.h / 2 - p.anchor.y
        return (
          <Fragment key={p.key}>
            <span
              className="absolute h-px origin-left bg-accent/60"
              style={{
                left: p.anchor.x,
                top: p.anchor.y,
                width: Math.hypot(dx, dy),
                transform: `rotate(${Math.atan2(dy, dx)}rad)`,
              }}
            />
            <span
              className="absolute h-1.5 w-1.5 rounded-full bg-accent"
              style={{ left: p.anchor.x - 3, top: p.anchor.y - 3 }}
            />
          </Fragment>
        )
      })}
      {placed.map((p) => (
        <span
          key={p.key}
          data-welcome-callout={p.key}
          className="absolute flex items-center rounded-md border border-info-border bg-accent-subtle px-3 text-base font-medium whitespace-nowrap text-info-content shadow-pop"
          style={{ left: p.bubble.x, top: p.bubble.y, height: p.bubble.h, maxWidth: p.bubble.w }}
        >
          {t(`onboarding.welcome.callout.${p.key}`)}
        </span>
      ))}
    </div>
  )
}
