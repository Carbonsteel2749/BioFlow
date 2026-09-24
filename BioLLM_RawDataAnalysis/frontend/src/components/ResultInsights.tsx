import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { tasksApi } from '../api/tasks'
import type { Interpretation, InterpretationHighlights, InterpretationReasoning } from '../api/tasks'
import './result-tools.css'

function Reasoning({ value }: { value: InterpretationReasoning }) {
  return <dl className="insight-reasoning">{([['finding','主要发现'],['interpretation','数据支持的解释'],['limitation','局限'],['recommendation','建议关注']] as const).map(([key,title])=><div key={key}><dt>{title}</dt><dd>{value[key]}</dd></div>)}</dl>
}

export function ResultInsights({ taskId }: { taskId: string }) {
  const query = useQuery({ queryKey: ['interpretation', taskId], queryFn: () => tasksApi.interpretation(taskId), retry: false })
  const [copied, setCopied] = useState('')
  const [busy,setBusy]=useState(false)
  const [ai,setAi]=useState<{taskId:string;baseline:Interpretation;result:InterpretationHighlights}|null>(null)
  const [aiError,setAiError]=useState('')
  const data = query.data
  const valid = data && Array.isArray(data.sections) && Array.isArray(data.sources)
  const sections = Array.isArray(data?.display_sections) ? data.display_sections : data?.sections ?? []
  const copy = async () => {
    try { await navigator.clipboard.writeText(data!.text); setCopied('已复制') }
    catch { setCopied('无法访问剪贴板，请下载文本') }
  }
  const copyPrompt=async()=>{
    try {const prompt=await tasksApi.interpretationPrompt(taskId);await navigator.clipboard.writeText(prompt.text);setCopied('生成提示词已复制（使用样本代号）')}
    catch {setCopied('提示词获取或复制失败，请稍后重试')}
  }
  const refine=async()=>{
    if (!valid) return
    setBusy(true);setAiError('')
    try {const result=await tasksApi.interpretationRefine(taskId);if(!Array.isArray(result.highlights))throw new Error();setAi({taskId,baseline:data,result})}
    catch {setAiError('重点提炼暂时不可用，下方规则解读不受影响。')}
    finally {setBusy(false)}
  }
  const highlights=ai?.taskId===taskId && ai.baseline===data ? ai.result : null
  return <section className="panel result-tools" aria-label="结果初步解读">
    <header><div><h2>结果初步解读</h2><p>基于完整结果表的描述性分析 · 固定规则计算 · 非临床诊断</p></div><button className="btn secondary" onClick={() => query.refetch()} disabled={query.isFetching}>刷新解读</button></header>
    {query.isLoading ? <p>正在核对分析结果和数据来源…</p> : query.error || !valid ? <p role="alert">暂时无法生成解读，已有图表不受影响。</p> : <>
      <p className="insight-boundary">{data.partial ? '任务尚未全部完成：仅解读已成功阶段的数据。' : '分析流程已完成：以下结论仍需结合实验设计与样本背景复核。'}</p>
      <div className="insight-ai-controls"><button className="btn secondary" disabled={busy} onClick={refine}>{busy?'正在提炼（最多约 45 秒）…':'AI 提炼重点'}</button><button className="btn secondary" onClick={copyPrompt}>复制生成提示词</button><p>可选：本地模型仅从已核验的发现中选择、排序重点，不新增事实；不影响完整解读和结果下载。</p></div>
      {aiError && <p role="status">{aiError}</p>}
      {highlights && <aside className="insight-ai" aria-label="解读重点"><p role="status">{highlights.message}</p>{highlights.highlights.map(item=><div key={item.id}><b>{item.sample_id} · {({qc:'质控与过滤',taxonomy:'物种组成',functional:'功能潜力'} as Record<string,string>)[item.category] ?? item.category}</b><Reasoning value={item} /></div>)}</aside>}
      {sections.length ? sections.map((section, index) => <article className="insight-section" key={`${section.category}-${section.sample_id}-${index}`}>
        <h3>{({ qc:'质控与过滤', taxonomy:'物种组成', functional:'功能潜力' } as Record<string,string>)[section.category] ?? section.category} · {section.sample_id}</h3>
        {section.paragraphs ? <>
          {section.paragraphs.map(paragraph=><div className="insight-subsection" key={paragraph.title}><h4>{paragraph.title}</h4><p>{paragraph.text}</p></div>)}
          {section.common_notes && <p className="insight-common"><b>单位与解释边界：</b>{section.common_notes}</p>}
          {section.diagnostics && <p className="insight-diagnostics"><b>诊断类别（分来源）：</b>{section.diagnostics}</p>}
        </> : <p>{section.detail_text ?? section.text}</p>}
        {section.reasoning && <Reasoning value={section.reasoning} />}
        <details><summary>查看数据依据</summary>{section.source_ids.map(id => { const source=data.sources.find(s=>s.id===id); return source ? <p className="insight-source" key={id}>{source.path}<br />SHA-256：{source.sha256}</p> : null })}</details>
      </article>) : <p>未获得可用于解读的已完成阶段数据。</p>}
      <details className="insight-limitations" open><summary>分析边界与待核实事项</summary><ul>{[...data.warnings,...data.limitations].map((line,index)=><li key={index}>{line}</li>)}</ul></details>
      <footer><button className="btn secondary" onClick={copy}>复制解读</button><a className="btn secondary" href={tasksApi.interpretationDownloadUrl(taskId)} download>下载解读</a><span role="status">{copied}</span></footer>
    </>}
  </section>
}
