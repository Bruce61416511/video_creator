import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // ⚠️ 端口必须钉死：草稿存在浏览器 localStorage，而 localStorage 是按
    // 「协议 + 域名 + 端口」隔离的。端口一漂（5175/5176/5177），草稿就读不到了
    // ——数据没丢，只是换了抽屉。strictPort=true 让端口被占时直接报错，
    // 而不是静默换端口导致"分镜全没了"。
    port: 5176,
    strictPort: true,
    proxy: {
      '/api': 'http://localhost:8002',
      '/uploads': 'http://localhost:8002',
    },
  },
})
