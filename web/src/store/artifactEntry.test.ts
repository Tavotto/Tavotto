import { seedExactRender } from '@/test/renderFixtures'
import { EngineError, type Manifest } from '@/lib/api'
import { materializeRelink } from '@/lib/clipboard'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { literal } from '@/i18n'
import { emptyProject, type PanelObject } from '@/types/document'
import { useDocumentStore } from './documentStore'
import { useUiStore } from './uiStore'
import { useAssetStore } from './assetStore'
import { enterElementEdit, resetOverrides, clearOverride, clearOverrides, setOverrides, resolveArtifactBackground } from './actions'
import { useRenderStore, renderKeyOf, exactPanelRender, panelRender, rejectArtifactRenders } from './renderStore'
import { requestRender, hasScheduledRender } from './renderScheduler'
import { syncEngine } from '@/hooks/useEngineSync'
import { artifactValidationIssue, FIGSIZE_SOURCE_POLICY, ArtifactValidationError } from '@/lib/artifactValidation'
import { setCurrentProjectId } from '@/lib/session'
import { migrateFigureFrames } from '@/lib/figureFrameMigration'
import { focusObject } from '@/lib/issueFocus'
import { useSelectionStore } from './selectionStore'
import { useWorkspaceStore } from './workspace'
import { useMountedPngStore } from './mountedPngStore'
const render = vi.fn()
const pendingStyle = vi.fn(()=>false)
vi.mock('@/lib/api',async original=>({...await original<typeof import('@/lib/api')>(),engineRender:(...args:unknown[])=>render(...args)}))
vi.mock('./styleWork',()=>({hasPendingStyleWork:(...args:unknown[])=>pendingStyle(...args as [])}))
const FRAME={gid:'figure',prop:'frame',value:'figsize'}
const guard={version:1 as const,requiredFrame:'figsize' as const,sourceId:'plot.png',bytesSha256:'a'.repeat(64),sizeBytes:10}
const source={render_policy:FIGSIZE_SOURCE_POLICY,source_id:'plot.png',origin:'static',kind:'png',bytes_sha256:guard.bytesSha256,size_bytes:10}
const response=()=>({rev:1,svg:'<svg/>',manifest:{size_mm:[127,81.28],elements:[]},artifact_source:source})
const panel=():PanelObject=>({id:'p',type:'panel',fileId:'plot.png',fileKind:'raster',script:'figure.py',figureFrame:1,nativeW:126.99,nativeH:81.13,pxW:360,pxH:230,x:17,y:21,w:63,h:47,crop:{x:.11,y:.14,w:.62,h:.57},rotation:90,flipH:true,flipV:true,opacity:.8,overrides:[]})
const S=()=>useDocumentStore.getState()
const P=()=>S().doc.objects[0] as PanelObject
const flush=()=>new Promise(r=>setTimeout(r,0))
const visual=(p:PanelObject)=>({x:p.x,y:p.y,w:p.w,h:p.h,crop:p.crop,rotation:p.rotation,flipH:p.flipH,flipV:p.flipV,opacity:p.opacity,pxW:p.pxW,pxH:p.pxH})
async function seed(p=panel()) {
 await S().switchDocument(emptyProject(),'entry-test')
 S().commit(literal('seed'),d=>{d.objects.push(p)})
 useDocumentStore.setState({past:[],future:[]})
}
beforeEach(async()=>{
 render.mockReset().mockResolvedValue(response());pendingStyle.mockReturnValue(false)
 useRenderStore.getState().clear();useUiStore.getState().setElementPanel(null)
 useSelectionStore.getState().clear();useWorkspaceStore.getState().clear();useMountedPngStore.setState({byPanel:{}})
 useAssetStore.setState({byId:{}});setCurrentProjectId('A');await seed()
})
describe('atomic source entry and old-compatible history',()=>{
 it('keeps original while pending; commits FRAME/guard/native only, then guarded render',async()=>{
  let release!:(v:unknown)=>void;render.mockImplementationOnce(()=>new Promise(r=>{release=r}))
  const before=structuredClone(P());const entering=enterElementEdit('p')
  await vi.waitFor(()=>expect(render).toHaveBeenCalledTimes(1))
  expect(P()).toEqual(before);expect(S().past).toHaveLength(0);expect(useUiStore.getState().elementPanelId).toBeNull()
  release(response());expect(await entering).toBe(true)
  expect(P().artifactValidation).toEqual(guard);expect(P().overrides).toEqual([FRAME]);expect(visual(P())).toEqual(visual(before))
  expect([P().nativeW,P().nativeH]).toEqual([127,81.28]);expect(S().past).toHaveLength(1)
  syncEngine(S().doc.objects,'p');await flush()
  expect(render.mock.calls[1][2]).toMatchObject({source_policy:FIGSIZE_SOURCE_POLICY,expected_source:{bytes_sha256:guard.bytesSha256,size_bytes:10}})
  expect(exactPanelRender(useRenderStore.getState(),P())).not.toBeNull()
  S().undo();render.mockClear();syncEngine(S().doc.objects,'p');await flush()
  expect(useUiStore.getState().elementPanelId).toBeNull();expect(render).not.toHaveBeenCalled();expect(P()).toEqual(before)
  S().redo();syncEngine(S().doc.objects,null);await flush();expect(P().artifactValidation).toEqual(guard)
  expect(useUiStore.getState().elementPanelId).toBeNull();expect(render).toHaveBeenCalled()
 })
 it.each(['undo','other-edit','project','exit','replace','gesture'])('late result after %s cannot adopt or enter',async action=>{
  let release!:(v:unknown)=>void;render.mockImplementationOnce(()=>new Promise(r=>{release=r}))
  const entering=enterElementEdit('p');await vi.waitFor(()=>expect(render).toHaveBeenCalledTimes(1))
  if(action==='undo'){S().commit(literal('move'),d=>{d.objects[0].x++});S().undo()}
  if(action==='other-edit')S().commit(literal('name'),d=>{d.name='other'})
  if(action==='project')setCurrentProjectId('B')
  if(action==='exit')useUiStore.getState().setElementPanel(null)
  if(action==='replace')S().commit(literal('replace'),d=>{(d.objects[0] as PanelObject).fileId='other.png'})
  if(action==='gesture')S().beginTxn(literal('drag'))
  const before=structuredClone(P()),history=S().past.length;release(response());expect(await entering).toBe(false)
  expect(P()).toEqual(before);expect(P().artifactValidation).toBeUndefined();expect(S().past).toHaveLength(history);expect(useUiStore.getState().elementPanelId).toBeNull()
  if(S().txn)S().endTxn({discard:true})
 })
 it.each(['baked','style','pending-style','tracked'])('latent %s cannot be treated as untouched',async reason=>{
  if(reason==='baked')useAssetStore.setState({byId:{'plot.png':{baked_overrides:[{gid:'x',prop:'visible',value:false}]} as never}})
  if(reason==='style')useDocumentStore.setState({doc:{...S().doc,style:{id:'bound'} as never}})
  if(reason==='pending-style')pendingStyle.mockReturnValue(true)
  if(reason==='tracked')useRenderStore.setState({tracked:{'plot.png':true}})
  const before=structuredClone(P());expect(await enterElementEdit('p')).toBe(false)
  expect(render).not.toHaveBeenCalled();expect(P()).toEqual(before);expect(S().past).toHaveLength(0)
 })
 it.each(['select-other','select-return','clear-selection','page-problem','other-fast-edit','workspace-clear'])('navigation %s cancels pending adoption without history',async action=>{
  S().commit(literal('second panel'),d=>{d.objects.push({...panel(),id:'q',fileId:'other.png'})})
  useSelectionStore.getState().set(['p'])
  let release!:(v:unknown)=>void;render.mockImplementationOnce(()=>new Promise(r=>{release=r}))
  const entering=enterElementEdit('p');await vi.waitFor(()=>expect(render).toHaveBeenCalledTimes(1))
  if(action==='select-other'||action==='select-return')useSelectionStore.getState().set(['q'])
  if(action==='select-return')useSelectionStore.getState().set(['p'])
  if(action==='clear-selection')useSelectionStore.getState().clear()
  if(action==='page-problem')expect(focusObject({documentId:S().documentId!,canvasId:S().activeCanvasId,objectId:null,gid:null})).toMatchObject({ok:true,mode:'layout'})
  if(action==='other-fast-edit')useWorkspaceStore.getState().enterFastEdit('q')
  if(action==='workspace-clear')useWorkspaceStore.getState().clear()
  const before=structuredClone(S().doc),history=S().past.length
  const selected=useSelectionStore.getState().ids,workspace=useWorkspaceStore.getState()
  release(response());expect(await entering).toBe(false)
  expect(S().doc).toEqual(before);expect(S().past).toHaveLength(history)
  expect(useSelectionStore.getState().ids).toBe(selected);expect(useWorkspaceStore.getState()).toBe(workspace)
  expect(useUiStore.getState().elementPanelId).toBeNull()
 })
 it('a no-op selection does not discard the current admission',async()=>{
  useSelectionStore.getState().set(['p'])
  let release!:(v:unknown)=>void;render.mockImplementationOnce(()=>new Promise(r=>{release=r}))
  const entering=enterElementEdit('p');await vi.waitFor(()=>expect(render).toHaveBeenCalledTimes(1))
  useSelectionStore.getState().set(['p'])
  release(response());expect(await entering).toBe(true)
 })
 it('missing source acknowledgement refuses without history; normal edit undo retains intent',async()=>{
  render.mockResolvedValueOnce({...response(),artifact_source:undefined});expect(await enterElementEdit('p')).toBe(false);expect(S().past).toHaveLength(0)
  expect(await enterElementEdit('p')).toBe(true)
  setOverrides('p',literal('color'),[{gid:'line',prop:'color',value:'red'}],false);S().undo()
  syncEngine(S().doc.objects,'p');expect(useUiStore.getState().elementPanelId).toBe('p')
 })
 it('explicit source re-entry renders all saved edits after a prior source refusal',async()=>{
  await enterElementEdit('p');await flush()
  setOverrides('p',literal('edit'),[{gid:'line',prop:'color',value:'blue'}],true);await flush()
  const saved=structuredClone(P());rejectArtifactRenders(saved.fileId,guard,new ArtifactValidationError('source_changed'))
  expect(exactPanelRender(useRenderStore.getState(),saved)).toBeNull();render.mockClear()
  expect(await enterElementEdit('p')).toBe(true);await flush()
  expect(render.mock.calls.map(c=>c[1])).toEqual([[],saved.overrides])
  expect(exactPanelRender(useRenderStore.getState(),P())).not.toBeNull();expect(P()).toEqual(saved)
 })
 it('deliberate relink removes guard only with source/patch replacement; undo restores it',async()=>{
  await enterElementEdit('p');const before=structuredClone(P())
  useAssetStore.setState({byId:{'other.png':{id:'other.png',kind:'raster',native_w_mm:20,native_h_mm:10,script:'other.py'} as never}})
  materializeRelink([{fileId:'plot.png',name:'plot',count:1,relinkTo:'other.png'}])
  expect(P().fileId).toBe('other.png');expect(P().overrides).toEqual([]);expect(Object.hasOwn(P(),'artifactValidation')).toBe(false)
  S().undo();expect(P()).toEqual(before)
 })
 it('figure-only reset keeps FRAME; deliberate full discard clears mismatched empty guard and undo restores it',async()=>{
  await enterElementEdit('p');setOverrides('p',literal('edit'),[{gid:'figure',prop:'size_mm',value:[140,90]},{gid:'title',prop:'pos_frac',value:[.4,.3]}],false)
  clearOverrides('p',literal('figure reset'),P().overrides.filter(o=>o.gid==='figure').map(({gid,prop})=>({gid,prop})))
  expect(P().overrides).toContainEqual(FRAME);expect(artifactValidationIssue(P())).toBeNull()
  S().commit(literal('old reset'),d=>{(d.objects[0] as PanelObject).overrides=[]})
  const mismatch=structuredClone(P());expect(await enterElementEdit('p')).toBe(false)
  resetOverrides('p');expect(Object.hasOwn(P(),'artifactValidation')).toBe(false);S().undo();expect(P()).toEqual(mismatch)
 })
})
describe('guard refusal before cached authority',()=>{
 it.each(['current','selection-away','new-project','new-render-epoch'])('re-entry source refusal invalidates only its project render epoch: %s',async scenario=>{
  const p={...panel(),overrides:[FRAME,{gid:'line',prop:'color',value:'blue'}],artifactValidation:guard};await seed(p)
  await useRenderStore.getState().render(p.fileId,p.overrides,undefined,undefined,undefined,guard)
  const key=renderKeyOf(p),cached=exactPanelRender(useRenderStore.getState(),p)!
  useMountedPngStore.getState().show(p.id,key,cached.rev,cached.manifest,'blob:old')
  useMountedPngStore.getState().loaded(p.id,{key,rev:cached.rev,source:cached.manifest,url:'blob:old',manifest:cached.manifest})
  expect(exactPanelRender(useRenderStore.getState(),p)).not.toBeNull()
  useSelectionStore.getState().set(['p'])
  let reject!:(e:unknown)=>void;render.mockImplementationOnce(()=>new Promise((_resolve,r)=>{reject=r}))
  const entering=enterElementEdit(p.id);await flush()
  if(scenario==='selection-away')useSelectionStore.getState().clear()
  if(scenario==='new-project'||scenario==='new-render-epoch'){
   if(scenario==='new-project')setCurrentProjectId('B')
   else useRenderStore.getState().clear()
   await useRenderStore.getState().render(p.fileId,p.overrides,undefined,undefined,undefined,guard)
  }
  const navigationStatus=literal('New navigation')
  if(scenario!=='current')useUiStore.getState().setStatus(navigationStatus,'error')
  const before=structuredClone(P()),history=S().past.length
  reject(new EngineError('changed','','artifact_source_unavailable','',undefined,undefined,{params:{reason:'source_changed'}}))
  expect(await entering).toBe(false);expect(P()).toEqual(before);expect(S().past).toHaveLength(history)
  if(scenario==='new-project'||scenario==='new-render-epoch')expect(exactPanelRender(useRenderStore.getState(),p)).not.toBeNull()
  else {
   expect(exactPanelRender(useRenderStore.getState(),p)).toBeNull()
   expect(useRenderStore.getState().byKey[key]).toMatchObject({status:'error',manifest:null,svg:null})
   expect(useMountedPngStore.getState().byPanel[p.id]).toBeUndefined()
  }
  if(scenario!=='current')expect(useUiStore.getState().status).toEqual(navigationStatus)
 })
 it('a re-entry response without the source acknowledgement revokes cached authority',async()=>{
  const p={...panel(),overrides:[FRAME],artifactValidation:guard};await seed(p)
  await useRenderStore.getState().render(p.fileId,p.overrides,undefined,undefined,undefined,guard)
  render.mockResolvedValueOnce({...response(),artifact_source:undefined})
  expect(await enterElementEdit(p.id)).toBe(false)
  expect(exactPanelRender(useRenderStore.getState(),p)).toBeNull()
 })
 it('a non-source re-entry failure does not revoke an exact source cache',async()=>{
  const p={...panel(),overrides:[FRAME],artifactValidation:guard};await seed(p)
  await useRenderStore.getState().render(p.fileId,p.overrides,undefined,undefined,undefined,guard)
  const cached=exactPanelRender(useRenderStore.getState(),p)
  render.mockRejectedValueOnce(new EngineError('busy','','worker_timeout'))
  expect(await enterElementEdit(p.id)).toBe(false)
  expect(exactPanelRender(useRenderStore.getState(),p)).toBe(cached)
 })
 it('invalid guard shares no geometry, shows error, cancels old debounce and never repairs migration',async()=>{
  const p={...panel(),overrides:[FRAME],artifactValidation:guard};await seed(p)
  await useRenderStore.getState().render(p.fileId,p.overrides,undefined,undefined,undefined,guard)
  requestRender(p,false);expect(hasScheduledRender(p.id)).toBe(true)
  const bad={...p,overrides:[]};requestRender(bad,false);expect(hasScheduledRender(p.id)).toBe(false)
  expect(exactPanelRender(useRenderStore.getState(),bad)).toBeNull()
  expect(panelRender(useRenderStore.getState(),bad)).toMatchObject({status:'error',manifest:null,svg:null})
  expect(panelRender(useRenderStore.getState(),bad)?.error).not.toBeNull()
  const noMarker={...bad,figureFrame:undefined};expect(migrateFigureFrames([noMarker])).toEqual([noMarker])
 })
 it('present undefined guard cannot clear a legacy same-patch cache entry',async()=>{
  const legacy=panel();render.mockResolvedValue(response())
  await useRenderStore.getState().render(legacy.fileId,legacy.overrides)
  const before=useRenderStore.getState().byKey[renderKeyOf(legacy)]
  const bad={...legacy,artifactValidation:undefined};expect(renderKeyOf(bad)).not.toBe(renderKeyOf(legacy))
  requestRender(bad,true);expect(useRenderStore.getState().byKey[renderKeyOf(legacy)]).toBe(before)
  expect(exactPanelRender(useRenderStore.getState(),bad)).toBeNull()
 })
 it('same-file selected and legacy variants do not supply fallback, and server refusal revokes selected pixels',async()=>{
  const p={...panel(),overrides:[FRAME],artifactValidation:guard}
  await useRenderStore.getState().render(p.fileId,p.overrides,undefined,undefined,undefined,guard)
  expect(useRenderStore.getState().latest[p.fileId]).toBeUndefined()
  expect(panelRender(useRenderStore.getState(),{...p,overrides:[FRAME,{gid:'line',prop:'color',value:'blue'}]})).toBeUndefined()
  rejectArtifactRenders(p.fileId,guard,new ArtifactValidationError('source_changed'))
  expect(exactPanelRender(useRenderStore.getState(),p)).toBeNull()
  expect(useRenderStore.getState().byKey[renderKeyOf(p)]).toMatchObject({status:'error',svg:null,manifest:null})
 })
})

