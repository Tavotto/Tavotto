import { formatMessage, i18n } from '@/i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { engineRender, enginePreviewPngSnapshot, exportFigure, startExport, ApiError, EngineError, engineErrorMsg, backendErrorMsg, type ExportRequest } from './api'
import { FIGSIZE_SOURCE_POLICY } from './artifactValidation'
import { setCurrentProjectId } from './session'
const expected={bytes_sha256:'a'.repeat(64),size_bytes:10}
const source={render_policy:FIGSIZE_SOURCE_POLICY,source_id:'plot.png',origin:'static',kind:'png',...expected}
const fields={source_policy:FIGSIZE_SOURCE_POLICY,expected_source:expected}
const req:ExportRequest={scope:'original',filename:'out',formats:['png'],ppi:300,background:'transparent',overwrite:'rename',original:{figure_id:'plot.png',...fields}}
const ok=(body:unknown)=>new Response(JSON.stringify(body),{status:200,headers:{'Content-Type':'application/json'}})
beforeEach(()=>{setCurrentProjectId('A');vi.restoreAllMocks()})
describe('selected API negotiation',()=>{
 it('render preserves source fields and refuses missing old-server acknowledgement',async()=>{
  const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValue(ok({rev:1,manifest:{}}))
  await expect(engineRender('plot.png',[],fields)).rejects.toThrow()
  expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toMatchObject(fields)
 })
 it('paired preview sends and validates source identity',async()=>{
  const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValue(ok({png:'AA',manifest:{elements:[]},artifact_source:source}))
  await expect(enginePreviewPngSnapshot('plot.png',[],800,undefined,fields)).resolves.toMatchObject({url:'data:image/png;base64,AA'})
  expect(JSON.parse(String(fetch.mock.calls[0][1]?.body))).toMatchObject({...fields,with_manifest:true})
  fetch.mockResolvedValue(ok({png:'AA',manifest:{},artifact_source:{...source,bytes_sha256:'b'.repeat(64)}}))
  await expect(enginePreviewPngSnapshot('plot.png',[],800,undefined,fields)).rejects.toThrow()
 })
 it.each([exportFigure,startExport])('an old validation200 cannot cause an export POST',async run=>{
  const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValue(ok({ok:true}))
  await expect(run(req)).rejects.toThrow()
  expect(fetch).toHaveBeenCalledTimes(1);expect(String(fetch.mock.calls[0][0])).toContain('/api/export/validate')
 })
 it.each([exportFigure,startExport])('acknowledged export stays bound to captured project across the preflight await',async run=>{
  let release!:(value:Response)=>void
  const fetch=vi.spyOn(globalThis,'fetch').mockImplementationOnce(()=>new Promise(r=>{release=r})).mockResolvedValue(ok({job_id:'ok'}))
  const promise=run(req);setCurrentProjectId('B')
  release(ok({ok:true,artifact_sources:{'plot.png':source}}));await promise
  expect(fetch).toHaveBeenCalledTimes(2)
  for(const call of fetch.mock.calls)expect(new Headers(call[1]?.headers).get('X-Tavotto-Project')).toBe('A')
 })
 it('server source refusal keeps its actionable reason in export/API status',()=>{
  const error=new ApiError('source unavailable',409,{code:'artifact_source_unavailable',params:{reason:'source_changed'}})
  expect(formatMessage(backendErrorMsg(error))).toMatch(/源文件已变化|source file changed/i)
 })
 it('legacy export does not gain a validation request',async()=>{
  const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValue(ok({job_id:'legacy'}))
  await startExport({...req,original:{figure_id:'plot.png'}})
  expect(fetch).toHaveBeenCalledTimes(1);expect(String(fetch.mock.calls[0][0])).toContain('/api/export/start')
 })
})

it('background visibility reason stays translated across engine/API and language changes',async()=>{
 const params={reason:'background_visibility_required'}
 const messages=[backendErrorMsg(new ApiError('untranslated',409,{code:'artifact_source_unavailable',params})),engineErrorMsg(new EngineError('untranslated','','artifact_source_unavailable','',undefined,undefined,{params}))]
 const previous=i18n.language
 try {
  await i18n.changeLanguage('en-US');for(const message of messages)expect(formatMessage(message)).toContain('Choose whether')
  await i18n.changeLanguage('zh-CN');for(const message of messages)expect(formatMessage(message)).toContain('请选择')
 } finally { await i18n.changeLanguage(previous) }
})
