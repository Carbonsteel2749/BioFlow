import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, test, vi } from 'vitest'
import { ResultInsights } from './ResultInsights'

afterEach(() => vi.unstubAllGlobals())

test('按样本展示统一功能板块与单个数据依据折叠区', async () => {
 const legacy = {category:'functional',sample_id:'S1',text:'旧逐表文字',metrics:{},source_ids:['a']}
 vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify({
   task_id:'t',generated_at:'2026-09-23',version:'1.1',method:'deterministic_templates',partial:false,sample_count:1,
   sections:[legacy,legacy],display_sections:[{...legacy,text:'合并文本',
    paragraphs:[{title:'基因家族',text:'家族结果'},{title:'KO',text:'KO结果'},{title:'EC',text:'EC结果'},{title:'通路丰度与覆盖度',text:'通路结果'}],
    common_notes:'统一单位说明',diagnostics:'KO：UNMAPPED=1',source_ids:['a','b']}],
   sources:[{id:'a',path:'gene.tsv',sha256:'abc'},{id:'b',path:'ko.tsv',sha256:'def'}],warnings:[],limitations:[],text:'下载与复制文本',
 }),{status:200,headers:{'Content-Type':'application/json'}})))
 render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><ResultInsights taskId="t" /></QueryClientProvider>)
 const heading=await screen.findByRole('heading',{name:'功能潜力 · S1'})
 expect(screen.getAllByRole('heading',{name:'功能潜力 · S1'})).toHaveLength(1)
 const section=within(heading.closest('article')!)
 expect(section.getAllByRole('heading',{level:4}).map(e=>e.textContent)).toEqual(['基因家族','KO','EC','通路丰度与覆盖度'])
 expect(section.getAllByText('统一单位说明')).toHaveLength(1)
 expect(section.getAllByText('查看数据依据')).toHaveLength(1)
 await userEvent.click(section.getByText('查看数据依据'))
 expect(section.getByText(/gene.tsv/)).toBeVisible()
 expect(section.getByText(/ko.tsv/)).toBeVisible()
 expect(screen.queryByText('旧逐表文字')).not.toBeInTheDocument()
})

test('展示主要发现解释与建议，模型不可用时保留规则重点', async () => {
 const reasoning={finding:'前三物种累计占比73.95%。',interpretation:'少数条目贡献过半份额。',limitation:'不作健康判断。',recommendation:'核对分类率。'}
 const section={category:'taxonomy',sample_id:'S1',source_ids:['a'],text:'完整下载文字',detail_text:'原始结果详情',reasoning}
 vi.stubGlobal('fetch',vi.fn(async(input:RequestInfo | URL)=>new Response(JSON.stringify(String(input).endsWith('/refine') ?
   {status:'fallback',message:'模型不可用，保留规则重点',model:'qwen3:14b',prompt_version:'biointerpretation-1.0',evidence_sha256:'abc',highlights:[{id:'E1',sample_id:'S1',category:'taxonomy',source_ids:['a'],...reasoning}]} :
   {text:'完整下载文字',partial:false,sections:[section],display_sections:[section],sources:[{id:'a',path:'species.tsv',sha256:'abc'}],warnings:[],limitations:[]}
 ),{status:200,headers:{'Content-Type':'application/json'}})))
 render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><ResultInsights taskId="t" /></QueryClientProvider>)
 expect(await screen.findByText('前三物种累计占比73.95%。')).toBeInTheDocument()
 expect(screen.getByText('数据支持的解释')).toBeInTheDocument()
 expect(screen.getByText('核对分类率。')).toBeInTheDocument()
 await userEvent.click(screen.getByRole('button',{name:'AI 提炼重点'}))
 expect(await screen.findByText('模型不可用，保留规则重点')).toBeInTheDocument()
 expect(screen.getAllByText('前三物种累计占比73.95%。')).toHaveLength(2)
 expect(screen.getByRole('button',{name:'复制生成提示词'})).toBeEnabled()
})
