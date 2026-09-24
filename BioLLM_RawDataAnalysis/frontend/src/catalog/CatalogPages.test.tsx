import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, test, vi } from 'vitest'
import App from '../App'

const dataset = { id: 'a'.repeat(32), name: '医院批次 A', stage: 'raw', source: 'uploaded', status: 'available', reason: null, created_at: '2026-09-23', sample_count: 1, size_bytes: 80, task_count: 0 }
const file = {name:'S01_R1.fastq.gz',size_bytes:40,upload_id:'r1',checksum:'sha256:abc',validation:'upload_initial_check',availability:'available'}
const page = (items: unknown[], total=items.length) => ({items,total,limit:20,offset:0})
const response = (value:unknown,status=200) => Promise.resolve(new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}}))
function route(path:string) {
  const client=new QueryClient({defaultOptions:{queries:{retry:false,gcTime:0}}})
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><App/></MemoryRouter></QueryClientProvider>)
  return client
}
afterEach(()=>vi.unstubAllGlobals())

test('删除数据集后返回目录，不再请求已删除的详情',async()=>{
  let deleted=false,staleReads=0
  vi.stubGlobal('fetch',vi.fn((input:RequestInfo|URL,init?:RequestInit)=>{
    const url=String(input)
    if(url.includes('/cleanup-preview'))return response({token:'plan',estimated_bytes:80,scope:['manifests/a.csv'],dataset_cleanup:{will_delete:true,reasons:[]}})
    if(url.endsWith('/cleanup')&&init?.method==='POST'){deleted=true;return response({status:'deleted'})}
    if(url.includes(`/datasets/${dataset.id}`)) {
      if(deleted){staleReads++;return response({detail:'不存在'},404)}
      return response({...dataset,samples:[],tasks:[],verification_note:'初检'})
    }
    return response(page([]))
  }))
  route(`/datasets/${dataset.id}`)
  await userEvent.click(await screen.findByRole('button',{name:'删除数据集'}))
  await screen.findByText('可以清理此数据集')
  await userEvent.type(screen.getByLabelText('输入完整数据集 ID'),dataset.id)
  await userEvent.click(screen.getByRole('checkbox',{name:/我已确认范围/}))
  await userEvent.click(screen.getByRole('button',{name:'确认删除数据集'}))
  expect(await screen.findByRole('heading',{name:'数据与样本'})).toBeInTheDocument()
  expect(deleted).toBe(true)
  expect(staleReads).toBe(0)
})

test('数据集删除先预览，共享引用存在时禁止确认',async()=>{
  vi.stubGlobal('fetch',vi.fn((input:RequestInfo|URL)=>{
    if(String(input).includes('cleanup-preview'))return response({token:'plan',estimated_bytes:0,scope:[],dataset_cleanup:{will_delete:false,reasons:['仍被任务 T1 引用，保留数据']}})
    return response({...dataset,samples:[],tasks:[],verification_note:'初检'})
  }))
  route(`/datasets/${dataset.id}`)
  await userEvent.click(await screen.findByRole('button',{name:'删除数据集'}))
  expect(await screen.findByText('仍被任务 T1 引用，保留数据')).toBeInTheDocument()
  expect(screen.getByRole('button',{name:'确认删除数据集'})).toBeDisabled()
})

test('数据列表按名称查询并进入详情，保留正确的校验含义',async()=>{
  vi.stubGlobal('fetch',vi.fn((input:RequestInfo|URL)=>{
    const url=new URL(String(input),'http://localhost')
    if(url.pathname==='/api/datasets-unpaired-uploads') return response(page([]))
    if(url.pathname===`/api/datasets/${dataset.id}`) return response({...dataset,samples:[{sample_id:'S01',read1:file,read2:{...file,name:'S01_R2.fastq.gz'}}],tasks:[],verification_note:'上传初检仅检查首条 FASTQ 记录，不代表完整配对校验或质控通过。'})
    if(url.pathname==='/api/datasets') return response(page(url.searchParams.get('q')==='不存在'?[]:[dataset]))
    return response({reads_analysis:true,file_uploads:true})
  }))
  route('/datasets')
  expect(await screen.findByRole('link',{name:'医院批次 A'})).toBeInTheDocument()
  await userEvent.type(screen.getByRole('textbox',{name:'搜索数据集或样本'}),'不存在')
  await userEvent.click(screen.getByRole('button',{name:'搜索'}))
  expect(await screen.findByText('没有匹配的数据集')).toBeInTheDocument()
  await userEvent.clear(screen.getByRole('textbox',{name:'搜索数据集或样本'}))
  await userEvent.click(screen.getByRole('button',{name:'搜索'}))
  await userEvent.click(await screen.findByRole('link',{name:'医院批次 A'}))
  expect(await screen.findByText(/上传初检仅检查首条/)).toBeInTheDocument()
  expect(screen.getByText('S01_R1.fastq.gz')).toBeInTheDocument()
  expect(screen.getByRole('link',{name:'使用此数据新建分析'})).toHaveAttribute('href',`/tasks/new?dataset=${dataset.id}`)
})

