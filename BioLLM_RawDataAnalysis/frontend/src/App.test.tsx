import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, test, vi } from 'vitest'
import App from './App'
import { makeTask, parameters, step } from './test/fixtures'
import type { StepName } from './types'

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
}

function renderRoute(route: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[route]}><App /></MemoryRouter></QueryClientProvider>)
}

afterEach(() => vi.unstubAllGlobals())

test('失败摘要展示阶段与原因，诊断详情折叠并可直达日志', async () => {
  const failed=makeTask({status:'paused',current_step:'host_depletion',
    error_message:'原始流程错误（host_depletion，exit_code=65）：failure_stage=threshold reason=retained reads violate threshold\nAI 辅助诊断：本地错误诊断服务暂时不可用。\n建议操作：请人工核对保留量阈值。',
    steps:[step('validate','succeeded'),step('host_depletion','failed')],alerts:[]})
  vi.stubGlobal('fetch',vi.fn((input:RequestInfo|URL)=>{
    const url=String(input)
    if(url.includes('/logs?'))return json({step:'host_depletion',text:'complete redacted stage log'})
    if(url.endsWith('/artifacts'))return json([])
    return json(failed)
  }))
  renderRoute(`/tasks/${failed.id}`)
  await screen.findByText('分析任务未能完成')
  expect(screen.getByText('失败阶段：去除宿主序列')).toBeInTheDocument()
  expect(screen.getByText('failure_stage=threshold reason=retained reads violate threshold',{exact:true})).toBeVisible()
  const disclosure=screen.getByText('完整错误与辅助诊断').closest('details')!
  expect(disclosure).not.toHaveAttribute('open')
  await userEvent.click(screen.getByText('完整错误与辅助诊断'))
  expect(disclosure).toHaveAttribute('open')
  await userEvent.click(screen.getByRole('button',{name:'查看失败阶段日志'}))
  expect(await screen.findByText('complete redacted stage log')).toBeVisible()
})

test('等待诊断的暂停任务会继续刷新直到诊断完成', async () => {
  const pending=makeTask({status:'paused',current_step:'host_depletion',error_message:'AI 辅助诊断处理中',alerts:[]})
  let calls=0
  vi.stubGlobal('fetch',vi.fn((input:RequestInfo|URL)=>{
    const url=String(input)
    if(url.endsWith(`/api/tasks/${pending.id}`))return json(++calls===1 ? pending : {...pending,error_message:'诊断已完成：请检查保留量阈值。'})
    if(url.endsWith('/artifacts'))return json([])
    return json({})
  }))
  renderRoute(`/tasks/${pending.id}`)
  await screen.findByText('AI 辅助诊断处理中',{selector:'.failure-cause'})
  expect(await screen.findByText('诊断已完成：请检查保留量阈值。',{selector:'.failure-cause'},{timeout:4000})).toBeVisible()
})

test('长告警诊断默认折叠，展开后可查阅完整内容', async () => {
  const message='原始流程错误：阈值未满足。'+'详细诊断信息。'.repeat(60)
  const task=makeTask({status:'paused',alerts:[{level:'error',message,created_at:'2026-09-23T00:00:00Z'}]})
  vi.stubGlobal('fetch',vi.fn(()=>json(task)))
  renderRoute(`/tasks/${task.id}`)
  const text=await screen.findByText(message)
  const details=text.closest('details')
  expect(details).not.toBeNull()
  expect(details).not.toHaveAttribute('open')
  await userEvent.click(screen.getByText('查看告警详情'))
  expect(details).toHaveAttribute('open')
})

test('失败摘要不重复显示状态记录中的完整运行命令', async () => {
  const task=makeTask({status:'paused',current_step:'host_depletion',error_message:
    '原始流程错误（host_depletion，exit_code=65）：[ERROR] [step=host_depletion] failure_stage=threshold reason=retained reads violate configured host-depletion thresholds\n[ERROR] threshold: retained reads violate configured host-depletion thresholds bowtie2 --threads 4 | samtools view\nAI 辅助诊断：不可用。',alerts:[]})
  vi.stubGlobal('fetch',vi.fn(()=>json(task)))
  renderRoute(`/tasks/${task.id}`)
  await screen.findByText('分析任务未能完成')
  const cause=document.querySelector('.failure-cause')!
  expect(cause.textContent).toContain('retained reads violate')
  expect(cause.textContent).not.toContain('bowtie2 --threads')
  expect(document.querySelector('.failure-summary details')?.textContent).toContain('bowtie2 --threads')
})

