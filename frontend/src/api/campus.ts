// api/campus.ts —— 空教室查询（服务端代查版）
//
// ★ 为什么不再经过桌面端：查询本身是纯 HTTP JSON 接口（教务的 free-list），
//   桌面端此前只是"带着学校会话发请求"的容器。现在会话由服务端在登录时留存
//   （见 backend/security/school_auth.remember_session），任何浏览器都能直接查 ——
//   所以这里不判断"是不是桌面端"，一条路走到底。
//
//   Electron 的定位随之改变：不再承担查询，而是保留内嵌学校网页视图，
//   用于后续爬取别的接口（那些仍需要真实页面上下文）。
import api from '.'
import type { CampusSummary } from './desktop'

export interface FreeClassroomResult {
  date: string
  units: string[]
  summary: CampusSummary
  /** 'server' 表示由后端代理查询 */
  source: string
}

export interface FreeClassroomBody {
  date: string
  /** 节次，优先于 period */
  units?: string[]
  /** 时段词：morning / noon / afternoon / evening */
  period?: string
  buildingId?: string
  seats?: number
  campusId?: number
}

export async function queryFreeClassroom(body: FreeClassroomBody) {
  return (await api.post<FreeClassroomResult>('/campus/free-classroom', body)).data
}

/** 网页端用来判断「还要不要先登录学校账号」，避免用户白问一次。 */
export async function campusSessionState() {
  return (await api.get<{ has_session: boolean }>('/campus/session-state')).data
}
