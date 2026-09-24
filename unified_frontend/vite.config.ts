import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// BioFlow 统一入口（工作台）。
// 默认端口 5180；`allowedHosts: true` 让通过服务器 IP / SSH 隧道访问时不被 Vite 拦成 403。
// 这里显式声明 process，避免为一个端口号引入 @types/node 依赖。
declare const process: { env: Record<string, string | undefined> }

const PORT = Number(process.env.UNIFIED_FRONTEND_PORT || 5180)

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: PORT,
    strictPort: false,
    allowedHosts: true,
  },
  preview: {
    host: '0.0.0.0',
    port: PORT,
    strictPort: false,
    allowedHosts: true,
  },
})