test('任务可重命名，列表立即更新并支持按新名称搜索', async () => {
  let task=makeTask()
  vi.stubGlobal('fetch',vi.fn((_input:RequestInfo|URL,options?:RequestInit)=>{
    if(options?.method==='PATCH') {
      task={...task,...JSON.parse(String(options.body))}
      return json(task)
    }
    return json([task])
  }))
  renderRoute('/tasks')
  await userEvent.click(await screen.findByRole('button',{name:/重命名任务/}))
  const input=screen.getByRole('textbox',{name:'任务名称'})
  await userEvent.clear(input)
  await userEvent.type(input,'肠道菌群初步分析')
  await userEvent.click(screen.getByRole('button',{name:'保存名称'}))
  expect(await screen.findByText('肠道菌群初步分析')).toBeInTheDocument()
  await userEvent.type(screen.getByPlaceholderText(/搜索任务/),'肠道菌群')
  expect(screen.getByText('肠道菌群初步分析')).toBeInTheDocument()
})

test('任务中心仅保留导航中的新建分析入口且可以进入创建页', async () => {
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    if (String(input).endsWith('/api/tasks')) return json([makeTask()])
    return json({ reads_analysis: true, mag_analysis: false, mag_unavailable_reason: '未配置', database_profile: 'test' })
  }))
  renderRoute('/tasks')
  expect(await screen.findByText('demo.csv')).toBeInTheDocument()
  expect(screen.getAllByRole('link', { name: '新建分析' })).toHaveLength(1)
  const entry = within(screen.getByRole('navigation')).getByRole('link', { name: '新建分析' })
  expect(entry).toHaveAttribute('href', '/tasks/new')
  await userEvent.click(entry)
  expect(await screen.findByRole('switch', { name: '启用 MAG 分析' })).toBeInTheDocument()
})

test('任务详情展示来源明确的初步解读并可预览清理日志', async () => {
  const task = makeTask({ status: 'paused' })
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url=String(input)
    if(url.endsWith('/interpretation')) return json({ text:'S1 的描述性分析，不代表基因表达或代谢活性。', partial:true, sections:[{category:'functional',sample_id:'S1',text:'S1 的描述性分析，不代表基因表达或代谢活性。',source_ids:['src1']}], sources:[{id:'src1',path:'functional_annotation/S1/read_ko.tsv',sha256:'abc'}],warnings:[],limitations:['未经组间检验，不报告显著差异。'] })
    if(url.endsWith('/log-files/cleanup-preview')) return json({token:'signed',estimated_bytes:10,files:[{id:'log1',name:'S1.fastp.log'}]})
    if(url.endsWith('/log-files')) return json({files:[{id:'log1',name:'S1.fastp.log',relative_path:'outputs/task/logs/S1.fastp.log',step:'fastp',size_bytes:10,selectable:true,modified_at:'2026-09-23',reason:'',deleted:false}],scope:'保护分析数据'})
    if(url.endsWith('/artifacts')) return json([])
    return json(task)
  }))
  renderRoute(`/tasks/${task.id}`)
  expect(await screen.findByRole('heading', { name:'结果初步解读' })).toBeInTheDocument()
  expect(await screen.findByText('S1 的描述性分析，不代表基因表达或代谢活性。')).toBeInTheDocument()
  expect(screen.getByRole('link', {name:'下载解读'})).toHaveAttribute('href',`/api/tasks/${task.id}/interpretation/download`)
  await userEvent.click(screen.getByRole('button',{name:'管理日志'}))
  await userEvent.click(await screen.findByRole('checkbox',{name:'选择 S1.fastp.log'}))
  await userEvent.click(screen.getByRole('button',{name:'预览清理范围'}))
  expect(await screen.findByText(/将删除 1 个日志/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button',{name:'返回选择'}))
  expect(screen.queryByRole('button',{name:'确认删除日志'})).not.toBeInTheDocument()
})

