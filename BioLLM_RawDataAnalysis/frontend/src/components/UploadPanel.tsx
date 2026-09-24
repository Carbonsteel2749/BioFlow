import { useMemo, useRef, useState } from 'react'
import { AlertCircle, Check, FileUp, LoaderCircle, Plus, ShieldCheck, Trash2, UploadCloud, X } from 'lucide-react'
import { ApiError, tasksApi, uploadFastq } from '../api/tasks'

type Mate = 'R1' | 'R2'
type LocalStatus = 'ready' | 'uploading' | 'uploaded' | 'failed'

interface LocalFastq {
  key: string
  file: File
  sampleId: string | null
  mate: Mate | null
  pairState: 'paired' | 'missing' | 'duplicate' | 'invalid'
  status: LocalStatus
  progress: number
  error?: string
}

function parseFastq(name: string): { sampleId: string; mate: Mate } | null {
  const stem = name.replace(/\.(fastq|fq)(\.gz)?$/i, '')
  if (stem === name) return null
  const match = stem.match(/^(.+?)[_.-]R?([12])(?:[_.-]?\d+)?$/i)
  if (!match || !match[1]) return null
  return { sampleId: match[1], mate: match[2] === '1' ? 'R1' : 'R2' }
}

function classify(items: LocalFastq[]): LocalFastq[] {
  const counts = new Map<string, number>()
  for (const item of items) if (item.sampleId && item.mate) counts.set(`${item.sampleId}:${item.mate}`, (counts.get(`${item.sampleId}:${item.mate}`) ?? 0) + 1)
  const mates = new Map<string, Set<Mate>>()
  for (const item of items) if (item.sampleId && item.mate) {
    if (!mates.has(item.sampleId)) mates.set(item.sampleId, new Set())
    mates.get(item.sampleId)!.add(item.mate)
  }
  return items.map(item => ({
    ...item,
    pairState: !item.sampleId || !item.mate ? 'invalid'
      : (counts.get(`${item.sampleId}:${item.mate}`) ?? 0) > 1 ? 'duplicate'
      : mates.get(item.sampleId)?.size === 2 ? 'paired' : 'missing',
  }))
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

const pairLabels = { paired: '配对完整', missing: '缺少配对文件', duplicate: '存在重复端', invalid: '文件名无法识别' }

export function UploadPanel({ supported, onManifest }: { supported: boolean; onManifest: (path: string, samples: number, datasetId?: string) => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<LocalFastq[]>([])
  const [dragging, setDragging] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [globalError, setGlobalError] = useState<string | null>(null)
  const [datasetName,setDatasetName] = useState('')
  const pairs = useMemo(() => new Set(files.filter(item => item.pairState === 'paired').map(item => item.sampleId)).size, [files])
  const allValid = files.length >= 2 && files.every(item => item.pairState === 'paired')

  const addFiles = (selected: FileList | File[]) => {
    setGlobalError(null)
    setFiles(current => {
      const known = new Set(current.map(item => item.key))
      const added = Array.from(selected).flatMap(file => {
        const key = `${file.name}:${file.size}:${file.lastModified}`
        if (known.has(key)) return []
        const parsed = parseFastq(file.name)
        return [{ key, file, sampleId: parsed?.sampleId ?? null, mate: parsed?.mate ?? null, pairState: 'invalid' as const, status: 'ready' as const, progress: 0 }]
      })
      return classify([...current, ...added])
    })
  }
  const remove = (key: string) => setFiles(current => classify(current.filter(item => item.key !== key)))
  const patch = (key: string, fields: Partial<LocalFastq>) => setFiles(current => current.map(item => item.key === key ? { ...item, ...fields } : item))

  const upload = async () => {
    if (!supported) { setGlobalError('服务端尚未开放 FASTQ 上传接口，请联系管理员升级后端。'); return }
    if (!allValid) { setGlobalError('请先补齐或移除未正确配对的 FASTQ 文件。'); return }
    setSubmitting(true); setGlobalError(null)
    const uploadIds = new Map<string, string>()
    try {
      await Promise.all(files.map(async item => {
        patch(item.key, { status: 'uploading', progress: 0, error: undefined })
        try {
          const uploaded = await uploadFastq(item.file, progress => patch(item.key, { progress }))
          uploadIds.set(item.key, uploaded.id)
          patch(item.key, { status: 'uploaded', progress: 100 })
        } catch (error) {
          patch(item.key, { status: 'failed', error: error instanceof Error ? error.message : '上传失败' })
          throw error
        }
      }))
      const grouped = new Map<string, Partial<Record<Mate, string>>>()
      for (const item of files) {
        const sample = grouped.get(item.sampleId!) ?? {}
        sample[item.mate!] = uploadIds.get(item.key)!
        grouped.set(item.sampleId!, sample)
      }
      const manifest = await tasksApi.createUploadedManifest([...grouped].map(([sample_id, item]) => ({ sample_id, read1_upload_id: item.R1!, read2_upload_id: item.R2! })),datasetName.trim()||undefined)
      onManifest(manifest.manifest_path, manifest.sample_count, manifest.dataset_id)
    } catch (error) {
      setGlobalError(error instanceof ApiError ? error.message : '部分文件上传失败，请检查失败项后重试。')
    } finally { setSubmitting(false) }
  }

  return <div className="upload-panel">
    <label className="field"><span>数据集名称（可选）</span><input value={datasetName} maxLength={120} disabled={submitting} onChange={e=>setDatasetName(e.target.value)} placeholder="例如：医院测序批次 01" /></label>
    <div className={`upload-dropzone ${dragging ? 'dragging' : ''}`} onDragOver={event => { event.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={event => { event.preventDefault(); setDragging(false); addFiles(event.dataTransfer.files) }}>
      <input ref={input} type="file" multiple accept=".fastq,.fq,.fastq.gz,.fq.gz" onChange={event => event.target.files && addFiles(event.target.files)} />
      <div className="upload-cloud"><UploadCloud /></div><div><b>选择或拖入双端 FASTQ 文件</b><p>支持同时选择多个样本，系统会根据 R1/R2 文件名自动配对</p></div>
      <button type="button" className="btn secondary" onClick={() => input.current?.click()}><Plus />选择文件</button>
    </div>
    {!supported && <div className="upload-capability-warning"><AlertCircle /><div><b>服务器上传能力尚未就绪</b><span>你仍可选择文件预览配对结果；正式上传需后端实现上传接口后启用。</span></div></div>}
    {files.length > 0 && <><div className="upload-summary"><div><b>{files.length}</b><span>个文件</span></div><div><b>{pairs}</b><span>个完整样本</span></div><div className={allValid ? 'valid' : 'invalid'}>{allValid ? <Check /> : <AlertCircle />}<span>{allValid ? '配对校验通过' : '请处理配对问题'}</span></div><button type="button" onClick={() => setFiles([])} disabled={submitting}><Trash2 />清空</button></div>
      <div className="upload-file-list">{files.map(item => <div className={`upload-file ${item.pairState} ${item.status}`} key={item.key}>
        <div className="fastq-icon">{item.mate ?? '?'}</div><div className="upload-file-main"><div><b title={item.file.name}>{item.file.name}</b><span>{formatBytes(item.file.size)}</span></div><div className="file-meta"><span>{item.sampleId ? `样本 ${item.sampleId}` : '无法识别样本名'}</span><span className={`pair-state ${item.pairState}`}>{pairLabels[item.pairState]}</span>{item.status === 'failed' && <span className="upload-error">{item.error}</span>}</div>{item.status === 'uploading' && <div className="upload-progress"><i style={{ width: `${item.progress}%` }} /><span>{item.progress}%</span></div>}</div>
        <div className="file-status">{item.status === 'uploading' ? <LoaderCircle className="spin" /> : item.status === 'uploaded' ? <Check /> : item.status === 'failed' ? <AlertCircle /> : null}</div><button type="button" className="remove-file" disabled={submitting} onClick={() => remove(item.key)} aria-label={`移除 ${item.file.name}`}><X /></button>
      </div>)}</div></>}
    {globalError && <div className="inline-error upload-global-error"><AlertCircle />{globalError}</div>}
    {files.length > 0 && <div className="upload-actions"><div><ShieldCheck /><span>文件上传后将在服务器内完成校验，不会发送到外部服务</span></div><button type="button" className="btn primary" disabled={!allValid || submitting || !supported} onClick={upload}>{submitting ? <><LoaderCircle className="spin" />正在上传…</> : <><FileUp />上传并生成样本清单</>}</button></div>}
  </div>
}
