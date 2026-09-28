const {
  METHODS_TEACHER_AUTHS, METHODS_TEACHER_AUTHS_APPROVE, METHODS_TEACHER_AUTHS_REJECT,
  METHODS_ADMIN_AUTHS, METHODS_ADMIN_AUTHS_APPROVE, METHODS_ADMIN_AUTHS_REJECT,
  METHODS_CERTIFIED_USERS, METHODS_CERTIFIED_USERS_CHANGE_ROLE,
} = require('../../config/api')
const { UI_TEXT } = require('../../config/instance')
const { read } = require('../../utils/instance-keys')

const CONFIG = {
  'auth-teacher': {
    url: METHODS_TEACHER_AUTHS, title: '老师认证',
    approveUrl: METHODS_TEACHER_AUTHS_APPROVE, rejectUrl: METHODS_TEACHER_AUTHS_REJECT,
    extract(d) {
      if (d && d.items) return d.items.map(r => ({
        id: r.id, status: r.status, label: '#' + r.id + ' ' + (r.name || ''),
        value: '教职工号: ' + (r.staff_id || '-') + '  理由: ' + (r.reason || '-'),
        extra: (r.created_at || ''),
      }))
      return []
    },
  },
  'auth-admin': {
    url: METHODS_ADMIN_AUTHS, title: '管理员认证',
    approveUrl: METHODS_ADMIN_AUTHS_APPROVE, rejectUrl: METHODS_ADMIN_AUTHS_REJECT,
    extract(d) {
      if (d && d.items) return d.items.map(r => ({
        id: r.id, status: r.status, label: '#' + r.id + ' ' + (r.name || ''),
        value: '教职工号: ' + (r.staff_id || '-') + '  理由: ' + (r.reason || '-'),
        extra: (r.created_at || ''),
      }))
      return []
    },
  },
  'certified-users': {
    url: METHODS_CERTIFIED_USERS, title: '教师/管理员',
    extract(d) {
      if (d && d.users) return d.users.map(r => ({
        id: r.user_id, status: r.role,
        label: (r.name || r.username || r.user_id) + ' (' + _roleLabel(r.role) + ')',
        value: (r.username && r.username !== r.user_id ? '用户名: ' + r.username + '  ' : '') + '学/工号: ' + (r.staff_id || '-'),
        extra: '',
      }))
      return []
    },
  },
}

const IDENTITY_TYPES = ['auth-teacher', 'auth-admin', 'certified-users']
const IDENTITY_TITLE = '认证与身份'

function _roleLabel(s) {
  const map = { admin: '管理员', owner: '所有者', teacher: UI_TEXT.ROLE_TEACHER }
  return map[s] || s
}

Page({
  data: {
    items: [], loading: true, empty: false,
    isAuth: false, isIdentity: false,
    isCertifiedUsers: false, showChangeRole: false,
    changeUserId: '', changeTargetRole: '',
    uiText: UI_TEXT,          // 实例界面文案（wxml 绑定）
  },

  onLoad(options) {
    const type = options.type || 'auth-teacher'
    const cfg = CONFIG[type]
    if (!cfg) { wx.navigateBack(); return }
    const isIdentity = IDENTITY_TYPES.includes(type)
    wx.setNavigationBarTitle({ title: isIdentity ? IDENTITY_TITLE : cfg.title })
    this._cfg = cfg
    this._type = type
    const isCertifiedUsers = (type === 'certified-users')
    this.setData({ isIdentity, _type: type, isCertifiedUsers })
    this.setData({ isAuth: type.startsWith('auth-') })
    this._fetch()
  },

  onShow() { if (this._cfg) this._fetch() },

  _fetch() {
    const cfg = this._cfg
    const app = getApp()
    let token = read('SESSION_TOKEN_KEY') || ''
    if (!token && app && app.globalData) token = app.globalData.sessionToken || ''
    if (!token) { this.setData({ loading: false, empty: true }); return }

    wx.request({
      url: cfg.url, method: 'GET', timeout: 30000,
      header: { 'Authorization': `Bearer ${token}` },
      success: (res) => {
        if (res.statusCode === 403) { wx.showToast({ title: '权限不足', icon: 'none' }); wx.navigateBack(); return }
        if (res.statusCode >= 400) { this.setData({ loading: false, empty: true }); return }
        const items = cfg.extract(res.data || {})
        this.setData({ items: items || [], loading: false, empty: !items || items.length === 0 })
      },
      fail: () => { this.setData({ loading: false, empty: true }) },
    })
  },

  switchTab(e) {
    const type = e.currentTarget.dataset.type
    if (!type) return
    wx.redirectTo({ url: `/pages/list/list?type=${type}` })
  },

  tapApprove(e) { this._authAction(e, 'approve') },
  tapReject(e) {
    wx.showModal({
      title: '拒绝申请', editable: true, placeholderText: '请输入拒绝理由',
      success: (r) => { if (r.confirm) this._authAction(e, 'reject', r.content || '') },
    })
  },
  _authAction(e, action, reason) {
    const cfg = this._cfg
    const url = action === 'approve' ? (cfg.approveUrl) : (cfg.rejectUrl)
    if (!url) return
    const item = this.data.items[e.currentTarget.dataset.index]
    if (!item) return
    const app = getApp()
    let token = read('SESSION_TOKEN_KEY') || ''
    if (!token && app && app.globalData) token = app.globalData.sessionToken || ''
    wx.request({
      url: url, method: 'POST', timeout: 15000,
      header: { 'content-type': 'application/json', 'Authorization': `Bearer ${token}` },
      data: { request_id: item.id, reason: reason || '' },
      success: (res) => {
        const d = res.data || {}
        if (res.statusCode === 200 && d.success !== false) {
          wx.showToast({ title: action === 'approve' ? '已通过' : '已拒绝', icon: 'success' })
        } else {
          wx.showToast({ title: d.reply || d.error || '操作未生效', icon: 'none' })
        }
        this._fetch()
      },
      fail: () => { wx.showToast({ title: '操作失败', icon: 'none' }) },
    })
  },

  // ─── 变更身份（certified-users）─────
  tapChangeRole(e) {
    const item = this.data.items[e.currentTarget.dataset.index]
    if (!item) return
    this.setData({ showChangeRole: true, changeUserId: item.id, changeTargetRole: '' })
  },
  onTargetRoleChange(e) { this.setData({ changeTargetRole: e.detail.value }) },
  submitChangeRole() {
    const { changeUserId, changeTargetRole } = this.data
    if (!changeTargetRole) { wx.showToast({ title: '请选择目标身份', icon: 'none' }); return }
    const app = getApp()
    let token = read('SESSION_TOKEN_KEY') || ''
    if (!token && app && app.globalData) token = app.globalData.sessionToken || ''
    wx.request({
      url: METHODS_CERTIFIED_USERS_CHANGE_ROLE, method: 'POST', timeout: 15000,
      header: { 'content-type': 'application/json', 'Authorization': `Bearer ${token}` },
      data: { user_id: changeUserId, role: changeTargetRole },
      success: (res) => {
        const d = res.data || {}
        if (d.success) {
          wx.showToast({ title: '身份已变更', icon: 'success' })
          this.setData({ showChangeRole: false })
          this._fetch()
        } else {
          wx.showToast({ title: d.error || '变更失败', icon: 'none' })
        }
      },
      fail: () => { wx.showToast({ title: '操作失败', icon: 'none' }) },
    })
  },
  cancelChangeRole() { this.setData({ showChangeRole: false }) },
  preventBubble() {},
})
