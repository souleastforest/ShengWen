import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // 默认指向测试环境后端（21001）；生产 21010 由部署侧反代。
  // 覆盖：frontend/.env.local 设 VITE_DEV_API_TARGET（见 .env.example）
  const devApiTarget = env.VITE_DEV_API_TARGET || 'http://127.0.0.1:21001'
  const packageJsonPath = resolve(process.cwd(), 'package.json')
  const packageJson = JSON.parse(readFileSync(packageJsonPath, 'utf-8')) as { version?: string }
  const appVersion = packageJson.version || '0.0.0'

  return {
    plugins: [vue()],
    define: {
      __APP_VERSION__: JSON.stringify(appVersion),
    },
    server: {
      proxy: {
        '/tasks': {
          target: devApiTarget,
          changeOrigin: true,
        },
        '/upload': {
          target: devApiTarget,
          changeOrigin: true,
        },
        '/llm': {
          target: devApiTarget,
          changeOrigin: true,
        },
        '/transcription': {
          target: devApiTarget,
          changeOrigin: true,
        },
        '/summarization': {
          target: devApiTarget,
          changeOrigin: true,
        },
        '/bilibili': {
          target: devApiTarget,
          changeOrigin: true,
        },
        '/local-path': {
          target: devApiTarget,
          changeOrigin: true,
        },
        '/local-folder': {
          target: devApiTarget,
          changeOrigin: true,
        },
        '/ws': {
          target: devApiTarget,
          ws: true,
          changeOrigin: true,
        },
      },
    },
  }
})
