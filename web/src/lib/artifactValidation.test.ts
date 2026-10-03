import { describe, expect, it } from 'vitest'
import { artifactRequestFields, artifactValidationIssue, originalArtifactOverrides, validationFromSource, FIGSIZE_SOURCE_POLICY } from './artifactValidation'
import type { PanelObject } from '@/types/document'
const guard = {version:1 as const, requiredFrame:'figsize' as const, sourceId:'plot.png', bytesSha256:'a'.repeat(64), sizeBytes:10}
const frame = {gid:'figure',prop:'frame',value:'figsize'}
const panel = () => ({fileId:'plot.png',overrides:[frame],artifactValidation:guard}) as PanelObject
const source = () => ({render_policy:FIGSIZE_SOURCE_POLICY,origin:'static',kind:'png',source_id:'canonical.png',bytes_sha256:guard.bytesSha256,size_bytes:10})
describe('nonsemantic source validation', () => {
 it('legacy stays unchanged; selected requests retain the byte identity', () => {
  expect(artifactRequestFields({fileId:'x',overrides:[]})).toEqual({})
  expect(artifactRequestFields(panel())).toEqual({source_policy:FIGSIZE_SOURCE_POLICY,expected_source:{bytes_sha256:guard.bytesSha256,size_bytes:10}})
 })
 it('uses last-wins FRAME; never fixes a missing or changed frame', () => {
  const p=panel();p.overrides=[{...frame,value:'savefig'},frame];expect(artifactValidationIssue(p)).toBeNull()
  p.overrides.push({...frame,value:'savefig'});expect(artifactValidationIssue(p)).toBe('frame_changed')
  p.overrides=[];const before=structuredClone(p);expect(()=>artifactRequestFields(p)).toThrow();expect(p).toEqual(before)
 })
 it.each([null,undefined,{}, {...guard,version:2}, {...guard,sizeBytes:0}, {...guard,bytesSha256:'bad'}])('present malformed guard refuses: %j', value => {
  const p={...panel(),artifactValidation:value} as PanelObject;expect(artifactValidationIssue(p)).toBe('invalid_guard');expect(()=>artifactRequestFields(p)).toThrow()
 })
 it('old relink is refused despite identical bytes; unknown server response does not admit', () => {
  expect(artifactValidationIssue({...panel(),fileId:'other.png'})).toBe('source_changed')
  for(const s of [undefined,{}, {...source(),render_policy:'selected-artifact-v1'}, {...source(),kind:'pdf'}]) expect(()=>validationFromSource(s,'plot.png')).toThrow()
  expect(validationFromSource(source(),'plot.png')).toEqual(guard)
  expect(()=>validationFromSource({...source(),bytes_sha256:'b'.repeat(64)},'plot.png',guard)).toThrow()
 })
 it('only structural FRAME is omitted from original copy, never user overrides', () => {
  expect(originalArtifactOverrides(panel())).toEqual([])
  expect(originalArtifactOverrides({...panel(),overrides:[{...frame,value:'savefig'},frame]})).toEqual([])
  const p=panel();p.overrides.push({gid:'title',prop:'pos_frac',value:[.2,.3]} as never)
  expect(originalArtifactOverrides(p)).toEqual(p.overrides)
 })
})
