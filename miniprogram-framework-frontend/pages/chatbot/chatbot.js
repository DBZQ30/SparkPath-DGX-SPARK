const { LOGIN_URL, CHAT_URL, HISTORY_URL, RUN_PROGRESS_URL } = require('../../config/api')
const { ASSISTANT_TITLE, WELCOME_TEXT, CHAT_PLACEHOLDER, INSTANCE_ID, CHAT_SUGGESTIONS } = require('../../config/instance')

// 空态引导问题：优先读实例配置 CHAT_SUGGESTIONS，缺省用这组通用教务问题
//
// 选型原则（改动前务必先读）：预设问题要「一问就答」，否则演示时会假超时。
// 一次 run 会串行跑 N 轮「LLM → 检索」，单次知识库检索约 12–23s，N 一大就必然
// 撞上超时上限（现为 300s：chatbot.js 的 wx.request timeout、后端 response_timeout
// 与 nginx proxy_read_timeout 三者一致）。
// 反面例子（已实测）：
//   ・「选修课要修满多少学分？」      → 5 次检索 + 6 次 LLM ≈ 165s，前后端双双超时
//   ・「工商管理2024级的毕业总学分？」 → 9 次 LLM ≈ 206s，同样超时
// 正面例子（已实测）：
//   ・「培养方案里的先修关系是什么意思？」 → 2 次检索 + 3 次 LLM ≈ 65s，安全
// 所以这里只放「概念解释 / 单一文档即可作答」的问题，并让每个问题各命中一个 skill
// （training-plan-interpretation / multi-path-academic-planning / academic-warning），
// 这样轨迹页也能稳定展示「思考 → 命中 Skill → 执行 → 结果」。
const DEFAULT_SUGGESTIONS = [
  '帮我解读一下工商管理专业的培养方案',
  '我是工业工程专业的新生，帮我规划一下我四年的学习路径',
  '帮我检查一下23级全体同学的选课预警情况',
]
const SUGGESTIONS = (Array.isArray(CHAT_SUGGESTIONS) && CHAT_SUGGESTIONS.length)
  ? CHAT_SUGGESTIONS
  : DEFAULT_SUGGESTIONS

const { PREFIX, KEYS, read } = require('../../utils/instance-keys')
const { TRACE_KEY, TRACE_INDEX_KEY, answerKey, summarize } = require('../../utils/trace')
const { DEMO_TRACE } = require('../../utils/trace-demo')

// 比赛演示：把内置录播轨迹挂到一条助手回复上，让「执行过程」折叠条可见、
// 可进全屏观测台。接入真实事件流后（后端在回复体里回传 trace）置 false。
const DEMO_TRACE_ENABLED = true

// 等待期的进度文案：把后端返回的 stage/tool 翻成用户看得懂的话
const TOOL_STAGE_LABEL = {
  knowledge_search: '正在检索知识库',
  terminal: '正在执行教务脚本',
  file: '正在读取文件',
  web: '正在联网检索',
}

const TEMP_USER_ID_KEY = KEYS.TEMP_USER_ID_KEY
const OPENID_KEY = KEYS.OPENID_KEY
const USER_ID_KEY = KEYS.USER_ID_KEY
const SESSION_ID_KEY = KEYS.SESSION_ID_KEY
const SESSION_TOKEN_KEY = KEYS.SESSION_TOKEN_KEY
const LEGACY_USER_KEYS = [
  'miniapp_test_user',
  'miniapp_test_session',
  'user_id',
  'session_id',
]

