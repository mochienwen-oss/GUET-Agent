// store/auth.ts —— 登录态（token + user）
import { defineStore } from 'pinia'
import api from '../api'
import { disableDevMock } from '../mock/devMock'

export interface User {
  id: number
  username: string
  name: string
  role: string
}

function readUser(): User | null {
  try {
    return JSON.parse(localStorage.getItem('user') || 'null')
  } catch {
    return null
  }
}

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('token') || '',
    user: readUser(),
  }),
  getters: {
    isLoggedIn: (s) => !!s.token,
    isAdmin: (s) => s.user?.role === 'admin',
  },
  actions: {
    setAuth(token: string, user: User) {
      this.token = token
      this.user = user
      localStorage.setItem('token', token)
      localStorage.setItem('user', JSON.stringify(user))
    },
    clear() {
      this.token = ''
      this.user = null
      localStorage.removeItem('token')
      localStorage.removeItem('user')
      disableDevMock()
    },
    async login(username: string, password: string) {
      const res = await api.post('/auth/login', { username, password })
      this.setAuth(res.data.token, res.data.user)
    },
    async register(username: string, password: string, name: string) {
      const res = await api.post('/auth/register', { username, password, name })
      this.setAuth(res.data.token, res.data.user)
    },
    async logout() {
      try {
        await api.post('/auth/logout')
      } catch {
        /* 忽略登出接口异常，本地照常清理 */
      }
      this.clear()
    },
  },
})