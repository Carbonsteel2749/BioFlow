import type { Artifact, ArtifactCenterPayload, ArtifactMetadata, ArtifactStatus } from './types'

const digest = 'a'.repeat(64)
let index = 0

function artifact(
  type: string,
  fileName: string,
  mediaType: string,
  nodeId: string,
  sampleId: string | null,
  metadata: ArtifactMetadata,
  status: ArtifactStatus = 'active',
): Artifact {
  index += 1
  return {
    artifact_id: `mock-artifact-${index}`,
    task_id: 'mock-task',
    node_id: nodeId,
    producer: 'biollm-pipeline',
    artifact_type: type,
    schema_version: '1.0',
    sample_scope: sampleId ? 'sample' : 'cohort',
    sample_id: sampleId,
    cohort_id: sampleId ? null : 'cohort-01',
    media_type: mediaType,
    file_name: fileName,
    sha256: digest,
    size_bytes: mediaType === 'application/pdf' ? 2_840_000 : 84_200,
    metadata,
    downloadable: true,
    status,
    created_at: '2026-09-08T08:30:00Z',
    derived_from: [],
    download_url: `/api/artifacts/mock-artifact-${index}/download`,
  }
}

export const MOCK_ARTIFACT_PAYLOAD: ArtifactCenterPayload = {
  artifacts: [
    artifact('figure.qc', 'S01-read-quality.png', 'image/png', 'fastqc_raw', 'S01', { title: 'S01 Reads 质量分布', source: 'FastQC 0.12.1', parameters: { encoding: 'Sanger / Illumina 1.9' }, paper_sections: ['Method', 'Result'] }),
    artifact('qc.read_counts', 'S01-read-counts.tsv', 'text/tab-separated-values', 'fastp', 'S01', { title: 'S01 Reads 过滤统计', source: 'fastp 0.23.4', parameters: { qualified_quality_phred: 20 }, paper_sections: ['Method', 'Result'] }),
    artifact('figure.taxonomy', 'species-composition.svg', 'image/svg+xml', 'taxonomy', null, { title: '物种组成 Top 20', source: 'Kraken2 + Bracken', parameters: { rank: 'species', top_n: 20 }, paper_sections: ['Result', 'Discussion'] }),
    artifact('taxonomy.species_abundance', 'species-abundance.tsv', 'text/tab-separated-values', 'taxonomy', null, { title: '物种丰度表', source: 'Bracken 2.9', parameters: { read_length: 150 }, paper_sections: ['Result'] }),
    artifact('figure.functional', 'pathway-abundance.png', 'image/png', 'functional_annotation', null, { title: '代谢通路丰度', source: 'HUMAnN 3.9', parameters: { normalization: 'relative abundance' }, paper_sections: ['Result', 'Discussion'] }),
    artifact('functional.ko_abundance', 'ko-abundance.tsv', 'text/tab-separated-values', 'functional_annotation', null, { title: 'KO 丰度表', source: 'HUMAnN regroup', parameters: { group: 'uniref90_ko' }, paper_sections: ['Method', 'Result'] }),
    artifact('figure.mag', 'mag-quality.png', 'image/png', 'bin_refinement', null, { title: 'MAG 完整度与污染度', source: 'CheckM2', parameters: { completeness: 70, contamination: 5 }, paper_sections: ['Method', 'Result'] }),
    artifact('mag.annotation', 'mag-annotation.tsv', 'text/tab-separated-values', 'bin_annotation', null, { title: 'MAG 物种注释', source: 'GTDB-Tk', parameters: { release: 'R220' }, paper_sections: ['Result', 'Discussion'] }),
    artifact('report.summary', 'analysis-summary.pdf', 'application/pdf', 'report', null, { title: '分析结果汇总', source: 'BioLLM report', parameters: { template: 'standard' }, paper_sections: ['Introduction', 'Method', 'Result', 'Discussion'] }),
    artifact('figure.qc', 'S02-read-quality.png', 'image/png', 'fastqc_raw', 'S02', { title: 'S02 Reads 质量分布', source: 'FastQC 0.12.1', error_message: '服务器上的产物文件已不存在', paper_sections: ['Result'] }, 'missing'),
  ],
  notices: [
    { id: 'skip-1', category: 'mag', artifact_type: 'figure.mag', node_id: 'bin_reassembly', sample_id: null, state: 'skipped', message: '未启用候选 Bin 重组装，对应图表已跳过。' },
    { id: 'fail-1', category: 'functional', artifact_type: 'figure.functional', node_id: 'functional_annotation', sample_id: 'S02', state: 'generation_failed', message: 'S02 功能通路图生成失败：上游表格为空。' },
    { id: 'invalid-1', category: 'report', artifact_type: 'report.multiqc', node_id: 'report', sample_id: null, state: 'validation_failed', message: 'MultiQC 报告未通过完整性校验，已禁止下载。' },
  ],
}

export async function loadMockArtifacts(taskId: string): Promise<ArtifactCenterPayload> {
  return {
    artifacts: MOCK_ARTIFACT_PAYLOAD.artifacts.map(item => ({ ...item, task_id: taskId })),
    notices: MOCK_ARTIFACT_PAYLOAD.notices,
  }
}
