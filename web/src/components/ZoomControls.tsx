import { useTranslation } from 'react-i18next'
import { ChevronDown } from '@/components/ui/icons'
import { ICON_SIZE } from '@/components/ui/Icon'
import { keyOf } from '@/lib/keymap'
import { MAX_ZOOM, MIN_ZOOM, useViewportStore } from '@/store/viewportStore'
import { fitStage, useCanZoomToSelection, zoomToSelection } from '@/store/zoomToSelection'
import { Numbers } from '@sfinterface/numbers'
import { Button } from './ui/Button'
import { NumberField } from './ui/Input'
import { Menu, MenuItem, MenuRadioGroup, MenuRadioItem, MenuSeparator } from './ui/Menu'

const ZOOM_PRESETS = [0.5, 0.75, 1, 1.5, 2, 4]

/**
 * 缩放菜单「114% ⌄」：住在画布标签行右侧（2026-09-30 重设计 A1；此前在顶栏右段）。
 * 「适应画布」的图标钮搬进了画布底部的浮动工具条，这里的菜单里仍有同一条。
 */
export function ZoomControls() {
  const { t, i18n } = useTranslation('workspace')
  const zoom = useViewportStore((s) => s.zoom)
  // 读数显示的是「用户要去的那一档」（补间的终点），不是补间中的每一帧；见 viewportStore
  const readoutZoom = useViewportStore((s) => s.readoutZoom)
  const readoutRolls = useViewportStore((s) => s.readoutRolls)
  // 预设那一组是**互斥取值**：当前档带勾。缩放不是整数档时一个都不勾（「不知道是哪一档」
  // 有自己的取值，不能就近归到相邻那一档）
  const preset = ZOOM_PRESETS.find((z) => Math.abs(z - zoom) < 1e-6)
  // 与动作同一个判据：选中的全隐藏了 / 面积为 0 / 快速编辑里选区不含正在编辑的那张图都置灰
  // （隐藏不清选区，只看 ids 长度会留一个空转的入口；快编里 ⌘A 选进来的版面对象这一屏看不见）
  const canZoomSelection = useCanZoomToSelection()

  return (
    /* 缩放是一颗文本钮「114% ⌄」（2026-09-15 打磨批次 F，L3；适应画布的图标钮已并进浮动工具条）：
       此前是四格边框组，与旁边的边框钮、黑钮三种壳相邻。放大 / 缩小进了菜单，快捷键照旧。
       弹层本身从 Popover + 九行手写 button 换成 `Menu`（2026-09-15 打磨 M1）：全产品的菜单
       只有一份实现——role=menu、方向键 / 首字母跳转、内边距 4 都跟着来，不再是第二种菜单 */
    <div className="flex items-center gap-0.5">
      <Menu
        width={168}
        align="end"
        trigger={
          <Button
            size="md"
            aria-label={t('topbar.zoomValue', { percent: Math.round(zoom * 100) })}
            className="type-number"
            // e2e 的稳定锚点
            data-zoom-menu
          >
            {/* 会滚的数字（@sfinterface/numbers，2026-09-15 调研后只上这一处）：一步到位的缩放
                （± / 预设 / 适应）只有变了的位滚过去，说的是「变了多少、往哪变」；滚轮 / 捏合是
                连续输入，读数即时换（duration 0），柱子不会永远在半路。静止时与普通文字像素一致。
                时长接 --duration-slow（index.css 的 --sfi-resolve），分组关掉——Tavotto 的读数不分组。
                可达名在按钮的 aria-label 上，组件自己那份读屏文本由 label 保持同一句 */}
            <Numbers
              value={Math.round(readoutZoom * 100)}
              suffix="%"
              duration={readoutRolls ? undefined : 0}
              format={{ useGrouping: false }}
              locale={i18n.language}
              label={t('topbar.zoomValue', { percent: Math.round(readoutZoom * 100) })}
              data-zoom-readout
            />
            <ChevronDown size={ICON_SIZE.xs} className="text-ink-3" />
          </Button>
        }
      >
        {/* 可以直接敲一个倍率（2026-10-07 设计审计 §10.1）：菜单顶上一格数字框，回车 / 失焦生效。
            按键不交给菜单（Radix 菜单会把字母当首字母跳转、把方向键当换项） */}
        <div className="px-1 pb-1" onKeyDown={(e) => e.stopPropagation()} data-zoom-value-row>
          <NumberField
            fill
            value={Math.round(zoom * 100)}
            min={MIN_ZOOM * 100}
            max={MAX_ZOOM * 100}
            step={10}
            precision={0}
            unit="%"
            ariaLabel={t('topbar.zoomValueInput')}
            dataProp="zoom-value"
            onChange={(v) => useViewportStore.getState().setZoomCentered(v / 100)}
          />
        </div>
        <MenuItem shortcut={keyOf('zoomIn')} onSelect={() => useViewportStore.getState().zoomBy(1.25)}>
          {t('topbar.zoomIn')}
        </MenuItem>
        <MenuItem shortcut={keyOf('zoomOut')} onSelect={() => useViewportStore.getState().zoomBy(1 / 1.25)}>
          {t('topbar.zoomOut')}
        </MenuItem>
        <MenuSeparator />
        <MenuRadioGroup
          value={preset != null ? String(preset) : undefined}
          onValueChange={(v) => useViewportStore.getState().setZoomCentered(Number(v))}
        >
          {ZOOM_PRESETS.map((z) => (
            <MenuRadioItem key={z} value={String(z)} shortcut={z === 1 ? keyOf('zoomActual') : undefined}>
              {`${z * 100}%`}
            </MenuRadioItem>
          ))}
        </MenuRadioGroup>
        <MenuSeparator />
        <MenuItem
          shortcut={keyOf('zoomFit')}
          onSelect={fitStage}
          data-fit-canvas-item
        >
          {t('topbar.fitCanvas')}
        </MenuItem>
        {/* 缩放到选中（⇧2）：没有（可见的）选中时置灰，不去适应整页冒充 */}
        <MenuItem
          shortcut={keyOf('zoomSelection')}
          disabled={!canZoomSelection}
          onSelect={() => void zoomToSelection()}
          data-zoom-selection-item
        >
          {t('topbar.zoomSelection')}
        </MenuItem>
      </Menu>
    </div>
  )
}