test('暂停任务仍能查看阶段图表并明确不是完整报告', async () => {
  const task = makeTask({ status: 'paused', current_step: 'report' })
  const artifact = { artifact_id: 'stage-figure', artifact_type: 'figure.taxonomy', status: 'active',
    file_name: 'taxonomy.png', media_type: 'image/png', downloadable: true, size_bytes: 10,
    sha256: 'test', producer: 'taxonomy_plots', metadata: { title: '物种组成图' } }
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => String(input).endsWith('/artifacts') ? json([artifact]) : json(task)))
  renderRoute(`/tasks/${task.id}`)
  expect(await screen.findByText(/阶段性结果，非完整报告/)).toBeInTheDocument()
  await userEvent.click(await screen.findByRole('button', { name: /^物种/ }))
  expect(await screen.findByText('物种组成图')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /预览/ }))
  expect(screen.getByRole('img', { name: '物种组成图' })).toHaveAttribute('src', '/api/artifacts/stage-figure/download')
  await userEvent.click(screen.getByRole('button', { name: '放大图片' }))
  expect(screen.getByRole('button', { name: '适应窗口' })).toBeInTheDocument()
})

test('普通任务只展示七个核心阶段', async () => {
  const task = makeTask()
  vi.stubGlobal('fetch', vi.fn(() => json(task)))
  renderRoute(`/tasks/${task.id}`)

  expect(await screen.findByRole('heading', { name: 'demo.csv' })).toBeInTheDocument()
  const pipeline = screen.getByRole('heading', { name: '分析流程' }).closest('section')!
  for (const label of ['上传与校验','原始质量控制','过滤与质控','去除宿主序列','物种组成分析','功能组成分析','汇总分析报告']) {
    expect(within(pipeline).getByText(label)).toBeInTheDocument()
  }
  expect(within(pipeline).queryByText('宏基因组组装')).not.toBeInTheDocument()
})

test('MAG 不可用时禁用开关、展示原因且不提供数据库路径输入', async () => {
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    expect(String(input)).toContain('/api/capabilities')
    return json({ reads_analysis: true, mag_analysis: false, mag_unavailable_reason: 'MAG annotation databases are incomplete', database_profile: 'hospital-core' })
  }))
  renderRoute('/tasks/new')

  const toggle = await screen.findByRole('switch', { name: '启用 MAG 分析' })
  expect(toggle).toBeDisabled()
  expect(await screen.findByText('MAG annotation databases are incomplete')).toBeInTheDocument()
  expect(screen.queryByPlaceholderText('留空以使用服务器默认配置')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /高级数据库配置/ }))
  expect(await screen.findByText('hospital-core')).toBeInTheDocument()
  expect(screen.getByText('数据库路径由服务器统一管理')).toBeInTheDocument()
})

test('MAG 可用时可启用动态阶段并展示专属参数', async () => {
  vi.stubGlobal('fetch', vi.fn(() => json({
    reads_analysis: true,
    mag_analysis: true,
    mag_unavailable_reason: null,
    database_profile: 'hospital-mags',
  })))
  renderRoute('/tasks/new')

  const toggle = await screen.findByRole('switch', { name: '启用 MAG 分析' })
  await waitFor(() => expect(toggle).toBeEnabled())
  await userEvent.click(toggle)

  expect(screen.getByText('13 个阶段 · 自动顺序执行')).toBeInTheDocument()
  expect(screen.getByText('MAG 运行参数')).toBeInTheDocument()
  expect(screen.getByText('宏基因组组装')).toBeInTheDocument()
  expect(screen.getByText('MAG 分类与注释')).toBeInTheDocument()
  expect(screen.getByRole('spinbutton', { name: /MAG 计算线程/ })).toHaveValue(8)
})

test('失败任务展示告警、失败原因并可打开对应阶段日志', async () => {
  const failed = makeTask({
    status: 'failed', current_step: 'host_depletion', error_message: 'Nextflow exited with status 1',
    steps: [step('validate','succeeded'),step('fastqc_raw','succeeded'),step('fastp','succeeded'),step('host_depletion','failed'),step('taxonomy'),step('functional_annotation'),step('report')],
    alerts: [{ level: 'error', message: '宿主去除失败，请检查索引。', created_at: '2026-07-27T01:05:00Z' }],
  })
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => String(input).includes('/logs?') ? json({ step: 'host_depletion', text: 'redacted bowtie2 error' }) : json(failed)))
  renderRoute(`/tasks/${failed.id}`)

  expect(await screen.findByText('Nextflow exited with status 1',{selector:'.failure-cause'})).toBeVisible()
  expect(screen.getByText('宿主去除失败，请检查索引。')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: /去除宿主序列/ }))
  expect(await screen.findByText(/redacted bowtie2 error/)).toBeInTheDocument()
})

