import { createRouter, createWebHistory } from 'vue-router'
import { isLoggedIn } from '../auth'

// 创建路由实例，使用 HTML5 History 模式（URL 不带 #）
const router = createRouter({
  history: createWebHistory(),
  routes: [
    // 首页/报告列表
    {
      path: '/',
      name: 'reports',
      component: () => import('../views/ReportList.vue'),
      meta: { requiresAuth: true },  // 需要登录才能访问
    },
    // 登录页
    {
      path: '/login',
      name: 'login',
      component: () => import('../views/Login.vue'),
    },
    // 报告详情（某一天的选股结果）
    {
      path: '/report/:date',
      name: 'report-detail',
      component: () => import('../views/ReportDetail.vue'),
      props: true,                    // 保留路由参数 props 映射能力，当前页面内部通过 useRoute 读取
      meta: { requiresAuth: true },
    },
    // 个股详情（单只股票的评分、AI 分析、历史表现）
    {
      path: '/stock/:code',
      name: 'stock-detail',
      component: () => import('../views/StockDetail.vue'),
      props: true,                    // 保留路由参数 props 映射能力，当前页面内部通过 useRoute 读取
      meta: { requiresAuth: true },
    },
    // 数据洞察页（查看资金流、龙虎榜和席位明细）
    {
      path: '/data',
      name: 'data-insight',
      component: () => import('../views/DataInsight.vue'),
      meta: { requiresAuth: true },
    },
    // 执行页面（手动触发选股任务或查看任务状态）
    {
      path: '/execute',
      name: 'execute',
      component: () => import('../views/Execute.vue'),
      meta: { requiresAuth: true },
    },
  ],
})

/**
 * 路由守卫：访问需要认证的页面时检查登录状态
 *
 * - 若页面需要认证但用户未登录 -> 重定向到登录页，携带当前路径作为 redirect 参数
 * - 若用户已访问登录页且已登录 -> 直接跳转到首页（避免重复登录）
 */
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
