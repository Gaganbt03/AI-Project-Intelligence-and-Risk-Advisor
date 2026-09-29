import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The backend runs on http://127.0.0.1:8000 (see backend/run.py).
// Vite dev server proxies /api to it so the frontend never hard-codes a base URL.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
});