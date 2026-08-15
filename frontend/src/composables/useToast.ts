import { ref } from 'vue'

// Toast 类型定义
export type ToastType = 'success' | 'error' | 'warning' | 'info'

export interface ToastItem {
  id: string
  message: string
  type: ToastType
  duration?: number
  isPaused?: boolean
}

// 全局 toast 状态
const toasts = ref<ToastItem[]>([])
const MAX_TOASTS = 3

// 定时器管理
const timers = new Map<string, {
  timer: ReturnType<typeof setTimeout> | null
  remainingTime: number
  startTime: number
}>()

function getDefaultDuration(type: ToastType): number {
  switch (type) {
    case 'success': return 3000
    case 'error': return 5000
    case 'warning': return 4000
    case 'info': return 3000
    default: return 3000
  }
}

// 声明 removeToast 函数
function removeToast(id: string) {
  const index = toasts.value.findIndex((t: ToastItem) => t.id === id)
  if (index > -1) {
    toasts.value.splice(index, 1)
    const timerData = timers.get(id)
    if (timerData?.timer) {
      clearTimeout(timerData.timer)
    }
    timers.delete(id)
  }
}

function startTimer(id: string, duration: number) {
  const startTime = Date.now()
  const timer = setTimeout(() => {
    removeToast(id)
  }, duration)

  timers.set(id, {
    timer,
    remainingTime: duration,
    startTime
  })
}

function pauseTimer(id: string) {
  const timerData = timers.get(id)
  if (timerData && timerData.timer) {
    clearTimeout(timerData.timer)
    timerData.remainingTime -= Date.now() - timerData.startTime
    timerData.timer = null
  }
}

function resumeTimer(id: string) {
  const timerData = timers.get(id)
  if (timerData && !timerData.timer && timerData.remainingTime > 0) {
    timerData.startTime = Date.now()
    timerData.timer = setTimeout(() => {
      removeToast(id)
    }, timerData.remainingTime)
  }
}

export function useToast() {
  const addToast = (message: string, type: 'success' | 'error' | 'warning' | 'info', duration?: number) => {
    const id = `toast-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`

    const toast: ToastItem = {
      id,
      message,
      type,
      duration,
      isPaused: false
    }

    // 移除最早的 toast 如果超出限制
    if (toasts.value.length >= MAX_TOASTS) {
      const removed = toasts.value.shift()
      if (removed) {
        const timerData = timers.get(removed.id)
        if (timerData?.timer) {
          clearTimeout(timerData.timer)
        }
        timers.delete(removed.id)
      }
    }

    toasts.value.push(toast)

    // 启动定时器
    const finalDuration = duration ?? getDefaultDuration(type)
    if (finalDuration > 0) {
      startTimer(id, finalDuration)
    }

    return id
  }

  const pauseToast = (id: string) => {
    const toast = toasts.value.find(t => t.id === id)
    if (toast) {
      toast.isPaused = true
      pauseTimer(id)
    }
  }

  const resumeToast = (id: string) => {
    const toast = toasts.value.find(t => t.id === id)
    if (toast) {
      toast.isPaused = false
      resumeTimer(id)
    }
  }

  return {
    toasts,
    addToast,
    removeToast,
    pauseToast,
    resumeToast,
    success: (message: string, duration?: number) => addToast(message, 'success', duration),
    error: (message: string, duration?: number) => addToast(message, 'error', duration),
    warning: (message: string, duration?: number) => addToast(message, 'warning', duration),
    info: (message: string, duration?: number) => addToast(message, 'info', duration)
  }
}
