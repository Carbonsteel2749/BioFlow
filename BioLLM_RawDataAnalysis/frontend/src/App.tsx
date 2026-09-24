import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, NavLink, Navigate, Route, Routes, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  Activity, AlertCircle, ArrowLeft, Beaker, Check, CheckCircle2, ChevronDown, ChevronRight,
  Circle, Clock3, Copy, Database, Dna, Download, FileArchive, FileText, FlaskConical,
  Gauge, HelpCircle, LayoutDashboard, ListFilter, LoaderCircle, Menu, Microscope, Play,
  Plus, RefreshCw, RotateCcw, Search, Server, Settings2, ShieldCheck, Sparkles, Terminal, Workflow,
  TriangleAlert, X, XCircle,
} from 'lucide-react'
import { ApiError, tasksApi } from './api/tasks'
import type { AnalysisTask, CreateTaskPayload, PlatformCapabilities, StepName, StepStatus, TaskStatus, WorkflowParameters, WorkflowStep } from './types'
import { UploadPanel } from './components/UploadPanel'
import { TaskCleanupActions } from './components/TaskCleanupActions'
import { TaskRenameAction } from './components/TaskRenameAction'
import { ResultsPreview } from './components/ResultsPreview'
import { ArtifactCenter } from './artifact-center/ArtifactCenter'
import { ResultInsights } from './components/ResultInsights'
import { LogManager } from './components/LogManager'
import { WorkflowStudioPage } from './workflow-editor/WorkflowStudioPage'
import { DatasetsPage, DatasetDetailPage } from './catalog/DatasetsPage'
import { ResultsPage } from './catalog/ResultsPage'
import { catalogApi } from './api/catalog'

const STEP_META: Record<StepName, { label: string; short: string; description: string }> = {
  validate: { label: '上传与校验', short: '校验', description: '识别双端 FASTQ，核对样本名、文件完整性与元数据' },
  fastqc_raw: { label: '原始质量控制', short: 'FastQC', description: '评估原始测序数据质量并生成 FastQC 报告' },
  fastp: { label: '过滤与质控', short: 'fastp', description: '去接头、过滤低质量 reads 并输出过滤统计' },
  host_depletion: { label: '去除宿主序列', short: '去宿主', description: '使用 Bowtie2 从样本中剔除人类宿主序列' },
  taxonomy: { label: '物种组成分析', short: '物种注释', description: '通过 Kraken2 / Bracken 进行物种分类与丰度估计' },
  functional_annotation: { label: '功能组成分析', short: '功能注释', description: '通过 HUMAnN 分析基因家族、代谢通路及丰度' },
  assembly: { label: '宏基因组组装', short: '序列组装', description: '将质控后的 reads 组装为宏基因组 contigs' },
  binning: { label: '基因组分箱', short: '基因组分箱', description: '通过多种分箱工具恢复候选微生物基因组' },
  bin_refinement: { label: '分箱优化', short: '分箱优化', description: '整合并优化候选 bins，评估完整度与污染度' },
  bin_quantification: { label: 'MAG 丰度定量', short: '丰度定量', description: '计算各样本中高质量 MAG 的相对丰度' },
  bin_reassembly: { label: 'MAG 重组装', short: '重组装', description: '对候选 bins 进行可选的精细重组装' },
  bin_annotation: { label: 'MAG 分类与注释', short: 'MAG 注释', description: '完成 MAG 物种分类与功能注释' },
  report: { label: '汇总分析报告', short: '报告生成', description: '生成 MultiQC、结果表、溯源记录与可下载结果包' },
}

const CORE_STEP_NAMES: StepName[] = ['validate','fastqc_raw','fastp','host_depletion','taxonomy','functional_annotation','report']
const MAG_STEP_NAMES: StepName[] = ['assembly','binning','bin_refinement','bin_quantification','bin_reassembly','bin_annotation']

const STATUS_META: Record<TaskStatus, { label: string; tone: string }> = {
  queued: { label: '排队中', tone: 'neutral' }, validating: { label: '校验中', tone: 'info' },
  running: { label: '运行中', tone: 'info' }, paused: { label: '已暂停', tone: 'warning' },
  failed: { label: '运行失败', tone: 'danger' }, completed: { label: '已完成', tone: 'success' },
  cancelled: { label: '已取消', tone: 'neutral' },
}

const STEP_STATUS_LABEL: Record<StepStatus, string> = {
  pending: '待运行', running: '运行中', succeeded: '已完成', failed: '失败', skipped: '已跳过',
}

function formatTime(value: string | null, includeDate = true) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: includeDate ? '2-digit' : undefined, day: includeDate ? '2-digit' : undefined,
    hour: '2-digit', minute: '2-digit', second: includeDate ? undefined : '2-digit', hour12: false,
  }).format(date)
}

function duration(start: string | null, finish: string | null) {
  if (!start) return '尚未开始'
  const ms = Math.max(0, new Date(finish ?? Date.now()).getTime() - new Date(start).getTime())
  const minutes = Math.floor(ms / 60_000), hours = Math.floor(minutes / 60)
  if (hours) return `${hours} 小时 ${minutes % 60} 分`
  if (minutes) return `${minutes} 分钟`
  return `${Math.max(1, Math.floor(ms / 1000))} 秒`
}