test('完成任务提供指向结果接口的下载链接', async () => {
  const completed = makeTask({
    status: 'completed', current_step: 'report', finished_at: '2026-07-27T02:00:00Z',
    result_archive: '/srv/outputs/task/deliverables/task.tar.gz',
    steps: (['validate','fastqc_raw','fastp','host_depletion','taxonomy','functional_annotation','report'] as StepName[]).map(name => step(name, 'succeeded')),
  })
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => String(input).endsWith('/artifacts') ? json([]) : json(completed)))
  renderRoute(`/tasks/${completed.id}`)

  const link = await screen.findByRole('link', { name: '下载结果包' })
  expect(link).toHaveAttribute('href', `/api/tasks/${completed.id}/results`)
  expect(link).toHaveAttribute('download')
})

test('完成任务详情挂载真实 Artifact 结果中心', async () => {
  const completed = makeTask({
    status: 'completed', current_step: 'report', finished_at: '2026-07-27T02:00:00Z',
    result_archive: '/srv/outputs/task/deliverables/task.tar.gz',
    steps: (['validate','fastqc_raw','fastp','host_depletion','taxonomy','functional_annotation','report'] as StepName[]).map(name => step(name, 'succeeded')),
  })
  const artifact = {
    artifact_id: 'real-artifact-1', artifact_type: 'figure.qc', status: 'active',
    file_name: 'qc.png', media_type: 'image/png', downloadable: true, size_bytes: 1,
    sha256: 'sha256', producer: 'fastqc', sample_id: 'S01', metadata: { title: '真实 QC 图' },
  }
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/capabilities')) return json({ result_preview: false })
    if (url.endsWith('/artifacts')) return json([artifact])
    return json(completed)
  })
  vi.stubGlobal('fetch', fetchMock)
  renderRoute(`/tasks/${completed.id}`)

  expect(await screen.findByRole('heading', { name: '任务结果中心' })).toBeInTheDocument()
  expect(screen.getByText('真实 QC 图')).toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith(`/api/tasks/${completed.id}/artifacts`))).toBe(true)
})

test('后端支持时可确认取消运行任务', async () => {
  const running = makeTask()
  const cancelled = makeTask({ status: 'cancelled', current_step: null, finished_at: '2026-07-27T01:10:00Z' })
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/capabilities')) return json({ reads_analysis: true, mag_analysis: false, mag_unavailable_reason: null, database_profile: 'core', task_cancellation: true })
    if (url.endsWith('/cancel') && init?.method === 'POST') return json(cancelled)
    return json(running)
  }))
  renderRoute(`/tasks/${running.id}`)

  const cancel = await screen.findByRole('button', { name: '取消任务' })
  expect(cancel).toBeEnabled()
  await userEvent.click(cancel)
  await userEvent.click(screen.getByRole('button', { name: '确认' }))
  expect((await screen.findAllByText('已取消')).length).toBeGreaterThanOrEqual(1)
})

test('重新加载页面后从后端恢复任务阶段和进度', async () => {
  const task = makeTask({ current_step: 'host_depletion', steps: [step('validate','succeeded'),step('fastqc_raw','succeeded'),step('fastp','succeeded'),step('host_depletion','running'),step('taxonomy'),step('functional_annotation'),step('report')] })
  const fetchMock = vi.fn((input: RequestInfo | URL) => String(input).endsWith('/capabilities') ? json({ reads_analysis: true, mag_analysis: false, mag_unavailable_reason: null, database_profile: 'core' }) : json(task))
  vi.stubGlobal('fetch', fetchMock)
  const first = renderRoute(`/tasks/${task.id}`)
  expect(await screen.findByText('去宿主', { selector: '.detail-stats b' })).toBeInTheDocument()
  first.unmount()

  renderRoute(`/tasks/${task.id}`)
  expect(await screen.findByText('去宿主', { selector: '.detail-stats b' })).toBeInTheDocument()
  expect(fetchMock.mock.calls.filter(([url]) => String(url).includes(`/tasks/${task.id}`)).length).toBeGreaterThanOrEqual(2)
})

test('正式导航可以进入节点式流程设计器', async () => {
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
    const url = String(input)
    if (url.endsWith('/workflows/registry')) {
      return json({ registry_version: '1.0.0', nodes: [] })
    }
    if (url.endsWith('/workflows/templates')) return json([])
    return json({}, 404)
  }))

  renderRoute('/workflows/new')

  expect(await screen.findByRole('heading', { name: '节点式分析工作流' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /流程设计器/ })).toHaveClass('active')
  expect(screen.getByText('请先上传双端 FASTQ，再设计或载入分析流程。')).toBeInTheDocument()
})
