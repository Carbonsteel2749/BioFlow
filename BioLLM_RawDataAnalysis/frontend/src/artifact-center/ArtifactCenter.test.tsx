import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/tasks'
import { ArtifactCenter } from './ArtifactCenter'
import { loadMockArtifacts } from './mockArtifacts'
import type { ArtifactCenterLoader } from './types'

describe('ArtifactCenter', () => {
  const renderMock = () => render(<ArtifactCenter taskId="task-1" loader={loadMockArtifacts} />)

  it('清单格式异常时显示错误而不是使任务页面崩溃', async () => {
    const malformed = async () => ({ artifacts: {}, notices: [] })
    render(<ArtifactCenter taskId="task-1" loader={malformed as unknown as ArtifactCenterLoader} />)
    expect(await screen.findByText('结果加载失败')).toBeInTheDocument()
  })

  it('按质控、物种、功能、MAG 和报告分类展示产物', async () => {
    renderMock()
    await screen.findByText('S01 Reads 质量分布')
    for (const category of ['质控', '物种', '功能', 'MAG', '报告']) {
      expect(screen.getByRole('button', { name: new RegExp(category) })).toBeInTheDocument()
    }
    await userEvent.click(screen.getByRole('button', { name: /^物种/ }))
    expect(screen.getByText('物种组成 Top 20')).toBeInTheDocument()
    expect(screen.getByText('物种丰度表')).toBeInTheDocument()
  })

  it('支持按样本、artifact 类型和生成节点筛选', async () => {
    renderMock()
    await screen.findByText('S01 Reads 质量分布')
    await userEvent.selectOptions(screen.getByLabelText('按样本筛选'), 'S02')
    expect(screen.getByText('S02 Reads 质量分布')).toBeInTheDocument()
    expect(screen.queryByText('S01 Reads 质量分布')).not.toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('按样本筛选'), 'all')
    await userEvent.selectOptions(screen.getByLabelText('按 artifact 类型筛选'), 'qc.read_counts')
    expect(screen.getByText('S01 Reads 过滤统计')).toBeInTheDocument()
    await userEvent.selectOptions(screen.getByLabelText('按生成节点筛选'), 'fastp')
    expect(screen.getAllByTestId('artifact-card')).toHaveLength(1)
  })

  it('通过 Artifact API URL 预览 PNG/SVG 并下载文件', async () => {
    renderMock()
    await screen.findByText('S01 Reads 质量分布')
    const card = screen.getByText('S01 Reads 质量分布').closest<HTMLElement>('[data-testid="artifact-card"]')!
    expect(within(card).getByRole('link', { name: '下载' })).toHaveAttribute('href', '/api/artifacts/mock-artifact-1/download')
    await userEvent.click(within(card).getByRole('button', { name: /预览/ }))
    expect(screen.getByRole('img', { name: 'S01 Reads 质量分布' })).toHaveAttribute('src', '/api/artifacts/mock-artifact-1/download')
  })

  it('展示图表来源、参数和论文用途标签', async () => {
    renderMock()
    await screen.findByText('S01 Reads 质量分布')
    await userEvent.click(screen.getByRole('button', { name: /预览/ }))
    const dialog = screen.getByRole('dialog', { name: '产物详情' })
    expect(within(dialog).getByText('FastQC 0.12.1')).toBeInTheDocument()
    expect(within(dialog).getByText(/"encoding": "Sanger \/ Illumina 1.9"/)).toBeInTheDocument()
    expect(within(dialog).getByText('Method')).toBeInTheDocument()
    expect(within(dialog).getByText('Result')).toBeInTheDocument()
  })

  it('展示部分跳过、生成失败、校验失败和文件不存在', async () => {
    renderMock()
    await screen.findByText('S01 Reads 质量分布')
    expect(screen.getByText('文件不存在')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /^MAG/ }))
    expect(screen.getByText('未启用候选 Bin 重组装，对应图表已跳过。')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /^功能/ }))
    expect(screen.getByText(/S02 功能通路图生成失败/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /^报告/ }))
    expect(screen.getByText(/MultiQC 报告未通过完整性校验/)).toBeInTheDocument()
  })

  it('完整处理加载中、无结果和接口不可用', async () => {
    const pending: ArtifactCenterLoader = () => new Promise(() => undefined)
    const empty: ArtifactCenterLoader = async () => ({ artifacts: [], notices: [] })
    const unavailable: ArtifactCenterLoader = async () => { throw new ApiError(503, 'offline') }
    const view = render(<ArtifactCenter taskId="task-1" loader={pending} />)
    expect(screen.getByText('正在加载任务产物')).toBeInTheDocument()
    view.unmount()

    render(<ArtifactCenter taskId="task-1" loader={empty} />)
    expect(await screen.findByText('暂无分析结果')).toBeInTheDocument()

    render(<ArtifactCenter taskId="task-1" loader={unavailable} />)
    expect(await screen.findByText('Artifact 接口不可用')).toBeInTheDocument()
  })

  it('预览 API 返回文件缺失时显示可操作状态', async () => {
    renderMock()
    await screen.findByText('S01 Reads 质量分布')
    await userEvent.click(screen.getByRole('button', { name: /预览/ }))
    const image = screen.getByRole('img', { name: 'S01 Reads 质量分布' })
    image.dispatchEvent(new Event('error'))
    await waitFor(() => expect(screen.getByText('预览文件不存在')).toBeInTheDocument())
  })
})