function taskProgress(task: AnalysisTask) {
  if (!task.steps.length) return 0
  const sum = task.steps.reduce((total, step) => {
    if (step.status === 'succeeded' || step.status === 'skipped') return total + 100
    if (step.status === 'running') return total + (step.progress ?? 35)
    return total
  }, 0)
  return Math.round(sum / task.steps.length)
}

function manifestName(path: string) { return path.split(/[\\/]/).pop() || path }
function shortId(id: string) { return id.length > 13 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id }

function AppShell({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  return <div className="app-shell">
    <aside className={`sidebar ${open ? 'is-open' : ''}`}>
      <div className="brand"><div className="brand-mark"><Dna size={25} /></div><div><strong>BioFlow</strong><span>宏基因组分析平台</span></div></div>
      <nav>
        <div className="nav-group-label">分析工作</div>
        <NavLink end to="/tasks" onClick={() => setOpen(false)} className={({ isActive }) => isActive ? 'active' : ''}><LayoutDashboard size={19} />任务中心</NavLink>
        <NavLink to="/tasks/new" onClick={() => setOpen(false)} className={({ isActive }) => isActive ? 'active' : ''}><Plus size={19} />新建分析</NavLink>
        <NavLink to="/workflows/new" onClick={() => setOpen(false)} className={({ isActive }) => isActive ? 'active' : ''}><Workflow size={19} />流程设计器</NavLink>
        <div className="nav-group-label">数据与成果</div>
        <NavLink to="/datasets" onClick={() => setOpen(false)} className={({ isActive }) => isActive ? 'active' : ''}><Database size={19} />数据与样本</NavLink>
        <NavLink to="/results" onClick={() => setOpen(false)} className={({ isActive }) => isActive ? 'active' : ''}><FileArchive size={19} />结果中心</NavLink>
      </nav>
      <div className="sidebar-spacer" />
      <div className="security-note"><ShieldCheck size={18} /><div><b>本地安全运行</b><span>原始数据与完整日志不会离开服务器</span></div></div>
      <div className="sidebar-footer"><span className="live-dot" />分析服务 V1 <small>Research only</small></div>
    </aside>
    {open && <button className="sidebar-overlay" onClick={() => setOpen(false)} aria-label="关闭导航" />}
    <main className="main-content">
      <header className="topbar"><button className="mobile-menu" aria-label="打开导航" onClick={() => setOpen(true)}><Menu size={21} /></button><div className="topbar-title">宏基因组自动化分析</div><div className="topbar-right"><span className="server-chip"><Server size={15} />本地服务器</span><div className="avatar">研</div></div></header>
      <div className="page-wrap">{children}</div>
      <footer>仅供科研分析使用，不用于临床诊断 · 原始数据与可识别信息保留在本地环境</footer>
    </main>
  </div>
}

function StatusBadge({ status }: { status: TaskStatus }) {
  const meta = STATUS_META[status]
  return <span className={`status-badge ${meta.tone}`}><span className={status === 'running' || status === 'validating' ? 'pulse-dot' : 'badge-dot'} />{meta.label}</span>
}

function ErrorState({ error, retry }: { error: Error; retry: () => void }) {
  return <div className="state-panel"><div className="state-icon danger"><AlertCircle /></div><h3>暂时无法读取数据</h3><p>{error.message || '请确认后端服务是否正常运行。'}</p><button className="btn secondary" onClick={retry}><RefreshCw size={17} />重新加载</button></div>
}

function EmptyTasks() {
  return <div className="empty-state"><div className="empty-visual"><Dna size={54} /><span /><span /><span /></div><h2>开始第一次宏基因组分析</h2><p>导入服务器中的 FASTQ 样本清单，系统会依次完成质量控制、去宿主、物种与功能注释。</p><Link className="btn primary" to="/tasks/new"><Plus size={18} />创建分析任务</Link><div className="flow-preview"><span>FASTQ 校验</span><ChevronRight /><span>质量控制</span><ChevronRight /><span>物种与功能</span><ChevronRight /><span>报告</span></div></div>
}

function taskIsUpdating(task:AnalysisTask) {
  return ['queued','validating','running'].includes(task.status) ||
    (task.status==='paused' && task.error_message?.includes('AI 辅助诊断处理中'))
}

function TaskListPage() {
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<'all' | TaskStatus>('all')
  const query = useQuery({ queryKey: ['tasks'], queryFn: tasksApi.list, refetchInterval: (q) => q.state.data?.some(taskIsUpdating) ? 3000 : false })
  const tasks = query.data ?? []
  const shown = useMemo(() => tasks.filter(t => (filter === 'all' || t.status === filter) && (`${t.id} ${t.name ?? ''} ${t.manifest_path}`).toLowerCase().includes(search.toLowerCase())), [tasks, filter, search])
  const counts = { running: tasks.filter(t => ['running','validating','queued'].includes(t.status)).length, completed: tasks.filter(t => t.status === 'completed').length, attention: tasks.filter(t => ['failed','paused'].includes(t.status)).length }

  return <>
    <div className="page-heading"><div><div className="eyebrow"><Activity size={15} />分析工作台</div><h1>任务中心</h1><p>集中查看分析进度、运行状态与结果产物。</p></div></div>
    <div className="metric-grid">
      <div className="metric-card"><div className="metric-icon teal"><FlaskConical /></div><div><span>全部任务</span><strong>{tasks.length}</strong><small>历史分析总数</small></div></div>
      <div className="metric-card"><div className="metric-icon blue"><LoaderCircle /></div><div><span>正在处理</span><strong>{counts.running}</strong><small>排队与运行任务</small></div></div>
      <div className="metric-card"><div className="metric-icon green"><CheckCircle2 /></div><div><span>已完成</span><strong>{counts.completed}</strong><small>结果可供下载</small></div></div>
      <div className="metric-card"><div className="metric-icon amber"><TriangleAlert /></div><div><span>需要关注</span><strong>{counts.attention}</strong><small>失败或暂停任务</small></div></div>
    </div>
    <section className="panel task-panel">
      <div className="panel-header"><div><h2>分析任务</h2><p>任务运行状态每隔数秒自动更新</p></div><button className="icon-btn" onClick={() => query.refetch()} title="刷新"><RefreshCw size={18} className={query.isFetching ? 'spin' : ''} /></button></div>
      <div className="toolbar"><label className="search-box"><Search size={18} /><input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索任务名称、ID 或样本清单" /></label><label className="select-box"><ListFilter size={17} /><select value={filter} onChange={e => setFilter(e.target.value as typeof filter)}><option value="all">全部状态</option><option value="queued">排队中</option><option value="running">运行中</option><option value="completed">已完成</option><option value="failed">运行失败</option><option value="paused">已暂停</option></select><ChevronDown size={16} /></label></div>
      {query.isLoading ? <div className="loading-list">{[1,2,3].map(i => <div className="skeleton-row" key={i} />)}</div> : query.error ? <ErrorState error={query.error} retry={() => query.refetch()} /> : tasks.length === 0 ? <EmptyTasks /> : shown.length === 0 ? <div className="no-results"><Search /><b>没有匹配的任务</b><span>尝试修改搜索词或筛选条件</span></div> : <div className="task-table-wrap"><table className="task-table"><thead><tr><th>分析任务</th><th>状态</th><th>当前阶段</th><th>总进度</th><th>创建时间</th><th>耗时</th><th /></tr></thead><tbody>{shown.map(task => <TaskRow key={task.id} task={task} />)}</tbody></table></div>}
    </section>
  </>
}

function TaskRow({ task }: { task: AnalysisTask }) {
  const activeStep = task.steps.find(step => step.status === 'running')?.name ?? task.current_step
  const progress = taskProgress(task), current = activeStep ? STEP_META[activeStep]?.short : task.status === 'completed' ? '全部完成' : '等待调度'
  return <tr><td><Link className="task-identity" to={`/tasks/${task.id}`}><div className={`file-avatar ${task.parameters.enable_mags ? 'mag' : ''}`}>{task.parameters.enable_mags ? <Dna size={20} /> : <FileText size={20} />}</div><div><b>{task.name || manifestName(task.manifest_path)}{task.parameters.enable_mags && <span className="mag-badge">MAG</span>}</b><span title={task.id}>{shortId(task.id)}</span></div></Link></td><td><StatusBadge status={task.status} /></td><td><span className="stage-cell">{current}</span>{task.alerts.length > 0 && <span className="alert-count">{task.alerts.length}</span>}</td><td><div className="progress-cell"><div className="mini-progress"><i style={{ width: `${progress}%` }} /></div><span>{progress}%</span></div></td><td>{formatTime(task.created_at)}</td><td>{duration(task.started_at, task.finished_at)}</td><td><TaskRenameAction task={task} /><TaskCleanupActions task={task} /><Link className="row-link" to={`/tasks/${task.id}`} aria-label="查看详情"><ChevronRight /></Link></td></tr>
}

const DEFAULT_PARAMS: Partial<WorkflowParameters> = {
  threads: 4, fastp_qualified_quality_phred: 20, fastp_unqualified_percent_limit: 40,
  fastp_n_base_limit: 5, fastp_length_required: 50, fastp_cut_front: true, fastp_cut_tail: true,
  fastp_cut_window_size: 4, fastp_cut_mean_quality: 20, fastp_trim_poly_g: true,
  fastp_correction: false, fastp_detect_adapter_for_pe: true, host_filter_mode: 'strict_both_unmapped',
  host_min_retained_pairs: 0, host_max_removed_pct: 100, host_bowtie2_preset: 'very-sensitive', read_length: 150,
  enable_mags: false, enable_reassembly: true, mag_threads: 8, mag_memory_gb: 32,
  assembler: 'megahit', bin_completeness: 70, bin_contamination: 5,
}

function CreateTaskPage() {
  const navigate = useNavigate(), client = useQueryClient()
  const [searchParams,setSearchParams] = useSearchParams()
  const selectedDataset = searchParams.get('dataset') ?? ''
  const [uploadManifest, setManifest] = useState('')
  const [taskName,setTaskName]=useState('')
  const [uploadCount, setUploadedSamples] = useState(0)
  const [uploadedDataset, setUploadedDataset] = useState<string|undefined>()
  const reused = useQuery({queryKey:['dataset-reuse',selectedDataset],queryFn:()=>catalogApi.reuse(selectedDataset),enabled:Boolean(selectedDataset),retry:false,staleTime:0})
  const manifest = selectedDataset ? reused.data?.manifest_path ?? '' : uploadManifest
  const uploadedSamples = selectedDataset ? reused.data?.sample_count ?? 0 : uploadCount
  const [advanced, setAdvanced] = useState(false)
  const [params, setParams] = useState<Partial<WorkflowParameters>>(DEFAULT_PARAMS)
  const capabilities = useQuery({ queryKey: ['capabilities'], queryFn: tasksApi.capabilities, retry: false })
  const mutation = useMutation({ mutationFn: (payload: CreateTaskPayload) => tasksApi.create(payload), onSuccess: task => { client.invalidateQueries({ queryKey: ['tasks'] }); navigate(`/tasks/${task.id}`) } })
  const set = <K extends keyof WorkflowParameters>(key: K, value: WorkflowParameters[K]) => setParams(p => ({ ...p, [key]: value }))
  const submit = (e: React.FormEvent) => { e.preventDefault(); if (manifest.trim() && !(selectedDataset && reused.isError)) mutation.mutate({ manifest_path: manifest.trim(), parameters: params, dataset_id: selectedDataset || uploadedDataset, name:taskName.trim()||undefined }) }
  const magAvailable = capabilities.data?.mag_analysis === true
  const magReason = capabilities.isError
    ? '后端能力接口暂不可用，请联系管理员确认 MAG 环境。'
    : capabilities.data?.mag_unavailable_reason
  const previewSteps = params.enable_mags
    ? [...CORE_STEP_NAMES.slice(0, -1), ...MAG_STEP_NAMES.filter(step => step !== 'bin_reassembly' || params.enable_reassembly), 'report' as StepName]
    : CORE_STEP_NAMES

  return <>
    <div className="page-heading compact"><div><Link to="/tasks" className="back-link"><ArrowLeft size={16} />返回任务中心</Link><h1>创建分析任务</h1><p>导入服务器中的配对 FASTQ 样本清单，启动标准宏基因组分析流程。</p></div></div>
    <form className="create-layout" onSubmit={submit}>
      <div className="form-main">
        <section className="panel form-section"><label className="field"><span>任务名称（可选）</span><input value={taskName} onChange={e=>setTaskName(e.target.value)} maxLength={120} placeholder="例如：肠道菌群试运行 · 批次 A"/></label></section>
        <section className="panel form-section"><SectionTitle n="01" icon={<FileText />} title="上传测序数据" text="选择双端 FASTQ 文件，系统会自动识别样本并完成 R1/R2 配对" />
          <div className="dataset-choice"><span>上传新数据，或复用已登记的数据集。</span><Link className="btn secondary" to="/datasets">选择已有数据</Link></div>
          {selectedDataset ? <div className="dataset-selection">{reused.isLoading ? <p>正在检查数据集是否可用…</p> : reused.error ? <p role="alert">{reused.error.message}</p> : <><h3>{reused.data?.name}</h3><p>{uploadedSamples} 个双端样本，已带入已有清单，不会重新上传。点击下方按钮后才会开始分析。</p></>}<button className="btn secondary" type="button" onClick={()=>setSearchParams({})}>改为上传新数据</button></div> : <UploadPanel supported={capabilities.data?.file_uploads === true} onManifest={(path, samples, id) => { setManifest(path); setUploadedSamples(samples); setUploadedDataset(id) }} />}
          {manifest && !selectedDataset && <div className="manifest-ready"><CheckCircle2 /><div><b>样本清单已生成</b><span>已登记 {uploadedSamples} 个双端样本；完整配对与质量检查将在流程中执行。</span></div></div>}
        </section>
        <section className="panel form-section"><SectionTitle n="02" icon={<Settings2 />} title="运行参数" text="推荐参数适用于首期标准双端测序分析" />
          <div className="form-grid">
            <NumberField label="计算线程" value={params.threads!} min={1} max={256} onChange={v => set('threads', v)} suffix="线程" />
            <NumberField label="最低保留长度" value={params.fastp_length_required!} min={1} onChange={v => set('fastp_length_required', v)} suffix="bp" />
            <NumberField label="合格碱基质量" value={params.fastp_qualified_quality_phred!} min={1} max={93} onChange={v => set('fastp_qualified_quality_phred', v)} prefix="Q" />
            <label className="field"><span>宿主过滤模式</span><div className="native-select"><select value={params.host_filter_mode} onChange={e => set('host_filter_mode', e.target.value as WorkflowParameters['host_filter_mode'])}><option value="strict_both_unmapped">严格双端未比对</option><option value="concordant_unmapped">一致性未比对</option></select><ChevronDown /></div></label>
            <label className="field"><span>Bowtie2 灵敏度</span><div className="native-select"><select value={params.host_bowtie2_preset} onChange={e => set('host_bowtie2_preset', e.target.value as WorkflowParameters['host_bowtie2_preset'])}><option value="very-sensitive">Very sensitive（推荐）</option><option value="sensitive">Sensitive</option><option value="fast">Fast</option><option value="very-fast">Very fast</option></select><ChevronDown /></div></label>
            <label className="field"><span>Bracken 读长</span><div className="native-select"><select value={params.read_length} onChange={e => set('read_length', Number(e.target.value) as WorkflowParameters['read_length'])}>{[50,75,100,150,200,250,300].map(v => <option key={v}>{v}</option>)}</select><ChevronDown /></div></label>
          </div>
          <div className={`mag-option ${magAvailable ? '' : 'unavailable'}`}>
            <div className="mag-option-icon"><Dna /></div>
            <div className="mag-option-copy"><div><b>启用 MAG 基因组分析</b><span className="optional-tag">可选</span></div><p>在常规 reads 分析后增加组装、分箱、优化、定量与 MAG 注释阶段。</p>
              {!capabilities.isLoading && !magAvailable && <div className="capability-reason"><AlertCircle />{magReason || '当前数据库配置不支持 MAG 分析。'}</div>}
            </div>
            <Toggle checked={params.enable_mags === true} disabled={!magAvailable} label="启用 MAG 分析" onChange={checked => set('enable_mags', checked)} />
          </div>
          {params.enable_mags && <div className="mag-settings"><div className="mag-settings-head"><Dna /><div><b>MAG 运行参数</b><span>这些参数会显著影响运行时间与资源消耗</span></div></div><div className="form-grid">
            <NumberField label="MAG 计算线程" value={params.mag_threads!} min={1} max={256} onChange={v => set('mag_threads', v)} suffix="线程" />
            <NumberField label="MAG 内存上限" value={params.mag_memory_gb!} min={4} onChange={v => set('mag_memory_gb', v)} suffix="GB" />
            <label className="field"><span>组装器</span><div className="native-select"><select value={params.assembler} onChange={e => set('assembler', e.target.value as WorkflowParameters['assembler'])}><option value="megahit">MEGAHIT（推荐）</option><option value="metaspades">metaSPAdes</option></select><ChevronDown /></div></label>
            <div className="field toggle-field"><span>候选 Bin 重组装</span><Toggle checked={params.enable_reassembly === true} label="启用重组装" onChange={checked => set('enable_reassembly', checked)} /></div>
            <NumberField label="最低完整度" value={params.bin_completeness!} min={0} max={100} onChange={v => set('bin_completeness', v)} suffix="%" />
            <NumberField label="最高污染度" value={params.bin_contamination!} min={0} max={100} onChange={v => set('bin_contamination', v)} suffix="%" />
          </div></div>}
          <button type="button" className="advanced-toggle" onClick={() => setAdvanced(v => !v)}><Settings2 size={17} />高级数据库配置 <small>默认使用服务器预设</small><ChevronDown className={advanced ? 'rotate' : ''} /></button>
          {advanced && <DatabaseConfiguration capabilities={capabilities.data} loading={capabilities.isLoading} />}
        </section>
      </div>
      <aside className="submit-card panel"><div className="submit-card-head"><Sparkles /><div><b>{params.enable_mags ? 'Reads + MAG 分析流程' : '标准 Reads 分析流程'}</b><span>{previewSteps.length} 个阶段 · 自动顺序执行</span></div></div><div className="mini-pipeline">{previewSteps.map((key, i) => { const meta = STEP_META[key]; return <div key={key} className={MAG_STEP_NAMES.includes(key) ? 'mag-stage' : ''}><span>{i + 1}</span><div><b>{meta.short}</b><small>{meta.label}</small></div></div> })}</div><div className="summary-line"><span>上传样本</span><b>{manifest ? `${uploadedSamples} 个双端样本` : '尚未完成上传'}</b></div><div className="summary-line"><span>分析模式</span><b>{params.enable_mags ? 'Reads + MAG' : 'Reads'}</b></div><div className="summary-line"><span>计算资源</span><b>{params.enable_mags ? `${params.threads} / ${params.mag_threads} 线程` : `${params.threads} 线程`}</b></div>{mutation.error && <div className="inline-error"><AlertCircle />{mutation.error instanceof ApiError ? mutation.error.message : '创建任务失败'}</div>}<button className="btn primary wide" type="submit" disabled={!manifest.trim() || mutation.isPending}>{mutation.isPending ? <><LoaderCircle className="spin" />正在创建…</> : <><Play size={18} />创建并开始分析</>}</button><p className="submit-hint">提交后可在任务详情页持续查看进度与日志</p></aside>
    </form>
  </>
}

function SectionTitle({ n, icon, title, text }: { n: string; icon: React.ReactNode; title: string; text: string }) { return <div className="section-title"><span>{n}</span><div className="section-icon">{icon}</div><div><h2>{title}</h2><p>{text}</p></div></div> }
function NumberField({ label, value, min, max, onChange, suffix, prefix }: { label: string; value: number; min: number; max?: number; onChange: (v: number) => void; suffix?: string; prefix?: string }) { return <label className="field"><span>{label}</span><div className="number-input">{prefix && <i>{prefix}</i>}<input type="number" value={value} min={min} max={max} onChange={e => onChange(Number(e.target.value))} />{suffix && <i>{suffix}</i>}</div></label> }

function Toggle({ checked, disabled = false, onChange, label }: { checked: boolean; disabled?: boolean; onChange: (checked: boolean) => void; label: string }) { return <button type="button" role="switch" aria-label={label} aria-checked={checked} disabled={disabled} className={`toggle-switch ${checked ? 'checked' : ''}`} onClick={() => onChange(!checked)}><span /></button> }

function DatabaseConfiguration({ capabilities, loading }: { capabilities?: PlatformCapabilities; loading: boolean }) { return <div className="advanced-fields database-readonly"><div className="database-profile"><Database /><div><span>数据库配置档案</span><b>{loading ? '正在读取…' : capabilities?.database_profile || '未配置'}</b></div><span className={capabilities?.reads_analysis ? 'ready' : 'not-ready'}>{capabilities?.reads_analysis ? 'Reads 数据库就绪' : 'Reads 数据库未就绪'}</span></div><div className="server-managed-note"><ShieldCheck /><div><b>数据库路径由服务器统一管理</b><p>为保证结果可重复和避免误用数据库，任务创建页不允许修改宿主、Kraken2、HUMAnN 或 MetaPhlAn 路径。实际路径与版本由后端配置并写入运行溯源。</p></div></div></div> }

function TaskDetailPage() {
  const { id = '' } = useParams(), navigate = useNavigate()
  const query = useQuery({ queryKey: ['task', id], queryFn: () => tasksApi.get(id), enabled: !!id, refetchInterval: q => q.state.data && taskIsUpdating(q.state.data) ? 2500 : false })
  const capabilities = useQuery({ queryKey: ['capabilities'], queryFn: tasksApi.capabilities, retry: false })
  const task = query.data
  const [selectedStep, setSelectedStep] = useState<StepName | null>(null)
  const [tab, setTab] = useState<'overview' | 'parameters'>('overview')
  if (query.isLoading) return <div className="detail-skeleton"><div /><div /><div /></div>
  if (query.error) return <><button className="back-link" onClick={() => navigate('/tasks')}><ArrowLeft />返回</button><ErrorState error={query.error} retry={() => query.refetch()} /></>
  if (!task) return <Navigate to="/tasks" replace />
  const progress = taskProgress(task)
  const displayedStep = task.steps.find(step => step.status === 'running')?.name ?? task.current_step
  return <>
    <div className="detail-heading"><div><Link to="/tasks" className="back-link"><ArrowLeft size={16} />返回任务中心</Link><div className="detail-title-row"><div className={`detail-icon ${task.parameters.enable_mags ? 'mag' : ''}`}>{task.parameters.enable_mags ? <Dna /> : <Microscope />}</div><div><div className="title-line"><h1>{task.name || manifestName(task.manifest_path)}</h1>{task.parameters.enable_mags && <span className="mag-badge large">MAG 分析</span>}<StatusBadge status={task.status} /></div><p>任务 ID：<code>{task.id}</code> <button className="copy-btn" onClick={() => navigator.clipboard.writeText(task.id)}><Copy size={14} /></button></p></div></div></div><div className="detail-actions"><TaskRenameAction task={task} /><button className="btn secondary" onClick={() => query.refetch()}><RefreshCw className={query.isFetching ? 'spin' : ''} />刷新</button><TaskControls task={task} canCancel={capabilities.data?.task_cancellation === true} />{task.status === 'completed' && task.result_archive && <a className="btn primary" href={tasksApi.resultUrl(task.id)} download><Download />下载结果包</a>}</div></div>
    {task.error_message && <FailureNotice task={task} onOpenLog={() => task.current_step && setSelectedStep(task.current_step)} />}
    {task.alerts.length > 0 && <Alerts alerts={task.alerts} />}
    <div className="detail-stats"><div><Gauge /><span>整体进度</span><b>{progress}%</b><div className="hero-progress"><i style={{ width: `${progress}%` }} /></div></div><div><Clock3 /><span>累计耗时</span><b>{duration(task.started_at, task.finished_at)}</b><small>{task.started_at ? `开始于 ${formatTime(task.started_at)}` : '等待任务调度'}</small></div><div><Activity /><span>当前阶段</span><b>{displayedStep ? STEP_META[displayedStep].short : task.status === 'completed' ? '全部完成' : '尚未开始'}</b><small>{task.status === 'running' ? '以阶段实时状态为准' : STATUS_META[task.status].label}</small></div><div><FileArchive /><span>结果产物</span><b>{task.result_archive ? '已就绪' : task.status === 'failed' ? '未生成' : '生成中'}</b><small>{task.result_archive ? '可下载完整结果包' : task.status === 'failed' ? '请先处理失败原因' : '完成后自动打包'}</small></div></div>
    <div className="tabs"><button className={tab === 'overview' ? 'active' : ''} onClick={() => setTab('overview')}>运行进度</button><button className={tab === 'parameters' ? 'active' : ''} onClick={() => setTab('parameters')}>参数与溯源</button></div>
    {tab === 'overview' ? <section className="panel pipeline-panel"><div className="panel-header"><div><h2>分析流程</h2><p>点击任一阶段查看运行日志与详细信息</p></div><span className="auto-refresh"><span />运行中自动刷新</span></div><div className="pipeline-list">{task.steps.map((step, i) => <StepRow key={step.name} step={step} index={i} last={i === task.steps.length - 1} onClick={() => setSelectedStep(step.name)} />)}</div></section> : <ParametersPanel task={task} />}
    {task.status === 'completed' && <ResultsPreview taskId={task.id} supported={capabilities.data?.result_preview === true} />}
    {task.status !== 'completed' && <div className="artifact-partial" role="note"><AlertCircle /><span><b>阶段性结果，非完整报告</b><small>下方可预览和下载已完成节点生成的图表；任务暂停或失败不会隐藏已有结果。</small></span></div>}
    <div id="result-insights"><ResultInsights taskId={task.id} /></div>
    <ArtifactCenter taskId={task.id} taskStatus={task.status} />
    <LogManager taskId={task.id} />
    {selectedStep && <LogDrawer task={task} step={selectedStep} close={() => setSelectedStep(null)} />}
  </>
}

function FailureNotice({task,onOpenLog}:{task:AnalysisTask;onOpenLog:()=>void}) {
  const message=task.error_message || ''
  const source=message.split(/\nAI 辅助诊断：|\n建议操作：/)[0].replace(/^原始流程错误（[^）]+）：/,'')
  const lines=source.split('\n')
  const specific=lines.find(line=>/\breason=/.test(line))
  const cause=specific?.replace(/^.*?\[step=[^\]]+\]\s*/,'') || lines.filter(line=>/failure_stage=|\[ERROR\]|Error:|Exception:|Not a valid|No such file/.test(line)).join('\n') || source
  const suggestion=message.split('\n建议操作：').slice(1).join('；') || '请查看失败阶段日志，核对输入文件、数据库与运行条件；不要直接修改分析阈值。'
  return <div className="failure-banner"><div><XCircle /></div><section className="failure-summary"><b>分析任务未能完成</b><p>失败阶段：{task.current_step ? STEP_META[task.current_step].label : '尚未确定'}</p><p className="failure-cause">{cause.length>700 ? `${cause.slice(0,700)}…` : cause}</p><p>建议操作：{suggestion}</p><details><summary>完整错误与辅助诊断</summary><pre>{message}</pre></details>{task.current_step && <button className="btn secondary" onClick={onOpenLog}>查看失败阶段日志</button>}</section>{task.status === 'failed' && !task.retry_allowed && <span className="retry-unavailable"><ShieldCheck />该失败不符合安全重试策略</span>}</div>
}

