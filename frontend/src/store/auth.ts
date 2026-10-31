// store/auth.ts —— 登录态（token + user）
import { defineStore } from 'pinia'
import api from '../api'
import { disableDevMock } from '../mock/devMock'
import { desktopBridge } from '../api/desktop'

export interface User {
  id: number
  username: string
  name: string
  role: string
  avatar?: string | null
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
    async schoolChallenge(username: string) {
      const res = await api.post('/auth/school/challenge', { username })
      return res.data as { challenge_id: string; captcha_required: boolean; captcha_image: string | null }
    },
    async schoolLogin(username: string, password: string, challengeId: string, captcha: string) {
      const res = await api.post('/auth/school/login', { username, password, challenge_id: challengeId, captcha })
      const bridge = desktopBridge()
      if (bridge) {
        try { await bridge.importSchoolSession({ token: res.data.token }) }
        catch {
          await fetch('/api/auth/logout', { method: 'POST', headers: { Authorization: `Bearer ${res.data.token}` } }).catch(() => {})
          throw new Error('学校认证已通过，但未能恢复校园会话，请重新登录')
        }
      }
      this.setAuth(res.data.token, res.data.user)
    },
    async updateProfile(name: string) {
      const res = await api.patch('/auth/profile', { name })
      this.setAuth(this.token, res.data)
    },
    async updateAvatar(file: File) {
      const data = new FormData()
      data.append('file', file)
      const res = await api.put('/auth/profile/avatar', data)
      this.setAuth(this.token, res.data)
    },
    async removeAvatar() {
      const res = await api.delete('/auth/profile/avatar')
      this.setAuth(this.token, res.data)
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
