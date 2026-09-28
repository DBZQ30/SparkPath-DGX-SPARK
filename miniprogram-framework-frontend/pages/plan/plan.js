// pages/plan/plan.js —— 培养方案智能解读（004 设计，学生侧只读）
// 四区块：学分结构 / 学期地图 / 先修关系 / 毕业授学位条件
// 不对 guest 开放；数据未就绪（queued/parsing/未收录）时不渲染半成品。
const {
  PLAN_MAJORS_URL, PLAN_OVERVIEW_URL, PLAN_PREREQ_URL,
} = require('../../config/api')
const { PLAN_API_KEY, PLAN_API_BASE } = require('../../config/instance')
const { FALLBACK_GRADES, fetchGrades } = require('../../utils/grades')

const ALL_GRADES = '全部'

// 从 applies_to 展开可选年级（"全部" → 展开为注册表年级；学生按自己年级选）
function gradesOf(plan, universe) {
  const ap = plan.applies_to || []
  if (ap.indexOf(ALL_GRADES) >= 0) return (universe || FALLBACK_GRADES).slice()
  return ap.slice()
}

Page({
  data: {
    enabled: false,
    loading: true,
    state: '',              // done / parsing / unavailable
    stateText: '',
    majors: [],
    years: [],
    major: '',
    entryYear: '',
    overview: null,         // 学分结构 + 毕业条件 + top
    creditRows: [],         // 学分结构顶层级（用于占比条）
    prereq: null,
    prereqImage: '',
    courseCount: 0,
    semesters: [],
    activeSem: '',
    activeCourses: [],
    showPrereqImage: false,
  },

  onLoad(options) {
    const enabled = !!PLAN_MAJORS_URL
    this.setData({ enabled })
    if (!enabled) {
      this.setData({ loading: false, stateText: '培养方案解读功能未启用' })
      return
    }
    // 支持从聊天跳转带参：?major=工商管理&entry_year=2023
    const presetMajor = options && options.major ? decodeURIComponent(options.major) : ''
    const presetYear = options && options.entry_year ? decodeURIComponent(options.entry_year) : ''
    this._preset = { major: presetMajor, year: presetYear }
    this._grades = FALLBACK_GRADES
    fetchGrades((grades) => { this._grades = grades; this.loadMajors() })
  },

  onPullDownRefresh() {
    this.loadMajors(() => wx.stopPullDownRefresh())
  },

  // ── 可选（专业, 年级）清单 ──
  loadMajors(cb) {
    wx.request({
      url: PLAN_MAJORS_URL,
      header: { 'X-API-Key': PLAN_API_KEY },
      success: (res) => {
        const body = (res.statusCode === 200 && res.data) || {}
        const majors = body.majors || []
        const plans = body.plans || []
        // 每个专业下可用年级（按方案的 applies_to 展开；"全部"= 所有年级通用）
        const yearsOf = {}
        plans.forEach((p) => {
          if (p.status !== 'done') return
          const list = yearsOf[p.major] = yearsOf[p.major] || []
          gradesOf(p, this._grades).forEach((g) => { if (list.indexOf(g) < 0) list.push(g) })
        })
        const major = this._preset.major || this._firstWithData(yearsOf, majors)
        const years = yearsOf[major] || []
        const entryYear = this._preset.year && years.includes(this._preset.year)
          ? this._preset.year : (years[0] || '')
        this.setData({ majors, years, major, entryYear, _yearsOf: yearsOf, _plans: plans })
        if (major && entryYear) this.loadOverview()
        else {
          this.setData({ loading: false, state: 'unavailable',
                         stateText: '培养方案尚未收录，请联系教务上传' })
        }
        if (cb) cb()
      },
      fail: () => {
        this.setData({ loading: false, state: 'unavailable', stateText: '网络异常，请稍后重试' })
        if (cb) cb()
      },
    })
  },

  _firstWithData(yearsOf, majors) {
    for (const m of majors) if ((yearsOf[m] || []).length) return m
    return majors[0] || ''
  },

  onMajorChange(e) {
    const major = this.data.majors[Number(e.detail.value)]
    const years = (this.data._yearsOf || {})[major] || []
    this.setData({ major, years, entryYear: years[0] || '', loading: true,
                   overview: null, prereq: null, semesters: [], activeCourses: [] })
    if (years.length) this.loadOverview()
    else this.setData({ loading: false, state: 'unavailable',
                        stateText: '该专业培养方案尚未收录' })
  },

  onYearChange(e) {
    const entryYear = this.data.years[Number(e.detail.value)]
    this.setData({ entryYear, loading: true, overview: null, prereq: null,
                   semesters: [], activeCourses: [] })
    this.loadOverview()
  },

  // ── 解读数据 ──
  loadOverview() {
    const { major, entryYear } = this.data
    if (!major || !entryYear) return
    wx.request({
      url: `${PLAN_OVERVIEW_URL}?major=${encodeURIComponent(major)}&entry_year=${encodeURIComponent(entryYear)}`,
      header: { 'X-API-Key': PLAN_API_KEY },
      success: (res) => {
        const d = (res.statusCode === 200 && res.data) || {}
        if (d.state !== 'done') {
          this.setData({ loading: false, state: d.state || 'unavailable',
                         stateText: d.message || '培养方案暂不可用' })
          return
        }
        const creditRows = this._creditRows(d.credit_tree, d.top, d.course_type_summary)
        const semesters = (d.semester_map || []).map((s) => ({
          sem: s.semester, credit: s.credit, count: (s.courses || []).length, courses: s.courses,
        }))
        const active = semesters[0] || null
        this.setData({
          loading: false, state: 'done',
          overview: d,
          creditRows,
          top: d.top || {},
          courseCount: d.course_count || 0,
          semesters,
          activeSem: active ? active.sem : '',
          activeCourses: active ? active.courses : [],
        })
        this._syncReqChecked()
        this.loadPrereq()
      },
      fail: () => this.setData({ loading: false, state: 'unavailable', stateText: '网络异常' }),
    })
  },

  // 学分结构：顶层级用于占比条；子项用于明细
  _creditRows(tree, top, typeSummary) {
    const ct = (top && top.course_teaching) || 0
    const pr = (top && top.practice) || 0
    const ep = (top && top.extra_practice) || 0
    const sum = ct + pr + ep || 1
    const rows = []
    const push = (name, credit) => rows.push({
      name, credit,
      pct: Math.round((Number(credit || 0) / sum) * 100),
    })
    if (ct) push('课程教学', ct)
    if (pr) push('集中实践', pr)
    if (ep) push('课外实践', ep)
    // 课程教学子项：优先按课程类型聚合（Table 0 二级小计为合并值时不可靠）
    return { rows, children: typeSummary || [] }
  },

  loadPrereq() {
    const { major, entryYear } = this.data
    wx.request({
      url: `${PLAN_PREREQ_URL}?major=${encodeURIComponent(major)}&entry_year=${encodeURIComponent(entryYear)}`,
      header: { 'X-API-Key': PLAN_API_KEY },
      success: (res) => {
        if (res.statusCode !== 200) return
        const d = res.data || {}
        this.setData({
          prereq: d,
          // 图片 URL 用后端下发的短期签名（<image> 无法带 X-API-Key header）
          prereqImage: d.image_path ? `${PLAN_API_BASE}${d.image_path}` : '',
        })
      },
    })
  },

  onSemTap(e) {
    const sem = e.currentTarget.dataset.sem
    const item = this.data.semesters.find((s) => s.sem === sem)
    if (item) this.setData({ activeSem: sem, activeCourses: item.courses })
  },

  togglePrereqImage() {
    this.setData({ showPrereqImage: !this.data.showPrereqImage })
  },

  // 毕业条件本地勾选（自查用，仅本地状态）
  onReqCheck(e) {
    const idx = Number(e.currentTarget.dataset.idx)
    const reqs = (this.data.overview && this.data.overview.graduation_requirements) || []
    const key = `checked_${this.data.major}_${this.data.entryYear}_${idx}`
    const next = !wx.getStorageSync(key)
    wx.setStorageSync(key, next)
    const checked = reqs.map((_, i) => !!wx.getStorageSync(
      `checked_${this.data.major}_${this.data.entryYear}_${i}`))
    this.setData({ reqChecked: checked })
  },

  _syncReqChecked() {
    const reqs = (this.data.overview && this.data.overview.graduation_requirements) || []
    const checked = reqs.map((_, i) => !!wx.getStorageSync(
      `checked_${this.data.major}_${this.data.entryYear}_${i}`))
    this.setData({ reqChecked: checked })
  },
})
