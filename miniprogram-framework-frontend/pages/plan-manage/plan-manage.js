// pages/plan-manage/plan-manage.js —— 培养方案管理（004 设计，仅 admin/owner）
// 四区块：上传 / 批量解析进度（含排队位次）/ 先修关系校对（原图对照）/ 状态
const {
  PLAN_STATUS_URL, PLAN_UPLOAD_URL, PLAN_RETRY_URL, PLAN_PREREQ_URL,
  PLAN_PREREQ_VERIFY_URL, PLAN_MAJORS_URL,
} = require('../../config/api')
const { PLAN_API_KEY, PLAN_API_BASE } = require('../../config/instance')
const { FALLBACK_GRADES, fetchGrades } = require('../../utils/grades')

const MAJORS = ['工商管理', '工业工程', '会计学（ACCA）', '大数据管理与应用']
const POLL_INTERVAL = 3000
const MAX_FILE_SIZE = 10 * 1024 * 1024

Page({
  data: {
    enabled: false,
    majors: MAJORS,
    years: FALLBACK_GRADES,
    majorIndex: 0,
    yearIndex: 0,
    uploading: false,
    summary: null,
    items: [],
    plans: [],           // 已收录方案（含先修校对进度与入口）
    // 先修校对
    reviewMajor: '',
    reviewYear: '',
    edges: [],
    allChecked: false,
    reviewImg: '',
    verifying: false,
  },

  onLoad() {
    const enabled = !!PLAN_STATUS_URL && !!PLAN_MAJORS_URL
    this.setData({ enabled })
    if (!enabled) return
    fetchGrades((grades) => this.setData({ years: grades }))
    this.fetchStatus()
    this.fetchPlans()
    this._timer = setInterval(() => {
      if (!this.data.uploading) this.fetchStatus()
    }, POLL_INTERVAL)
  },

  onUnload() { if (this._timer) clearInterval(this._timer) },

  onPullDownRefresh() { this.fetchStatus(); this.fetchPlans(() => wx.stopPullDownRefresh()) },

  // ── 已收录方案（含先修校对进度 + 校对入口） ──
  fetchPlans(cb) {
    wx.request({
      url: PLAN_MAJORS_URL,
      header: { 'X-API-Key': PLAN_API_KEY },
      success: (res) => {
        if (res.statusCode !== 200 || !res.data) return cb && cb()
        const plans = (res.data.plans || []).map((p) => {
          const total = p.prereq_total || 0
          const done = p.prereq_verified || 0
          const stateText = { done: '已收录', queued: '排队中', parsing: '解析中',
                              failed: '解析失败', rejected: '已拒绝' }[p.status] || p.status
          return Object.assign({}, p, {
            stateText,
            prereqText: total ? `先修校对 ${done}/${total}` : '无可校对先修关系',
            needReview: total > done,
          })
        })
        this.setData({ plans })
        if (cb) cb()
      },
      fail: () => cb && cb(),
    })
  },

  // ── 队列进度 ──
  fetchStatus(cb) {
    wx.request({
      url: PLAN_STATUS_URL,
      header: { 'X-API-Key': PLAN_API_KEY },
      success: (res) => {
        if (res.statusCode !== 200 || !res.data) return cb && cb()
        const items = (res.data.items || []).map((it) => {
          let desc = ''
          if (it.state === 'queued') desc = `排队中（第 ${it.position} 位，前方 ${it.ahead} 份）`
          else if (it.state === 'parsing') desc = `解析中（已 ${it.elapsed_s || 0}s）`
          else if (it.state === 'done') desc = it.note || '完成'
          else if (it.state === 'failed') desc = it.error || '解析失败'
          else desc = it.error || it.state
          const icon = { queued: '⏳', parsing: '🔄', done: '✅', failed: '❌' }[it.state] || '⚠️'
          return Object.assign({}, it, { desc, icon })
        })
        this.setData({ summary: res.data.summary, items })
        if (cb) cb()
      },
      fail: () => cb && cb(),
    })
  },

  onMajorChange(e) { this.setData({ majorIndex: Number(e.detail.value) }) },
  onYearChange(e) { this.setData({ yearIndex: Number(e.detail.value) }) },

  // ── 上传（一次一份；多份可连续选） ──
  tapUpload() {
    if (this.data.uploading) return
    const major = this.data.majors[this.data.majorIndex]
    const entryYear = this.data.years[this.data.yearIndex]
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      extension: ['docx'],
      success: (res) => {
        const f = res.tempFiles[0]
        if (!f) return
        if (f.size > MAX_FILE_SIZE) {
          wx.showToast({ title: '文件超过 10MB 上限', icon: 'none' })
          return
        }
        this._doUpload(f, major, entryYear)
      },
    })
  },

  _doUpload(file, major, entryYear) {
    this.setData({ uploading: true })
    wx.uploadFile({
      url: PLAN_UPLOAD_URL,
      filePath: file.path,
      name: 'file',
      header: { 'X-API-Key': PLAN_API_KEY },
      formData: { major, entry_year: entryYear, orig_name: file.name || '' },
      timeout: 120000,
      success: (r) => {
        this.setData({ uploading: false })
        let body = {}
        try { body = JSON.parse(r.data || '{}') } catch (e) { body = {} }
        if (r.statusCode === 200 && body.status !== 'rejected') {
          wx.showToast({ title: body.message || '已入队', icon: 'none' })
          this.fetchStatus()
          this.fetchPlans()
        } else {
          wx.showToast({ title: body.message || `上传失败（HTTP ${r.statusCode}）`, icon: 'none' })
        }
      },
      fail: () => {
        this.setData({ uploading: false })
        wx.showToast({ title: '上传失败，请检查网络', icon: 'none' })
      },
    })
  },

  tapRetry(e) {
    const id = e.currentTarget.dataset.id
    wx.request({
      url: PLAN_RETRY_URL,
      method: 'POST',
      header: { 'X-API-Key': PLAN_API_KEY, 'Content-Type': 'application/json' },
      data: { doc_id: id },
      success: (r) => {
        wx.showToast({ title: (r.data && r.data.message) || '已重试', icon: 'none' })
        this.fetchStatus()
      },
    })
  },

  // ── 先修关系校对 ──
  tapReview(e) {
    const { major, year } = e.currentTarget.dataset
    this.setData({ reviewMajor: major, reviewYear: year, edges: [], reviewImg: '' })
    const q = `?major=${encodeURIComponent(major)}&entry_year=${encodeURIComponent(year)}&include_unverified=1`
    wx.request({
      url: PLAN_PREREQ_URL + q,
      header: { 'X-API-Key': PLAN_API_KEY },
      success: (res) => {
        if (res.statusCode !== 200 || !res.data) return
        const edges = (res.data.edges || []).map((x) => ({
          from: x.from_course_name, to: x.to_course_name, verified: x.verified,
        }))
        this.setData({
          edges,
          allChecked: edges.length > 0 && edges.every((x) => x.verified),
          // 图片 URL 用后端下发的短期签名（<image> 无法带 X-API-Key header）
          reviewImg: res.data.image_path ? `${PLAN_API_BASE}${res.data.image_path}` : '',
        })
      },
    })
  },

  closeReview() { this.setData({ reviewMajor: '', edges: [], reviewImg: '' }) },

  // 全选 / 取消全选（只作用于未校对项，已校对项本就为勾选态）
  toggleSelectAll() {
    const edges = this.data.edges.slice()
    const allChecked = edges.every((x) => x._checked || x.verified)
    edges.forEach((x) => { x._checked = !allChecked })
    this.setData({ edges, allChecked: !allChecked })
  },

  onEdgeCheck(e) {
    const idx = Number(e.currentTarget.dataset.idx)
    const edges = this.data.edges.slice()
    edges[idx]._checked = !edges[idx]._checked
    const allChecked = edges.length > 0 && edges.every((x) => x._checked || x.verified)
    this.setData({ edges, allChecked })
  },

  submitVerify() {
    const picked = this.data.edges.filter((x) => x._checked)
    if (!picked.length) {
      wx.showToast({ title: '请先勾选已核对的先修关系', icon: 'none' })
      return
    }
    this.setData({ verifying: true })
    wx.request({
      url: PLAN_PREREQ_VERIFY_URL,
      method: 'POST',
      header: { 'X-API-Key': PLAN_API_KEY, 'Content-Type': 'application/json' },
      data: {
        major: this.data.reviewMajor,
        entry_year: this.data.reviewYear,
        edges: picked.map((x) => ({ action: 'confirm', from: x.from, to: x.to })),
      },
      success: (r) => {
        this.setData({ verifying: false })
        wx.showToast({ title: (r.data && `已确认 ${r.data.applied} 条`) || '已提交', icon: 'none' })
        this.tapReview({ currentTarget: { dataset: { major: this.data.reviewMajor, year: this.data.reviewYear } } })
        this.fetchStatus()
        this.fetchPlans()
      },
      fail: () => {
        this.setData({ verifying: false })
        wx.showToast({ title: '提交失败', icon: 'none' })
      },
    })
  },
})
