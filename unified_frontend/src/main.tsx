import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Activity,
  ArrowUpRight,
  BookOpen,
  CheckCircle2,
  CircleAlert,
  Dna,
  FilePenLine,
  FlaskConical,
  LayoutDashboard,
  RefreshCw,
  Server,
  Sparkles,
  Workflow,
} from 'lucide-react'
import './styles.css'

type Service = {
  id: string
  name: string
  eyebrow: string
  description: string
  url: string
  healthUrl: string
  tone: 'green' | 'blue' | 'coral'
  icon: typeof BookOpen
}

type ServiceState = 'checking' | 'online' | 'offline'

// 模块地址解析：
//  1. 优先读 VITE_* 环境变量（构建时注入，见 .env.example）
//  2. 否则用「当前页面的主机名 + 各模块端口」推导
// 这样用服务器 IP / 域名访问时，子模块链接会自动指向同一台机器，
// 而不会像硬编码 127.0.0.1 那样指向访问者自己的电脑。
const ENV = import.meta.env as unknown as Record<string, string | undefined>

function moduleUrl(envKey: string, port: number, path = '/'): string {
  const override = (ENV[envKey] || '').trim()
  if (override) return override
  const protocol = typeof window !== 'undefined' ? window.location.protocol : 'http:'
  const hostname =
    typeof window !== 'undefined' && window.location.hostname ? window.location.hostname : '127.0.0.1'
  return `${protocol}//${hostname}:${port}${path}`
}

const services: Service[] = [
  {
    id: 'literature',
    name: '文献知识库',
    eyebrow: '01 / Literature',
    description: '抓取 PubMed 文献，沉淀到 ai-localbase 向量库并支持 RAG 检索。',
    url: moduleUrl('VITE_LITERATURE_UI_URL', 8080),
    healthUrl: moduleUrl('VITE_LITERATURE_HEALTH_URL', 8080, '/health'),
    tone: 'green',
    icon: BookOpen,
  },
  {
    id: 'analysis',
    name: '数据分析',
    eyebrow: '02 / Analysis',
    description: '上传宏基因组数据，运行质控、物种注释、功能注释与结果打包。',
    url: moduleUrl('VITE_ANALYSIS_UI_URL', 5173),
    // 分析后端（uvicorn :8000）默认只监听 127.0.0.1，外部浏览器直连不到。
    // 前端 Vite dev server 已配置 proxy('/api' -> 127.0.0.1:8000)，
    // 因此这里经 5173 的 /api/health 探活，等价于「UI + 后端」整体可用性。
    healthUrl: moduleUrl('VITE_ANALYSIS_HEALTH_URL', 5173, '/api/health'),
    tone: 'blue',
    icon: FlaskConical,
  },
  {
    id: 'writing',
    name: '论文撰写',
    eyebrow: '03 / Writing',
    description: '把文献证据和分析结果组织成可审阅、可导出的科研论文。',
    url: moduleUrl('VITE_WRITING_UI_URL', 8765),
    healthUrl: moduleUrl('VITE_WRITING_HEALTH_URL', 8765, '/api/health'),
    tone: 'coral',
    icon: FilePenLine,
  },
]

const stages = [
  { label: '检索文献', detail: 'PubMed → 向量库', icon: BookOpen, tone: 'green' },
  { label: '运行分析', detail: 'FASTQ → 结果包', icon: FlaskConical, tone: 'blue' },
  { label: '生成论文', detail: '证据 → IMRaD', icon: FilePenLine, tone: 'coral' },
]

