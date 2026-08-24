import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { Tip } from '@/components/ui/Tooltip'
import { McpProviders } from './McpProviders'

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true

let container: HTMLDivElement
let root: Root

beforeEach(() => {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

it('supplies Radix tooltip context to the standalone MCP canvas', () => {
  expect(() => {
    act(() => {
      root.render(
        <McpProviders>
          <Tip label="MCP tooltip">
            <button type="button">Canvas control</button>
          </Tip>
        </McpProviders>,
      )
    })
  }).not.toThrow()
  expect(container.textContent).toContain('Canvas control')
})
