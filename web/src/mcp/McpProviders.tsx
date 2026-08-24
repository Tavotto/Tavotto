import type { ReactNode } from 'react'
import { TooltipProvider } from '@/components/ui/Tooltip'

/** Providers shared by every component mounted through the standalone MCP entry. */
export function McpProviders({ children }: { children: ReactNode }) {
  return <TooltipProvider>{children}</TooltipProvider>
}
