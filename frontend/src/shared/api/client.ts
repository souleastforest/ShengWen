/**
 * 共享 axios 实例（P2-B 网络生命周期）：全前端唯一 HTTP 出口。
 *
 * - baseURL 统一收敛 VITE_API_BASE_URL（原 useTaskViewModel 内 normalizeBase 逻辑）；
 * - 默认超时 60s；大文件上传（/upload）在调用点显式 timeout: 0（无总时长
 *   限制，保持旧行为）——XHR timeout 为整请求总时长（不被 onUploadProgress
 *   重置），固定超时会误杀慢链路大文件上传；
 * - 错误提取统一导出：getAxiosErrorMessage / isCanceledRequest / isAxiosError。
 *
 * 设计取舍（行为等价铁律）：错误提取选择"导出公共函数"而非响应拦截器——
 * 各调用点对 err.response?.data?.detail / isAxiosError(err) 的读取语义必须
 * 完全不变（拦截器改写错误对象会破坏这些读取），故收敛为公共函数而非拦截。
 *
 * 测试兼容：既有 13 个测试文件以 vi.mock('axios')（工厂无 create）拦截 HTTP，
 * 本模块在 axios.create 缺失时回退直接使用 axios 默认导出，使既有测试的
 * mock 语义原样作用于共享实例（生产环境 axios.create 恒存在，走实例分支）。
 */
import axios from 'axios'
import type { AxiosInstance } from 'axios'

const normalizeBase = (base?: string) => (base || '').trim().replace(/\/+$/, '')

/** 默认请求超时：60s */
export const DEFAULT_TIMEOUT_MS = 60_000

/** 统一错误提取：detail 字符串 / detail 数组（参数错误）/ fallback：message */
export const getAxiosErrorMessage = (err: unknown, fallback: string): string => {
  if (!axios.isAxiosError(err)) return fallback

  const detail = err.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) {
    return detail
  }

  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0]
    if (first && typeof first === 'object' && 'msg' in first) {
      const message = String((first as { msg?: unknown }).msg || '').trim()
      if (message) {
        return `请求参数错误：${message}`
      }
    }
  }

  return err.message ? `${fallback}：${err.message}` : fallback
}

/** 统一取消识别：axios.isCancel 或 code === 'ERR_CANCELED' */
export const isCanceledRequest = (err: unknown): boolean => {
  if (axios.isCancel(err)) return true
  if (!axios.isAxiosError(err)) return false
  return err.code === 'ERR_CANCELED'
}

export const isAxiosError = axios.isAxiosError

const apiClient: AxiosInstance =
  typeof axios.create === 'function'
    ? axios.create({
        baseURL: normalizeBase(import.meta.env.VITE_API_BASE_URL),
        timeout: DEFAULT_TIMEOUT_MS,
      })
    : (axios as unknown as AxiosInstance)

export { apiClient }
export default apiClient
