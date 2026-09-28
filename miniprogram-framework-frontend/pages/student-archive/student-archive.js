// pages/student-archive —— 学生档案管理（admin/owner，011）
// 学籍信息由「电话白名单 → 导入学生信息」上传；本页供管理员检索与修改。
const { METHODS_STUDENT_ARCHIVE } = require('../../config/api')
const { read } = require('../../utils/instance-keys')

// 可编辑字段（学号为主键，不可改）
const EDIT_FIELDS = [
  ['姓名', 'name'], ['性别', 'gender'], ['出生日期', 'birth_date'], ['民族', 'ethnicity'],
  ['政治面貌', 'political_status'], ['所属书院', 'residence_college'], ['年级', 'grade'],
  ['学院', 'school'], ['系', 'department'], ['托管院系', 'host_department'],
  ['专业', 'major'], ['专业方向', 'major_direction'], ['学制', 'study_years'],
  ['班级', 'class_name'], ['是否在籍', 'enrolled'], ['入学日期', 'enroll_date'],
  ['入学年级', 'enroll_grade'], ['入学专业', 'enroll_major'], ['个人手机', 'phone'],
  ['个人邮箱', 'email'], ['紧急联系人', 'emergency_contact'], ['紧急联系方式', 'emergency_phone'],
]

Page({
  data: {
    loading: true,
    loadFailed: false,
    keyword: '',
    items: [],
    count: 0,
    editing: false,
    editStudentId: '',
    editName: '',
    editFields: [],
    saving: false,
  },

  onLoad() {
    const app = getApp()
    let token = read('SESSION_TOKEN_KEY') || ''
    if (!token && app && app.globalData) token = app.globalData.sessionToken || ''
    if (!token) { this.setData({ loading: false, loadFailed: true }); return }
    this._token = token
    this._load()
  },

  _load() {
    this.setData({ loading: true, loadFailed: false })
    const kw = encodeURIComponent((this.data.keyword || '').trim())
    wx.request({
      url: `${METHODS_STUDENT_ARCHIVE}?keyword=${kw}`,
      method: 'GET', timeout: 15000,
      header: { 'Authorization': `Bearer ${this._token}` },
      success: (res) => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          this.setData({ loading: false, loadFailed: true }); return
        }
        const d = res.data || {}
        const items = (d.items || []).map((it) => ({
          ...it,
          main: it.name ? `${it.name}（${it.student_id}）` : it.student_id,
          sub: [it.major, it.class_name, it.phone].filter(Boolean).join(' · '),
        }))
        this.setData({ loading: false, items, count: d.count || items.length })
      },
      fail: () => this.setData({ loading: false, loadFailed: true }),
    })
  },

  onSearch(e) { this.setData({ keyword: e.detail.value }) },
  tapSearch() { this._load() },
  clearSearch() { this.setData({ keyword: '' }); this._load() },
  noop() {},

  // ─── 编辑 ───
  tapEdit(e) {
    const studentId = e.currentTarget.dataset.id
    const item = (this.data.items || []).find((it) => it.student_id === studentId)
    if (!item) return
    this.setData({
      editing: true,
      editStudentId: studentId,
      editName: item.name || '',
      editFields: EDIT_FIELDS.map(([label, key]) => ({
        label, key, value: (item[key] || '').toString(),
      })),
    })
  },

  onField(e) {
    const index = Number(e.currentTarget.dataset.index)
    const value = e.detail.value
    const editFields = this.data.editFields.slice()
    if (editFields[index]) editFields[index].value = value
    this.setData({ editFields })
  },

  tapCancelEdit() { this.setData({ editing: false }) },

  tapSave() {
    if (this.data.saving) return
    const payload = { student_id: this.data.editStudentId }
    for (const f of this.data.editFields) payload[f.key] = (f.value || '').trim()
    this.setData({ saving: true })
    wx.showLoading({ title: '保存中...' })
    wx.request({
      url: METHODS_STUDENT_ARCHIVE, method: 'PUT', timeout: 15000,
      header: { 'Authorization': `Bearer ${this._token}`, 'content-type': 'application/json' },
      data: payload,
      success: (res) => {
        wx.hideLoading()
        this.setData({ saving: false })
        if (res.statusCode >= 200 && res.statusCode < 300) {
          wx.showToast({ title: '已保存', icon: 'success' })
          this.setData({ editing: false })
          this._load()
        } else {
          const d = res.data || {}
          wx.showToast({ title: d.error || '保存失败', icon: 'none' })
        }
      },
      fail: () => { wx.hideLoading(); this.setData({ saving: false }); wx.showToast({ title: '网络错误', icon: 'none' }) },
    })
  },
})
