import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertCircle, BarChart3, ExternalLink, FileQuestion, LoaderCircle, Table2 } from 'lucide-react'
import { ApiError, tasksApi } from '../api/tasks'
import type { ResultPreview, TablePreview } from '../types'

type Tab = 'qc' | 'taxonomy' | 'ko' | 'ec' | 'pathways'

function formatNumber(value: number) { return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value) }

function DataTable({ data, empty }: { data: TablePreview | null; empty: string }) {
  return <>{data?.measurement && <p className="measurement-note" data-status={data.measurement.status}>{data.measurement.note}</p>}<TableRows data={data} empty={empty} /></>
}

function TableRows({ data, empty }: { data: TablePreview | null; empty: string }) {
  if (!data || !data.rows.length) return <div className="result-empty compact"><FileQuestion /><b>{empty}</b><span>该结果可能尚未生成或不包含可展示的数据。</span></div>
  return <div className="preview-table-wrap"><table className="preview-table"><thead><tr>{data.columns.map(column => <th key={column}>{column}</th>)}</tr></thead><tbody>{data.rows.slice(0, 10).map((row, index) => <tr key={index}>{row.map((cell, cellIndex) => <td key={cellIndex}>{cell ?? '—'}</td>)}</tr>)}</tbody></table>{data.total_rows != null && data.total_rows > data.rows.length && <p className="table-caption">显示前 {Math.min(10, data.rows.length)} 行，共 {formatNumber(data.total_rows)} 行；完整结果请下载结果包。</p>}</div>
}

function QcPanel({ preview }: { preview: ResultPreview }) {
  if (!preview.qc_summary.length) return <div className="result-empty compact"><FileQuestion /><b>暂无质控汇总</b><span>报告包中未发现可读取的 reads 统计。</span></div>
  return <div className="qc-preview-grid">{preview.qc_summary.map(row => {
    const retained = row.raw_reads ? Math.max(0, Math.min(100, row.clean_reads * 100 / row.raw_reads)) : 0
    return <div className="sample-qc-card" key={row.sample_id}><div><b>{row.sample_id}</b><span>保留 {retained.toFixed(1)}%</span></div><div className="reads-comparison"><section><span>质控前</span><b>{formatNumber(row.raw_reads)}</b></section><i>→</i><section><span>质控后</span><b>{formatNumber(row.clean_reads)}</b></section></div><div className="retained-bar"><i style={{ width: `${retained}%` }} /></div><p>去宿主过滤移除比例（read pairs） <b>{row.host_removed_pct == null ? '未获得' : `${row.host_removed_pct.toFixed(2)}%`}</b></p></div>
  })}</div>
}

function TaxonomyPanel({ preview }: { preview: ResultPreview }) {
  return <><p className="measurement-note" data-status={preview.taxonomy_measurement?.status ?? 'unknown'}>{preview.taxonomy_measurement?.note ?? '单位未确认，保留接口原值，不标注百分比。'}</p><TaxonomyRows preview={preview} /></>
}

function TaxonomyRows({ preview }: { preview: ResultPreview }) {
  if (!preview.taxonomy_top.length) return <div className="result-empty compact"><FileQuestion /><b>暂无物种丰度预览</b><span>物种注释结果可能尚未生成。</span></div>
  const max = Math.max(...preview.taxonomy_top.map(row => row.abundance), 1)
  return <div className="taxonomy-chart">{preview.taxonomy_top.slice(0, 10).map((row, index) => <div className="taxon-row" key={`${row.name}-${index}`}><span className="taxon-rank">{index + 1}</span><b title={row.name}>{row.name}</b><div><i style={{ width: `${row.abundance * 100 / max}%` }} /></div><span>{row.abundance.toFixed(2)}{preview.taxonomy_measurement?.display_unit === 'percent_of_reads' ? '%' : ''}</span></div>)}</div>
}

export function ResultsPreview({ taskId, supported }: { taskId: string; supported: boolean }) {
  return <SampleResultsPreview key={taskId} taskId={taskId} supported={supported} />
}