Page({
  data: {
    assistantTitle: ASSISTANT_TITLE,
    chatPlaceholder: CHAT_PLACEHOLDER,
    sealChar: (ASSISTANT_TITLE || '教').slice(0, 1),
    suggestions: SUGGESTIONS,
    showMenu: false,
    inputValue: '',
    messages: [
      {
        id: 'welcome',
        role: 'assistant',
        content: WELCOME_TEXT,
        rowClass: 'message-row message-row-assistant',
        bubbleClass: 'bubble bubble-assistant',
      },
    ],
    loading: false,
    loginStatus: '登录中...',
    scrollIntoView: '',
    userId: '',
    sessionId: '',
    sessionToken: '',
  },

  onLoad() {
    this.initUserIdentity()
    this.loginWithWechat()
  },

  onUnload() {
    this._stopProgressPoll()
  },

  initUserIdentity() {
    this.clearLegacyIdentityCache()

    const app = getApp()
    const globalData = app && app.globalData ? app.globalData : {}
    const cachedUserId = read('USER_ID_KEY')
    const cachedSessionId = read('SESSION_ID_KEY')
    const cachedOpenid = read('OPENID_KEY')
    const cachedToken = read('SESSION_TOKEN_KEY')
    const stableUserId = globalData.userId || globalData.openid || cachedUserId || cachedOpenid || this.getOrCreateTempUserId()
    const sessionId = globalData.sessionId || cachedSessionId || `${PREFIX}_${stableUserId}`

    this.setData({
      userId: stableUserId,
      sessionId,
      sessionToken: cachedToken || '',
    })
  },

  loginWithWechat() {
    this.setData({
      loginStatus: '登录中...',
    })

    wx.login({
      success: (loginRes) => {
        if (!loginRes.code) {
          console.warn('wx.login succeeded without code, fallback to temp user id:', loginRes)
          this.ensureFallbackIdentity()
          return
        }

        this.exchangeCodeForOpenid(loginRes.code)
      },
      fail: (err) => {
        console.warn('wx.login failed, fallback to temp user id:', err)
        this.ensureFallbackIdentity()
      },
    })
  },

  exchangeCodeForOpenid(code) {
    wx.request({
      url: LOGIN_URL,
      method: 'POST',
      timeout: 15000,
      header: {
        'content-type': 'application/json',
      },
      data: { code, instance_id: INSTANCE_ID },
      success: (res) => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          console.warn('miniapp login returned non-2xx, fallback to temp user id:', res)
          this.ensureFallbackIdentity()
          return
        }
  
        const data = res.data || {}
        const openid = data.openid
        const userId = data.user_id || openid
        const sessionToken = data.session_token || ''  // 获取 token
  
        if (!userId) {
          console.warn('miniapp login response missing openid/user_id, fallback to temp user id:', data)
          this.ensureFallbackIdentity()
          return
        }
  
        const sessionId = data.session_id || `${PREFIX}_${userId}`

        // 持久化身份信息（包含 token）
        wx.setStorageSync(SESSION_TOKEN_KEY, sessionToken)
        this.persistIdentity({
          openid,
          userId,
          sessionId,
          sessionToken,
        })

        // 写入 globalData 供其他页面读取
        const app = getApp()
        if (app && app.globalData) {
          app.globalData.sessionToken = sessionToken
        }

        // 更新页面数据
        this.setData({
          sessionToken: sessionToken,
          loginStatus: '已登录',
        })

        this.loadHistory()
      },
      fail: (err) => {
        console.warn('miniapp login request failed, fallback to temp user id:', err)
        this.ensureFallbackIdentity()
      },
    })
  },

  persistIdentity(identity) {
    const app = getApp()
    const openid = identity.openid || ''
    const userId = identity.userId
    const sessionId = identity.sessionId || `${PREFIX}_${userId}`

    if (openid) {
      wx.setStorageSync(OPENID_KEY, openid)
    }
    wx.setStorageSync(USER_ID_KEY, userId)
    wx.setStorageSync(SESSION_ID_KEY, sessionId)

    if (app && app.globalData) {
      app.globalData.openid = openid
      app.globalData.userId = userId
      app.globalData.sessionId = sessionId
    }

    this.setData({
      userId,
      sessionId,
    })
  },

  ensureFallbackIdentity() {
    const userId = this.data.userId || this.getOrCreateTempUserId()
    const sessionId = this.data.sessionId || `${PREFIX}_${userId}`

    this.persistIdentity({
      userId,
      sessionId,
    })

    this.setData({
      loginStatus: '临时身份',
    })
  },

  clearLegacyIdentityCache() {
    LEGACY_USER_KEYS.forEach((key) => {
      const cachedValue = wx.getStorageSync(key)

      if (cachedValue === 'miniapp_test_user' || cachedValue === 'miniapp_test_session') {
        wx.removeStorageSync(key)
      }
    })
  },

  getOrCreateTempUserId() {
    const cachedUserId = read('TEMP_USER_ID_KEY')

    if (cachedUserId && cachedUserId !== 'miniapp_test_user') {
      return cachedUserId
    }

    const randomPart = Math.random().toString(36).slice(2, 10)
    const timePart = Date.now().toString(36)
    const tempUserId = `miniapp_temp_${timePart}_${randomPart}`

    wx.setStorageSync(TEMP_USER_ID_KEY, tempUserId)

    return tempUserId
  },

  onInput(e) {
    this.setData({
      inputValue: e.detail.value,
    })
  },

  loadHistory() {
    const sessionToken = this.data.sessionToken
    if (!sessionToken) {
      return
    }

    wx.request({
      url: HISTORY_URL,
      method: 'POST',
      timeout: 15000,
      header: {
        'content-type': 'application/json',
        'Authorization': `Bearer ${sessionToken}`,
      },
      data: {
        openid: this.data.userId,
      },
      success: (res) => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          console.warn('load history failed:', res)
          return
        }

        const data = res.data || {}
        const historyMessages = Array.isArray(data.messages) ? data.messages : []

        if (historyMessages.length === 0) {
          return
        }

        const messages = historyMessages
          .filter((item) => item && (item.role === 'user' || item.role === 'assistant'))
          // 中间的工具调用轮次 content 为空（state.db 里确实存在空 assistant 行），
          // 不能渲染成空白气泡。后端已过滤，这里再兜底一次。
          .filter((item) => this._plainText(item.content || '').length > 0)
          .map((item, index) => this.createMessage({
            id: `history_${index}_${Date.now()}`,
            role: item.role,
            content: item.content || '',
          }))

        // 折叠条接回：trace 不在后端历史里，用本地索引按答案文本匹配；
        // 只有全量 trace 仍在本地缓存（id 一致）时才挂，保证「全屏查看」可用。
        try {
          const hit = wx.getStorageSync(TRACE_INDEX_KEY)
          const cached = wx.getStorageSync(TRACE_KEY)
          if (hit && hit.key && hit.id && cached && cached.id === hit.id) {
            messages.forEach((m) => {
              if (m.role === 'assistant' && answerKey(this._plainText(m.content)) === hit.key) {
                m.trace = { id: hit.id, summary: hit.summary }
              }
            })
          }
        } catch (e) { /* 索引读取失败则不带折叠条 */ }

        this.setData({
          messages,
          scrollIntoView: `msg-${messages[messages.length - 1].id}`,
        })
      },
      fail: (err) => {
        console.warn('load history request failed:', err)
      },
    })
  },

  tapMenu() {
    this.setData({ showMenu: true })
  },

  closeMenu() {
    this.setData({ showMenu: false })
  },

  noop() {},

  // 点引导问题：填入输入框，用户可再编辑后发送（避免误发）
  tapSuggestion(e) {
    const text = (e.currentTarget.dataset.text || '').trim()
    if (!text) return
    this.setData({ inputValue: text })
  },

  tapClearConversation() {
    this.setData({ showMenu: false })
    if (this.data.loading) {
      return
    }
    if (!this.data.sessionToken) {
      wx.showToast({ title: '请先登录', icon: 'none' })
      return
    }
    wx.showModal({
      title: '清空对话',
      content: '将结束当前会话并开启一个新会话，之前的聊天记录不再显示。确定继续？',
      confirmText: '清空',
      success: (r) => {
        if (r.confirm) {
          this._resetConversation()
        }
      },
    })
  },

  // 发送 /new 让网关结束旧会话并开启新会话，成功后清空本地消息列表
  _resetConversation() {
    this.setData({ loading: true })
    wx.request({
      url: CHAT_URL,
      method: 'POST',
      timeout: 300000,
      header: {
        'content-type': 'application/json',
        'Authorization': `Bearer ${this.data.sessionToken}`,
      },
      data: { message: '/new' },
      success: (res) => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          wx.showToast({ title: '清空失败，请稍后重试', icon: 'none' })
          return
        }
        this.setData({
          messages: [this.createMessage({ id: 'welcome', role: 'assistant', content: WELCOME_TEXT })],
          scrollIntoView: 'msg-welcome',
        })
        wx.showToast({ title: '已开启新会话', icon: 'success' })
      },
      fail: (err) => {
        console.error('reset conversation failed:', err)
        wx.showToast({ title: '清空失败，请检查网络', icon: 'none' })
      },
      complete: () => {
        this.setData({ loading: false })
      },
    })
  },

  sendMessage() {
    if (this.data.loading) {
      return
    }

    const text = this.data.inputValue.trim()

    if (!text) {
      wx.showToast({
        title: '请输入消息',
        icon: 'none',
      })
      return
    }

    const userMessage = this.createMessage({
      id: `${Date.now()}_user`,
      role: 'user',
      content: text,
    })
    const loadingMessage = this.createMessage({
      id: `${Date.now()}_assistant_loading`,
      role: 'assistant',
      content: '正在思考...',
      loading: true,
    })
    const messages = this.data.messages.concat(userMessage, loadingMessage)

    this.setData({
      inputValue: '',
      loading: true,
      messages,
      scrollIntoView: `msg-${loadingMessage.id}`,
    })
    // 边等边显示真实阶段与已用时（轮询后端当前 run 的活动缓冲）
    this._startProgressPoll()

    const userId = this.data.userId || this.getOrCreateTempUserId()
    const sessionId = this.data.sessionId || `${PREFIX}_${userId}`

    this.setData({
      userId,
      sessionId,
    })

    wx.request({
      url: CHAT_URL,
      method: 'POST',
      timeout: 300000,
      header: {
        'content-type': 'application/json',
        'Authorization': `Bearer ${this.data.sessionToken}`,
      },
      data: {
        message: text,
        user_id: userId,
        session_id: sessionId,
      },
      success: (res) => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          this.replaceLoadingMessage(`请求失败：HTTP ${res.statusCode}`)
          return
        }

        const data = res.data || {}
        const reply = data.reply || data.answer || data.content || data.message || '后端没有返回可展示的文本。'

        const trace = data.trace || this._takeDemoTrace(reply)
        this.replaceLoadingMessage(reply, this._attachTrace(trace))
      },
      fail: (err) => {
        const message = err && err.errMsg ? err.errMsg : '网络请求超时或不可达'
        console.error('chat request failed:', err)
        this.replaceLoadingMessage(`请求失败：${message}。请确认后端服务已启动，并在开发者工具本地设置中关闭合法域名校验。`)
      },
      complete: () => {
        this._stopProgressPoll()
        this.setData({
          loading: false,
        })
      },
    })
  },

  // ── 等待期实时进度：轮询后端当前 run 的活动，把「正在思考...」换成真实阶段 ──
  _startProgressPoll() {
    this._stopProgressPoll()
    const tick = () => {
      if (!this.data.loading || !this.data.sessionToken) {
        this._stopProgressPoll()
        return
      }
      wx.request({
        url: RUN_PROGRESS_URL,
        method: 'GET',
        timeout: 5000,
        header: { 'Authorization': `Bearer ${this.data.sessionToken}` },
        success: (res) => {
          if (!this.data.loading) return
          // 404/405：后端尚未部署该接口（如网关未重启）→ 停轮询，避免刷屏
          if (res.statusCode === 404 || res.statusCode === 405) {
            this._stopProgressPoll()
            return
          }
          if (res.statusCode < 200 || res.statusCode >= 300) return
          this._updateLoadingMessage(this._progressLabel(res.data || {}))
        },
      })
    }
    this._progressTimer = setInterval(tick, 1200)
    tick()
  },

  _stopProgressPoll() {
    if (this._progressTimer) {
      clearInterval(this._progressTimer)
      this._progressTimer = null
    }
  },

  _progressLabel(d) {
    const sec = Math.max(0, Math.round(Number(d.elapsed_s) || 0))
    const suffix = `（已用时 ${sec}s）`
    if (d.stage === 'tool') {
      const base = TOOL_STAGE_LABEL[d.tool] || '正在执行工具'
      const detail = d.detail ? `：${d.detail}` : ''
      return `${base}${detail}${suffix}`
    }
    if (d.stage === 'skill') {
      const detail = d.detail ? ` · ${d.detail}` : ''
      return `已命中技能${detail}，正在处理${suffix}`
    }
    if (d.stage === 'thinking' || d.stage === 'idle') return `正在思考…${suffix}`
    return `正在准备…${suffix}`
  },

  // 原地更新「正在思考...」那条 loading 消息（不新增气泡）
  _updateLoadingMessage(content) {
    const messages = this.data.messages.slice()
    const index = messages.findIndex((item) => item.loading)
    if (index < 0) return
    messages[index] = Object.assign({}, messages[index], {
      content: this._formatRichText(content),
    })
    this.setData({ messages })
  },

  replaceLoadingMessage(content, trace) {
    const messages = this.data.messages.slice()
    const index = messages.findIndex((item) => item.loading)
    const next = this.createMessage({
      id: `${Date.now()}_assistant`,
      role: 'assistant',
      content,
      trace: trace || null,
      traceOpen: false,
    })

    if (index >= 0) {
      messages[index] = next
    } else {
      messages.push(next)
    }

    const lastMessage = messages[messages.length - 1]

    this.setData({
      messages,
      scrollIntoView: `msg-${lastMessage.id}`,
    })
  },

  // 规范化：完整轨迹存本地缓存（轨迹页按 id 读），消息上只挂 {id, summary}
  // 供内联折叠条渲染。真实轨迹（后端随回复回传的 trace）与内置样例走同一条路。
  _attachTrace(trace) {
    if (!trace || !trace.id) return null
    const summary = summarize(trace)
    if (!summary) return null
    try { wx.setStorageSync(TRACE_KEY, trace) } catch (e) { /* 缓存失败不影响展示 */ }
    // 记录折叠条索引：刷新/重新编译后消息由后端历史重建（历史里没有 trace），
    // 用它把「执行过程」接回对应的那条回答（按答案文本前缀匹配）。
    try {
      const result = (trace.events || []).find((e) => e.type === 'result')
      const key = answerKey(result && result.answer)
      if (key) wx.setStorageSync(TRACE_INDEX_KEY, { key, id: trace.id, summary })
    } catch (e) { /* 索引入库失败不影响当前展示 */ }
    return { id: trace.id, summary }
  },

  // 演示用：取一次内置录播轨迹，并把**本次真实回答**写进结论，
  // 让回放页的结论与对话一致。过程 / 算力 / 产出仍是内置样例 ——
  // 轨迹页会明确标注（trace.demo），不会让人误以为是真实回放。
  _takeDemoTrace(reply) {
    if (!DEMO_TRACE_ENABLED || this._demoTraceDone) return null
    this._demoTraceDone = true
    const answer = this._plainText(reply)
    return Object.assign({}, DEMO_TRACE, {
      demo: true,
      answerSource: answer ? 'reply' : 'sample',
      events: answer
        ? (DEMO_TRACE.events || []).map((e) => (
          e.type === 'result' ? Object.assign({}, e, { answer }) : e
        ))
        : (DEMO_TRACE.events || []),
    })
  },

  // rich-text / Markdown → 纯文本（轨迹页按纯文本渲染结论）
  _plainText(html) {
    return String(html || '')
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<\/(p|div|tr|li|h[1-6])>/gi, '\n')
      .replace(/<[^>]+>/g, '')
      .replace(/&nbsp;/g, ' ')
      .replace(/&amp;/g, '&')
      .replace(/&lt;/g, '<')
      .replace(/&gt;/g, '>')
      .replace(/\|/g, ' ')
      .replace(/[ \t]+/g, ' ')
      .replace(/\n{2,}/g, '\n')
      .trim()
  },

  // ── 执行过程折叠条：点开就地展开时间轴 ──
  tapToggleTrace(e) {
    const index = Number(e.currentTarget.dataset.index)
    const messages = this.data.messages.slice()
    const msg = messages[index]
    if (!msg || !msg.trace) return
    msg.traceOpen = !msg.traceOpen
    this.setData({ messages })
  },

  // ── 全屏「执行轨迹」观测台（演示模式）──
  tapOpenTrace(e) {
    const id = e.currentTarget.dataset.trace || ''
    const url = id ? `/pages/trace/trace?id=${encodeURIComponent(id)}` : '/pages/trace/trace'
    wx.navigateTo({ url })
  },

  // 运行中进全屏看实时轨迹（轨迹页走 live 模式，轮询后端缓冲）
  tapOpenLiveTrace() {
    wx.navigateTo({ url: '/pages/trace/trace?live=1' })
  },

  createMessage(message) {
    const isUser = message.role === 'user'
    const isLoading = Boolean(message.loading)
    const content = this._formatRichText(message.content || '')

    return Object.assign({}, message, {
      content,
      rowClass: `message-row ${isUser ? 'message-row-user' : 'message-row-assistant'}`,
      bubbleClass: `bubble ${isUser ? 'bubble-user' : 'bubble-assistant'}${isLoading ? ' bubble-loading' : ''}`,
    })
  },

  // rich-text 的节点不继承气泡上的折行样式 —— 折行/换行必须写成**内联样式**，
  // 否则后端返回的长行会直接撑出屏幕（rich-text 只认内联 style）。
  // 顺带把 Markdown 表格转成带内联样式的 HTML 表格；表格用 table-layout:fixed
  // 保证长单元格换行而不是撑宽。
  _formatRichText(html) {
    const WRAP = 'word-break:break-word;overflow-wrap:anywhere;white-space:pre-wrap;max-width:100%'
    const CELL = 'border:1rpx solid #E2E6EC;padding:8rpx 12rpx;word-break:break-all;'
      + 'white-space:normal;overflow-wrap:anywhere;vertical-align:top'
    const TABLE = 'border-collapse:collapse;width:100%;table-layout:fixed;margin:10rpx 0'
    let s = String(html)

    // Markdown 表格 → HTML 表格
    s = s.replace(/\|(.+)\|\s*\n\s*\|[-| :]+\|\s*\n((?:\s*\|.+\|\s*\n?)*)/g, (match, header, rows) => {
      const hCells = header.split('|').filter((c) => c.trim() !== '').map(
        (c) => `<th style="${CELL};background:#F3F5F8;font-weight:600">${c.trim()}</th>`
      ).join('')
      const rCells = rows.trim().split('\n').filter((r) => r.trim()).map((row) =>
        '<tr>' + row.split('|').filter((c) => c.trim() !== '').map(
          (c) => `<td style="${CELL}">${c.trim()}</td>`
        ).join('') + '</tr>'
      ).join('')
      return `<table border="1" style="${TABLE}"><thead><tr>${hCells}</tr></thead><tbody>${rCells}</tbody></table>`
    })

    // 已有的 HTML 表格：补齐（覆盖）内联样式
    s = s.replace(/<table(\s[^>]*)?>/gi, `<table border="1" style="${TABLE}">`)
    s = s.replace(/<th(\s[^>]*)?>/gi, `<th style="${CELL};background:#F3F5F8;font-weight:600">`)
    s = s.replace(/<td(\s[^>]*)?>/gi, `<td style="${CELL}">`)

    return `<div style="${WRAP}">${s}</div>`
  },
})