function StepRow({ step, index, last, onClick }: { step: WorkflowStep; index: number; last: boolean; onClick: () => void }) {
  const meta = STEP_META[step.name], Icon = step.status === 'succeeded' ? Check : step.status === 'failed' ? X : step.status === 'running' ? LoaderCircle : Circle
  return <button className={`step-row ${step.status}`} onClick={onClick}><div className="step-rail"><span className="step-node"><Icon className={step.status === 'running' ? 'spin' : ''} /></span>{!last && <i />}</div><div className="step-number">0{index + 1}</div><div className="step-main"><b>{meta.label}</b><p>{meta.description}</p></div><div className="step-progress">{step.status === 'running' && <><div><i style={{ width: `${step.progress ?? 35}%` }} /></div><span>{step.progress == null ? '运行中' : `${Math.round(step.progress)}%`}</span></>}</div><div className="step-time"><b>{STEP_STATUS_LABEL[step.status]}</b><span>{step.started_at ? formatTime(step.started_at, false) : '—'}</span></div><ChevronRight className="step-chevron" /></button>
}

function Alerts({ alerts }: { alerts: AnalysisTask['alerts'] }) { const [open, setOpen] = useState(false), latest = alerts[alerts.length - 1]; return <div className={`alert-strip ${latest.level}`}><TriangleAlert /><div><b>{latest.level === 'error' ? '运行告警' : '系统提示'}</b>{latest.message.length>240 || latest.message.includes('AI 辅助诊断') ? <details className="alert-details"><summary>查看告警详情</summary><p>{latest.message}</p></details> : <span>{latest.message}</span>}</div><time>{formatTime(latest.created_at)}</time>{alerts.length > 1 && <button onClick={() => setOpen(!open)}>共 {alerts.length} 条 <ChevronDown className={open ? 'rotate' : ''} /></button>}{open && <div className="alert-history">{alerts.slice(0,-1).reverse().map((a,i) => <p key={i}><b>{formatTime(a.created_at)}</b>{a.message}</p>)}</div>}</div> }

