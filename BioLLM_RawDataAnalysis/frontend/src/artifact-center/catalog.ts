import type { Artifact, ArtifactCategory } from './types'

export const CATEGORIES: Array<{ id: ArtifactCategory; label: string; description: string }> = [
  { id: 'qc', label: '质控', description: 'Reads 质量、数量与过滤统计' },
  { id: 'taxonomy', label: '物种', description: '物种组成与丰度分析' },
  { id: 'functional', label: '功能', description: 'KO、EC 与通路分析' },
  { id: 'mag', label: 'MAG', description: '组装、分箱、注释与定量' },
  { id: 'report', label: '报告', description: '综合报告与运行溯源' },
]

export function artifactCategory(artifactType: string): ArtifactCategory {
  if (artifactType.startsWith('qc.') || artifactType === 'figure.qc') return 'qc'
  if (artifactType.startsWith('taxonomy.') || artifactType === 'figure.taxonomy') return 'taxonomy'
  if (artifactType.startsWith('functional.') || artifactType === 'figure.functional') return 'functional'
  if (artifactType.startsWith('mag.') || artifactType === 'figure.mag') return 'mag'
  return 'report'
}

export function isPreviewable(artifact: Artifact) {
  return artifact.status === 'active' && ['image/png', 'image/svg+xml'].includes(artifact.media_type)
}

export function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function figureTitle(artifact: Artifact) {
  if (artifact.metadata.title) return artifact.metadata.title
  const names: Record<string,string> = {
    taxonomy_top20_stacked:'物种组成堆叠图', taxonomy_species_heatmap:'物种相对丰度热图',
    taxonomy_alpha_diversity:'样本内 α 多样性', taxonomy_bray_curtis_pcoa:'Bray–Curtis 样本间排序',
  }
  const stem=artifact.file_name.replace(/\.png$/, '')
  if(names[stem]) return names[stem]
  const match=stem.match(/^functional_(gene_families|ko|ec|pathway_abundance|pathway_coverage)_top(\d+)_(heatmap|composition)$/)
  if(match) return `${({gene_families:'基因家族',ko:'KO',ec:'EC',pathway_abundance:'通路丰度',pathway_coverage:'通路覆盖度'} as Record<string,string>)[match[1]]} Top ${match[2]}${match[3]==='heatmap'?'热图':'组成图'}`
  return artifact.file_name
}

export function figureCaption(artifact: Artifact) {
  if(artifact.metadata.description) return artifact.metadata.description
  if(artifact.artifact_type==='figure.functional') {
    if(artifact.metadata.analysis_version!=='2.0') return '历史绘图口径：可能包含未注释类别；通路覆盖度不应解释为组成比例。请结合修订版及原始结果表复核。'
    if(artifact.file_name.includes('pathway_coverage')) return '显示已命名通路的 HUMAnN 覆盖度原值（0–1），不求和归一化，也不代表通路丰度或活性。'
    return '仅展示已命名功能，未注释诊断条目另列于初步解读。热图使用 log1p 原始丰度；组成图以已命名条目丰度之和为分母，仅用于样本内描述，不代表表达活性或特定菌种功能。'
  }
  if(artifact.file_name.includes('alpha_diversity')) return '根据已检出物种丰度表计算的样本内多样性描述；受测序深度和参考数据库影响，不代表健康程度，未经分组检验不能声称组间差异。'
  if(artifact.artifact_type==='figure.taxonomy') return '描述当前数据库与分类参数下的物种相对组成；不是绝对菌量，不支持因果或疾病诊断结论。'
  if(artifact.artifact_type==='figure.mag') return 'MAG 注释描述联合组装基因组；只有读段回贴定量才支持样本与 MAG 的检出关系，不能将一个 MAG 唯一归属于一个样本。'
  return '请结合来源数据、生成参数和运行溯源记录解读。'
}
