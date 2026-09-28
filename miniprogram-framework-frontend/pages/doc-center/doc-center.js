// pages/doc-center/doc-center.js —— 文件中心（007 设计，管理员）
// 公共教学文件统一上传；勾选 适用功能 × 年级 × 专业；查看解析状态；重解析/删除。
const {
  PLAN_DOC_CENTER_FILES_URL, PLAN_DOC_CENTER_UPLOAD_URL, PLAN_DOC_CENTER_STATUS_URL,
  PLAN_DOC_CENTER_FILE_URL, PLAN_DOC_CENTER_RETRY_URL,
} = require('../../config/api')
const { PLAN_API_KEY } = require('../../config/instance')
const { toggleScope } = require('../../utils/scope-select')
const { FALLBACK_GRADES, fetchGrades } = require('../../utils/grades')

const FEATURES = [
  { key: 'interpret', label: '智能解读' },
  { key: 'plan', label: '学业规划' },
  { key: 'warning', label: '选课预警' },
]
const GRADES = ['全部'].concat(FALLBACK_GRADES)
const MAJORS = ['', '工商管理', '工业工程', '会计学（ACCA）', '大数据管理与应用']
const DOC_TYPES = ['培养方案', '政策', '大纲', '清单', '教学计划', '通识表',
  '转专业政策', '转专业考核安排', '专业选择方案', '操作指引', '其他']
const JOB_LABEL = { plan_structured: '解读/规划', warning_plan: '预警方案', warning_gen_ed: '预警通识', text: '文本' }
const JOB_STATUS = { queued: '排队', parsing: '解析中', done: '完成', failed: '失败' }

