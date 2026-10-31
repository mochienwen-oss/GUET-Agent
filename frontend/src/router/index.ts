// router/index.ts —— 路由 + 全局登录/角色守卫
import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '../store/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('../views/Login.vue') },
    { path: '/', component: () => import('../components/layout/AppShell.vue'), children: [
      { path: '', component: () => import('../views/Chat.vue') },
      { path: 'profile', component: () => import('../views/Profile.vue') },
      { path: 'presentations', component: () => import('../views/Presentations.vue') },
      { path: 'presentations/:taskId', component: () => import('../views/Presentations.vue') },
    ] },
    { path: '/admin', component: () => import('../views/Admin.vue'), meta: { admin: true } },
  ],
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (!auth.isLoggedIn && to.path !== '/login') return '/login'
  if (to.meta.admin && !auth.isAdmin) return '/'
  return true
})

export default router
