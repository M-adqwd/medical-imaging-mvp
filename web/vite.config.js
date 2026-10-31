import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // Cornerstone 的 codec worker 依赖 ES module 输出
  worker: { format: 'es' },
  optimizeDeps: {
    // dicom-image-loader 的 ESM 产物静态 import 四个 CJS/UMD codec 包，
    // 必须交给 esbuild 预打包做 CJS→ESM 互操作，否则浏览器报
    // "does not provide an export named 'default'"，整页白屏
    include: [
      '@cornerstonejs/core',
      '@cornerstonejs/tools',
      '@cornerstonejs/dicom-image-loader',
      '@cornerstonejs/codec-libjpeg-turbo-8bit',
      '@cornerstonejs/codec-charls',
      '@cornerstonejs/codec-openjph',
      '@cornerstonejs/codec-openjpeg',
    ],
  },
  server: {
    host: true,
    port: Number(process.env.VITE_PORT || 5173),
    // 工程位于 E: 盘（外置/网络卷），原生 fs 事件不触发会导致 HMR 与热更新失效，
    // 页面一直跑旧代码。开启轮询保证源码改动一定被感知。
    watch: { usePolling: true, interval: 300 },
    // 后端端口由启动器自动避让后传入，避免 8000 被占用时整条链路起不来
    proxy: {
      '/api': { target: process.env.VITE_API_TARGET || 'http://127.0.0.1:8000', changeOrigin: true },
      '/dicomweb': {
        target: process.env.VITE_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      // FHIR 门面与 /api 同源代理：少了它，dev 下 /fhir 会落进 SPA fallback
      // 返回 index.html，面板拿到 HTML 解析失败却显示成空数据
      '/fhir': {
        target: process.env.VITE_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  // vite preview（构建产物静态预览）默认不带 server.proxy，API/DICOMweb 会 404。
  // 显式镜像一份，保证 dev 与 preview 行为一致。
  preview: {
    host: true,
    port: Number(process.env.VITE_PORT || 4173),
    proxy: {
      '/api': { target: process.env.VITE_API_TARGET || 'http://127.0.0.1:8000', changeOrigin: true },
      '/dicomweb': {
        target: process.env.VITE_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      // FHIR 门面与 /api 同源代理：少了它，dev 下 /fhir 会落进 SPA fallback
      // 返回 index.html，面板拿到 HTML 解析失败却显示成空数据
      '/fhir': {
        target: process.env.VITE_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})