function TaskControls({ task, canCancel }: { task: AnalysisTask; canCancel: boolean }) {
  const [confirmCancel, setConfirmCancel] = useState(false)
  const client = useQueryClient(), navigate = useNavigate()
  const cancel = useMutation({ mutationFn: () => tasksApi.cancel(task.id), onSuccess: data => { client.setQueryData(['task', task.id], data); setConfirmCancel(false) } })
  const rerun = useMutation({ mutationFn: () => tasksApi.rerun(task), onSuccess: data => { client.invalidateQueries({ queryKey: ['tasks'] }); navigate(`/tasks/${data.id}`) } })
  if (['queued','validating','running','paused'].includes(task.status)) return <div className="task-control-wrap">{confirmCancel ? <div className="cancel-confirm"><span>确认取消任务？</span><button onClick={() => cancel.mutate()} disabled={cancel.isPending}>{cancel.isPending ? '取消中…' : '确认'}</button><button onClick={() => setConfirmCancel(false)}>返回</button></div> : <button className="btn danger-outline" disabled={!canCancel} title={canCancel ? '安全停止当前分析任务' : '当前后端尚未开放取消接口'} onClick={() => setConfirmCancel(true)}><XCircle />取消任务</button>}{cancel.error && <span className="action-error">{cancel.error.message}</span>}</div>
  if (task.status === 'failed' && task.retry_allowed) return <RetryButton task={task} />
  if (task.status === 'completed' || task.status === 'cancelled') return <button className="btn secondary" disabled={rerun.isPending} onClick={() => rerun.mutate()}><RotateCcw />{rerun.isPending ? '正在创建…' : '再次运行'}</button>
  return null
}

