/**
 * idle 首屏：案例体验是主角，上传是次级入口。
 *
 * 信息架构（PLAYGROUND_V2.md §三）：
 *   标题与说明
 *   案例库 ←→ 中央试验台（≥1280 左右分栏；中屏卡片两列、台面在下；
 *              <640 单列卡片，台面退化成「选择一个案例开始」提示）
 *   「已有一个独立脚本？」（compact 上传 + 支持范围 disclosure）
 *   隐私与运行时说明 + 桌面版出口
 *
 * 拖拽状态由这里协调：卡片手势 → onDragChange → 台面点亮。任何一条启动
 * 路径最终都走同一个 onLaunch——启动逻辑（真 Pyodide 会话）在 PlaygroundApp。
 */
import { useRef, useState } from 'react'
import { Download } from 'lucide-react'
import { RELEASES_LATEST_URL } from '@/lib/brand'
import type { PlaygroundExample } from '../examples'
import { pg } from '../pgText'
import { PYODIDE_VERSION, RUNTIME_PACKAGES } from '../runtime'
import type { CardDragEvent } from './ExampleCard'
import { ExampleCodeSheet } from './ExampleCodeSheet'
import { ExampleGallery } from './ExampleGallery'
import { ExampleStage } from './ExampleStage'
import { IndependentScriptUpload } from './IndependentScriptUpload'

export function PlaygroundLanding({
  onLaunch,
  onFile,
}: {
  onLaunch: (example: PlaygroundExample) => void
  onFile: (f: File) => void
}) {
  const stageRef = useRef<HTMLDivElement>(null)
  const [drag, setDrag] = useState<CardDragEvent | null>(null)
  const [codeExample, setCodeExample] = useState<PlaygroundExample | null>(null)

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto flex w-full max-w-[1120px] flex-col gap-7 px-4 py-8 sm:px-6">
        <header className="flex flex-col gap-1.5">
          <h1 className="text-[19px] font-semibold tracking-tight text-ink">
            {pg('landingTitle')}
          </h1>
          <p className="max-w-[52ch] text-[13px] leading-relaxed text-ink-2">
            {pg('landingSubtitle')}
          </p>
        </header>

        <div className="flex flex-col gap-5 xl:grid xl:grid-cols-[340px_minmax(0,1fr)] xl:items-start xl:gap-6">
          <ExampleGallery
            stageRef={stageRef}
            onLaunch={onLaunch}
            onViewCode={setCodeExample}
            onDragChange={setDrag}
            className="grid gap-4 sm:grid-cols-2 xl:grid-cols-1"
          />
          {/* 台面在窄屏（没有指针拖拽的世界）退化成一句提示——点击卡片
              就是那条完整路径，不要求任何人执行拖放 */}
          <div className="hidden sm:flex xl:sticky xl:top-6 xl:min-h-[440px]">
            <ExampleStage stageRef={stageRef} drag={drag} />
          </div>
          <p className="text-center text-xs text-ink-3 sm:hidden">{pg('stageMobile')}</p>
        </div>

        <IndependentScriptUpload onFile={onFile} />

        <footer className="flex flex-col gap-2 border-t border-border pt-4">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <p className="text-xs leading-relaxed text-ink-2">{pg('privacyNote')}</p>
            <a
              href={RELEASES_LATEST_URL}
              className="flex h-7 shrink-0 items-center gap-1.5 rounded-[6px] border border-border px-2.5 text-xs text-ink-2 transition-colors hover:border-ink-faint hover:text-ink"
            >
              <Download size={12} aria-hidden />
              {pg('downloadDesktop')}
            </a>
          </div>
          <p className="text-xs leading-relaxed text-ink-3">{pg('desktopNote')}</p>
          <p className="font-mono text-[11px] text-ink-3">
            {Object.entries(RUNTIME_PACKAGES)
              .map(([n, v]) => `${n} ${v}`)
              .join(' · ')}
          </p>
          <p className="font-mono text-[11px] text-ink-faint">
            {pg('cdnNote', { version: PYODIDE_VERSION })}
          </p>
        </footer>
      </div>

      <ExampleCodeSheet
        example={codeExample}
        onClose={() => setCodeExample(null)}
        onStart={(ex) => {
          setCodeExample(null)
          onLaunch(ex)
        }}
      />
    </div>
  )
}
