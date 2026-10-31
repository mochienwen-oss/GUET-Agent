// api/desktop.ts —— 与 Electron 桌面端的窄接口
//
// 浏览器里打开时 window.saiDesktop 不存在，所有调用自动降级为空操作；
// 校园网页拿不到这个对象（preload 只注入 SAI 视图），因此无法驱动主进程。
export interface CampusStatus {
  taskId: string
  service?: string
  status: 'awaiting_login' | 'navigating' | 'at_query_page' | 'filled' | 'completed' | 'needs_user' | 'paused' | 'failed'
  detail?: string
  result?: CampusSummary
  resultDate?: string
}

/** 学校视图在窗口里的矩形（CSS 像素 / DIP）。 */
export interface CampusRect {
  x: number
  y: number
  width: number
  height: number
}

/** 一次空教室查询的参数（由模型/前端翻译后交给桌面端执行）。 */
export interface CampusQuery {
  start_date?: string
  end_date?: string
  weekdays?: string[]
  units?: string[]
  building?: string
  room?: string
  room_type?: string
  date?: string
  period?: 'morning' | 'noon' | 'afternoon' | 'evening'
  campusId?: number
  buildingId?: string
  seats?: number
}

/** 桌面端查询成功后回传的精简结果。 */
export interface CampusSummary {
  total: number
  buildings: Array<{ name: string; rooms: Array<{ code: string; name: string; type: string; seats: number; remark: string }> }>
}

interface DesktopBridge {
  importSchoolSession(payload: { token: string }): Promise<{ ok: boolean }>
  openCampus(payload: { taskId: string; token: string }): Promise<{ ok: boolean }>
  hideCampus(): Promise<{ ok: boolean }> | void
  resetCampus(): Promise<{ ok: boolean }>
  resumeCampus(): Promise<{ ok: boolean }>
  pauseCampus(): Promise<{ ok: boolean }>
  zoomCampus(direction: 'in' | 'out' | 'fit'): Promise<{ ok: boolean }>
  reloadCampus(): Promise<{ ok: boolean }> | void
  backCampus(): Promise<{ ok: boolean }> | void
  forwardCampus(): Promise<{ ok: boolean }> | void
  queryCampus(payload: { taskId: string; token: string; query: CampusQuery }): Promise<{ ok: boolean; reason?: string; summary?: CampusSummary }>
  setCampusBounds(rect: CampusRect): Promise<{ ok: boolean }> | void
  onCampusStatus(callback: (status: CampusStatus) => void): () => void
  onCampusVisible(callback: (payload: { visible: boolean }) => void): () => void
  onCampusUrl(callback: (payload: { url: string }) => void): () => void
  onCampusZoom(callback: (payload: { factor: number; auto: boolean }) => void): () => void
}

export function desktopBridge(): DesktopBridge | null {
  return (window as unknown as { saiDesktop?: DesktopBridge }).saiDesktop ?? null
}

export function isDesktop(): boolean {
  return desktopBridge() !== null
}