function RetryButton({ task }: { task: AnalysisTask }) { const client = useQueryClient(); const mutation = useMutation({ mutationFn: () => tasksApi.retry(task.id), onSuccess: data => client.setQueryData(['task', task.id], data) }); return <button className="btn danger-outline" title="仅使用后端批准的幂等重试策略，不改变分析参数" disabled={mutation.isPending} onClick={() => mutation.mutate()}><ShieldCheck />{mutation.isPending ? '正在提交' : '安全重试'}</button> }

function LogDrawer({ task, step, close }: { task: AnalysisTask; step: StepName; close: () => void }) {
  const query = useQuery({ queryKey: ['log', task.id, step], queryFn: () => tasksApi.log(task.id, step), refetchInterval: task.status === 'running' && task.current_step === step ? 3000 : false, retry: false })
  const info = task.steps.find(s => s.name === step)!, meta = STEP_META[step]
  return <div className="drawer-layer"><button className="drawer-backdrop" onClick={close} aria-label="关闭" /><aside className="log-drawer"><header><div className="drawer-icon"><Terminal /></div><div><span>阶段日志</span><h2>{meta.label}</h2></div><button onClick={close}><X /></button></header><div className="drawer-meta"><div><span>状态</span><b className={`text-${info.status}`}>{STEP_STATUS_LABEL[info.status]}</b></div><div><span>开始时间</span><b>{formatTime(info.started_at)}</b></div><div><span>运行耗时</span><b>{duration(info.started_at, info.finished_at)}</b></div></div>{info.error_message && <div className="drawer-error"><AlertCircle /><div><b>失败原因</b><p>{info.error_message}</p></div></div>}<div className="log-title"><span><Terminal />脱敏运行日志</span><div><button onClick={() => query.refetch()}><RefreshCw className={query.isFetching ? 'spin' : ''} /></button><button onClick={() => navigator.clipboard.writeText(query.data?.text ?? '')}><Copy /></button></div></div><pre className="terminal-log">{query.isLoading ? '正在读取日志…' : query.error ? query.error instanceof ApiError && query.error.status === 404 ? '此阶段尚未生成日志。\n日志将在阶段开始运行后显示。' : `读取失败：${query.error.message}` : query.data?.text || '日志内容为空。'}</pre><div className="drawer-privacy"><ShieldCheck />日志已进行路径与身份信息脱敏，最多显示最近 64 KB</div></aside></div>
}

