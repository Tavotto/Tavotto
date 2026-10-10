import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { ProfilesSettings } from './ProfilesSettings'
import { TooltipProvider } from '@/components/ui/Tooltip'
import { builtinCatalog } from '@/lib/specBinding'
import type { ProfileRecord } from '@/lib/api'
import { useDocumentStore } from '@/store/documentStore'
import { useProfileStore } from '@/store/profileStore'
import { emptyProject } from '@/types/document'

globalThis.IS_REACT_ACT_ENVIRONMENT = true
const specs: ProfileRecord[] = builtinCatalog().map((e) => ({
  id: e.id, kind: 'spec', schema_version: 1, revision: 1, display_name: e.display_name,
  name_key: e.name_key ?? '', version: e.version, created_at: 0, updated_at: 0,
  built_in: true, read_only: true, is_default: false, derived_from: '', warnings: [], data: e.data,
}))
const copy: ProfileRecord = { ...specs[0], id: 'validation-copy', display_name: 'Copy before load',
  name_key: '', built_in: false, read_only: false, derived_from: specs[0].id,
  data: { ...specs[0].data, min_effective_font_size_pt: 7 },
}
/** Regression uses real ProfilesSettings, NumberField and profileStore; only the HTTP boundary is delayed. */
let root: Root
let host: HTMLDivElement
beforeEach(async () => {
  useProfileStore.setState({ specs, styles: [], loaded: false, loading: false, error: null, conflict: null })
  await useDocumentStore.getState().switchDocument(emptyProject(), 'validation-race')
  host = document.createElement('div'); document.body.append(host); root = createRoot(host)
})
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })
const button = (selector: string) => {
  const matches = host.querySelectorAll<HTMLButtonElement>(selector)
  expect(matches).toHaveLength(1)
  return matches[0]
}

it('retains a committed 9pt local draft after duplicate completes before the first list and its mutation refresh', async () => {
  let release!: () => void
  const gate = new Promise<void>((resolve) => { release = resolve })
  let lists = 0
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (init?.method === 'POST' && url.endsWith('/duplicate')) return json({ profile: copy })
    expect(init?.method ?? 'GET').toBe('GET')
    const initial = ++lists <= 2
    if (initial) await gate
    return json({ profiles: url.endsWith('/style') ? [] : initial ? specs : [...specs, copy] })
  })
  vi.stubGlobal('fetch', fetcher)
  const onDirty = vi.fn()
  await act(async () => root.render(<TooltipProvider><ProfilesSettings kind="spec" onDirtyChange={onDirty} /></TooltipProvider>))
  expect(lists).toBe(2)
  expect(useProfileStore.getState().loaded).toBe(false)
  // The read-only action group contains exactly one icon-bearing duplicate button, selected by its stable group anchor.
  await act(async () => button('[data-profile-readonly] button:has(svg)').click())
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
  expect(host.querySelector('[data-profile-library]')?.textContent).toContain(copy.display_name)
  expect(onDirty).not.toHaveBeenCalledWith(true)
  const matching = [...host.querySelectorAll<HTMLInputElement>('[data-field-group="fonts"] input')].filter((x) => x.value === '7')
  expect(matching).toHaveLength(1)
  const field = matching[0]
  await act(async () => {
    field.focus()
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(field, '9')
    field.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => field.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })))
  expect(field.value).toBe('9')
  const names = host.querySelectorAll<HTMLInputElement>('[data-profile-identity] input')
  expect(names).toHaveLength(1)
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(names[0], 'Unfinished name')
    names[0].dispatchEvent(new Event('input', { bubbles: true }))
  })
  expect(onDirty).toHaveBeenLastCalledWith(true)
  expect(useProfileStore.getState().get('spec', copy.id)?.data.min_effective_font_size_pt).toBe(7)
  await act(async () => { release(); await gate })
  await act(async () => {})
  expect(lists).toBe(4)
  expect(useProfileStore.getState().loaded).toBe(true)
  expect(host.querySelector('[data-profile-library]')?.textContent).toContain(copy.display_name)
  expect(field.isConnected).toBe(true)
  expect(field.value, 'the unsaved local 9pt draft must survive the delayed list').toBe('9')
  expect(names[0].isConnected).toBe(true)
  expect(names[0].value).toBe('Unfinished name')
  expect(button('[data-profile-save]').disabled).toBe(false)
  expect(button('[data-profile-discard]').disabled).toBe(false)
  expect(onDirty).toHaveBeenLastCalledWith(true)
  expect(useProfileStore.getState().get('spec', copy.id)?.data.min_effective_font_size_pt).toBe(7)
  await act(async () => button('[data-profile-discard]').click())
  expect(field.value).toBe('7')
  expect(names[0].value).toBe(copy.display_name)
  expect(button('[data-profile-save]').disabled).toBe(true)
  expect(onDirty).toHaveBeenLastCalledWith(false)
})
