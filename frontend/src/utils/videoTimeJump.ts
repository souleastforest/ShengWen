const normalizeHost = (inputHost: string) => {
  return inputHost.trim().toLowerCase().replace(/^www\./, '')
}

export const toTimestampSeconds = (hh: string, mm: string, ss: string): number => {
  return Number(hh) * 3600 + Number(mm) * 60 + Number(ss)
}

/**
 * 生成跳转视频指定秒数的 URL。
 *
 * partIndex（0-based 分P序号）：仅 bilibili.com（含子域名）追加 p={partIndex + 1}
 * （B 站分P参数），b23.tv / YouTube 不追加（向后兼容——b23.tv 短链不支持 p=，
 * YouTube 用 t={n}s 单参数）。
 */
export const buildTimestampJumpUrl = (
  videoUrl: string,
  seconds: number,
  partIndex?: number,
): string | null => {
  const source = (videoUrl || '').trim()
  if (!source || !Number.isFinite(seconds) || seconds < 0) {
    return null
  }

  try {
    const url = new URL(source)
    const host = normalizeHost(url.hostname)

    if (host === 'youtube.com' || host.endsWith('.youtube.com') || host === 'youtu.be') {
      url.searchParams.set('t', `${Math.floor(seconds)}s`)
      return url.toString()
    }

    if (host === 'bilibili.com' || host.endsWith('.bilibili.com')) {
      url.searchParams.set('t', String(Math.floor(seconds)))
      if (partIndex != null && partIndex >= 0) {
        url.searchParams.set('p', String(partIndex + 1))
      }
      return url.toString()
    }

    if (host === 'b23.tv') {
      url.searchParams.set('t', String(Math.floor(seconds)))
      return url.toString()
    }

    return null
  } catch {
    return null
  }
}