function ParametersPanel({ task }: { task: AnalysisTask }) {
  const labels: Partial<Record<keyof WorkflowParameters,string>> = { threads:'计算线程',fastp_qualified_quality_phred:'fastp 质量阈值',fastp_length_required:'最低保留长度',fastp_correction:'双端纠错',host_filter_mode:'宿主过滤模式',host_bowtie2_preset:'Bowtie2 模式',enable_mags:'MAG 分析',enable_reassembly:'候选 Bin 重组装',mag_threads:'MAG 线程',mag_memory_gb:'MAG 内存 (GB)',assembler:'组装器',bin_completeness:'最低完整度 (%)',bin_contamination:'最高污染度 (%)',host_index:'宿主参考索引',kraken_db:'Kraken2 数据库',read_length:'Bracken 读长',humann_nucleotide_db:'HUMAnN 核酸库',humann_protein_db:'HUMAnN 蛋白库',metaphlan_db:'MetaPhlAn 数据库' }
  return <section className="panel parameters-panel"><div className="panel-header"><div><h2>参数与运行溯源</h2><p>记录本次任务提交时使用的分析参数</p></div><ShieldCheck /></div><div className="provenance-summary"><div><span>任务标识</span><code>{task.id}</code></div><div><span>样本清单</span><code>{task.manifest_path.replace(/^\/home\/[^/]+/, '[HOME]')}</code></div><div><span>重试次数</span><b>{task.retry_count}</b></div><div><span>创建时间</span><b>{formatTime(task.created_at)}</b></div></div><div className="parameter-grid">{Object.entries(labels).map(([key,label]) => { const v = task.parameters[key as keyof WorkflowParameters]; return <div key={key}><span>{label}</span><b title={String(v ?? '')}>{v === null || v === '' ? '服务器默认' : typeof v === 'boolean' ? v ? '启用' : '关闭' : String(v)}</b></div> })}</div><div className="research-note"><Beaker /><div><b>科研用途声明</b><p>以上参数用于保证分析过程可追溯。结果不具备临床诊断效力，关键分析方法或数据库的变更需经过人工确认。</p></div></div></section>
}

function NotFound() { return <div className="state-panel not-found"><div className="state-icon"><HelpCircle /></div><h2>页面不存在</h2><p>你访问的页面可能已被移动。</p><Link className="btn primary" to="/tasks">返回任务中心</Link></div> }

export default function App() {
  return <AppShell><Routes><Route path="/" element={<Navigate to="/tasks" replace />} /><Route path="/tasks" element={<TaskListPage />} /><Route path="/tasks/new" element={<CreateTaskPage />} /><Route path="/tasks/:id" element={<TaskDetailPage />} /><Route path="/datasets" element={<DatasetsPage />} /><Route path="/datasets/:id" element={<DatasetDetailPage />} /><Route path="/results" element={<ResultsPage />} /><Route path="/workflows/new" element={<WorkflowStudioPage />} /><Route path="*" element={<NotFound />} /></Routes></AppShell>
}