test('已有数据进入创建页只预填且提交时携带数据集身份',async()=>{
  let submitted:Record<string,unknown>|undefined
  vi.stubGlobal('fetch',vi.fn((input:RequestInfo|URL,init?:RequestInit)=>{
    const url=String(input)
    if(url.includes('/reuse')) return response({dataset_id:dataset.id,name:dataset.name,manifest_path:'/incoming/a.csv',sample_count:1})
    if(url==='/api/tasks'&&init?.method==='POST') {
      submitted=JSON.parse(String(init.body))
      return response({detail:'测试拒绝启动'},422)
    }
    return response({reads_analysis:true,file_uploads:true,mag_analysis:false,mag_unavailable_reason:'未配置'})
  }))
  route(`/tasks/new?dataset=${dataset.id}`)
  expect(await screen.findByText('医院批次 A')).toBeInTheDocument()
  expect(submitted).toBeUndefined()
  await userEvent.click(screen.getByRole('button',{name:'创建并开始分析'}))
  await waitFor(()=>expect(submitted).toMatchObject({dataset_id:dataset.id,manifest_path:'/incoming/a.csv'}))
  expect(await screen.findByText('测试拒绝启动')).toBeInTheDocument()
})

test('不可用数据集禁止复用，接口错误不会显示为空列表',async()=>{
  vi.stubGlobal('fetch',vi.fn((input:RequestInfo|URL)=>{
    if(String(input).includes('/reuse')) return response({detail:'配对文件已缺失'},409)
    return response({reads_analysis:true,file_uploads:true})
  }))
  route(`/tasks/new?dataset=${dataset.id}`)
  expect(await screen.findByText('配对文件已缺失')).toBeInTheDocument()
  expect(screen.getByRole('button',{name:'创建并开始分析'})).toBeDisabled()
})

test('结果中心显示来源与阶段性标记并按样本过滤和预览',async()=>{
  const item={artifact_id:'art1',task_id:'task1',task_status:'paused',task_kind:'standard',partial:true,has_result_archive:false,run_parameters:{threads:4},node_id:'taxonomy',producer:'taxonomy',artifact_type:'figure.taxonomy',schema_version:'1.0',sample_scope:'sample',sample_id:'S01',cohort_id:null,media_type:'image/png',file_name:'plot.png',sha256:'abc',size_bytes:100,metadata:{title:'物种组成图'},downloadable:true,status:'active',created_at:'2026-09-23',derived_from:[],download_url:'/api/artifacts/art1/download'}
  vi.stubGlobal('fetch',vi.fn((input:RequestInfo|URL)=>{
    const url=new URL(String(input),'http://localhost')
    if(url.pathname==='/api/result-types') return response(['figure.taxonomy'])
    if(url.pathname==='/api/results') return response(page(url.searchParams.get('sample_id')==='other'?[]:[item]))
    return response({})
  }))
  route('/results')
  expect(await screen.findByText('物种组成图')).toBeInTheDocument()
  expect(screen.getByText('阶段性结果')).toBeInTheDocument()
  expect(screen.getByRole('link',{name:'来源任务 task1'})).toHaveAttribute('href','/tasks/task1')
  await userEvent.click(screen.getByRole('button',{name:'预览'}))
  const dialog=screen.getByRole('dialog',{name:'产物详情'})
  expect(within(dialog).getByRole('img',{name:'物种组成图'})).toHaveAttribute('src','/api/artifacts/art1/download')
  await userEvent.click(within(dialog).getByRole('button',{name:'关闭'}))
  await userEvent.type(screen.getByRole('textbox',{name:'样本 ID'}),'other')
  await userEvent.click(screen.getByRole('button',{name:'筛选'}))
  expect(await screen.findByText('没有匹配的结果')).toBeInTheDocument()
})

test('导航分组且移动菜单选择入口后关闭',async()=>{
  vi.stubGlobal('fetch',vi.fn((input:RequestInfo|URL)=>response(String(input).includes('/datasets')?page([]):[])))
  route('/tasks')
  await userEvent.click(screen.getByRole('button',{name:'打开导航'}))
  await userEvent.click(screen.getByRole('link',{name:'数据与样本'}))
  expect(await screen.findByRole('heading',{name:'数据与样本'})).toBeInTheDocument()
  expect(screen.queryByRole('button',{name:'关闭导航'})).not.toBeInTheDocument()
  expect(screen.getByText('分析工作')).toBeInTheDocument()
  expect(within(screen.getByRole('navigation')).getByText('数据与成果')).toBeInTheDocument()
})

test('结果刷新先登记新图表且登记失败仍可读取已有结果',async()=>{
  let refreshed=false
  vi.stubGlobal('fetch',vi.fn((input:RequestInfo|URL)=>{
    const url=new URL(String(input),'http://localhost')
    if(url.pathname==='/api/results/refresh') {refreshed=true;return response({processed:1,next_offset:1,remaining:0,warnings:[]})}
    if(url.pathname==='/api/result-types')return response([])
    return response(page([],refreshed?7:0))
  }))
  route('/results')
  expect(await screen.findByText(/共 7 条/)).toBeInTheDocument()
})
