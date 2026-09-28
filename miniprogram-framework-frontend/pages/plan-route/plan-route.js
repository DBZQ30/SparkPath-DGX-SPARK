// pages/plan-route/plan-route.js —— 多路径个性化学业规划（010 设计，学生侧只读）
// 场景：路线图（四方向四年）/ 专业选择（分流）/ 转专业模拟
// 不对 guest 开放；数据未就绪（未收录/解析中）时不渲染半成品。
const {
  PLAN_MAJORS_URL, PLAN_ROUTE_URL, PLAN_ROUTE_COMPARE_URL,
  PLAN_SELECT_SIMULATE_URL, PLAN_SIMULATE_TRANSFER_URL,
} = require('../../config/api')
const { PLAN_API_KEY } = require('../../config/instance')
const { FALLBACK_GRADES, fetchGrades } = require('../../utils/grades')

const ALL_GRADES = '全部'
const MODES = ['常规型', '科学研究型', '交叉融合型', '创新创业型']
// 负荷档 → WXSS 类名（WXSS 不支持中文类名）
const LOAD_CLASS = { '轻松': 'light', '适中': 'medium', '偏紧': 'tight', '超限': 'over' }

function gradesOf(plan, universe) {
  const ap = plan.applies_to || []
  if (ap.indexOf(ALL_GRADES) >= 0) return (universe || FALLBACK_GRADES).slice()
  return ap.slice()
}

Page({
  data: {
    enabled: false,
    loading: true,
    stateText: '',
    majors: [],
    years: [],
    major: '',
    entryYear: '',
    scene: 'route',            // route / select / transfer
    modes: MODES,
    mode: '常规型',
    route: null,
    compare: null,
    select: null,
    transfer: null,
    fromMajor: '',
    toMajor: '',
    errorText: '',
  },

  onLoad(options) {
    const enabled = !!PLAN_ROUTE_URL
    this.setData({ enabled })
    if (!enabled) {
      this.setData({ loading: false, stateText: '学业规划功能未启用' })
      return
    }
    this._preset = {
      major: options && options.major ? decodeURIComponent(options.major) : '',
      year: options && options.entry_year ? decodeURIComponent(options.entry_year) : '',
      mode: options && options.mode ? decodeURIComponent(options.mode) : '',
      toMajor: options && options.to_major ? decodeURIComponent(options.to_major) : '',
    }
    this._grades = FALLBACK_GRADES
    fetchGrades((grades) => { this._grades = grades; this.loadMajors() })
  },

  onPullDownRefresh() {
    this.loadMajors(() => wx.stopPullDownRefresh())
  },

  loadMajors(cb) {
    wx.request({
      url: PLAN_MAJORS_URL,
      header: { 'X-API-Key': PLAN_API_KEY },
      success: (res) => {
        const body = (res.statusCode === 200 && res.data) || {}
        const majors = body.majors || []
        const plans = body.plans || []
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
        const mode = MODES.includes(this._preset.mode) ? this._preset.mode : '常规型'
        this.setData({
          majors, years, major, entryYear, mode,
          fromMajor: major, toMajor: this._preset.toMajor || '',
          _yearsOf: yearsOf, _plans: plans, loading: false,
        })
        if (major && entryYear) this.refresh()
        else this.setData({ stateText: '培养方案尚未收录，请联系教务上传' })
        if (cb) cb()
      },
      fail: () => {
        this.setData({ loading: false, stateText: '网络异常，请稍后重试' })
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
    this.setData({ major, years, entryYear: years[0] || '', fromMajor: major,
                   route: null, compare: null, select: null, transfer: null })
    if (years.length) this.refresh()
  },

  onYearChange(e) {
    this.setData({ entryYear: this.data.years[Number(e.detail.value)],
                   route: null, select: null, transfer: null })
    this.refresh()
  },

  onSceneTap(e) {
    const scene = e.currentTarget.dataset.scene
    this.setData({ scene })
    this.refresh()
  },

  onModeTap(e) {
    this.setData({ mode: e.currentTarget.dataset.mode, route: null })
    this.loadRoute()
  },

  onToMajorChange(e) {
    this.setData({ toMajor: this.data.majors[Number(e.detail.value)], transfer: null })
    this.loadTransfer()
  },

  onFromMajorChange(e) {
    this.setData({ fromMajor: this.data.majors[Number(e.detail.value)], transfer: null })
    this.loadTransfer()
  },

  refresh() {
    const { scene } = this.data
    if (scene === 'route') this.loadRoute()
    else if (scene === 'select') this.loadSelect()
    else this.loadTransfer()
  },

  _get(url, cb) {
    wx.request({
      url, header: { 'X-API-Key': PLAN_API_KEY },
      success: (res) => {
        const body = (res.statusCode === 200 && res.data) || {}
        if (body.state && body.state !== 'done') {
          this.setData({ errorText: body.message || '数据未就绪', loading: false })
          if (cb) cb(null)
          return
        }
        this.setData({ errorText: '' })
        if (cb) cb(body)
      },
      fail: () => {
        this.setData({ errorText: '网络异常，请稍后重试', loading: false })
        if (cb) cb(null)
      },
    })
  },

  loadRoute() {
    const { major, entryYear, mode } = this.data
    if (!major || !entryYear) return
    this.setData({ loading: true })
    this._get(`${PLAN_ROUTE_URL}?major=${encodeURIComponent(major)}&entry_year=${encodeURIComponent(entryYear)}&mode=${encodeURIComponent(mode)}`,
      (body) => {
        if (body && body.mode_overlay) {
          const ov = body.mode_overlay
          ov.scopesText = (ov.scopes || []).join('、')
          ov.coursesText = (ov.courses || []).map(
            (c) => `${c.course_name}(${c.credit}${c.semester ? '·' + c.semester : ''})`).join('、')
        }
        if (body && body.semesters) {
          body.semesters.forEach((s) => { s.loadClass = LOAD_CLASS[s.load_level] || 'medium' })
        }
        this.setData({ route: body, loading: false })
      })
  },

  loadCompare() {
    const { major, entryYear } = this.data
    if (!major || !entryYear) return
    this._get(`${PLAN_ROUTE_COMPARE_URL}?major=${encodeURIComponent(major)}&entry_year=${encodeURIComponent(entryYear)}`,
      (body) => this.setData({ compare: body }))
  },

  loadSelect() {
    // 专业选择方案的年级由其发布年级决定（当前 2025级），不沿用路线图的年级
    this.setData({ loading: true })
    this._get(`${PLAN_SELECT_SIMULATE_URL}`,
      (body) => this.setData({ select: body, loading: false }))
  },

  loadTransfer() {
    const { fromMajor, toMajor, entryYear } = this.data
    if (!toMajor) return
    this.setData({ loading: true })
    this._get(`${PLAN_SIMULATE_TRANSFER_URL}?from_major=${encodeURIComponent(fromMajor)}&to_major=${encodeURIComponent(toMajor)}&entry_year=${encodeURIComponent(entryYear)}`,
      (body) => this.setData({ transfer: body, loading: false }))
  },
})
