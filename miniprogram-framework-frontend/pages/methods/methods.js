const {
  METHODS_IDENTITY,
  METHODS_APPLY_AUTH,
  METHODS_TEACHER_AUTHS,
  METHODS_ADMIN_AUTHS,
  METHODS_CHANGE_ROLE,
  UPLOAD_URL,
  METHODS_UNREAD,
} = require('../../config/api')

const { UI_TEXT, FEATURES } = require('../../config/instance')
const { read } = require('../../utils/instance-keys')

Page({
  data: {
    role: 'guest',
    roleLabel: '访客',
    loginStatus: '',
    loading: false,
    resultText: '',
    resultVisible: false,
    showAuthForm: false,
    authType: '',
    authName: '',
    authStaffId: '',
    authReason: '',
    badgeAuthPending: 0,
    // 「入库知识」卡描述（教职工在「我的」里也能用，文案按角色切换）
    ingestDesc: '上传文件到个人知识库，仅自己可检索',
    authStatus: '',
    keyword: '',              // 功能搜索关键词
    groups: [],               // 功能分组（数据驱动，见 _buildGroups）
    uiText: UI_TEXT,          // 实例界面文案（wxml 绑定）
    features: FEATURES,       // 实例功能开关（wx:if 控制模块显隐）
  },

  onLoad() {
    const app = getApp()
    let token = read('SESSION_TOKEN_KEY') || ''
    if (!token && app && app.globalData) token = app.globalData.sessionToken || ''
    if (!token) { this.setData({ loginStatus: '未登录' }); this._refreshGroups(); return }
    this.setData({ sessionToken: token, loginStatus: '已登录' })
    this._call(METHODS_IDENTITY, 'GET', null, (data) => {
      const labels = { guest: '访客', student: '学生', teacher: UI_TEXT.ROLE_TEACHER, admin: '管理员', owner: '所有者' }
      const role = data.role || 'guest'
      const isStaff = role === 'teacher' || role === 'admin' || role === 'owner'
      this.setData({
        role,
        roleLabel: labels[role] || role,
        ingestDesc: isStaff
          ? '上传文件入库，按身份可选择个人 / 教师 / 公共范围'
          : '上传文件到个人知识库，仅自己可检索',
        authStatus: data.auth_status || '',
        openid: data.openid || '',
      })
      this._refreshGroups()
    })
    this._updateBadge()
  },

  onShow() { this._updateBadge() },

  _updateBadge() {
    if (FEATURES.badge === false) return // 实例关闭了未读角标
    const t = this.data.sessionToken || read('SESSION_TOKEN_KEY')
    if (!t) return
    wx.request({
      url: METHODS_UNREAD, method: 'GET', timeout: 10000,
      header: { 'Authorization': `Bearer ${t}` },
      success: (res) => {
        const d = res.data || {}
        const n = d.unread || 0
        if (n > 0) wx.setTabBarBadge({ index: 1, text: String(n) })
        else wx.removeTabBarBadge({ index: 1 })
        this.setData({
          badgeAuthPending: (d.auth_teacher_pending || 0) + (d.auth_admin_pending || 0),
        })
        this._refreshGroups()
      },
    })
  },

  _call(url, method, body, cb) {
    const t = this.data.sessionToken
    if (!t) { wx.showToast({ title: '请先登录', icon: 'none' }); return }
    this.setData({ loading: true })
    wx.request({
      url, method, timeout: 30000,
      header: { 'content-type': 'application/json', 'Authorization': `Bearer ${t}` },
      data: body || {},
      success: (res) => {
        const d = res.data || {}
        if (cb) { cb(d); return }
        if (res.statusCode === 403) { this.setData({ resultText: '权限不足', resultVisible: true }); return }
        this.setData({ resultText: d.reply || JSON.stringify(d), resultVisible: true })
      },
      fail: (e) => { this.setData({ resultText: '请求失败：' + (e.errMsg || '网络错误'), resultVisible: true }) },
      complete: () => this.setData({ loading: false }),
    })
  },

  // ─── 功能清单（数据驱动：分组 / 搜索 / 角色与开关过滤）──────
  _buildGroups() {
    const { role, features, ingestDesc, badgeAuthPending } = this.data
    const isGuest = role === 'guest'
    const isAdmin = role === 'admin' || role === 'owner'
    const groups = []

    if (!isGuest) {
      const personal = []
      if (features.plan) personal.push({ key: 'plan', icon: 'ic-plan', label: '培养方案解读', desc: '学分结构 · 先修关系 · 毕业条件' })
      if (features.planRoute) personal.push({ key: 'planRoute', icon: 'ic-route', label: '学业规划', desc: '四年路线图 · 专业选择 · 转专业模拟' })
      if (personal.length) groups.push({ title: '个人学业', items: personal })

      groups.push({
        title: '知识',
        items: [
          { key: 'ingest', icon: 'ic-addfile', label: '添加知识文件', desc: ingestDesc },
          { key: 'knowledge', icon: 'ic-archive', label: '知识库管理', desc: '查看并删除已入库文件' },
        ],
      })
    }

    if (role === 'teacher') {
      groups.push({
        title: '身份',
        items: [{ key: 'applyAdmin', icon: 'ic-shield', label: '申请管理员认证', desc: '申请成为管理员' }],
      })
    }

    if (isAdmin) {
      const admin = []
      if (features.warning) admin.push({ key: 'warning', icon: 'ic-warn', label: '选课预警', desc: '上传教务数据 · 触发选课检查' })
      if (features.plan) admin.push({ key: 'planManage', icon: 'ic-plan', label: '培养方案管理', desc: '上传方案 · 解析进度 · 先修校对' })
      if (features.docCenter) admin.push({ key: 'docCenter', icon: 'ic-folder', label: '文件中心', desc: '公共教学文件统一上传' })
      admin.push({ key: 'phoneWhitelist', icon: 'ic-phone', label: '电话白名单', desc: '手机号绑定后自动匹配身份' })
      admin.push({ key: 'studentArchive', icon: 'ic-person', label: '学生档案管理', desc: '检索并修改已上传学籍信息' })
      if (features.jxtzSync) admin.push({ key: 'jxtzSync', icon: 'ic-sync', label: '同步管理', desc: '教务通知定时同步与运行结果' })
      admin.push({ key: 'identity', icon: 'ic-shield', label: '认证与身份', desc: '审核申请 · 管理教师与管理员', badge: badgeAuthPending || 0 })
      groups.push({ title: '教务管理', items: admin })
    }

    return groups
  },

  _refreshGroups() {
    this._rawGroups = this._buildGroups()
    this._applyFilter()
  },

  _applyFilter() {
    const kw = (this.data.keyword || '').trim().toLowerCase()
    const raw = this._rawGroups || []
    const groups = raw
      .map((g) => Object.assign({}, g, {
        items: g.items.filter((it) => !kw || (it.label + it.desc).toLowerCase().indexOf(kw) >= 0),
      }))
      .filter((g) => g.items.length)
    this.setData({ groups })
  },

  onSearch(e) {
    this.setData({ keyword: e.detail.value })
    this._applyFilter()
  },

  clearSearch() {
    this.setData({ keyword: '' })
    this._applyFilter()
  },

  // 卡片统一入口：按 key 分发到原有处理函数（导航逻辑保持原样）
  tapCard(e) {
    const key = e.currentTarget.dataset.key
    const routes = {
      plan: 'tapPlan',
      planRoute: 'tapPlanRoute',
      ingest: 'tapIngestKnowledge',
      knowledge: 'tapKnowledgeManage',
      applyAdmin: 'tapApplyAdmin',
      warning: 'tapWarning',
      planManage: 'tapPlanManage',
      docCenter: 'tapDocCenter',
      phoneWhitelist: 'tapPhoneWhitelist',
      studentArchive: 'tapStudentArchive',
      jxtzSync: 'tapJxtzSync',
      identity: 'tapIdentityManage',
    }
    const fn = routes[key]
    if (fn && typeof this[fn] === 'function') this[fn]()
  },

  // ─── 学生 ──────────────────────────────
  tapApplyAdmin() {
    if (['admin','owner'].includes(this.data.role)) { wx.showToast({ title: '已是管理员', icon: 'none' }); return }
    this._maybePrefillAuth('admin')
  },

  _maybePrefillAuth(authType) {
    // Check if user already has a pending auth
    const pending = this.data.authStatus || ''
    if (pending.includes('pending')) {
      const pendingLabel = pending.includes('teacher') ? '教师身份' : '管理员身份'
      wx.showToast({ title: `您已提交${pendingLabel}申请，请勿重复提交`, icon: 'none', duration: 2500 })
      return
    }
    this.setData({ showAuthForm: true, authType, authName: '', authStaffId: '', authReason: '' })
  },

  // 认证弹窗
  onAuthName(e) { this.setData({ authName: e.detail.value }) },
  onAuthStaffId(e) { this.setData({ authStaffId: e.detail.value }) },
  onAuthReason(e) { this.setData({ authReason: e.detail.value }) },

  submitAuth() {
    const { authType, authName, authStaffId, authReason } = this.data
    if (!authName.trim() || !authStaffId.trim()) {
      wx.showToast({ title: '请填写姓名和教职工号', icon: 'none' }); return
    }
    this.setData({ showAuthForm: false })
    const s = this
    this._call(METHODS_APPLY_AUTH, 'POST', {
      auth_type: authType, name: authName.trim(), staff_id: authStaffId.trim(), reason: authReason.trim(),
    }, (d) => {
      // Update auth status to prevent re-submit
      if (d && d.status !== 'already_approved') {
        s.setData({ authStatus: authType === 'teacher' ? 'teacher_pending' : 'admin_pending' })
      }
    })
  },

  cancelAuth() { this.setData({ showAuthForm: false }) },
  preventBubble() {},

  // ─── 教师 ──────────────────────────────
  tapKnowledgeManage() { wx.navigateTo({ url: '/pages/knowledge/knowledge' }) },

  tapIngestKnowledge() {
    const token = this.data.sessionToken
    if (!token) { wx.showToast({ title: '请先登录', icon: 'none' }); return }
    const role = this.data.role || 'guest'
    if (role === 'guest') { wx.showToast({ title: '访客无法使用知识库', icon: 'none' }); return }
    const items = ['个人知识库（仅自己可检索）']
    const scopes = [this._personalScope()]
    if (scopes[0] === '') return
    if (role === 'teacher' || role === 'admin' || role === 'owner') {
      items.push('教师知识库（仅教师可检索）')
      scopes.push('teachers')
    }
    if (role === 'admin' || role === 'owner') {
      items.push('公共知识库（所有人可检索）')
      scopes.push('global')
    }
    wx.showActionSheet({
      itemList: items,
      success: ({ tapIndex }) => this._chooseKnowledgeFile(scopes[tapIndex]),
    })
  },

  _personalScope() {
    const app = getApp()
    const openid = this.data.openid || (app && app.globalData && app.globalData.openid) || ''
    if (!openid) {
      wx.showToast({ title: '未获取到身份信息，请重新登录', icon: 'none' })
      return ''
    }
    return `users/${openid}`
  },

  _chooseKnowledgeFile(scope) {
    const token = this.data.sessionToken
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['docx', 'xlsx', 'csv', 'txt', 'md', 'pdf', 'png', 'jpg', 'jpeg'],
      success: (chooseRes) => {
        const file = chooseRes.tempFiles[0]
        if (file && file.size > 10 * 1024 * 1024) {
          wx.showToast({ title: '文件超过 10MB 上限', icon: 'none' })
          return
        }
        wx.showLoading({ title: '上传并入库中...' })
        wx.uploadFile({
          url: `${UPLOAD_URL}?scope=${scope}`,
          filePath: file.path,
          name: 'file',
          timeout: 60000,
          header: { 'Authorization': `Bearer ${token}` },
          success: (uploadRes) => {
            wx.hideLoading()
            if (uploadRes.statusCode >= 200 && uploadRes.statusCode < 300) {
              try {
                const d = JSON.parse(uploadRes.data || '{}')
                const att = d.attachment || {}
                const ingested = att.ingested ? '已自动入库' : '上传成功'
                const nodes = att.ingest_nodes ? `，${att.ingest_nodes} 个知识片段` : ''
                wx.showToast({ title: `${ingested}${nodes}`, icon: 'success', duration: 2500 })
              } catch (_) {
                wx.showToast({ title: '上传成功', icon: 'success' })
              }
            } else if (uploadRes.statusCode === 413 || uploadRes.statusCode === 415) {
              wx.showToast({ title: '文件过大（上限 10MB）', icon: 'none' })
            } else {
              wx.showToast({ title: `上传失败：${uploadRes.statusCode}`, icon: 'none' })
            }
          },
          fail: () => {
            wx.hideLoading()
            wx.showToast({ title: '上传失败，请重试', icon: 'none' })
          },
        })
      },
    })
  },

  // ─── 管理员 ────────────────────────────
  tapIdentityManage() { wx.navigateTo({ url: '/pages/list/list?type=auth-teacher' }) },
  tapPhoneWhitelist() { wx.navigateTo({ url: '/pages/phone-whitelist/phone-whitelist' }) },
  tapStudentArchive() { wx.navigateTo({ url: '/pages/student-archive/student-archive' }) },
  tapWarning() { wx.navigateTo({ url: '/pages/warning/warning' }) },
  tapPlan() { wx.navigateTo({ url: '/pages/plan/plan' }) },
  tapPlanRoute() { wx.navigateTo({ url: '/pages/plan-route/plan-route' }) },
  tapPlanManage() { wx.navigateTo({ url: '/pages/plan-manage/plan-manage' }) },
  tapDocCenter() { wx.navigateTo({ url: '/pages/doc-center/doc-center' }) },
  tapJxtzSync() { wx.navigateTo({ url: '/pages/jxtz-sync/jxtz-sync' }) },

  tapRefresh() { this.onLoad() },
  closeResult() { this.setData({ resultVisible: false }) },
})
