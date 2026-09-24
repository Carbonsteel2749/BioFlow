import {
  AlertCircle, AlertTriangle, BarChart3, CheckCircle2, ChevronRight, Database,
  Download, FileChartColumn, FileQuestion, FileText, Filter, Image, Info, LoaderCircle,
  RefreshCw, SearchX, ShieldAlert, SlidersHorizontal, X,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { ApiError } from '../api/tasks'
import { artifactsApi, loadArtifacts } from '../api/artifacts'
import { artifactCategory, CATEGORIES, formatBytes, isPreviewable, figureTitle, figureCaption } from './catalog'
import type { Artifact, ArtifactCategory, ArtifactCenterLoader, ArtifactCenterPayload, ArtifactGenerationNotice } from './types'
import './artifact-center.css'

export interface ArtifactCenterProps {
  taskId: string
  taskStatus?: string
  loader?: ArtifactCenterLoader
}

type LoadState = 'loading' | 'ready' | 'error'

const PAPER_LABELS = {
  Introduction: 'Introduction', Method: 'Method', Result: 'Result', Discussion: 'Discussion',
}

const NOTICE_META = {
  skipped: { label: '已跳过', icon: Info },
  generation_failed: { label: '生成失败', icon: AlertCircle },
  validation_failed: { label: '校验失败', icon: ShieldAlert },
}

function StatePanel({ icon: Icon, title, message, action }: { icon: typeof FileQuestion; title: string; message: string; action?: () => void }) {
  return <div className="artifact-state"><div><Icon /></div><h2>{title}</h2><p>{message}</p>{action && <button onClick={action}><RefreshCw />重新加载</button>}</div>
}

function GenerationNotice({ notice }: { notice: ArtifactGenerationNotice }) {
  const meta = NOTICE_META[notice.state]
  const Icon = meta.icon
  return <article className={`artifact-notice ${notice.state}`}><Icon /><div><b>{meta.label}</b><p>{notice.message}</p><span>{notice.sample_id ?? '全局'} · {notice.node_id ?? '未知节点'} · {notice.artifact_type}</span></div></article>
}

function StatusBadge({ artifact }: { artifact: Artifact }) {
  if (artifact.status === 'active') return <span className="artifact-status active"><CheckCircle2 />可用</span>
  if (artifact.status === 'missing') return <span className="artifact-status missing"><FileQuestion />文件不存在</span>
  if (artifact.status === 'validation_failed') return <span className="artifact-status invalid"><ShieldAlert />校验失败</span>
  if (artifact.status === 'generation_failed') return <span className="artifact-status failed"><AlertCircle />生成失败</span>
  return <span className="artifact-status skipped"><Info />已跳过</span>
}

export function ArtifactCard({ artifact, onOpen }: { artifact: Artifact; onOpen: () => void }) {
  const previewable = isPreviewable(artifact)
  const Icon = previewable ? Image : artifact.media_type === 'application/pdf' ? FileText : Database
  const sections = artifact.metadata.paper_sections ?? []
  return <article className={`artifact-card ${artifact.status !== 'active' ? 'unavailable' : ''}`} data-testid="artifact-card">
    {previewable ? <button className="artifact-thumbnail" onClick={onOpen} aria-label={`查看 ${artifact.file_name}`}><img src={artifactsApi.contentUrl(artifact.artifact_id)} alt="" loading="lazy" /></button> : <div className={`artifact-file-icon category-${artifactCategory(artifact.artifact_type)}`}><Icon /></div>}
    <div className="artifact-card-body">
      <div className="artifact-card-title"><div><b>{figureTitle(artifact)}</b><span>{artifact.file_name}</span></div><StatusBadge artifact={artifact} /></div>
      <div className="artifact-facts"><span>{artifact.sample_id ?? artifact.cohort_id ?? '全局'}</span><span>{artifact.artifact_type}</span><span>{formatBytes(artifact.size_bytes)}</span></div>
      <div className="paper-tags">{sections.map(section => <span key={section}>{PAPER_LABELS[section]}</span>)}</div>
      {artifact.status !== 'active' && <p className="artifact-inline-error">{artifact.metadata.error_message ?? artifact.metadata.skip_reason ?? '该产物当前不可用。'}</p>}
    </div>
    <div className="artifact-card-actions">
      <button onClick={onOpen} disabled={!previewable && artifact.status !== 'active'}>{previewable ? '预览' : '详情'}<ChevronRight /></button>
      {artifact.downloadable && artifact.status === 'active' && <a href={artifactsApi.downloadUrl(artifact.artifact_id)} download={artifact.file_name}><Download />下载</a>}
    </div>
  </article>
}

export function ArtifactDetail({ artifact, onClose }: { artifact: Artifact; onClose: () => void }) {
  const [imageFailed, setImageFailed] = useState(false)
  const [zoomed, setZoomed] = useState(false)
  const parameters = artifact.metadata.parameters ?? {}
  return <div className="artifact-detail-layer" role="dialog" aria-modal="true" aria-label="产物详情">
    <button className="artifact-detail-backdrop" aria-label="关闭产物详情" onClick={onClose} />
    <aside className="artifact-detail">
      <header><div><span>{artifact.artifact_type}</span><h2>{figureTitle(artifact)}</h2></div><button aria-label="关闭" onClick={onClose}><X /></button></header>
      {isPreviewable(artifact) && <button className="artifact-zoom" onClick={() => setZoomed(value => !value)}>{zoomed ? '适应窗口' : '放大图片'}</button>}
      {isPreviewable(artifact) && <section className={`artifact-preview-frame ${zoomed ? 'zoomed' : ''}`}>
        {imageFailed ? <StatePanel icon={FileQuestion} title="预览文件不存在" message="服务器未能通过 Artifact API 返回该图表，可尝试重新生成。" /> : <img src={artifactsApi.contentUrl(artifact.artifact_id)} alt={figureTitle(artifact)} onError={() => setImageFailed(true)} />}
      </section>}
      {isPreviewable(artifact) && <section><h3>图表说明与解读边界</h3><p style={{fontSize:13,lineHeight:1.8}}>{figureCaption(artifact)}</p></section>}
      <section className="artifact-detail-meta"><h3><FileChartColumn />产物信息</h3><dl><div><dt>文件</dt><dd>{artifact.file_name}</dd></div><div><dt>样本 / 队列</dt><dd>{artifact.sample_id ?? artifact.cohort_id ?? '全局'}</dd></div><div><dt>生成节点</dt><dd>{artifact.node_id ?? '未标记'}</dd></div><div><dt>大小</dt><dd>{formatBytes(artifact.size_bytes)}</dd></div><div><dt>SHA-256</dt><dd><code>{artifact.sha256}</code></dd></div></dl></section>
      <section><h3><SlidersHorizontal />图表来源与参数</h3><div className="artifact-provenance"><div><span>生成来源</span><b>{artifact.metadata.source ?? artifact.producer}</b></div><pre>{Object.keys(parameters).length ? JSON.stringify(parameters, null, 2) : '未记录额外参数'}</pre></div></section>
      <section><h3><FileText />论文用途</h3><div className="paper-tags large">{(artifact.metadata.paper_sections ?? []).map(section => <span key={section}>{section}</span>)}{!artifact.metadata.paper_sections?.length && <em>未标记论文用途</em>}</div></section>
      <footer>{artifact.downloadable && <a href={artifactsApi.downloadUrl(artifact.artifact_id)} download={artifact.file_name}><Download />下载 {artifact.media_type === 'application/pdf' ? 'PDF' : artifact.file_name.endsWith('.tsv') ? 'TSV' : '产物'}</a>}</footer>
    </aside>
  </div>
}

export function ArtifactCenter({ taskId, taskStatus, loader = loadArtifacts }: ArtifactCenterProps) {
  const [state, setState] = useState<LoadState>('loading')
  const [payload, setPayload] = useState<ArtifactCenterPayload | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [reload, setReload] = useState(0)
  const [category, setCategory] = useState<ArtifactCategory>('qc')
  const [sample, setSample] = useState('all')
  const [type, setType] = useState('all')
  const [node, setNode] = useState('all')
  const [selected, setSelected] = useState<Artifact | null>(null)

  useEffect(() => {
    let current = true
    setState('loading')
    setError(null)
    const load = () => loader(taskId).then(result => {
      if (!current) return
      if (!Array.isArray(result?.artifacts) || !Array.isArray(result?.notices)) throw new Error('服务器返回的产物清单格式无效，请刷新重试。')
      setPayload(result)
      setCategory(previous => result.artifacts.some(item => artifactCategory(item.artifact_type) === previous) ? previous : result.artifacts.length ? artifactCategory(result.artifacts[0].artifact_type) : previous)
      setState('ready')
    }).catch(reason => {
      if (!current) return
      setError(reason)
      setState('error')
    })
    void load()
    const timer = taskStatus && ['queued', 'validating', 'running'].includes(taskStatus) ? window.setInterval(() => { void load() }, 15000) : undefined
    return () => { current = false; window.clearInterval(timer) }
  }, [loader, reload, taskId, taskStatus])

  const categoryArtifacts = useMemo(() => payload?.artifacts.filter(item => artifactCategory(item.artifact_type) === category) ?? [], [category, payload])
  const options = useMemo(() => ({
    samples: [...new Set(categoryArtifacts.map(item => item.sample_id).filter((value): value is string => Boolean(value)))],
    types: [...new Set(categoryArtifacts.map(item => item.artifact_type))],
    nodes: [...new Set(categoryArtifacts.map(item => item.node_id).filter((value): value is string => Boolean(value)))],
  }), [categoryArtifacts])
  const visible = categoryArtifacts.filter(item =>
    (sample === 'all' || item.sample_id === sample)
    && (type === 'all' || item.artifact_type === type)
    && (node === 'all' || item.node_id === node),
  )
  const categoryNotices = payload?.notices.filter(item => item.category === category) ?? []
  const resetFilters = (next?: ArtifactCategory) => { if (next) setCategory(next); setSample('all'); setType('all'); setNode('all') }

  if (state === 'loading') return <section className="artifact-center artifact-loading" aria-label="任务结果中心"><LoaderCircle className="spin" /><div><b>正在加载任务产物</b><span>读取 Artifact 清单与校验状态…</span></div></section>
  if (state === 'error') {
    const unavailable = error instanceof ApiError && [501, 503].includes(error.status)
    const missing = error instanceof ApiError && error.status === 404
    return <section className="artifact-center"><StatePanel icon={unavailable ? AlertTriangle : missing ? FileQuestion : AlertCircle} title={unavailable ? 'Artifact 接口不可用' : missing ? '任务或结果不存在' : '结果加载失败'} message={unavailable ? '当前后端版本尚未开放 Artifact API，请在服务升级后重试。' : error instanceof Error ? error.message : '无法读取任务产物。'} action={() => setReload(value => value + 1)} /></section>
  }
  if (!payload?.artifacts.length && !payload?.notices.length) return <section className="artifact-center"><StatePanel icon={FileQuestion} title="暂无分析结果" message="暂未发现可展示的已完成节点产物，可稍后刷新；无需等待完整报告。" action={() => setReload(value => value + 1)} /></section>

  return <section className="artifact-center" aria-label="任务结果中心">
    <header className="artifact-center-header"><div><span>RESULT ARTIFACTS</span><h1>任务结果中心</h1><p>按分析阶段查看、预览和下载已校验的产物</p></div><button onClick={() => setReload(value => value + 1)}><RefreshCw />刷新产物</button></header>
    <nav className="artifact-categories" aria-label="产物分类">{CATEGORIES.map(item => {
      const count = payload.artifacts.filter(artifact => artifactCategory(artifact.artifact_type) === item.id).length
      const problems = payload.notices.filter(notice => notice.category === item.id).length
      return <button className={category === item.id ? 'active' : ''} key={item.id} onClick={() => resetFilters(item.id)}><span>{item.label}</span><small>{item.description}</small><b>{count}{problems > 0 && <em>+{problems}</em>}</b></button>
    })}</nav>
    <div className="artifact-filterbar"><span><Filter />筛选</span><label>样本<select aria-label="按样本筛选" value={sample} onChange={event => setSample(event.target.value)}><option value="all">全部样本</option>{options.samples.map(value => <option key={value}>{value}</option>)}</select></label><label>Artifact 类型<select aria-label="按 artifact 类型筛选" value={type} onChange={event => setType(event.target.value)}><option value="all">全部类型</option>{options.types.map(value => <option key={value}>{value}</option>)}</select></label><label>生成节点<select aria-label="按生成节点筛选" value={node} onChange={event => setNode(event.target.value)}><option value="all">全部节点</option>{options.nodes.map(value => <option key={value}>{value}</option>)}</select></label><b>{visible.length} 个产物</b></div>
    {categoryNotices.length > 0 && <div className="artifact-notices"><div className="artifact-partial"><AlertTriangle /><span><b>部分图表未生成</b><small>已生成的产物仍可正常查看和下载。</small></span></div>{categoryNotices.map(notice => <GenerationNotice notice={notice} key={notice.id} />)}</div>}
    <div className="artifact-list-heading"><div><BarChart3 /><span><b>{CATEGORIES.find(item => item.id === category)?.label}产物</b><small>{categoryArtifacts.length} 个已登记 · {categoryNotices.length} 个异常</small></span></div></div>
    {visible.length ? <div className="artifact-list">{visible.map(artifact => <ArtifactCard artifact={artifact} onOpen={() => setSelected(artifact)} key={artifact.artifact_id} />)}</div> : <div className="artifact-filter-empty"><SearchX /><b>没有匹配的产物</b><p>请调整样本、类型或生成节点筛选条件。</p><button onClick={() => resetFilters()}>清除筛选</button></div>}
    {selected && <ArtifactDetail key={selected.artifact_id} artifact={selected} onClose={() => setSelected(null)} />}
  </section>
}
