// tests/unit/api.test.js —— config/api.js URL 派生完整性
// 夹具（tests/fixtures/instance.js）：API_BASE_URL/WARNING_API_BASE 带尾斜杠（测去斜杠），
// PLAN_API_BASE 为空（测「未启用 → 全部 PLAN_* URL 为空串」）。
// 派生规则同时受 scripts/validate.js 的 startsWith 校验约束，两侧应保持一致。
'use strict'

const api = require('../../config/api')

describe('BASE_URL 派生', () => {
  test('尾斜杠被去掉，主入口不带双斜杠', () => {
    expect(api.LOGIN_URL).toBe('https://api.example.test/dgx-agentapi/api/miniapp/login')
    expect(api.CHAT_URL).toBe('https://api.example.test/dgx-agentapi/api/chat')
    expect(api.HISTORY_URL).toBe('https://api.example.test/dgx-agentapi/api/miniapp/history')
    expect(api.UPLOAD_URL).toBe('https://api.example.test/dgx-agentapi/api/uploads')
  })

  test('主 BASE 的 URL 全部以去斜杠后的 BASE 开头', () => {
    const base = 'https://api.example.test/dgx-agentapi'
    for (const [key, url] of Object.entries(api)) {
      if (key.startsWith('METHODS_') || ['LOGIN_URL', 'CHAT_URL', 'HISTORY_URL',
        'RUN_PROGRESS_URL', 'RUN_TRACE_URL', 'UPLOAD_URL', 'MESSAGES_URL'].includes(key)) {
        expect(url.startsWith(`${base}/api/`)).toBe(true)
      }
    }
  })
})

describe('WARNING_BASE 派生', () => {
  test('独立 BASE 同样去尾斜杠', () => {
    expect(api.WARNING_STATUS_URL).toBe('https://api.example.test/warning/api/warning/status')
    expect(api.WARNING_UPLOAD_URL).toBe('https://api.example.test/warning/api/warning/upload')
    expect(api.WARNING_WAIVER_URL).toBe('https://api.example.test/warning/api/warning/waiver')
  })
})

describe('PLAN_BASE 为空（功能未启用）', () => {
  test('所有 PLAN_* URL 均为空串，页面据此隐藏入口', () => {
    const planKeys = Object.keys(api).filter((k) => k.startsWith('PLAN_'))
    expect(planKeys.length).toBeGreaterThan(10)
    for (const key of planKeys) {
      expect(api[key]).toBe('')
    }
  })
})

describe('URL 唯一性', () => {
  test('非空 URL 互不重复（method 与 path 拼错会在这里暴露）', () => {
    const seen = new Map()
    for (const [key, url] of Object.entries(api)) {
      if (!url) continue
      if (seen.has(url)) {
        throw new Error(`URL 重复: ${key} 与 ${seen.get(url)} 都是 ${url}`)
      }
      seen.set(url, key)
    }
  })

  test('无 URL 含双斜杠路径段', () => {
    for (const [key, url] of Object.entries(api)) {
      if (!url) continue
      expect(url.includes('//api/')).toBe(false)
    }
  })
})
