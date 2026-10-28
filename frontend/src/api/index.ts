// api/index.ts —— axios 实例 + token 拦截器 + 统一 {error} 处理
import axios from 'axios'

const api = axios.create({ baseURL: '/api', timeout: 30000 })

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (res) => res,
  (err) => {
    const msg = err.response?.data?.error || (
      err.code === 'ECONNABORTED' ? '请求超过30秒仍未完成，尚未取得查询结果，请稍后重试。' :
      err.code === 'ERR_CANCELED' ? '请求已取消' :
      !err.response ? '无法连接服务，请检查网络或确认服务器已启动。' :
      err.response.status === 403 ? '当前账号没有执行此操作的权限。' :
      '服务处理失败，请稍后重试。'
    )
    if (err.response?.status === 401) {
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      if (location.pathname !== '/login') location.href = '/login'
    }
    return Promise.reject(new Error(msg))
  }
)

export default api