describe('explicit selected background recovery', () => {
 const color = {gid:'figure',prop:'facecolor',value:'red'}
 const title = {gid:'title',prop:'text',value:'kept'}
 const transparency = (value:boolean) => ({gid:'figure',prop:'transparent',value})
 const ambiguous = () => ({...panel(),overrides:[FRAME,color,title],artifactValidation:guard})
 const refusal = () => new EngineError('choose background','','artifact_source_unavailable','',undefined,undefined,{params:{reason:'background_visibility_required'}})
 const baseline = (value:boolean) => ({...response().manifest,elements:[{gid:'figure',role:'figure',editable:[{prop:'transparent',type:'bool',value:!value,value_original:value}]}]}) as unknown as Manifest
 it.each([true,false])('explicit %s preserves other edits in one undoable commit and rerenders',async value=>{
  await seed(ambiguous());render.mockRejectedValueOnce(refusal())
  await useRenderStore.getState().render(P().fileId,P().overrides,undefined,undefined,undefined,guard)
  const before=structuredClone(P()),key=renderKeyOf(P());render.mockClear()
  resolveArtifactBackground(P().id,key,value);await flush()
  expect(P().overrides).toEqual([...before.overrides,transparency(value)]);expect(P().artifactValidation).toEqual(guard)
  expect(S().past).toHaveLength(1);expect(render).toHaveBeenCalledTimes(1);expect(exactPanelRender(useRenderStore.getState(),P())).not.toBeNull()
  S().undo();expect(P()).toEqual(before)
 })
 it('stale choices cannot write to a newer variant',async()=>{
  await seed(ambiguous());render.mockRejectedValueOnce(refusal())
  await useRenderStore.getState().render(P().fileId,P().overrides,undefined,undefined,undefined,guard)
  const key=renderKeyOf(P());setOverrides('p',literal('independent edit'),[{gid:'line',prop:'color',value:'blue'}],false)
  const before=structuredClone(P()),history=S().past.length
  resolveArtifactBackground('p',key,true);expect(P()).toEqual(before);expect(S().past).toHaveLength(history)
 })
 it.each([true,false])('transparency reset retains exact original %s while color survives; repeated reset is no-op',async original=>{
  await seed({...ambiguous(),overrides:[FRAME,transparency(!original),color,title]})
  seedExactRender(P(),baseline(original));render.mockResolvedValue({...response(),manifest:baseline(original)})
  const before=structuredClone(P())
  clearOverride('p','figure','transparent');await flush()
  expect(P().overrides).toEqual([FRAME,transparency(original),color,title]);expect(S().past).toHaveLength(1)
  render.mockClear();clearOverride('p','figure','transparent');await flush()
  expect(S().past).toHaveLength(1);expect(render).not.toHaveBeenCalled()
  S().undo();expect(P()).toEqual(before)
 })
 it('resetting both background edits removes both; full discard follows existing guard rules',async()=>{
  await seed({...ambiguous(),overrides:[FRAME,color,transparency(false),title]});seedExactRender(P(),baseline(true))
  clearOverrides('p',literal('background reset'),[{gid:'figure',prop:'facecolor'},{gid:'figure',prop:'transparent'}])
  expect(P().overrides).toEqual([FRAME,title]);expect(P().artifactValidation).toEqual(guard)
  resetOverrides('p');expect(P().overrides).toEqual([]);expect(P().artifactValidation).toBeUndefined()
 })
 it('no exact original value is guessed from another variant',async()=>{
  await seed({...ambiguous(),overrides:[FRAME,color,transparency(false),title]})
  seedExactRender({...P(),overrides:[FRAME]},baseline(true))
  clearOverride('p','figure','transparent');expect(P().overrides).toEqual([FRAME,color,title])
 })
 it('render refusal leaves good same-source variants and their geometry intact',async()=>{
  const good={...ambiguous(),overrides:[FRAME,color,transparency(false),title]}
  await useRenderStore.getState().render(good.fileId,good.overrides,undefined,undefined,undefined,guard)
  const goodRender=exactPanelRender(useRenderStore.getState(),good)
  render.mockRejectedValueOnce(refusal())
  const bad=ambiguous();await useRenderStore.getState().render(bad.fileId,bad.overrides,undefined,undefined,undefined,guard)
  expect(exactPanelRender(useRenderStore.getState(),good)).toBe(goodRender)
  expect(useRenderStore.getState().byKey[renderKeyOf(bad)].error?.key).toBe('artifact.backgroundVisibilityRequired')
  rejectArtifactRenders(good.fileId,guard,new ArtifactValidationError('source_changed'),renderKeyOf(bad))
  expect(exactPanelRender(useRenderStore.getState(),good)).toBeNull()
 })
})
