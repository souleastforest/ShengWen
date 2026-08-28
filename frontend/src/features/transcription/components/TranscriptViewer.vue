<script setup lang="ts">
import { computed } from 'vue'
import type { TranscriptSegment } from '../../../types'
import { formatHms, parseTranscriptLines } from '../../../utils/transcriptLines'
import { buildTimestampJumpUrl } from '../../../utils/videoTimeJump'

/**
 * 转录字幕化渲染：每行 [HH:MM:SS] 时间 chip（可点击跳视频）+ 文本；
 * 无时间戳行保持纯文本；无 segments 时回退 HHMMSS 行解析。
 * 时间 chip 复用 markdown 时间芯片样式（.ss-time-jump-chip 系，见
 * styles/markdown-themes/base.css，.ss-transcript-viewer 作用域追加）。
 */

interface Props {
  transcript?: string | null
  /** 段级转录结果（优先渲染；null/空数组 → 回退行解析） */
  segments?: TranscriptSegment[] | null
  videoUrl?: string
  /** 0-based 分P序号：跳转 URL 追加 p={partIndex+1}（仅 bilibili.com） */
  partIndex?: number
}

const props = defineProps<Props>()

interface ViewerRow {
  seconds?: number
  text: string
  isPlain: boolean
  /** 可跳转时间 chip 的 URL（本地文件/无 URL → null → disabled chip） */
  url: string | null
}

const hasSegments = computed(
  () => props.segments != null && props.segments.length > 0,
)

const jumpUrl = (seconds: number): string | null =>
  buildTimestampJumpUrl(props.videoUrl ?? '', seconds, props.partIndex)

const rows = computed<ViewerRow[]>(() => {
  if (hasSegments.value) {
    return props.segments!.map((seg) => ({
      seconds: seg.start,
      text: seg.text,
      isPlain: false,
      url: jumpUrl(seg.start),
    }))
  }
  return parseTranscriptLines(props.transcript).map((line) =>
    line.kind === 'subtitle'
      ? { seconds: line.seconds, text: line.text, isPlain: false, url: jumpUrl(line.seconds!) }
      : { text: line.text, isPlain: true, url: null },
  )
})
</script>

<template>
  <div class="ss-transcript-viewer space-y-2.5">
    <div
      v-for="(row, i) in rows"
      :key="i"
      class="ss-transcript-line flex items-start gap-2 leading-relaxed"
    >
      <a
        v-if="row.url"
        class="ss-time-jump-chip ss-transcript-chip"
        :href="row.url"
        target="_blank"
        rel="noopener noreferrer"
        :title="`跳转到 ${formatHms(row.seconds ?? 0)}`"
      >[{{ formatHms(row.seconds ?? 0) }}]</a>
      <span
        v-else-if="!row.isPlain"
        class="ss-time-jump-chip ss-time-jump-chip--disabled ss-transcript-chip"
      >[{{ formatHms(row.seconds ?? 0) }}]</span>
      <span
        class="ss-transcript-text whitespace-pre-wrap break-words text-sm text-slate-600"
        :class="{ 'ss-transcript-text--plain': row.isPlain }"
      >{{ row.text }}</span>
    </div>
    <p v-if="rows.length === 0" class="text-gray-400 italic">暂无转录内容</p>
  </div>
</template>
