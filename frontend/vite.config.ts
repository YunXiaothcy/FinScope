import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'

export default defineConfig({
  plugins: [
    vue(),
    tailwindcss(),
    {
      name: 'finscope-online-worker-version',
      apply: 'build',
      generateBundle(_, bundle) {
        const revision = createHash('sha256')
          .update(Object.keys(bundle).sort().join('|'))
          .digest('hex')
          .slice(0, 16)
        this.emitFile({
          type: 'asset',
          fileName: 'sw.js',
          source:
            `/* build ${revision} */\n` +
            readFileSync(new URL('./public/sw.js', import.meta.url), 'utf8'),
        })
      },
    },
  ],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          const path = id.replaceAll('\\', '/')
          if (path.includes('/node_modules/zrender/')) return 'chart-renderer'
          if (path.includes('/node_modules/echarts/')) return 'charts'
        },
      },
    },
  },
  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
  preview: {
    port: 4174,
    headers: { 'Cache-Control': 'no-store' },
    proxy: { '/api': { target: 'http://127.0.0.1:8001', changeOrigin: true } },
  },
})
