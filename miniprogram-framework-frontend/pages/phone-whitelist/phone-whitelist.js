// pages/phone-whitelist —— 电话白名单管理（admin/owner）
// 2026-09-08 改版：视觉/触控对齐预警页基线；添加表单改底部弹层；列表支持搜索；
// 导入结果新增「身份更新」行（白名单 → roles.json 最新为准，文案不暴露后端）
// 2026-09-08 二次确认：导入/删除先取后端 dry_run 预览（不写库），有人身份降低或
// 删除后「不在任何名单中」时弹窗提示，防止误传文件/误删名单
const {
  METHODS_PHONE_WHITELIST,
  METHODS_PHONE_WHITELIST_IMPORT_STUDENT,
  METHODS_PHONE_WHITELIST_IMPORT_TEACHER,
  METHODS_PHONE_WHITELIST_IMPORT_ADMIN,
} = require('../../config/api')
const { read } = require('../../utils/instance-keys')

const ROLE_LABELS = { student: '学生', teacher: '教师', admin: '管理员', owner: '负责人' }
const ROLE_CHIPS = { student: 'chip-student', teacher: 'chip-teacher',
                     admin: 'chip-admin', owner: 'chip-owner' }
const ROLE_OPTIONS = [
  { value: 'student', label: '学生' },
  { value: 'teacher', label: '教师' },
  { value: 'admin', label: '管理员' },
]

