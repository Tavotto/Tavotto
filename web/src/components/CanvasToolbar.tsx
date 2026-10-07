import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArrowUpRight,
  ChevronDown,
  Circle,
  Maximize2,
  MousePointerClick,
  Shapes,
  Slash,
  Square,
  Tags,
  Type,
} from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { addSubLabels } from '@/store/actions'
import { insertShape } from '@/lib/presets'
import { MOD } from '@/lib/utils'
import { useDocumentStore } from '@/store/documentStore'
import { useUiStore } from '@/store/uiStore'
import { TOOLBAR_FIT_CLEARANCE, useViewportStore } from '@/store/viewportStore'
import { useWorkspaceStore } from '@/store/workspace'
import { PresetsDialog } from './PresetsDialog'
import { Button } from './ui/Button'
import { Menu, MenuItem, MenuLabel, MenuRadioGroup, MenuRadioItem, MenuSeparator } from './ui/Menu'
import { Tip } from './ui/Tooltip'

/**
 * 画布底部的浮动工具条（2026-09-30 重设计 A1）：选择 / 文字 / 标注 ▾ / 序号 | 适应。
 * 它们此前住在顶栏中段与右段；搬家不改行为：快捷键（V / T / A / R / O / L / ⌘1）、`data-tool`、
 * `data-fit-canvas` 这些钩子都跟着元素走。
 *
 * **只在画布排版模式出现**（与搬家前的顶栏同一条判据，`mode !== 'fast_edit'`）：标注画的是画布
 * 对象，快速编辑那一屏只有一张图，画下去看不见。
 */

/**
 * 浮动工具条此刻是否显示；底部的 toast / HUD 据此抬高自己（工具条占掉画布底边 16 + 40 = 56，
 * 再留 8 的缝：抬到 bottom-16）
 */
export function useCanvasToolbarVisible(): boolean {
  return useWorkspaceStore((s) => s.mode !== 'fast_edit')
}

/**
 * 标注形状收进一个菜单：名字走 common:objectType / common:shape，这里只留图标与快捷键。
 * 「文字」有自己的按钮，不在这张表里。
 */
type MarkTool = 'arrow' | 'rect' | 'ellipse' | 'line'

const MARK_TOOLS: { tool: MarkTool; icon: typeof Type; key: string }[] = [
  { tool: 'arrow', icon: ArrowUpRight, key: 'A' },
  { tool: 'rect', icon: Square, key: 'R' },
  { tool: 'ellipse', icon: Circle, key: 'O' },
  { tool: 'line', icon: Slash, key: 'L' },
]

/**
 * 画布工具的显示名：箭头是对象类型，其余是形状。
 *
 * 两个分支各自收窄成自己的字面量联合——模板 key 的静态展开按参数类型走，
 * 混在一个 `Exclude<Tool,'select'>` 里会让提取器要求 `shape.arrow`、
 * `objectType.rect` 这类不存在的条目。
 */
const markToolKey = (tool: MarkTool): string =>
  tool === 'arrow' ? objectTypeKey(tool) : shapeKey(tool)

const objectTypeKey = (tool: 'arrow') => `common:objectType.${tool}`
const shapeKey = (tool: 'rect' | 'ellipse' | 'line') => `common:shape.${tool}`

/** 插入的形状（非工具，点一下直接落一个） */
const INSERT_SHAPES = ['triangle', 'diamond', 'polygon', 'brace'] as const

export function CanvasToolbar() {
  const visible = useCanvasToolbarVisible()
  // 适应取景据此给底部让位：与显示判据是同一个值，不另判
  useEffect(() => {
    useViewportStore.getState().setFitBottomClear(visible ? TOOLBAR_FIT_CLEARANCE : 0)
    return () => useViewportStore.getState().setFitBottomClear(0)
  }, [visible])
  return visible ? <Bar /> : null
}

