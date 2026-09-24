import { expect, test } from 'vitest'
import { figureTitle, figureCaption } from './catalog'
import type { Artifact } from './types'

test('功能图注区分覆盖度、丰度与历史口径', () => {
  const artifact:Artifact={artifact_id:'figure',task_id:'task',node_id:'functional_plots',producer:'functional_plots',schema_version:'1.0',sample_scope:'cohort',sample_id:null,cohort_id:'task',media_type:'image/png',sha256:'abc',size_bytes:1,downloadable:true,status:'active',created_at:'2026-09-23',derived_from:[],download_url:null,file_name:'functional_pathway_coverage_top20_heatmap.png',artifact_type:'figure.functional',metadata:{analysis_version:'2.0'}}
  expect(figureTitle(artifact)).toContain('通路覆盖度')
  expect(figureCaption(artifact)).toContain('0–1')
  expect(figureCaption({...artifact, metadata:{}})).toContain('历史')
  expect(figureCaption({...artifact,file_name:'functional_ko_top20_composition.png'})).toContain('已命名')
})