function App() {
  const [states, setStates] = useState<Record<string, ServiceState>>(
    Object.fromEntries(services.map((service) => [service.id, 'checking'])),
  )

  async function checkServices() {
    setStates(Object.fromEntries(services.map((service) => [service.id, 'checking'])))
    const results = await Promise.all(
      services.map(async (service) => {
        try {
          await fetch(service.healthUrl, { mode: 'no-cors', signal: AbortSignal.timeout(3000) })
          return [service.id, 'online'] as const
        } catch {
          return [service.id, 'offline'] as const
        }
      }),
    )
    setStates(Object.fromEntries(results))
  }

  useEffect(() => {
    void checkServices()
  }, [])

  const onlineCount = Object.values(states).filter((state) => state === 'online').length

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#top" aria-label="BioFLow 首页">
          <span className="brand-mark"><Dna size={23} /></span>
          <span><b>BioFLow</b><small>ASD · GUT MICROBIOME</small></span>
        </a>
        <nav className="side-nav" aria-label="主导航">
          <a className="active" href="#top"><LayoutDashboard size={17} />研究总览</a>
          <a href="#modules"><Workflow size={17} />三大模块</a>
          <a href="#workflow"><Sparkles size={17} />工作流程</a>
        </nav>
        <div className="side-note">
          <Activity size={17} />
          <div><b>本地科研工作台</b><span>数据留在你的服务器</span></div>
        </div>
        <div className="side-footer">BioFLow / Research only</div>
      </aside>

      <main id="top" className="main-content">
        <header className="topbar">
          <div className="breadcrumb"><span>WORKSPACE</span><i>/</i><b>研究总览</b></div>
          <div className="topbar-status"><span className="status-dot" />{onlineCount}/{services.length} 个服务在线</div>
        </header>

        <section className="hero">
          <div className="hero-copy">
            <div className="kicker"><span /> AUTISM · MICROBIOME · PAPER</div>
            <h1>把一项研究，<em>走完。</em></h1>
            <p>从文献发现到微生物组分析，再到论文成稿。BioFLow 把分散的科研动作连接成一条可追溯的工作路径。</p>
            <div className="hero-actions"><a className="primary-button" href="#modules">进入工作台 <ArrowUpRight size={17} /></a><a className="text-link" href="#workflow">查看流程 <ArrowUpRight size={15} /></a></div>
          </div>
          <div className="hero-visual" aria-hidden="true">
            <div className="visual-orbit orbit-one" /><div className="visual-orbit orbit-two" />
            <div className="visual-core"><Dna size={58} strokeWidth={1.25} /><span>研究<br />链路</span></div>
            <span className="float-label label-a">evidence</span><span className="float-label label-b">analysis</span><span className="float-label label-c">writing</span>
          </div>
        </section>

        <section id="modules" className="section-block">
          <div className="section-heading"><div><span className="section-index">01</span><h2>三大模块</h2></div><p>保留各模块原有前端，统一工作台负责导航和状态概览。</p></div>
          <div className="module-grid">{services.map((service) => <ModuleCard key={service.id} service={service} state={states[service.id]} />)}</div>
        </section>

        <section id="workflow" className="section-block workflow-block">
          <div className="section-heading"><div><span className="section-index">02</span><h2>研究工作流</h2></div><p>每一步都留下输入、参数与可复核的结果产物。</p></div>
          <div className="workflow-track">{stages.map((stage, index) => { const Icon = stage.icon; return <div className="workflow-stage" key={stage.label}><div className={`stage-icon ${stage.tone}`}><Icon size={21} /></div><div><b>{stage.label}</b><span>{stage.detail}</span></div>{index < stages.length - 1 && <div className="stage-line" />}</div> })}</div>
        </section>

        <footer className="main-footer"><span><Server size={15} /> 服务状态来自本地 API</span><button onClick={() => void checkServices()}><RefreshCw size={15} />重新检查</button></footer>
      </main>
    </div>
  )
}

function ModuleCard({ service, state }: { service: Service; state: ServiceState }) {
  const Icon = service.icon
  const statusLabel = state === 'online' ? '在线' : state === 'offline' ? '离线' : '检测中'
  return <article className={`module-card ${service.tone}`}>
    <div className="module-card-top"><span className="module-icon"><Icon size={22} /></span><span className={`service-state ${state}`}><i />{statusLabel}</span></div>
    <span className="module-eyebrow">{service.eyebrow}</span><h3>{service.name}</h3><p>{service.description}</p>
    <a className="module-link" href={service.url} target="_blank" rel="noreferrer">打开模块 <ArrowUpRight size={16} /></a>
  </article>
}

import { useEffect, useState } from 'react'

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>)