function SampleResultsPreview({ taskId, supported }: { taskId: string; supported: boolean }) {
  const [tab, setTab] = useState<Tab>('qc')
  const [sample, setSample] = useState<string>()
  const catalog = useQuery({ queryKey: ['task-preview', taskId], queryFn: () => tasksApi.preview(taskId), enabled: supported, retry: false })
  const scoped = useQuery({ queryKey: ['task-preview', taskId, sample], queryFn: () => tasksApi.preview(taskId, sample), enabled: supported && sample !== undefined, retry: false })
  const query = sample === undefined ? catalog : scoped
  if (!supported) return <section className="panel result-preview-panel"><div className="result-empty"><BarChart3 /><h2>在线结果预览尚未开放</h2><p>当前后端版本仅支持结果包下载。升级结果预览接口后，这里将显示 MultiQC、质控统计、物种和功能表。</p></div></section>
  if (catalog.isLoading) return <section className="panel result-preview-panel"><div className="preview-loading"><LoaderCircle className="spin" />正在读取分析结果…</div></section>
  if (catalog.error) {
    const missing = catalog.error instanceof ApiError && catalog.error.status === 404
    return <section className="panel result-preview-panel"><div className="result-empty"><FileQuestion /><h2>{missing ? '在线报告尚未生成' : '结果预览加载失败'}</h2><p>{missing ? '任务已完成，但服务器没有找到可在线展示的报告。你仍可以下载完整结果包。' : catalog.error.message}</p><button className="btn secondary" onClick={() => catalog.refetch()}>重新加载</button></div></section>
  }
  const preview = query.data
  const selected = sample ?? catalog.data?.selected_sample_id
  const multiqc = catalog.data?.multiqc_url
  const mismatch = !!selected && !!preview && preview.selected_sample_id !== selected
  return <section className="panel result-preview-panel"><div className="panel-header"><div><h2>结果在线预览</h2><p>快速浏览核心质控、物种及功能结果，完整数据以下载包为准</p></div>{multiqc ? <a className="btn secondary" href={multiqc.startsWith('/api/') ? multiqc : tasksApi.multiqcUrl(taskId)} target="_blank" rel="noopener noreferrer"><ExternalLink />打开 MultiQC 报告</a> : <span className="report-missing"><AlertCircle />MultiQC 报告不可用</span>}</div>
    {!!catalog.data?.sample_ids?.length && <div className="preview-sample-control"><label>预览样本<select value={selected} onChange={event => setSample(event.target.value)}>{catalog.data.sample_ids.map(id => <option key={id} value={id}>{id}</option>)}</select></label><p>以下标签仅展示所选样本，不合并样本排名。MultiQC、结果包和下方解读仍覆盖整个任务；未获得的结果不等于零。</p></div>}
    <div className="result-tabs">{([['qc','质控与 reads'],['taxonomy','物种丰度 Top 10'],['ko','KO'],['ec','EC'],['pathways','通路']] as Array<[Tab,string]>).map(([key,label]) => <button className={tab === key ? 'active' : ''} onClick={() => setTab(key)} key={key}>{key === 'taxonomy' ? <BarChart3 /> : <Table2 />}{label}</button>)}</div>
    <div className="result-tab-content" aria-live="polite">{query.isLoading ? <div className="preview-loading"><LoaderCircle className="spin" />正在读取所选样本…</div> : query.error || mismatch ? <div role="alert"><p>{mismatch ? '返回结果的样本不一致，已停止展示，请重新加载。' : query.error?.message}</p><button className="btn secondary" onClick={() => query.refetch()}>重新加载</button></div> : preview ? <>{tab === 'qc' ? <QcPanel preview={preview} /> : tab === 'taxonomy' ? <TaxonomyPanel preview={preview} /> : tab === 'ko' ? <DataTable data={preview.ko} empty="暂无 KO 表" /> : tab === 'ec' ? <DataTable data={preview.ec} empty="暂无 EC 表" /> : <DataTable data={preview.pathways} empty="暂无通路表" />}{(tab === 'taxonomy' ? preview.taxonomy_truncated : tab !== 'qc' && preview[tab]?.truncated) && <p className="table-caption">数据超过预览读取上限，当前内容并非完整统计；请下载完整结果核对。</p>}</> : null}</div>
  </section>
}
