import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, test, vi } from 'vitest'
import { TaskCleanupActions } from './TaskCleanupActions'
import { makeTask } from '../test/fixtures'

const preview = { token: 'confirmed-token', estimated_bytes: 4096, scope: ['work/test'], preserves_raw_reads: true }
function mount(status: 'cancelled' | 'running' = 'cancelled') {
  const task = makeTask({ status })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(<QueryClientProvider client={client}><TaskCleanupActions task={task} /></QueryClientProvider>)
  return task
}
function json(value: unknown, status = 200) { return Promise.resolve(new Response(JSON.stringify(value), { status })) }
afterEach(() => vi.unstubAllGlobals())

test('运行中的任务不能直接删除', () => {
  mount('running')
  expect(screen.getByRole('button', { name: /删除或清理任务/ })).toBeDisabled()
})

test('预览后确认才执行清理，并传递范围令牌', async () => {
  const fetcher = vi.fn((_input: RequestInfo | URL, options?: RequestInit) => options?.method === 'POST' ? json({ status: 'cleaned', removed_bytes: 4096 }) : json(preview))
  vi.stubGlobal('fetch', fetcher)
  const task = mount()
  await userEvent.click(screen.getByRole('button', { name: /删除或清理任务/ }))
  expect(await screen.findByText('约 4.0 KB')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '确认清理' })).toBeDisabled()
  expect(fetcher.mock.calls.every(([, options]) => options?.method !== 'POST')).toBe(true)
  await userEvent.click(screen.getByRole('checkbox'))
  await userEvent.click(screen.getByRole('button', { name: '确认清理' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  const call = fetcher.mock.calls.find(([, options]) => options?.method === 'POST')!
  expect(JSON.parse(call[1]!.body as string)).toEqual({ mode: 'cache', confirmation: task.id, token: 'confirmed-token' })
})

test('完整删除需要输入任务ID，引用错误不会被忽略', async () => {
  vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => String(input).includes('mode=delete') ? json({ detail: '结果仍被 paper/draft 引用' }, 409) : json(preview)))
  mount()
  await userEvent.click(screen.getByRole('button', { name: /删除或清理任务/ }))
  await userEvent.click(screen.getByRole('radio', { name: /删除任务及专属文件/ }))
  expect(await screen.findByText('结果仍被 paper/draft 引用')).toBeInTheDocument()
  expect(screen.getByLabelText('输入完整任务 ID')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '确认删除' })).toBeDisabled()
})

test('文件清理失败时保留弹窗，显示失败原因', async () => {
  vi.stubGlobal('fetch', vi.fn((_input: RequestInfo | URL, options?: RequestInit) => options?.method === 'POST' ? json({ detail: '文件清理失败，任务记录仍保留' }, 409) : json(preview)))
  mount()
  await userEvent.click(screen.getByRole('button', { name: /删除或清理任务/ }))
  await screen.findByText('约 4.0 KB')
  await userEvent.click(screen.getByRole('checkbox'))
  await userEvent.click(screen.getByRole('button', { name: '确认清理' }))
  expect(await screen.findByText('文件清理失败，任务记录仍保留')).toBeInTheDocument()
  expect(screen.getByRole('dialog')).toBeInTheDocument()
})

test('完整删除必须勾选确认并准确输入ID', async () => {
  const fetcher = vi.fn((_input: RequestInfo | URL, options?: RequestInit) => options?.method === 'POST' ? json({ status: 'deleted', removed_bytes: 4096 }) : json(preview))
  vi.stubGlobal('fetch', fetcher)
  const task = mount()
  await userEvent.click(screen.getByRole('button', { name: /删除或清理任务/ }))
  await userEvent.click(screen.getByRole('radio', { name: /删除任务及专属文件/ }))
  await screen.findByText('约 4.0 KB')
  await userEvent.click(screen.getByRole('checkbox', {name: /我已确认范围/}))
  await userEvent.type(screen.getByLabelText('输入完整任务 ID'), 'wrong')
  expect(screen.getByRole('button', { name: '确认删除' })).toBeDisabled()
  await userEvent.clear(screen.getByLabelText('输入完整任务 ID'))
  await userEvent.type(screen.getByLabelText('输入完整任务 ID'), task.id)
  await userEvent.click(screen.getByRole('button', { name: '确认删除' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  const call = fetcher.mock.calls.find(([, options]) => options?.method === 'POST')!
  expect(JSON.parse(call[1]!.body as string).mode).toBe('delete')
})

test('同步删除默认关闭，改变范围后重新预览并展示共享保留原因', async () => {
  const fetcher = vi.fn((input: RequestInfo | URL, options?: RequestInit) => options?.method === 'POST' ? json({status:'deleted',removed_bytes:4096}) : json({...preview, dataset_cleanup:String(input).includes('delete_dataset=true')?{will_delete:false,reasons:['仍被任务 other 引用，保留数据'],scope:[],estimated_bytes:0}:null}))
  vi.stubGlobal('fetch',fetcher)
  const task=mount()
  await userEvent.click(screen.getByRole('button',{name:/删除或清理任务/}))
  await userEvent.click(screen.getByRole('radio',{name:/删除任务及专属文件/}))
  const sync=screen.getByRole('checkbox',{name:/同时删除关联数据与样本/})
  expect(sync).not.toBeChecked()
  await userEvent.click(sync)
  expect(await screen.findByText('仍被任务 other 引用，保留数据')).toBeInTheDocument()
  await userEvent.type(screen.getByLabelText('输入完整任务 ID'),task.id)
  await userEvent.click(screen.getByRole('checkbox',{name:/我已确认范围/}))
  await userEvent.click(screen.getByRole('button',{name:'确认删除'}))
  await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  const call=fetcher.mock.calls.find(([,options])=>options?.method==='POST')!
  expect(JSON.parse(call[1]!.body as string).delete_dataset).toBe(true)
})