function Bar() {
  const { t } = useTranslation(['workspace', 'common'])
  const tool = useUiStore((s) => s.tool)
  const setTool = useUiStore((s) => s.setTool)
  const page = useDocumentStore((s) => s.doc.page)
  const [presetsOpen, setPresetsOpen] = useState(false)
  const activeMark = MARK_TOOLS.find((m) => m.tool === tool)
  const ActiveMark = activeMark?.icon

  // 按钮文字窄于 900 收成只有图标（名字仍在 aria-label 与读屏里）：画布窄时工具条不许比画布还宽
  const label = (text: string) => <span className="max-[899px]:sr-only">{text}</span>

  return (
    <div
      // `data-canvas-toolbar`：浮动工具条的稳定机器标识（e2e 量它的盒子，不认 role / 文案）
      data-canvas-toolbar
      role="toolbar"
      aria-label={t('workspace:canvasTools.label')}
      className="pointer-events-auto absolute bottom-4 left-1/2 z-canvas-chrome flex max-w-[calc(100%-1.5rem)] -translate-x-1/2 items-center gap-0.5 rounded-full bg-surface p-1 shadow-pop"
    >
      <Tip label={t('workspace:canvasTools.select')} shortcut="V">
        <Button
          size="md"
          active={tool === 'select'}
          data-tool="select"
          onClick={() => setTool('select')}
          aria-label={t('workspace:canvasTools.select')}
        >
          <MousePointerClick size={ICON_SIZE.md} />
          {label(t('workspace:canvasTools.select'))}
        </Button>
      </Tip>

      <Tip label={t('common:objectType.text')} shortcut="T">
        <Button
          size="md"
          active={tool === 'text'}
          // 稳定定位（e2e / 引导）：不认 aria-label 文案
          data-tool="text"
          onClick={() => setTool(tool === 'text' ? 'select' : 'text')}
          aria-label={t('common:objectType.text')}
        >
          <Type size={ICON_SIZE.md} />
          {label(t('common:objectType.text'))}
        </Button>
      </Tip>

      <Menu
        width={188}
        align="center"
        trigger={
          <Button size="md" active={!!activeMark} aria-label={t('workspace:topbar.annotate')}>
            {ActiveMark ? <ActiveMark size={ICON_SIZE.md} filled /> : <Shapes size={ICON_SIZE.md} />}
            {label(t('workspace:topbar.annotate'))}
            <ChevronDown size={ICON_SIZE.xs} className="text-ink-3" />
          </Button>
        }
      >
        {/* 四把工具是一组互斥取值：当前那把带勾（MenuRadioGroup），不再靠图标换个颜色
            说「选中的是我」——同一张菜单里图标的深浅还要兼职表示别的（2026-09-15 打磨 T6）。
            图标走 `MenuItem.icon` 这个唯一出处，不手写 span + 自定色 */}
        <MenuRadioGroup
          value={activeMark?.tool ?? ''}
          onValueChange={(v) => setTool(tool === v ? 'select' : (v as MarkTool))}
        >
          {MARK_TOOLS.map(({ tool: mark, icon: Icon, key }) => (
            <MenuRadioItem key={mark} value={mark} icon={Icon} shortcut={key}>
              {t(markToolKey(mark))}
            </MenuRadioItem>
          ))}
        </MenuRadioGroup>
        <MenuSeparator />
        <MenuLabel>{t('workspace:topbar.insertShape')}</MenuLabel>
        {INSERT_SHAPES.map((kind) => (
          <MenuItem key={kind} onSelect={() => insertShape(kind)}>
            {t(`common:shape.${kind}`)}
          </MenuItem>
        ))}
        <MenuSeparator />
        <MenuItem onSelect={() => setPresetsOpen(true)}>{t('workspace:topbar.presets')}</MenuItem>
      </Menu>
      <PresetsDialog open={presetsOpen} onClose={() => setPresetsOpen(false)} />

      <Tip label={t('workspace:topbar.subLabelsTip')}>
        <Button size="md" onClick={addSubLabels} aria-label={t('workspace:topbar.addSubLabels')}>
          <Tags size={ICON_SIZE.md} />
          {label(t('workspace:canvasTools.subLabels'))}
        </Button>
      </Tip>

      <span aria-hidden className="mx-1 h-5 w-px shrink-0 bg-border" />

      <Tip label={t('workspace:topbar.fitCanvas')} shortcut={`${MOD}1`}>
        <Button
          size="md"
          onClick={() => useViewportStore.getState().fitAnimated(page.w, page.h)}
          aria-label={t('workspace:topbar.fitCanvas')}
          // e2e 的稳定锚点（选择器不认 aria-label / 文案，web/AGENTS.md）
          data-fit-canvas
        >
          <Maximize2 size={ICON_SIZE.md} />
          {label(t('workspace:canvasTools.fit'))}
        </Button>
      </Tip>
    </div>
  )
}
