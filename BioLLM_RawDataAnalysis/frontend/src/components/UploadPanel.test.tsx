import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import { UploadPanel } from './UploadPanel'

afterEach(() => vi.unstubAllGlobals())

function selectPair(container: HTMLElement) {
  const input = container.querySelector('input[type="file"]') as HTMLInputElement
  const r1 = new File(['r1'], 'patient-free_R1_001.fastq.gz', { type: 'application/gzip' })
  const r2 = new File(['r2'], 'patient-free_R2_001.fastq.gz', { type: 'application/gzip' })
  fireEvent.change(input, { target: { files: [r1, r2] } })
}

test('自动配对 R1/R2 并展示文件大小与校验结果', async () => {
  const { container } = render(<UploadPanel supported={false} onManifest={vi.fn()} />)
  selectPair(container)

  expect(await screen.findByText('patient-free_R1_001.fastq.gz')).toBeInTheDocument()
  expect(screen.getByText('patient-free_R2_001.fastq.gz')).toBeInTheDocument()
  expect(screen.getByText('2', { selector: '.upload-summary b' })).toBeInTheDocument()
  expect(screen.getByText('1', { selector: '.upload-summary b' })).toBeInTheDocument()
  expect(screen.getAllByText('配对完整')).toHaveLength(2)
  expect(screen.getByText('配对校验通过')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '上传并生成样本清单' })).toBeDisabled()
})

test('单个文件上传失败时展示服务端原因', async () => {
  class FailingXhr {
    upload: { onprogress?: (event: ProgressEvent) => void } = {}
    responseType = ''
    response: unknown = null
    status = 0
    onerror?: () => void
    onabort?: () => void
    onload?: () => void
    open() {}
    send() {
      this.upload.onprogress?.({ lengthComputable: true, loaded: 30, total: 100 } as ProgressEvent)
      this.status = 507
      this.response = { detail: '服务器临时存储空间不足' }
      queueMicrotask(() => this.onload?.())
    }
  }
  vi.stubGlobal('XMLHttpRequest', FailingXhr)
  const { container } = render(<UploadPanel supported onManifest={vi.fn()} />)
  selectPair(container)
  await userEvent.click(await screen.findByRole('button', { name: '上传并生成样本清单' }))

  await waitFor(() => expect(screen.getAllByText('服务器临时存储空间不足').length).toBeGreaterThan(0))
  expect(screen.getAllByText('服务器临时存储空间不足').length).toBeGreaterThanOrEqual(2)
})
