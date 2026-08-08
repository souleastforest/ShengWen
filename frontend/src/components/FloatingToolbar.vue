<script setup lang="ts">
import { PhSparkle, PhArticle, PhCaretDown, PhArrowClockwise, PhCopy, PhDownloadSimple, PhImageSquare, PhGearSix, PhList } from '@phosphor-icons/vue'
import { TaskStatus, type Task, type MarkdownHeadingItem } from '../types'
import FloatingToolbarShell from './FloatingToolbarShell.vue'
import FloatingToolbarChapterNav from './FloatingToolbarChapterNav.vue'

const activeTab = defineModel<string>('activeTab', { required: true })

defineProps<{
  selectedTask: Task | null
  isSidebarOpen: boolean
  headings: MarkdownHeadingItem[]
  activeHeadingId?: string
}>()

const emit = defineEmits<{
  reSummarize: []
  reTranscribe: []
  copySummary: []
  copyTranscript: []
  downloadMarkdown: []
  downloadTxt: []
  exportSummaryImage: []
  openSummaryImageSettings: []
  toggleSidebar: []
  jumpHeading: [headingId: string]
}>()
</script>

<template>
  <div
    class="absolute top-4 left-1/2 -translate-x-1/2 md:left-4 md:translate-x-0 z-20 transition-all duration-300"
    :class="{ 'opacity-0 pointer-events-none md:opacity-100 md:pointer-events-auto': isSidebarOpen }"
  >
    <div class="flex flex-col items-center md:items-start gap-2">
      <div class="flex items-center gap-2">
        <!-- 气泡式工具栏 -->
        <FloatingToolbarShell variant="solid">
          <!-- 移动端侧栏切换按钮 -->
          <button
            @click="emit('toggleSidebar')"
            class="md:hidden p-2 text-slate-500 hover:text-slate-700 hover:bg-slate-100 rounded-full transition-colors"
            title="打开侧栏"
          >
            <PhList :size="18" />
          </button>

          <!-- Toggle Switch -->
          <div class="flex bg-slate-100 p-1 rounded-full relative">
            <!-- 滑块背景 -->
            <div
              class="absolute top-1 bottom-1 w-[calc(50%-4px)] bg-white rounded-full shadow-sm transition-all duration-300 ease-out"
              :class="activeTab === 'summary' ? 'left-1' : 'left-[calc(50%)]'"
            ></div>

            <!-- AI 总结选项 -->
            <div class="relative group/summary">
              <button
                @click="activeTab = 'summary'"
                class="relative z-10 px-3 py-1.5 rounded-full text-xs font-medium transition-colors flex items-center gap-1.5 whitespace-nowrap"
                :class="activeTab === 'summary' ? 'text-blue-600' : 'text-slate-500 hover:text-slate-700'"
              >
                <PhSparkle :size="14" :weight="activeTab === 'summary' ? 'fill' : 'regular'" />
                <span>AI 总结</span>
                <PhCaretDown :size="12" class="ml-0.5" />
              </button>

              <!-- AI 总结下拉菜单 -->
              <div class="absolute left-0 top-full mt-2 w-44 bg-white border border-slate-200 rounded-xl shadow-lg opacity-0 invisible group-hover/summary:opacity-100 group-hover/summary:visible transition-all z-30 overflow-hidden">
                <button
                  v-if="(selectedTask?.status === TaskStatus.COMPLETED || selectedTask?.status === TaskStatus.FAILED) && selectedTask?.transcript"
                  @click="emit('reSummarize')"
                  class="w-full text-left flex items-center gap-2 px-3 py-2 text-xs text-slate-700 hover:bg-slate-50 hover:text-blue-600 transition-colors"
                >
                  <PhArrowClockwise :size="14" />
                  AI 重新总结
                </button>
                <button
                  @click="emit('copySummary')"
                  class="w-full text-left flex items-center gap-2 px-3 py-2 text-xs text-slate-700 hover:bg-slate-50 hover:text-blue-600 transition-colors"
                >
                  <PhCopy :size="14" />
                  复制 Markdown
                </button>
                <button
                  @click="emit('downloadMarkdown')"
                  class="w-full text-left flex items-center gap-2 px-3 py-2 text-xs text-slate-700 hover:bg-slate-50 hover:text-blue-600 transition-colors"
                >
                  <PhDownloadSimple :size="14" />
                  下载 Markdown
                </button>
                <div
                  v-if="selectedTask?.summary"
                  class="flex items-center gap-1 px-2 py-1.5 bg-blue-50 border-t border-blue-100"
                >
                  <button
                    @click="emit('exportSummaryImage')"
                    class="flex-1 text-left flex items-center gap-2 px-1.5 py-1.5 text-xs text-blue-600 hover:bg-blue-100 hover:text-blue-700 transition-colors font-medium rounded-lg"
                  >
                    <PhImageSquare :size="14" />
                    一键成图
                  </button>
                  <button
                    @click="emit('openSummaryImageSettings')"
                    class="shrink-0 p-1.5 rounded-lg border border-blue-200 text-blue-600 hover:bg-blue-100 hover:text-blue-700 transition-colors"
                    title="成图参数设置与预览"
                    aria-label="成图参数设置与预览"
                  >
                    <PhGearSix :size="14" />
                  </button>
                </div>
              </div>
            </div>

            <!-- 原文选项 -->
            <div class="relative group/transcript">
              <button
                @click="activeTab = 'transcript'"
                class="relative z-10 px-3 py-1.5 rounded-full text-xs font-medium transition-colors flex items-center gap-1.5 whitespace-nowrap"
                :class="activeTab === 'transcript' ? 'text-blue-600' : 'text-slate-500 hover:text-slate-700'"
              >
                <PhArticle :size="14" :weight="activeTab === 'transcript' ? 'fill' : 'regular'" />
                <span>原文</span>
                <PhCaretDown :size="12" class="ml-0.5" />
              </button>

              <!-- 原文下拉菜单 -->
              <div class="absolute right-0 top-full mt-2 w-44 bg-white border border-slate-200 rounded-xl shadow-lg opacity-0 invisible group-hover/transcript:opacity-100 group-hover/transcript:visible transition-all z-30 overflow-hidden">
                <button
                  @click="emit('reTranscribe')"
                  class="w-full text-left flex items-center gap-2 px-3 py-2 text-xs text-slate-700 hover:bg-slate-50 hover:text-blue-600 transition-colors"
                >
                  <PhArrowClockwise :size="14" />
                  重新转录原文
                </button>
                <button
                  @click="emit('copyTranscript')"
                  class="w-full text-left flex items-center gap-2 px-3 py-2 text-xs text-slate-700 hover:bg-slate-50 hover:text-blue-600 transition-colors"
                >
                  <PhCopy :size="14" />
                  复制原文
                </button>
                <button
                  @click="emit('downloadTxt')"
                  class="w-full text-left flex items-center gap-2 px-3 py-2 text-xs text-slate-700 hover:bg-slate-50 hover:text-blue-600 transition-colors"
                >
                  <PhDownloadSimple :size="14" />
                  下载 TXT
                </button>
              </div>
            </div>
          </div>
        </FloatingToolbarShell>

        <FloatingToolbarShell variant="solid" :no-wrap="false" v-if="activeTab === 'summary'">
          <FloatingToolbarChapterNav
            :headings="headings"
            :active-heading-id="activeHeadingId"
            @jump="emit('jumpHeading', $event)"
          />
        </FloatingToolbarShell>
      </div>
    </div>
  </div>
</template>
