import { defineConfig, defaultExclude } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'happy-dom',
    globals: true,
    // e2e/ 是 Playwright spec（playwright.config.ts 管理），vitest 不得收集
    //（vibevoice-settings.spec.ts 曾被误收集：Playwright test() 在 vitest 上下文抛错）
    // 注意：exclude 是整体覆盖而非合并，必须展开 defaultExclude 保留 node_modules/.git 默认排除
    exclude: ['e2e/**', ...defaultExclude],
  },
})
