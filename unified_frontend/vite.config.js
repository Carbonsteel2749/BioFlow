import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
var PORT = Number(process.env.UNIFIED_FRONTEND_PORT || 5180);
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
});
