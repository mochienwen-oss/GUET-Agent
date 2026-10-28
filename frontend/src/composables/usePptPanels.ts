import { computed, onBeforeUnmount, onMounted, ref, watch, type Ref } from 'vue'

type Panel = 'chat' | 'rail' | 'sources'
/** 三处分隔线共享尺寸边界；窄工作区切换视图，不挤压画布。 */
export function usePptPanels(workspace: Ref<HTMLElement | null>, editor: Ref<HTMLElement | null>, pageView: () => boolean, sourcesOpen: () => boolean) {
  const widths = ref({ chat: 350, rail: 166, sources: 300 })
  const workspaceWidth = ref(0), editorWidth = ref(0), viewportWidth = ref(window.innerWidth)
  const compact = computed(() => viewportWidth.value <= 900 || (workspaceWidth.value > 0 && workspaceWidth.value < 720))
  const sourcesOverlay = computed(() => compact.value || editorWidth.value < (pageView() ? 680 : 560))
  function bounds(panel: Panel) {
    if (panel === 'chat') return { min: 280, max: Math.max(280, Math.min(560, workspaceWidth.value - 398)) }
    const railSpace = pageView() ? widths.value.rail + 8 : 0
    if (panel === 'sources') return { min: 240, max: Math.max(240, Math.min(440, editorWidth.value - (sourcesOverlay.value ? 32 : 280 + railSpace + 8))) }
    const sourceSpace = sourcesOpen() && !sourcesOverlay.value ? widths.value.sources + 8 : 0
    return { min: 112, max: Math.max(112, Math.min(280, editorWidth.value - sourceSpace - 288)) }
  }
  function set(panel: Panel, value: number) { const { min, max } = bounds(panel); widths.value[panel] = Math.round(Math.max(min, Math.min(max, value))) }
  function measure() {
    viewportWidth.value = window.innerWidth
    workspaceWidth.value = workspace.value?.clientWidth || 0; editorWidth.value = editor.value?.clientWidth || 0
    if (!compact.value) { set('chat', widths.value.chat); set('sources', widths.value.sources); set('rail', widths.value.rail) }
  }
  let stopDrag: (() => void) | undefined
  function start(event: PointerEvent, panel: Panel) {
    if (compact.value || event.button !== 0) return
    event.preventDefault(); stopDrag?.()
    const handle = event.currentTarget as HTMLElement, origin = event.clientX, width = widths.value[panel]
    handle.focus(); handle.setPointerCapture(event.pointerId)
    const move = (next: PointerEvent) => set(panel, width + (next.clientX - origin) * (panel === 'sources' ? -1 : 1))
    const previousCursor = document.body.style.cursor, previousSelect = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'; document.body.style.userSelect = 'none'
    const end = () => stopDrag?.()
    stopDrag = () => {
      handle.removeEventListener('pointermove', move); handle.removeEventListener('pointerup', end); handle.removeEventListener('pointercancel', end); handle.removeEventListener('lostpointercapture', end)
      document.body.style.cursor = previousCursor; document.body.style.userSelect = previousSelect; stopDrag = undefined
      if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId)
    }
    handle.addEventListener('pointermove', move); handle.addEventListener('pointerup', end); handle.addEventListener('pointercancel', end); handle.addEventListener('lostpointercapture', end)
  }
  function keydown(event: KeyboardEvent, panel: Panel) {
    if (compact.value) return
    const { min, max } = bounds(panel), direction = panel === 'sources' ? -1 : 1, step = event.shiftKey ? 40 : 16
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    set(panel, event.key === 'Home' ? min : event.key === 'End' ? max : widths.value[panel] + (event.key === 'ArrowRight' ? step : -step) * direction)
  }
  const style = computed(() => ({ '--chat-width': `${widths.value.chat}px`, '--rail-width': `${widths.value.rail}px`, '--sources-width': `${widths.value.sources}px` }))
  let observer: ResizeObserver | undefined
  onMounted(() => { observer = new ResizeObserver(measure); if (workspace.value) observer.observe(workspace.value); if (editor.value) observer.observe(editor.value); window.addEventListener('resize', measure); measure() })
  watch([pageView, sourcesOpen], measure)
  onBeforeUnmount(() => { stopDrag?.(); observer?.disconnect(); window.removeEventListener('resize', measure) })
  return { widths, compact, sourcesOverlay, style, bounds, start, keydown }
}
