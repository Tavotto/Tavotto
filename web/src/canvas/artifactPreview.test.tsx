import { LayoutSnapshot } from '@/components/VersionDialog'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { PanelView } from './PanelView'
import { useVariantPng, clearVariantPngCache } from '@/hooks/useVariantPng'
import { EngineError } from '@/lib/api'
import { useRenderStore, exactPanelManifest, renderKeyOf } from '@/store/renderStore'
import { useMountedPngStore } from '@/store/mountedPngStore'
import { useUiStore } from '@/store/uiStore'
import { useViewportStore } from '@/store/viewportStore'
import { VECTOR_PREVIEW } from '@/lib/previewBudget'
import type { PanelObject } from '@/types/document'
import type { Manifest } from '@/lib/api'
globalThis.IS_REACT_ACT_ENVIRONMENT = true
const preview=vi.fn()
vi.mock('@/lib/api',async original=>({...await original<typeof import('@/lib/api')>(),enginePreviewPngSnapshot:(...a:unknown[])=>preview(...a)}))
const guard={version:1 as const,requiredFrame:'figsize' as const,sourceId:'plot.png',bytesSha256:'a'.repeat(64),sizeBytes:10}
const FRAME={gid:'figure',prop:'frame',value:'figsize'}
const p:PanelObject={id:'p',type:'panel',fileId:'plot.png',fileKind:'raster',script:'figure.py',x:0,y:0,w:100,h:80,nativeW:100,nativeH:80,overrides:[FRAME],artifactValidation:guard}
const manifest={stem:'plot',size_mm:[100,80],elements:[{gid:'figure',role:'figure',bbox:[0,0,1,1],editable:[]}]} as unknown as Manifest
let el:HTMLDivElement,root:Root
beforeEach(()=>{
 el=document.createElement('div');document.body.appendChild(el);root=createRoot(el)
 useRenderStore.getState().clear();useMountedPngStore.setState({byPanel:{}});useUiStore.getState().setElementPanel(null);useViewportStore.setState({zoom:1})
 preview.mockReset();clearVariantPngCache();URL.revokeObjectURL=vi.fn()
})
afterEach(()=>{act(()=>root.unmount());el.remove()})
function seed(){useRenderStore.getState().patch(renderKeyOf(p),{fileId:p.fileId,artifactValidation:guard,rev:1,svg:'<svg/>',svgBytes:6,manifest,status:'ready',lastPatches:JSON.stringify(p.overrides),wantPatches:JSON.stringify(p.overrides),preview:VECTOR_PREVIEW})}
describe('selected pixels refuse stale source authority',()=>{
 it('changed source discovered by paired preview clears pixels, mounted geometry and exposes refusal',async()=>{
  seed();preview.mockResolvedValueOnce({url:'data:image/png;base64,OLD',manifest})
  await act(async()=>root.render(<PanelView obj={p}/>))
  const image=el.querySelector('img')!;expect(image.getAttribute('src')).toContain('OLD')
  await act(async()=>image.dispatchEvent(new Event('load')))
  expect(exactPanelManifest(useRenderStore.getState(),p)).not.toBeNull()
  preview.mockRejectedValueOnce(new EngineError('changed','','artifact_source_unavailable','',undefined,undefined,{params:{reason:'source_changed'}}))
  await act(async()=>useRenderStore.getState().patch(renderKeyOf(p),{rev:2}))
  expect(preview).toHaveBeenCalledTimes(2)
  expect(exactPanelManifest(useRenderStore.getState(),p)).toBeNull()
  expect(el.querySelector('img')?.getAttribute('src')).not.toContain('OLD')
  expect(useMountedPngStore.getState().byPanel[p.id]?.manifest ?? null).toBeNull()
  expect(useRenderStore.getState().byKey[renderKeyOf(p)].code).toBe('artifact_source_unavailable')
  expect(el.querySelector('[title]')?.getAttribute('title')).toMatch(/源文件|source file/i)
 })
 it('saved frame mismatch is visibly refused before cached SVG or geometry is shown',async()=>{
  seed();const bad={...p,overrides:[]};useUiStore.getState().setElementPanel(p.id)
  await act(async()=>root.render(<PanelView obj={bad}/>))
  expect(el.querySelector('[data-element-svg]')).toBeNull();expect(exactPanelManifest(useRenderStore.getState(),bad)).toBeNull()
  expect(el.querySelector('[title]')?.getAttribute('title')).toMatch(/图幅|frame/i);expect(preview).not.toHaveBeenCalled()
 })
})
function Thumb({patches=p.overrides}:{patches?:PanelObject['overrides']}){
 const state=useVariantPng(p.fileId,patches,200,true,0,guard)
 return <span data-thumb>{JSON.stringify(state)}</span>
}
describe('selected timeline keys own their pixels',()=>{
 it('A to B hides A immediately, and old late replies cannot repopulate cache after clear',async()=>{
  preview.mockResolvedValueOnce({url:'data:image/png;base64,A',manifest})
  await act(async()=>root.render(<Thumb/>));expect(el.textContent).toContain('base64,A')
  let release!:(v:unknown)=>void;preview.mockImplementationOnce(()=>new Promise(r=>{release=r}))
  await act(async()=>root.render(<Thumb patches={[FRAME,{gid:'line',prop:'color',value:'blue'}]}/>))
  expect(el.textContent).not.toContain('base64,A')
  clearVariantPngCache();await act(async()=>release({url:'data:image/png;base64,B',manifest}))
  expect(el.textContent).not.toContain('base64,B')
 })
 it('real timeline snapshot caller enables guard validation even when old reset left []',async()=>{
  const approximate=vi.fn()
  const doc={schema:2 as const,name:'history',page:{w:150,h:100},objects:[{...p,overrides:[]}],guides:[]}
  await act(async()=>root.render(<LayoutSnapshot doc={doc} renderOverrides onApproximate={approximate}/>))
  expect(approximate).toHaveBeenLastCalledWith(true);expect(preview).not.toHaveBeenCalled()
 })
 it('timeline source failure revokes matching live canvas authority, without touching another saved hash',async()=>{
  seed()
  const other={...p,artifactValidation:{...guard,bytesSha256:'b'.repeat(64)}}
  useRenderStore.getState().patch(renderKeyOf(other),{fileId:p.fileId,artifactValidation:other.artifactValidation,rev:1,manifest,status:'ready',lastPatches:JSON.stringify(p.overrides),preview:VECTOR_PREVIEW})
  preview.mockRejectedValue(new EngineError('changed','','artifact_source_unavailable','',undefined,undefined,{params:{reason:'source_changed'}}))
  await act(async()=>root.render(<Thumb/>))
  expect(exactPanelManifest(useRenderStore.getState(),p)).toBeNull()
  expect(exactPanelManifest(useRenderStore.getState(),other)).not.toBeNull()
  expect(el.textContent).toContain('"approximate":true')
 })
 it('a retained guard with empty old-reset patches is approximate/refused, not unedited',async()=>{
  await act(async()=>root.render(<Thumb patches={[]}/>))
  expect(el.textContent).toContain('"approximate":true');expect(preview).not.toHaveBeenCalled()
 })
})
