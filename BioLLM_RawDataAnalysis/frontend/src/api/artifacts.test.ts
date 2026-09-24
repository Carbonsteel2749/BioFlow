import { beforeEach, describe, expect, it, vi } from 'vitest'
import { artifactsApi, loadArtifacts } from './artifacts'

describe('artifactsApi', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => [] })))

  it('保留服务端未生成图表的原因', async () => {
    const notice={id:'pcoa',state:'skipped',message:'样本不足',category:'taxonomy',artifact_type:'figure.taxonomy',node_id:'taxonomy_plots',sample_id:null}
    vi.stubGlobal('fetch', vi.fn((url:string)=>Promise.resolve({ok:true,json:async()=>url.endsWith('/figure-notices')?[notice]:[]})))
    expect((await loadArtifacts('task-1')).notices).toEqual([notice])
  })

  it('对任务标识和查询参数编码', async () => {
    await artifactsApi.list('task/a', { artifact_type: 'figure.qc', producer: 'node/a', sample_scope: 'sample' })
    expect(fetch).toHaveBeenCalledWith(
      '/api/tasks/task%2Fa/artifacts?artifact_type=figure.qc&producer=node%2Fa&sample_scope=sample',
      { headers: { Accept: 'application/json' } },
    )
  })

  it('所有文件访问都只生成 Artifact API URL', () => {
    expect(artifactsApi.downloadUrl('../secret')).toBe('/api/artifacts/..%2Fsecret/download')
    expect(artifactsApi.contentUrl('chart-1')).toBe('/api/artifacts/chart-1/download')
  })

  it('将真实 Artifact API 响应转换为结果中心数据源', async () => {
    const artifact = { artifact_id: 'artifact-1' }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => [artifact] }))

    await expect(loadArtifacts('task-1')).resolves.toEqual({ artifacts: [artifact], notices: [] })
    expect(fetch).toHaveBeenCalledWith('/api/tasks/task-1/artifacts', { headers: { Accept: 'application/json' } })
  })
})