Page({
  data: {
    enabled: false,
    loading: true,
    files: [],
    statText: '',
    grades: FALLBACK_GRADES,   // 年级注册表（sheet 选项来源）
    // 筛选
    filterType: '',
    filterTypeIdx: 0,
    docTypes: ['全部类型'].concat(DOC_TYPES),
    filterFeature: '',
    filterFeatureIdx: 0,
    filterFeatureLabel: '',
    featureFilterOptions: ['全部功能'].concat(FEATURES.map((f) => f.label)),
    // 上传（range 用 DOC_TYPES，不含「全部类型」前缀，索引直接对应）
    uploadTypes: DOC_TYPES,
    uploadType: '培养方案',
    uploadTypeIdx: 0,
    // 适用性弹层
    showSheet: false,
    sheetFile: null,
    sheetFeatures: [],
    sheetGrades: [],
    sheetMajors: [],
    featureOptions: FEATURES.map((f) => ({ key: f.key, label: f.label, on: false })),
    gradeOptions: GRADES.map((g) => ({ label: g, on: false })),
    majorOptions: MAJORS.map((m) => ({ raw: m, label: m || '不限', on: false })),
  },

  onLoad() {
    const enabled = !!PLAN_DOC_CENTER_FILES_URL
    this.setData({ enabled })
    if (!enabled) {
      this.setData({ loading: false, statText: '文件中心未启用' })
      return
    }
    fetchGrades((grades) => {
      this.setData({ grades, gradeOptions: ['全部'].concat(grades).map((g) => ({ label: g, on: false })) })
      this.loadFiles()
    })
  },

  onPullDownRefresh() {
    this.loadFiles(() => wx.stopPullDownRefresh())
  },

  loadFiles(cb) {
    const { filterType, filterFeature } = this.data
    const params = []
    if (filterType) params.push(`doc_type=${encodeURIComponent(filterType)}`)
    if (filterFeature) params.push(`feature=${encodeURIComponent(filterFeature)}`)
    const url = PLAN_DOC_CENTER_FILES_URL + (params.length ? `?${params.join('&')}` : '')
    wx.request({
      url, header: { 'X-API-Key': PLAN_API_KEY },
      success: (res) => {
        const body = (res.statusCode === 200 && res.data) || {}
        const files = (body.files || []).map((f) => this._decorate(f))
        this.setData({ files, loading: false })
        this.loadStatus()
        if (cb) cb()
      },
      fail: () => { this.setData({ loading: false, statText: '网络异常' }); if (cb) cb() },
    })
  },

  _decorate(f) {
    const appl = f.applicability || []
    const feats = Array.from(new Set(appl.map((a) => a.feature)))
    const grades = Array.from(new Set(appl.map((a) => a.grade)))
    const jobs = (f.jobs || []).map((j) => `${JOB_LABEL[j.pipeline] || j.pipeline}:${JOB_STATUS[j.status] || j.status}`)
    return Object.assign({}, f, {
      featureText: feats.map((x) => (FEATURES.find((y) => y.key === x) || {}).label || x).join('/'),
      gradeText: grades.join('、'),
      jobText: jobs.join('  '),
    })
  },

  loadStatus() {
    wx.request({
      url: PLAN_DOC_CENTER_STATUS_URL, header: { 'X-API-Key': PLAN_API_KEY },
      success: (res) => {
        const s = ((res.data || {}).summary) || {}
        this.setData({ statText: `解析任务：总 ${s.total || 0} · 完成 ${s.done || 0} · 进行 ${s.parsing || 0} · 排队 ${s.queued || 0} · 失败 ${s.failed || 0}` })
      },
    })
  },

  onFilterType(e) {
    const idx = Number(e.detail.value) || 0
    const v = idx > 0 ? (DOC_TYPES[idx - 1] || '') : ''
    this.setData({ filterType: v, filterTypeIdx: idx }); this.loadFiles()
  },
  onFilterFeature(e) {
    const idx = Number(e.detail.value) || 0
    const feat = idx > 0 ? FEATURES[idx - 1] : null
    this.setData({
      filterFeature: feat ? feat.key : '',
      filterFeatureIdx: idx,
      filterFeatureLabel: feat ? feat.label : '',
    })
    this.loadFiles()
  },
  // 上传类型：range = DOC_TYPES（无「全部类型」前缀），索引直接对应，不做 -1 偏移
  onUploadType(e) {
    const idx = Number(e.detail.value) || 0
    this.setData({ uploadType: DOC_TYPES[idx] || DOC_TYPES[0], uploadTypeIdx: idx })
  },

  // ── 上传 ──
  onUpload() {
    wx.chooseMessageFile({
      count: 1,
      type: 'file',
      success: (r) => {
        const f = r.tempFiles[0]
        wx.showLoading({ title: '上传中' })
        wx.uploadFile({
          url: PLAN_DOC_CENTER_UPLOAD_URL,
          filePath: f.path,
          name: 'file',
          header: { 'X-API-Key': PLAN_API_KEY },
          formData: { doc_type: this.data.uploadType, uploaded_by: 'miniapp-admin',
                      orig_name: f.name || '' },
          success: (res) => {
            wx.hideLoading()
            let body = {}
            try { body = JSON.parse(res.data) } catch (e) { body = {} }
            if (body.status === 'ok') {
              wx.showToast({ title: body.created ? '已上传' : '已存在（去重）', icon: 'success' })
              this.loadFiles()
            } else {
              wx.showToast({ title: body.message || '上传失败', icon: 'none' })
            }
          },
          fail: () => { wx.hideLoading(); wx.showToast({ title: '上传失败', icon: 'none' }) },
        })
      },
    })
  },

  // ── 适用性 ──
  onEditApplicability(e) {
    const id = e.currentTarget.dataset.id
    const f = this.data.files.find((x) => x.id === id)
    if (!f) return
    const appl = f.applicability || []
    const feats = new Set(appl.map((a) => a.feature))
    const grades = new Set(appl.map((a) => a.grade))
    const majors = new Set(appl.map((a) => a.major))
    this.setData({
      showSheet: true, sheetFile: f,
      featureOptions: FEATURES.map((x) => ({ key: x.key, label: x.label, on: feats.has(x.key) })),
      gradeOptions: ['全部'].concat(this.data.grades || FALLBACK_GRADES)
        .map((g) => ({ label: g, on: grades.has(g) })),
      majorOptions: MAJORS.map((m) => ({ raw: m, label: m || '不限', on: majors.has(m) })),
    })
  },
  onSheetClose() { this.setData({ showSheet: false }) },
  noop() {},
  _toggleIdx(field, idx) {
    const list = this.data[field].slice()
    list[idx] = Object.assign({}, list[idx], { on: !list[idx].on })
    this.setData({ [field]: list })
  },
  onToggleFeature(e) { this._toggleIdx('featureOptions', e.currentTarget.dataset.idx) },
  // 年级：「全部」与具体年级互斥；具体年级全选 → 自动收敛为「全部」
  onToggleGrade(e) {
    const idx = Number(e.currentTarget.dataset.idx)
    const opts = this.data.gradeOptions
    const toggled = opts[idx] && opts[idx].label
    if (toggled == null) return
    const universe = opts.map((x) => x.label).filter((g) => g !== '全部')
    const current = opts.filter((x) => x.on).map((x) => x.label)
    const next = new Set(toggleScope(current, toggled, universe, '全部'))
    this.setData({ gradeOptions: opts.map((x) => Object.assign({}, x, { on: next.has(x.label) })) })
  },
  // 专业：「不限」与具体专业互斥（不把「全部已知专业」收敛为「不限」，语义更广）
  onToggleMajor(e) {
    const idx = Number(e.currentTarget.dataset.idx)
    const opts = this.data.majorOptions
    const toggled = opts[idx] && opts[idx].raw
    if (toggled == null) return
    const universe = opts.map((x) => x.raw).filter((m) => m !== '')
    const current = opts.filter((x) => x.on).map((x) => x.raw)
    const next = new Set(toggleScope(current, toggled, universe, '', { autoAll: false }))
    this.setData({ majorOptions: opts.map((x) => Object.assign({}, x, { on: next.has(x.raw) })) })
  },
  onSaveApplicability() {
    const f = this.data.sheetFile
    if (!f) return
    const features = this.data.featureOptions.filter((x) => x.on).map((x) => x.key)
    const grades = this.data.gradeOptions.filter((x) => x.on).map((x) => x.label)
    const majors = this.data.majorOptions.filter((x) => x.on).map((x) => x.raw)
    wx.request({
      url: `${PLAN_DOC_CENTER_FILE_URL}/${f.id}/applicability`,
      method: 'PUT',
      header: { 'X-API-Key': PLAN_API_KEY, 'content-type': 'application/json' },
      data: {
        features,
        grades: grades.length ? grades : ['全部'],
        majors: majors.length ? majors : [''],
      },
      success: () => { wx.showToast({ title: '已保存' }); this.setData({ showSheet: false }); this.loadFiles() },
      fail: () => wx.showToast({ title: '保存失败', icon: 'none' }),
    })
  },

  // ── 重解析 / 删除 ──
  onRetry(e) {
    const id = e.currentTarget.dataset.id
    wx.request({
      url: PLAN_DOC_CENTER_RETRY_URL, method: 'POST',
      header: { 'X-API-Key': PLAN_API_KEY, 'content-type': 'application/json' },
      data: { file_id: id },
      success: () => { wx.showToast({ title: '已重新入队' }); this.loadFiles() },
      fail: () => wx.showToast({ title: '重试失败', icon: 'none' }),
    })
  },
  onDelete(e) {
    const id = e.currentTarget.dataset.id
    wx.showModal({
      title: '删除文件', content: '将同时级联删除各功能库中的解析产物，确认？',
      success: (r) => {
        if (!r.confirm) return
        wx.request({
          url: `${PLAN_DOC_CENTER_FILE_URL}/${id}`, method: 'DELETE',
          header: { 'X-API-Key': PLAN_API_KEY },
          success: () => { wx.showToast({ title: '已删除' }); this.loadFiles() },
          fail: () => wx.showToast({ title: '删除失败', icon: 'none' }),
        })
      },
    })
  },
})
