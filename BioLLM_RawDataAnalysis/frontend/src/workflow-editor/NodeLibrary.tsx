import { Boxes, Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { categoryLabel } from './registry'
import type { NodeDefinition, WorkflowTemplateSummary } from './types'

interface Props {
  definitions: NodeDefinition[]
  templates: WorkflowTemplateSummary[]
  loadingTemplate: boolean
  onLoadTemplate: (template: WorkflowTemplateSummary) => void
}

const TEMPLATE_ALIASES: Record<string, string> = {
  'builtin-read-profile': '标准宏基因组',
  'builtin-mag': '标准宏基因组＋MAG',
}

export function NodeLibrary({ definitions, templates, loadingTemplate, onLoadTemplate }: Props) {
  const [query, setQuery] = useState('')
  const grouped = useMemo(() => {
    const normalized = query.trim().toLowerCase()
    return definitions
      .filter(item => !normalized || `${item.label} ${item.type} ${item.category}`.toLowerCase().includes(normalized))
      .reduce<Record<string, NodeDefinition[]>>((result, item) => {
        ;(result[item.category] ??= []).push(item)
        return result
      }, {})
  }, [definitions, query])
  const builtins = templates.filter(template => template.source === 'builtin')

  return <aside className="wf-library" aria-label="节点库">
    <header><div className="wf-section-icon"><Boxes size={18} /></div><div><b>节点库</b><span>拖入画布以添加</span></div></header>
    <label className="wf-search"><Search size={15} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索节点" /></label>
    <div className="wf-template-list">
      <span className="wf-eyebrow">快速开始</span>
      {builtins.map(template => <button
        type="button"
        key={template.template_id}
        disabled={loadingTemplate}
        onClick={() => onLoadTemplate(template)}
      ><b>{TEMPLATE_ALIASES[template.template_id] ?? template.name}</b><small>{template.description}</small></button>)}
      {!builtins.length && <small className="wf-muted">后端未返回内置模板</small>}
    </div>
    <div className="wf-library-groups">
      {Object.entries(grouped).map(([category, items]) => <section key={category}>
        <h3>{categoryLabel(category)}<span>{items.length}</span></h3>
        {items.map(item => <button
          type="button"
          className={`wf-library-node category-${category}`}
          key={item.type}
          draggable
          onDragStart={event => {
            event.dataTransfer.setData('application/x-workflow-node', item.type)
            event.dataTransfer.effectAllowed = 'copy'
          }}
          title={`${item.inputs.length} 输入 · ${item.outputs.length} 输出`}
        ><i /><span><b>{item.label}</b><small>{item.type}</small></span><em>{item.version}</em></button>)}
      </section>)}
    </div>
  </aside>
}
