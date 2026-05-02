import { createRouter, createWebHistory } from 'vue-router'
import { isLoggedIn } from '../auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'reports',
      component: () => import('../views/ReportList.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/Login.vue'),
    },
    {
      path: '/report/:date',
      name: 'report-detail',
      component: () => import('../views/ReportDetail.vue'),
      props: true,
      meta: { requiresAuth: true },
    },
    {
      path: '/stock/:code',
      name: 'stock-detail',
      component: () => import('../views/StockDetail.vue'),
      props: true,
      meta: { requiresAuth: true },
    },
    {
      path: '/execute',
      name: 'execute',
      component: () => import('../views/Execute.vue'),
      meta: { requiresAuth: true },
    },
  ],
})

router.beforeEach((to) => {
  if (to.meta.requiresAuth && !isLoggedIn()) {
    return {
      name: 'login',
      query: { redirect: to.fullPath },
    }
  }

  if (to.name === 'login' && isLoggedIn()) {
    const redirect = typeof to.query.redirect === 'string' ? to.query.redirect : '/'
    return redirect
  }
})

export default router
