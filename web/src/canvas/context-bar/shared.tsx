import { SlidersHorizontal } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Tip } from '@/components/ui/Tooltip'
import { useUiStore } from '@/store/uiStore'
import { qb } from './text'

export const Sep = () => <span aria-hidden className="mx-0.5 h-4 w-px shrink-0 bg-border" />

/** 「全部属性」——工具条到属性页的固定出口 */
export function OpenInspectorButton() {
  return (
    <Tip label={qb('openInspector')} side="bottom">
      <Button
        size="icon-sm"
        aria-label={qb('openInspector')}
        onClick={() => {
          const ui = useUiStore.getState()
          ui.setRightTab('properties')
        }}
      >
        <SlidersHorizontal size={12} />
      </Button>
    </Tip>
  )
}
