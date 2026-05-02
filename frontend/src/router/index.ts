import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'reports',
      component: () => import('../views/ReportList.vue'),
    },
    {
      path: '/report/:date',
      name: 'report-detail',
      component: () => import('../views/ReportDetail.vue'),
      props: true,
    },
    {
      path: '/stock/:code',
      name: 'stock-detail',
      component: () => import('../views/StockDetail.vue'),
      props: true,
    },
  ],
})

export default router
