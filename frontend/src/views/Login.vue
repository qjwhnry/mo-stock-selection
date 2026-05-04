<script setup lang="ts">
/**
 * 登录页
 *
 * 功能：
 * 1. 使用 HTTP Basic Auth 认证方式（生产环境由 Nginx 校验）
 * 2. 用户输入账号密码，前端构建 Basic Auth 字符串并发送到 /api/health 验证
 * 3. 验证成功后保存认证信息到 localStorage（记住登录）或 sessionStorage（仅会话）
 * 4. 验证成功后根据 redirect 参数或默认跳转到首页
 */

import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showToast } from 'vant'
import { verifyAuth } from '../api'
import { buildBasicAuth, setAuthSession } from '../auth'

const router = useRouter()
const route = useRoute()

// 是否显示密码明文
const showPassword = ref(false)

// 表单提交状态
const submitting = ref(false)

// 登录表单数据
const form = reactive({
  username: '',
  password: '',
  remember: true,   // 是否记住本机登录（持久化到 localStorage）
})

/**
 * 表单提交处理：构建 Basic Auth 字符串，调用后端验证接口
 * - 验证成功：保存认证会话并跳转
 * - 验证失败：提示错误信息
 */
async function handleSubmit() {
  if (!form.username || !form.password) {
    showToast('请输入账号和密码')
    return
  }

  submitting.value = true

  try {
    // 构建 Authorization 头值：'Basic base64(username:password)'
    const authorization = buildBasicAuth(form.username, form.password)
    // 调用 /api/health 验证认证信息（生产环境由 Nginx Basic Auth 校验）
    await verifyAuth(authorization)
    // 保存认证会话到本地存储
    setAuthSession(form.username, authorization, form.remember)
    // 根据 redirect 参数跳转（登录前访问的页面路径），默认为首页
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    router.push(redirect)
  } catch {
    showToast('账号或密码错误')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="login-page">

    <!-- 左侧品牌展示区 -->
    <section class="login-shell">
      <div class="brand-panel">
        <!-- 品牌标识 -->
        <div class="brand-mark">
          <span>MO</span>
          <strong>Stock Selection</strong>
        </div>
        <h1>登录</h1>
        <p>
          进入 A 股批量选股系统，查看策略报告、任务执行与 AI 分析结果。
        </p>

        <!-- 系统状态标签 -->
        <div class="signal-grid" aria-label="系统状态">
          <div class="signal-item">
            <span>策略</span>
            <strong>短线 / 波段</strong>
          </div>
          <div class="signal-item">
            <span>数据层</span>
            <strong>PostgreSQL</strong>
          </div>
          <div class="signal-item">
            <span>分析</span>
            <strong>规则 + AI</strong>
          </div>
        </div>
      </div>

      <!-- 右侧登录表单卡片 -->
      <form class="login-card" @submit.prevent="handleSubmit">
        <div class="card-heading">
          <div>
            <p>mo-stock 控制台</p>
            <h2>登录</h2>
          </div>
          <router-link class="home-link" to="/">首页</router-link>
        </div>

        <div class="form-stack">
          <!-- 用户名 -->
          <van-field
            v-model="form.username"
            label="账号"
            left-icon="manager-o"
            placeholder="请输入账号"
            autocomplete="username"
          />

          <!-- 密码（支持明文/密文切换） -->
          <van-field
            v-model="form.password"
            :type="showPassword ? 'text' : 'password'"
            label="密码"
            left-icon="lock"
            placeholder="请输入密码"
            autocomplete="current-password"
          >
            <template #right-icon>
              <van-icon
                :name="showPassword ? 'eye-o' : 'closed-eye'"
                @click="showPassword = !showPassword"
              />
            </template>
          </van-field>

          <!-- 记住本机选项和账号维护提示 -->
          <div class="form-options">
            <van-checkbox v-model="form.remember" icon-size="16px">
              记住本机
            </van-checkbox>
            <span>联系管理员</span>
          </div>

          <!-- 提交按钮 -->
          <van-button
            block
            native-type="submit"
            type="primary"
            :loading="submitting"
            loading-text="校验中..."
          >
            进入系统
          </van-button>
        </div>
      </form>
    </section>
  </main>
</template>

<style scoped>
/* 背景：深色渐变 + 装饰圆形 */
.login-page {
  min-height: 100vh;
  background:
    linear-gradient(115deg, rgba(6, 25, 22, 0.92) 0%, rgba(11, 36, 33, 0.78) 42%, rgba(246, 248, 245, 0) 42.1%),
    radial-gradient(circle at 78% 18%, rgba(42, 157, 143, 0.18), transparent 24%),
    radial-gradient(circle at 92% 86%, rgba(230, 185, 88, 0.18), transparent 22%),
    linear-gradient(135deg, #f7faf8 0%, #eef4f1 52%, #f7f1e8 100%);
  color: #16231f;
}

/* 左右分栏布局 */
.login-shell {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 430px;
  gap: 72px;
  align-items: center;
  width: min(1120px, calc(100% - 40px));
  min-height: 100vh;
  margin: 0 auto;
  padding: 56px 0;
}

/* 左侧品牌区 */
.brand-panel {
  display: flex;
  flex-direction: column;
  gap: 24px;
  color: #eef8f4;
}

.brand-mark {
  width: fit-content;
  display: inline-flex;
  align-items: center;
  gap: 12px;
  color: #f4fbf8;
}

.brand-mark span {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border: 1px solid rgba(255, 255, 255, 0.24);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.08);
  font-weight: 800;
}

.brand-mark strong {
  font-size: 15px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.brand-panel h1 {
  max-width: 640px;
  margin: 0;
  color: #ffffff;
  font-size: clamp(58px, 8vw, 96px);
  line-height: 0.95;
  letter-spacing: 0;
}

.brand-panel p {
  max-width: 560px;
  margin: 0;
  color: rgba(238, 248, 244, 0.74);
  font-size: 18px;
  line-height: 1.8;
}

/* 系统状态标签网格 */
.signal-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  max-width: 720px;
}

.signal-item {
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 8px;
  padding: 16px;
  background: rgba(255, 255, 255, 0.08);
  box-shadow: 0 18px 46px rgba(0, 0, 0, 0.12);
  backdrop-filter: blur(14px);
}

.signal-item span {
  display: block;
  margin-bottom: 8px;
  color: rgba(238, 248, 244, 0.6);
  font-size: 12px;
}

.signal-item strong {
  color: #ffffff;
  font-size: 15px;
}

/* 右侧登录卡片 */
.login-card {
  border: 1px solid rgba(20, 38, 33, 0.1);
  border-radius: 8px;
  padding: 34px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 28px 74px rgba(27, 47, 42, 0.18);
  backdrop-filter: blur(18px);
}

.card-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.card-heading p,
.card-heading h2 {
  margin: 0;
}

.card-heading p {
  color: #6d7f7b;
  font-size: 13px;
}

.card-heading h2 {
  margin-top: 6px;
  color: #17201d;
  font-size: 26px;
  line-height: 1.25;
  letter-spacing: 0;
}

.home-link {
  color: #28786f;
  font-size: 13px;
  font-weight: 700;
  text-decoration: none;
}

.form-stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-top: 8px;
}

.form-options {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  color: #526763;
  font-size: 13px;
}

.form-options span {
  color: #28786f;
}

/* Vant 组件主题色覆盖 */
:deep(.van-cell) {
  border: 1px solid #e3ece8;
  border-radius: 8px;
  padding: 13px 14px;
  background: #fbfdfc;
}

:deep(.van-cell::after) {
  display: none;
}

:deep(.van-button--primary) {
  border-color: #1f8a7f;
  background: #1f8a7f;
}

/* 移动端响应式布局 */
@media (max-width: 860px) {
  .login-page {
    background:
      linear-gradient(180deg, rgba(6, 25, 22, 0.94) 0%, rgba(12, 42, 38, 0.86) 64%, rgba(247, 250, 248, 1) 64.1%),
      linear-gradient(135deg, #f7faf8 0%, #eef4f1 52%, #f7f1e8 100%);
  }

  .login-shell {
    grid-template-columns: 1fr;
    gap: 28px;
    padding-top: 28px;
  }

  .brand-panel h1 {
    font-size: 58px;
  }

  .signal-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 520px) {
  .login-shell {
    width: calc(100% - 28px);
    padding-bottom: 32px;
  }

  .brand-panel {
    gap: 16px;
  }

  .brand-panel h1 {
    font-size: 48px;
  }

  .brand-panel p {
    font-size: 15px;
  }

  .login-card {
    padding: 20px;
  }

  .form-options {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
