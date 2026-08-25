/**
 * 中央试验台：一张等着放入 Figure 的工作纸。
 *
 * 三种形态：
 *   idle       「把案例拖到这里 / 或点击案例上的『开始体验』」
 *   dragging   有卡片被拖起但还没进来：轻微点亮，示意这里能接
 *   active     指针悬在台面上：明确说出「松开，运行『某案例』」
 *
 * 它自己不做 drop 判定——判定在 ExampleCard 的指针手势里（capture 在卡片上，
 * 台面收不到指针事件），这里只按 Landing 转发的状态渲染。屏幕阅读器用户
 * **不需要**拖放：aria-label 直接说明点击案例即可，卡片的按钮就是同一动作。
 *
 * 视觉：纸面质感（白 surface + 细边框 + 内衬网格线），不是上传云、不是
 * 垃圾桶、不是大面积虚线框。reduced-motion 下不缩放，只有边框与文字变化。
 */
import { cn } from '@/lib/utils'
import type { PlaygroundExample } from '../examples'
import { pg } from '../pgText'

export function ExampleStage({
  stageRef,
  drag,
}: {
  stageRef: React.RefObject<HTMLDivElement | null>
  /** 当前被拖动的案例与是否悬停台上；null = 没在拖 */
  drag: { example: PlaygroundExample; overStage: boolean } | null
}) {
  const active = drag?.overStage ?? false
  return (
    <div
      ref={stageRef}
      role="region"
      aria-label={pg('stageAria')}
      data-stage-state={active ? 'active' : drag ? 'ready' : 'idle'}
      className={cn(
        'relative flex min-h-[260px] flex-1 flex-col items-center justify-center gap-2 rounded-[10px] border bg-surface p-8',
        'transition-colors duration-150',
        active ? 'border-sel bg-sel/5' : drag ? 'border-ink-faint' : 'border-border',
      )}
    >
      {/* 工作纸的衬线：极淡的方格，暗示这是画布语言的一部分（纯装饰） */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-3 rounded-[6px] opacity-[0.5]"
        style={{
          backgroundImage:
            'linear-gradient(var(--color-border) 1px, transparent 1px), linear-gradient(90deg, var(--color-border) 1px, transparent 1px)',
          backgroundSize: '24px 24px',
          maskImage: 'radial-gradient(ellipse at center, black 30%, transparent 85%)',
        }}
      />
      {active ? (
        <p className="relative text-[14px] font-medium text-sel" aria-live="polite">
          {pg('stageActive', { name: pg(drag!.example.titleKey) })}
        </p>
      ) : (
        <>
          <p className="relative text-[14px] font-medium text-ink-2">{pg('stageIdle')}</p>
          <p className="relative text-xs text-ink-3">{pg('stageIdleHint')}</p>
        </>
      )}
    </div>
  )
}
