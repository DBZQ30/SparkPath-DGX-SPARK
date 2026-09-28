// pages/profile —— 我的档案（011 重构）
// 档案字段来自管理员上传的学籍信息（student_archive），学生只读；
// 管理员可在「学生档案管理」页维护。手机号绑定保留在本页（身份匹配依据）。
const {
  METHODS_ADMISSION_PROFILE, METHODS_PHONE_BIND,
  METHODS_IDENTITY, METHODS_STUDENT_ARCHIVE_LINK,
} = require('../../config/api')
const { FEATURES } = require('../../config/instance')
const { read } = require('../../utils/instance-keys')

// 学籍档案展示字段（顺序与后端 _STUDENT_ARCHIVE_DISPLAY 一致）
const ARCHIVE_FIELDS = [
  ['学号', 'student_id'], ['姓名', 'name'], ['性别', 'gender'],
  ['出生日期', 'birth_date'], ['民族', 'ethnicity'], ['政治面貌', 'political_status'],
  ['所属书院', 'residence_college'], ['年级', 'grade'], ['学院', 'school'],
  ['系', 'department'], ['托管院系', 'host_department'], ['专业', 'major'],
  ['专业方向', 'major_direction'], ['学制', 'study_years'], ['班级', 'class_name'],
  ['是否在籍', 'enrolled'], ['入学日期', 'enroll_date'], ['入学年级', 'enroll_grade'],
  ['入学专业', 'enroll_major'], ['个人手机', 'phone'], ['个人邮箱', 'email'],
  ['紧急联系人', 'emergency_contact'], ['紧急联系方式', 'emergency_phone'],
]

Page({
  data: {
    loading: true,
    phoneAuthMode: FEATURES.phoneAuthMode || 'manual',
    phone: '', phoneVerified: false,
    hasArchive: false,
    archiveMatch: '',        // 'phone' | 'student_id' | ''
    archiveFields: [],       // [{label, value}] 已过滤空值
    role: 'guest',
    isAdmin: false,
    linkId: '',              // 学号兜底匹配输入
    linking: false,
  },

  onLoad() {
    const app = getApp()
    let token = read('SESSION_TOKEN_KEY') || ''
    if (!token && app && app.globalData) token = app.globalData.sessionToken || ''
    if (!token) { this.setData({ loading: false }); return }
    this._token = token
    this._load()
  },

  onShow() { if (this._token) this._load() },

  _load() {
    wx.request({
      url: METHODS_ADMISSION_PROFILE, method: 'GET', timeout: 15000,
      header: { 'Authorization': `Bearer ${this._token}` },
      success: (res) => {
        const d = res.data || {}
        const archive = d.archive || null
        this.setData({
          loading: false,
          phone: d.phone || '',
          hasArchive: !!archive,
          archiveMatch: d.archive_match || '',
          archiveFields: archive ? this._decorate(archive) : [],
        })
      },
      fail: () => this.setData({ loading: false }),
    })
    // 手机号验证状态 + 角色
    wx.request({
      url: METHODS_IDENTITY, method: 'GET', timeout: 15000,
      header: { 'Authorization': `Bearer ${this._token}` },
      success: (res) => {
        const d = res.data || {}
        const role = d.role || 'guest'
        this.setData({
          phoneVerified: !!d.phone_verified,
          role,
          isAdmin: role === 'admin' || role === 'owner',
        })
      },
    })
  },

  _decorate(archive) {
    return ARCHIVE_FIELDS
      .map(([label, key]) => ({ label, value: (archive[key] || '').toString().trim() }))
      .filter((f) => f.value)
  },

  // ─── 学号兜底绑定（手机号匹配不到时）───
  onLinkId(e) { this.setData({ linkId: e.detail.value }) },
  tapLinkId() {
    const sid = (this.data.linkId || '').trim()
    if (!sid) { wx.showToast({ title: '请输入学号', icon: 'none' }); return }
    if (this.data.linking) return
    this.setData({ linking: true })
    wx.showLoading({ title: '匹配中...' })
    wx.request({
      url: METHODS_STUDENT_ARCHIVE_LINK, method: 'POST', timeout: 15000,
      header: { 'Authorization': `Bearer ${this._token}`, 'content-type': 'application/json' },
      data: { student_id: sid },
      success: (res) => {
        wx.hideLoading()
        this.setData({ linking: false })
        if (res.statusCode >= 200 && res.statusCode < 300) {
          const archive = (res.data || {}).archive || null
          this.setData({
            hasArchive: !!archive,
            archiveMatch: 'student_id',
            archiveFields: archive ? this._decorate(archive) : [],
          })
          wx.showToast({ title: '已匹配到学籍信息', icon: 'success' })
        } else {
          const d = res.data || {}
          wx.showToast({ title: d.error || '未找到该学号', icon: 'none' })
        }
      },
      fail: () => { wx.hideLoading(); this.setData({ linking: false }); wx.showToast({ title: '网络错误', icon: 'none' }) },
    })
  },

  tapArchiveManage() { wx.navigateTo({ url: '/pages/student-archive/student-archive' }) },

  // ─── 手机号绑定 ──────────────
  // manual 模式：手动输入电话直接绑定（dev/未认证阶段，后端 PHONE_VERIFY_MODE=dev 接受）
  bindPhoneManual() {
    const phone = (this.data.phone || '').trim()
    if (!phone) { wx.showToast({ title: '请先输入电话', icon: 'none' }); return }
    wx.showLoading({ title: '绑定中...' })
    wx.request({
      url: METHODS_PHONE_BIND, method: 'POST', timeout: 15000,
      header: { 'Authorization': `Bearer ${this._token}`, 'content-type': 'application/json' },
      data: { mock_phone: phone },
      success: (res) => {
        wx.hideLoading()
        if (res.statusCode >= 200 && res.statusCode < 300) {
          const d = res.data || {}
          this.setData({ phone: d.phone || phone, phoneVerified: true })
          wx.showToast({ title: '手机号已验证', icon: 'success' })
          this._load()
        } else {
          const d = res.data || {}
          wx.showToast({ title: d.error || `绑定失败：${res.statusCode}`, icon: 'none' })
        }
      },
      fail: () => { wx.hideLoading(); wx.showToast({ title: '绑定失败，请重试', icon: 'none' }) },
    })
  },

  // wechat 模式：微信 getPhoneNumber 授权绑定
  bindPhoneAuth(e) {
    const code = e.detail && e.detail.code
    if (!code) {
      const errMsg = (e.detail && e.detail.errMsg) || '未获得授权'
      console.error('getPhoneNumber 失败:', e.detail)
      wx.showToast({ title: errMsg.slice(0, 20), icon: 'none' })
      return
    }
    wx.showLoading({ title: '绑定中...' })
    wx.request({
      url: METHODS_PHONE_BIND, method: 'POST', timeout: 15000,
      header: { 'Authorization': `Bearer ${this._token}`, 'content-type': 'application/json' },
      data: { code },
      success: (res) => {
        wx.hideLoading()
        if (res.statusCode >= 200 && res.statusCode < 300) {
          const d = res.data || {}
          this.setData({ phone: d.phone || this.data.phone, phoneVerified: true })
          wx.showToast({ title: '手机号已验证', icon: 'success' })
          this._load()
        } else {
          const d = res.data || {}
          wx.showToast({ title: d.error || `绑定失败：${res.statusCode}`, icon: 'none' })
        }
      },
      fail: () => { wx.hideLoading(); wx.showToast({ title: '绑定失败，请重试', icon: 'none' }) },
    })
  },

  onPhoneInput(e) { this.setData({ phone: e.detail.value }) },
})
