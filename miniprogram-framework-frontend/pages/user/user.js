const { LOGIN_URL, METHODS_IDENTITY, METHODS_PROFILE } = require('../../config/api')
const { INSTANCE_ID, UI_TEXT, WELCOME_TEXT } = require('../../config/instance')
const { KEYS, read } = require('../../utils/instance-keys')

Page({
  data: {
    role: 'guest',
    roleLabel: '访客',
    loginStatus: '检查中...',
    username: '游客',
    avatarChar: '游',
    logging: false,
    showEdit: false,
    editValue: '',
    showAbout: false,
    aboutText: WELCOME_TEXT,
  },

  onLoad() {
    const app = getApp()
    let token = read('SESSION_TOKEN_KEY') || ''
    if (!token && app && app.globalData) token = app.globalData.sessionToken || ''
    if (!token) { this.setData({ loginStatus: '未登录' }); return }
    this.setData({ loginStatus: '已登录', _token: token })
    this._loadProfile(token)
  },

  onShow() { this.onLoad() },

  tapLogin() {
    if (this.data.logging) return
    this.setData({ logging: true, loginStatus: '登录中...' })
    wx.login({
      success: (res) => {
        if (!res.code) { this.setData({ logging: false, loginStatus: '登录失败' }); return }
        wx.request({
          url: LOGIN_URL, method: 'POST', timeout: 15000,
          header: { 'content-type': 'application/json' },
          data: { code: res.code, instance_id: INSTANCE_ID },
          success: (r) => {
            const d = r.data || {}
            const token = d.session_token || ''
            if (!token) { this.setData({ logging: false, loginStatus: '登录失败' }); return }
            wx.setStorageSync(KEYS.SESSION_TOKEN_KEY, token)
            const app = getApp()
            if (app && app.globalData) app.globalData.sessionToken = token
            this.setData({ _token: token })
            this._loadProfile(token)
          },
          fail: () => { this.setData({ logging: false, loginStatus: '连接失败' }) },
        })
      },
      fail: () => { this.setData({ logging: false, loginStatus: '登录失败' }) },
    })
  },

  _loadProfile(token) {
    let profileDone = false, identityDone = false

    wx.request({
      url: METHODS_PROFILE, method: 'GET', timeout: 15000,
      header: { 'Authorization': `Bearer ${token}` },
      success: (res) => {
        const d = res.data || {}
        const name = d.username || '游客'
        this.setData({ username: name, avatarChar: name.slice(0, 1) })
        wx.setStorageSync(KEYS.USERNAME_KEY, name)
        const app2 = getApp(); if (app2 && app2.globalData) app2.globalData.username = name
        profileDone = true
        if (identityDone) this.setData({ logging: false })
      },
      fail: () => { profileDone = true; if (identityDone) this.setData({ logging: false }) },
    })

    wx.request({
      url: METHODS_IDENTITY, method: 'GET', timeout: 15000,
      header: { 'Authorization': `Bearer ${token}` },
      success: (res) => {
        const d = res.data || {}
        const role = d.role || 'guest'
        const labels = { guest: '访客', student: '学生', teacher: UI_TEXT.ROLE_TEACHER, admin: '管理员', owner: '所有者' }
        this.setData({ role, roleLabel: labels[role] || role, loginStatus: '已登录' })
        identityDone = true
        if (profileDone) this.setData({ logging: false })
      },
      fail: () => { identityDone = true; if (profileDone) this.setData({ logging: false }) },
    })
  },

  tapEditName() {
    this.setData({ showEdit: true, editValue: this.data.username })
  },

  tapProfile() { wx.navigateTo({ url: '/pages/profile/profile' }) },

  onEditInput(e) { this.setData({ editValue: e.detail.value }) },

  submitEdit() {
    const name = (this.data.editValue || '').trim()
    if (!name) { wx.showToast({ title: '用户名不能为空', icon: 'none' }); return }
    const token = this.data._token
    this.setData({ showEdit: false })
    wx.request({
      url: METHODS_PROFILE, method: 'PUT', timeout: 15000,
      header: { 'content-type': 'application/json', 'Authorization': `Bearer ${token}` },
      data: { username: name },
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          this.setData({ username: name, avatarChar: name.slice(0, 1) })
          wx.setStorageSync(KEYS.USERNAME_KEY, name)
          wx.showToast({ title: '已保存', icon: 'success' })
        } else {
          wx.showToast({ title: '保存失败', icon: 'none' })
        }
      },
      fail: () => { wx.showToast({ title: '网络错误', icon: 'none' }) },
    })
  },

  cancelEdit() { this.setData({ showEdit: false }) },

  tapAbout() { this.setData({ showAbout: true }) },
  closeAbout() { this.setData({ showAbout: false }) },

  noop() {},

  preventBubble() {},
})
