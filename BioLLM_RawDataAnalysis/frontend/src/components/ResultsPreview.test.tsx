import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import { ResultsPreview } from './ResultsPreview'

function json(body: unknown, status = 200) { return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })) }
function renderPreview(supported = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<QueryClientProvider client={client}><ResultsPreview taskId="task-1" supported={supported} /></QueryClientProvider>)
}
afterEach(() => vi.unstubAllGlobals())

test('未知单位保留原值且不加百分号，功能表展示来源口径', async () => {
  const measurement={status:'unknown',source_unit:'HUMAnN_reported',display_unit:'unknown',note:'单位未确认，保留来源原值，不转换为百分比。'}
  vi.stubGlobal('fetch',vi.fn(()=>json({multiqc_url:null,qc_summary:[],taxonomy_top:[{name:'Unknown species',abundance:0.5}],taxonomy_measurement:measurement,ko:{columns:['function_id','abundance'],rows:[['K1',0.5]],measurement},ec:null,pathways:null})))
  renderPreview()
  await screen.findByRole('heading',{name:'结果在线预览'})
  await userEvent.click(screen.getByRole('button',{name:'物种丰度 Top 10'}))
  expect(screen.getByText('0.50')).toBeInTheDocument()
  expect(screen.queryByText('0.50%')).not.toBeInTheDocument()
  expect(screen.getByText(measurement.note)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button',{name:'KO'}))
  expect(screen.getByText(measurement.note)).toBeInTheDocument()
})

test('切换样本时清除旧预览，迟到的响应不会覆盖当前样本', async () => {
  const payload = (sample: string) => ({sample_ids:['S01','S02'],selected_sample_id:sample,
    multiqc_url:null,qc_summary:[],taxonomy_top:[{sample_id:sample,name:sample==='S01'?'First species':'Second species',abundance:20}],
    ko:{columns:['sample_id','function_id'],rows:[[sample,sample==='S01'?'FIRST_KO':'SECOND_KO']]},ec:null,pathways:null})
  let finishSecond!: (r: Response) => void
  let secondReady=false
  vi.stubGlobal('fetch', vi.fn((url: string) => url.includes('sample_id=S02')
    ? secondReady ? json(payload('S02')) : new Promise<Response>(resolve=>{finishSecond=resolve}) : json(payload('S01'))))
  renderPreview()
  const select=await screen.findByRole('combobox',{name:'预览样本'})
  await userEvent.click(screen.getByRole('button',{name:'物种丰度 Top 10'}))
  expect(screen.getByText('First species')).toBeInTheDocument()
  await userEvent.selectOptions(select,'S02')
  expect(screen.queryByText('First species')).not.toBeInTheDocument()
  await userEvent.selectOptions(screen.getByRole('combobox',{name:'预览样本'}),'S01')
  expect(await screen.findByText('First species')).toBeInTheDocument()
  secondReady=true
  await act(async()=>finishSecond(new Response(JSON.stringify(payload('S02')),{status:200})))
  expect(screen.queryByText('Second species')).not.toBeInTheDocument()
  await userEvent.selectOptions(screen.getByRole('combobox',{name:'预览样本'}),'S02')
  expect(await screen.findByText('Second species')).toBeInTheDocument()
  expect(screen.queryByText('First species')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button',{name:'KO'}))
  expect(screen.getByText('SECOND_KO')).toBeInTheDocument()
  expect(screen.queryByText('FIRST_KO')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button',{name:'EC'}))
  expect(screen.getByText('暂无 EC 表')).toBeInTheDocument()
})

test('报告不存在时保留明确提示而不隐藏下载能力', async () => {
  vi.stubGlobal('fetch', vi.fn(() => json({ detail: 'report not found' }, 404)))
  renderPreview()
  expect(await screen.findByRole('heading', { name: '在线报告尚未生成' })).toBeInTheDocument()
  expect(screen.getByText(/仍可以下载完整结果包/)).toBeInTheDocument()
})

test('展示质控、物种 Top10 和功能表基础预览', async () => {
  vi.stubGlobal('fetch', vi.fn(() => json({
    multiqc_url: '/api/tasks/task-1/reports/multiqc',
    qc_summary: [{ sample_id: 'S01', raw_reads: 10000, clean_reads: 8200, host_removed_pct: 3.25 }],
    taxonomy_top: [{ name: 'Bacteroides vulgatus', abundance: 24.5 }],
    ko: { columns: ['KO', 'abundance'], rows: [['K00001', 12.2]], total_rows: 1 },
    ec: { columns: ['EC', 'abundance'], rows: [['1.1.1.1', 8.1]], total_rows: 1 },
    pathways: { columns: ['pathway', 'abundance'], rows: [['PWY-001', 4.2]], total_rows: 1 },
  })))
  renderPreview()

  expect(await screen.findByText('S01')).toBeInTheDocument()
  expect(screen.getByText('10,000')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '打开 MultiQC 报告' })).toHaveAttribute('href', '/api/tasks/task-1/reports/multiqc')
  await userEvent.click(screen.getByRole('button', { name: '物种丰度 Top 10' }))
  expect(screen.getByText('Bacteroides vulgatus')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'KO' }))
  expect(screen.getByText('K00001')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: 'EC' }))
  expect(screen.getByText('1.1.1.1')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '通路' }))
  expect(screen.getByText('PWY-001')).toBeInTheDocument()
})