Page({
  data: {
    items: [],          // 全量（已派生 main/sub/roleLabel/roleChip）
    view: [],           // 搜索过滤后
    batches: [],        // 导入批次（可整份删除）
    total: 0,
    matchCount: 0,
    keyword: '',
    loading: true,
    loadFailed: false,
    hintOpen: false,
    showForm: false,
    formPhone: '', formStaffId: '', formName: '', formRole: 'student',
    roleOptions: ROLE_OPTIONS,
    importing: false,
    importResult: null,   // {imported, updated, skipped, failed, errors, roleSync}
    kbdH: 0,              // 键盘高度 px（弹层抬升防遮挡）
  },

  onLoad() {
    const app = getApp()
    let token = read('SESSION_TOKEN_KEY') || ''
    if (!token && app && app.globalData) token = app.globalData.sessionToken || ''
    this._token = token
    this._load()
  },

  // 派生展示字段：姓名缺失时手机号升为主行
  _decorate(list) {
    return (list || []).map((it) => {
      const name = (it.name || '').trim()
      const staff = (it.staff_id || '').trim()
      return {
        ...it,
        main: name || it.phone,
        sub: (name ? [it.phone, staff] : [staff]).filter(Boolean).join(' · '),
        roleLabel: ROLE_LABELS[it.role] || it.role,
        roleChip: ROLE_CHIPS[it.role] || 'chip-student',
      }
    })
  },

  // 本地过滤：姓名 / 手机号 / 工号
  _filter() {
    const kw = (this.data.keyword || '').trim().toLowerCase()
    const all = this.data.items || []
    const view = kw
      ? all.filter((it) => (it.name || '').toLowerCase().includes(kw)
          || (it.phone || '').includes(kw)
          || (it.staff_id || '').toLowerCase().includes(kw))
      : all
    this.setData({ view, matchCount: view.length })
  },

  _load() {
    if (!this._token) { this.setData({ loading: false, loadFailed: false }); return }
    this.setData({ loading: true, loadFailed: false })
    wx.request({
      url: METHODS_PHONE_WHITELIST, method: 'GET', timeout: 15000,
      header: { 'Authorization': `Bearer ${this._token}` },
      success: (res) => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          this.setData({ loading: false, loadFailed: true }); return
        }
        const data = res.data || {}
        const items = this._decorate(data.items)
        this.setData({ loading: false, loadFailed: false, items, total: items.length,
                       batches: data.batches || [] })
        this._filter()
      },
      fail: () => this.setData({ loading: false, loadFailed: true }),
    })
  },

  onSearch(e) { this.setData({ keyword: e.detail.value }); this._filter() },
  clearSearch() { this.setData({ keyword: '' }); this._filter() },
  toggleHint() { this.setData({ hintOpen: !this.data.hintOpen }) },
  noop() {},

  // —— 添加弹层（Bottom Sheet）——
  tapAdd() { this.setData({ showForm: true, kbdH: 0 }) },
  closeForm() { this.setData({ showForm: false, kbdH: 0 }) },
  onKbd(e) { this.setData({ kbdH: Number((e.detail && e.detail.height) || 0) }) },
  onKbdHide() { this.setData({ kbdH: 0 }) },
  onPhone(e) { this.setData({ formPhone: e.detail.value }) },
  onStaffId(e) { this.setData({ formStaffId: e.detail.value }) },
  onName(e) { this.setData({ formName: e.detail.value }) },
  onRole(e) { this.setData({ formRole: e.currentTarget.dataset.value }) },

  submitAdd() {
    const { formPhone, formStaffId, formName, formRole } = this.data
    if (!formPhone || formPhone.length !== 11) {
      wx.showToast({ title: '请输入11位手机号', icon: 'none' }); return
    }
    wx.showLoading({ title: '保存中...' })
    wx.request({
      url: METHODS_PHONE_WHITELIST, method: 'POST', timeout: 15000,
      header: { 'Authorization': `Bearer ${this._token}`, 'content-type': 'application/json' },
      data: { phone: formPhone, staff_id: formStaffId, name: formName, role: formRole },
      success: (res) => {
        wx.hideLoading()
        if (res.statusCode >= 200 && res.statusCode < 300) {
          wx.showToast({ title: '已保存', icon: 'success' })
          this.setData({ showForm: false, kbdH: 0,
                         formPhone: '', formStaffId: '', formName: '', formRole: 'student' })
          this._load()
        } else {
          const d = res.data || {}
          wx.showToast({ title: d.error || '保存失败', icon: 'none' })
        }
      },
      fail: () => { wx.hideLoading(); wx.showToast({ title: '保存失败', icon: 'none' }) },
    })
  },

  // 批量导入 Excel（011：学生/教师/管理员分开入口，角色由接口固定）：
  //  · 学生信息 = 学籍信息表（学号/姓名/个人手机 + 档案字段）→ 同时写档案与白名单
  //  · 教师/管理员 = 简版表（姓名/电话/工号）
  // 库中已存在/文件内重复 → 后到覆盖；坏行跳过并返回逐条原因；
  // 导入后按「最新为准」同步 roles.json（响应 role_sync）
  tapImportStudent() { this._pickImport(METHODS_PHONE_WHITELIST_IMPORT_STUDENT, '学生信息') },
  tapImportTeacher() { this._pickImport(METHODS_PHONE_WHITELIST_IMPORT_TEACHER, '教师名单') },
  tapImportAdmin() { this._pickImport(METHODS_PHONE_WHITELIST_IMPORT_ADMIN, '管理员名单') },

  _pickImport(url, kind) {
    if (this.data.importing) return
    wx.chooseMessageFile({
      count: 1, type: 'file', extension: ['xlsx', 'xls'],
      success: (res) => {
        const f = (res.tempFiles || [])[0]
        if (!f) return
        this._uploadImport(url, kind, f, f.name || kind, true)
      },
      fail: () => {},
    })
  },

  // 两阶段导入：dryRun=true 先预览（后端不写库），若有人身份会降低 → 二次确认；
  // 确认（或无人降级）后再用同一文件正式导入
  _uploadImport(url, kind, file, label, dryRun) {
    this.setData({ importing: true })
    wx.showLoading({ title: dryRun ? '校验中...' : '导入中...', mask: true })
    const formData = { label }   // multipart filename 对中文会乱码，名单名单独传
    if (dryRun) formData.dry_run = '1'
    wx.uploadFile({
      url,
      filePath: file.path, name: 'file',
      formData,
      header: { 'Authorization': `Bearer ${this._token}` },
      timeout: 60000,
      success: (r) => {
        let d = {}
        try { d = JSON.parse(r.data) } catch (e) {}
        if (r.statusCode < 200 || r.statusCode >= 300 || !d || typeof d.imported !== 'number') {
          wx.hideLoading()
          this.setData({ importing: false })
          wx.showToast({ title: (d && d.error) || `导入失败（HTTP ${r.statusCode}）`, icon: 'none' })
          return
        }
        if (dryRun) {
          const down = ((d.preview || {}).downgrades) || []
          if (!down.length) { this._uploadImport(url, kind, file, label, false); return }
          wx.hideLoading()
          this.setData({ importing: false })
          wx.showModal({
            title: `⚠️ ${down.length} 人身份将降低`,
            content: `${this._names(down)}\n确认按这份文件导入吗？取消不会写入任何数据。`,
            confirmText: '继续导入', confirmColor: '#e64340',
            success: (m) => { if (m.confirm) this._uploadImport(url, kind, file, label, false) },
          })
          return
        }
        this.setData({
          importResult: { kind, imported: d.imported, updated: d.updated,
                          skipped: d.skipped, failed: d.failed,
                          archiveImported: d.archive_imported || 0,
                          archiveUpdated: d.archive_updated || 0,
                          errors: d.errors || [], roleSync: d.role_sync || null },
          importing: false,
        })
        wx.hideLoading()
        wx.showToast({ title: `导入完成：新增 ${d.imported}，更新 ${d.updated}`, icon: 'none' })
        this._load()
      },
      fail: (err) => {
        wx.hideLoading()
        this.setData({ importing: false })
        wx.showToast({ title: (err && err.errMsg) || '网络错误', icon: 'none' })
      },
    })
  },
  closeImportResult() { this.setData({ importResult: null }) },

  // 「姓名（原身份→新身份）、…」摘要，最多 3 人
  _names(list) {
    const s = (list || []).slice(0, 3).map((it) => {
      const who = it.name || it.phone
      return `${who}（${ROLE_LABELS[it.from] || it.from}→${ROLE_LABELS[it.to] || it.to}）`
    })
    return s.join('、') + (list.length > 3 ? ` 等 ${list.length} 人` : '')
  },

  // 单条删除：先取预览（不写库），弹窗显示「当前身份 → 回退后」，再真正删除
  removeItem(e) {
    const { phone } = e.currentTarget.dataset
    wx.request({
      url: METHODS_PHONE_WHITELIST, method: 'DELETE', timeout: 15000,
      header: { 'Authorization': `Bearer ${this._token}`, 'content-type': 'application/json' },
      data: { phone, dry_run: true },
      success: (res) => {
        const d = res.data || {}
        if (res.statusCode < 200 || res.statusCode >= 300) {
          wx.showToast({ title: d.error || '操作失败', icon: 'none' }); return
        }
        const hit = (((d.preview || {}).changes) || [])[0]
        let content = `确认将 ${phone} 从名单中移除吗？`
        if (hit) {
          content += `\n该号码当前身份：${ROLE_LABELS[hit.from] || hit.from}，`
            + (hit.only_here
              ? `移除后不在任何名单中，身份将回退为${ROLE_LABELS[hit.to] || hit.to}。`
              : `移除后回落到其他名单的${ROLE_LABELS[hit.to] || hit.to}。`)
        } else {
          content += '移除后身份会按剩余名单重新计算。'
        }
        wx.showModal({
          title: '移出名单', content, confirmColor: '#e64340',
          success: (m) => { if (m.confirm) this._doRemoveItem(phone) },
        })
      },
      fail: () => wx.showToast({ title: '操作失败', icon: 'none' }),
    })
  },

  _doRemoveItem(phone) {
    wx.request({
      url: METHODS_PHONE_WHITELIST, method: 'DELETE', timeout: 15000,
      header: { 'Authorization': `Bearer ${this._token}`, 'content-type': 'application/json' },
      data: { phone },
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          const d = res.data || {}
          const demoted = d.role_sync && d.role_sync.demoted
          wx.showToast({ title: demoted ? '已移出，身份已回退' : '已移出', icon: 'success' })
          this._load()
        } else {
          wx.showToast({ title: '移除失败', icon: 'none' })
        }
      },
      fail: () => wx.showToast({ title: '移除失败', icon: 'none' }),
    })
  },

  // 删除一整份导入名单：先取预览（不写库），有人「只在这份名单中」或身份会降低 → 弹窗提示
  removeBatch(e) {
    const { batchId, label } = e.currentTarget.dataset
    wx.request({
      url: METHODS_PHONE_WHITELIST + '/batch', method: 'DELETE', timeout: 15000,
      header: { 'Authorization': `Bearer ${this._token}`, 'content-type': 'application/json' },
      data: { batch_id: batchId, dry_run: true },
      success: (res) => {
        const d = res.data || {}
        if (res.statusCode < 200 || res.statusCode >= 300) {
          wx.showToast({ title: d.error || '操作失败', icon: 'none' }); return
        }
        const p = d.preview || {}
        const vanish = p.vanish || []
        const down = p.downgrades || []
        const lines = []
        if (vanish.length) {
          lines.push(`⚠️ ${vanish.length} 人只在这份名单中，删除后身份将回退：${this._names(vanish)}`)
        }
        if (down.length) lines.push(`${down.length} 人身份会降低：${this._names(down)}`)
        const content = lines.length
          ? `${lines.join('\n')}\n确认删除「${label || '这份名单'}」吗？`
          : `确认删除「${label || '这份名单'}」吗？名单内号码的身份会重新按剩余名单计算。`
        wx.showModal({
          title: '删除名单', content, confirmColor: '#e64340',
          success: (m) => { if (m.confirm) this._doRemoveBatch(batchId) },
        })
      },
      fail: () => wx.showToast({ title: '操作失败', icon: 'none' }),
    })
  },

  _doRemoveBatch(batchId) {
    wx.request({
      url: METHODS_PHONE_WHITELIST + '/batch', method: 'DELETE', timeout: 15000,
      header: { 'Authorization': `Bearer ${this._token}`, 'content-type': 'application/json' },
      data: { batch_id: batchId },
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          wx.showToast({ title: '名单已删除', icon: 'success' })
          this._load()
        } else {
          const d = res.data || {}
          wx.showToast({ title: d.error || '删除失败', icon: 'none' })
        }
      },
      fail: () => wx.showToast({ title: '删除失败', icon: 'none' }),
    })
  },
})
