import type { ReactNode } from 'react'
import { IconProvider } from '@/components/ui/Icon'
import { TooltipProvider } from '@/components/ui/Tooltip'

/** Providers shared by every component mounted through the standalone MCP entry. */
export function McpProviders({ children }: { children: ReactNode }) {
  return (
    <TooltipProvider>
      <IconProvider>{children}</IconProvider>
    </TooltipProvider>
  )
}
