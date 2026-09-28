// pages/warning-detail/warning-detail.js —— 学生预警详情页（2026-09-06）
// 入口：学业预警名单行"详情" wx.navigateTo（栈：[warning, warning-detail]）——
// 原生导航栏返回即回到学业预警页，不会落到首界面。
// 职责：单生全量预警信息——触发概况（全部门类 chips）+ 缺修课程逐门（免修/平替；
// reason=failed 旁注"挂科未过"）+ 未及格科目逐条（D4：代表行不及格，命中方案必修
// 标"已报缺修"）+ 类别差额逐类（含认可标注）+ 已修课程两分组（匹配折叠 / 未匹配
// 候选可认可；行内 ok=false 标"未及格"、usable=false 按钮禁用）+ 该生豁免记录（可撤销）。
// 行数据经 eventChannel 传入（无渠道时兜底拉列表）。
const {
  WARNING_CHECK_URL, WARNING_STUDENT_URL, WARNING_WAIVER_URL,
} = require('../../config/api')
const { WARNING_API_KEY } = require('../../config/instance')

// v7：学分认可（kind=credit）计入类别白名单（与后端 7 类一致；pick range 数据源）
const CREDIT_CATEGORIES = ['公共课程', '模块课程', '学科门类基础课程',
                           '专业大类基础课程', '专业核心课程', '专业选修课程',
                           '集中实践']

function fmtN(n) {
  const v = Number(n)
  if (!isFinite(v)) return ''
  return Number.isInteger(v) ? String(v) : String(Math.round(v * 10) / 10)
}

Page({
  data: {
    grade: '',
    sid: '',
    loading: true,
    emptyTip: '',
    stu: null,          // student 端点 student 块（name/student_id/major/class_name）
    rowMsg: '',         // 兜底：最新批 message（details 为空的老批次）
    chips: [],          // 触发概况：全部预警门类 [{text, cls}]（非仅最差一类）
    failed: [],         // 未及格科目（D4）[{name,credit,meta,reported_missing}]
    selections: [],     // 本学期选中课程 [{name,code,meta}]（student 端点）
    // 2026-09-22：类别差额逐条，含该类缺修课下钻 [{cat,expected,gained,selected,
    // gap,extra,extraStr,missing[],gapEx}]——缺修课按类别归拢（不再有独立筐）
    cats: [],
    otherMissing: [],   // 所在类别学分已达标但仍缺修/挂科的必修课 [同 missing 结构]
    reasonMatched: [],  // 已修匹配课分组 [{cat,open,items}]
    reasonUnmatched: [],// 已修未匹配课候选（服务端已剔除被豁免消耗的）
    matchedCount: 0,
    coursesOpen: false,
    waivers: [],        // 该生豁免记录（撤销入口）
    creditCats: CREDIT_CATEGORIES,
    pickState: null,    // {op:'replace',code,name} | {op:'credit',course}
    pickCatIdx: CREDIT_CATEGORIES.indexOf('专业选修课程'),
    pickNote: '',
    kbdH: 0,            // 键盘高度 px（认可弹层抬升防遮挡）
    exemptState: null,  // 免修确认面板 {kind, code, name, tip, placeholder}
    exemptNote: '',     // 免修确认备注
  },

  onLoad(options) {
    // 微信不自动解码 navigateTo query：options 里可能是 URL 编码串（2023%E7%BA%A7）。
    // decodeURIComponent 幂等（无 % 时原样返回），编解码两种状态都安全——双编码曾致
    // student 端点查空（2026-09-06 修复）
    const rawGrade = options.grade || ''
    const grade = (() => { try { return decodeURIComponent(rawGrade) } catch (e) { return rawGrade } })()
    const sid = options.sid || ''
    this.setData({ grade, sid })
    // 名单行数据经 eventChannel（无渠道 = 冷启动直开，兜底拉列表行）
    const ch = this.getOpenerEventChannel && this.getOpenerEventChannel()
    if (ch && ch.on) {
      ch.on('studentRow', (row) => {
        this._row = row || null
        this._loadAll()
        this._maybeRender()   // 学生端点先返回、行后到时：补齐行兜底信息
      })
    }
    // 渠道监听为异步回调：先按无渠道路径走一次，_row 到后由 _loadAll 幂等覆盖
    this._loadAll()
  },

  // 双数据源加载（幂等）：student 端点（权威详情）+ 兜底行（列表/事件渠道）
  _loadAll() {
    if (!this._fetching) {
      this._fetching = true
      this._loadStudent()
      this._loadRowFallback()
    }
  },

  // 兜底行：渠道无数据时才拉 /selection-check 列表找该生（老批次 message 展示用）
  _loadRowFallback() {
    if (this._row) { this._maybeRender(); return }
    if (!WARNING_CHECK_URL || !this.data.grade) { this._maybeRender(); return }
    wx.request({
      url: WARNING_CHECK_URL + `?grade=${encodeURIComponent(this.data.grade)}`,
      header: { 'X-API-Key': WARNING_API_KEY },
      success: (res) => {
        if (res.statusCode === 200 && res.data) {
          const s = (res.data.students || []).find(
            (x) => x.student_id === this.data.sid)
          if (s) this._row = { ...s, details: s.details || {} }
        }
        this._maybeRender()
      },
      fail: () => this._maybeRender(),
    })
  },

  _loadStudent() {
    if (!WARNING_STUDENT_URL || !this.data.grade || !this.data.sid) {
      this._maybeRender()
      return
    }
    wx.request({
      url: WARNING_STUDENT_URL
        + `?grade=${encodeURIComponent(this.data.grade)}&sid=${encodeURIComponent(this.data.sid)}`,
      header: { 'X-API-Key': WARNING_API_KEY },
      success: (res) => {
        if (res.statusCode === 200 && res.data) this._applyDetail(res.data)
        else this._maybeRender()
      },
      fail: () => this._maybeRender(),
    })
  },

  _maybeRender() {
    if (!this._fetching) return
    const stu = this.data.stu || this._row
    const row = this._row
    // details 空（老批次/未触发）→ 用行 message 兜底全文（结构化 chips 无来源则不显示）
    const detEmpty = !this.data.cats.length && !this.data.otherMissing.length
    this.setData({
      loading: false,
      stu: stu ? { name: (this.data.stu && this.data.stu.name)
                   || (row && row.name) || '', student_id: this.data.sid,
                   major: (this.data.stu && this.data.stu.major)
                   || (row && row.major) || '', class_name: (this.data.stu
                   && this.data.stu.class_name) || (row && row.class_name) || '' }
               : null,
      rowMsg: detEmpty && row && row.message ? row.message : '',
      emptyTip: (!stu && !this._row && !this.data.stu)
        ? '名单中无该学生或暂无检查数据' : '',
    })
  },

  // student 端点响应 → 三段视图 + 概况 chips（全部触发门类，非仅最差一类）
  _applyDetail(d) {
    const waivers = d.waivers || []
    const waivedCodes = {}
    const creditByCat = {}
    waivers.forEach((w) => {
      if (w.kind === 'course' && w.course_code) waivedCodes[w.course_code] = true
      if (w.kind === 'credit' && w.category) {
        creditByCat[w.category] = (creditByCat[w.category] || 0) + Number(w.credit || 0)
      }
    })
    const detail = d.details || {}
    // 2026-09-22：缺修课按类别归拢。cats[].missing = 该类别下缺修/挂科课；
    // other_missing = 所在类别学分已达标但仍缺修的课（方案2 补充展示）。
    // 老批次（无 cats[].missing/other_missing）→ 回退读顶层 missing（旧契约）。
    const mkMissing = (m, i, catName) => {
      const isFailed = m.reason === 'failed'   // C1：老批次无 reason → 视为 unattempted
      const sc = isFailed && m.score ? String(m.score) : ''
      return {
        _k: m.code || `${catName || ''}-${i}-${m.name || ''}`,
        code: m.code || '', name: m.name || '', credit: fmtN(m.credit),
        // other_missing 段展示所属类别（cats[].missing 下钻时不显示，类别在卡片头）
        cat: m.cat || catName || '',
        waived: !!waivedCodes[m.code],
        failed: isFailed,
        // 2026-09-07：旁注附具体分数（数值 → "55 分"；缺考/字母等原文展示）
        failTip: isFailed
          ? `（挂科未过${sc ? (sc === '缺考' || /^[A-Za-z]/.test(sc) ? `：${sc}` : `，${sc} 分`) : ''}）`
          : '',
      }
    }
    const cats = (detail.cats || []).map((c) => {
      const extra = creditByCat[c.cat] || 0
      const cm = (c.missing || []).map((m, i) => mkMissing(m, i, c.cat))
      // gap_ex_missing：剔除已在该类 missing 里的课后，类别还差的净额
      // （差额可小于缺课学分——该类另有已修/已选；老批次缺省用 gap − missing 学分和）
      let gapEx = c.gap_ex_missing
      if (gapEx == null) {
        gapEx = Number(c.gap || 0) - cm.reduce((s, x) => s + (Number(x.credit) || 0), 0)
      }
      return {
        cat: c.cat,
        expected: fmtN(c.expected), gained: fmtN(c.gained),
        selected: fmtN(c.selected), gap: fmtN(c.gap),
        extra, extraStr: fmtN(extra),
        missing: cm, gapEx: fmtN(gapEx),
      }
    })
    // 老批次兼容：顶层 missing（旧契约）按 other_missing 兜底展示
    const otherMissing = ((detail.other_missing
      || detail.missing || []).map((m, i) => mkMissing(m, i, m.cat || '')))
    const missingCount = cats.reduce((s, c) => s + c.missing.length, 0)
      + otherMissing.length
    const chips = []
    cats.forEach((c) => chips.push({ text: `${c.cat} 差 ${c.gap}`, cls: 'chip-gap' }))
    if (missingCount) {
      chips.push({ text: `缺修 ${missingCount} 门`, cls: 'chip-alert' })
    }
    const courses = d.courses || {}
    const gmap = {}
    const groups = []
    ;(courses.matched || []).forEach((m) => {
      const key = m.category || '未分类'
      let g = gmap[key]
      if (!g) {
        g = { cat: key, open: false, items: [] }
        gmap[key] = g
        groups.push(g)
      }
      g.items.push({
        _k: `${key}-${g.items.length}`,
        name: m.course_name || '',
        fail: m.ok === false,   // F：挂科方案课保留行并标"未及格"
        meta: [m.credit != null ? `${fmtN(m.credit)} 学分` : '',
               m.term_label, m.best_raw].filter(Boolean).join(' · '),
      })
    })
    const matchedCount = (courses.matched || []).length
    const unmatched = (courses.unmatched || []).map((u, i) => ({
      _k: `${i}-${u.course_name || ''}`,
      course_name: u.course_name || '',
      usable: u.usable !== false,   // C2：挂科旧课保留候选但 usable=false（禁用）
      fail: u.ok === false,
      meta: [u.credit != null ? `${fmtN(u.credit)} 学分` : '',
             u.term_label, u.best_raw].filter(Boolean).join(' · '),
    }))
    // D4 未及格科目节：代表行不及格每课一条（含未匹配旧课 code=""）；failed 缺省 = 空节
    const failed = (courses.failed || []).map((f, i) => ({
      _k: `${i}-${f.course_name || ''}`,
      name: f.course_name || '',
      code: f.code || '',
      credit: fmtN(f.credit),
      reported_missing: !!f.reported_missing,
      waived: !!(f.code && waivedCodes[f.code]),
      meta: [f.term_label,
             f.grade_raw != null && f.grade_raw !== '' ? String(f.grade_raw) : '',
             f.marker].filter(Boolean).join(' · '),
    }))
    // 本学期选课（2026-09-07 #1）：student 端点 selections（status=选中）
    const selections = (d.selections || []).map((x, i) => ({
      _k: x.course_code || `${i}-${x.course_name || ''}`,
      name: x.course_name || '',
      code: x.course_code || '',
      meta: [x.credit != null ? `${fmtN(x.credit)} 学分` : '',
             x.nature, x.category,
             x.retake === '重修' ? '重修' : ''].filter(Boolean).join(' · '),
    }))
    const stu = d.student || null
    const wlist = waivers.map((w) => {
      const time = (w.created_at || '').slice(0, 16)
      if (w.kind === 'course') {
        const src = w.grade_source ? `平替《${w.grade_source}》` : '免修'
        return { id: w.id, label: `课程豁免《${w.course_name || w.course_code || ''}》（${src}）`,
                 sub: w.note ? `备注：${w.note}` : '' }
      }
      if (w.kind === 'grade') {
        return { id: w.id, label: `旧课豁免《${w.course_name || ''}》`,
                 sub: w.note ? `备注：${w.note}` : '' }
      }
      return { id: w.id,
               label: `学分认可《${w.course_name || ''}》→ ${w.category || ''} ${fmtN(w.credit)} 学分`,
               sub: w.note ? `备注：${w.note}` : '' }
    })
    this.setData({
      stu, cats, otherMissing, chips,
      reasonMatched: groups, reasonUnmatched: unmatched, matchedCount,
      failed, selections, waivers: wlist,
    })
    this._maybeRender()
  },

  // 未及格科目节免修（2026-09-07：全部行提供入口——code 非空=课程免修；
  // code 空=旧课豁免：方案外旧课纯记录豁免，退出未及格/候选清单，可撤销）
  waiveFailed(e) {
    const it = this.data.failed[e.currentTarget.dataset.index]
    if (!it || !WARNING_WAIVER_URL) return
    const score = (it.meta || '').split(' · ').filter(Boolean)[1] || ''
    const st = it.code
      ? {
          kind: 'course', code: it.code, name: it.name,
          tip: `《${it.name}》（${it.code}）最高分 ${score} 未过。豁免后该方案课视为已修：`
               + '缺修消除、所属类别差额同步减少，下次运行选课检查时生效。',
          placeholder: '备注依据（选填，如：学校已批准免修）',
        }
      : {
          kind: 'grade', code: '', name: it.name,
          tip: `《${it.name}》是方案外旧版课程（未匹配到培养方案任何课程），挂科未过。`
               + '豁免后该课不再列入"未及格科目"与"未匹配候选"清单（可随时撤销）；'
               + '因它本就不参与方案学分，不会改变任何缺修/差额判定。',
          placeholder: '备注依据（选填，如：旧培养方案课程，教务确认不再要求）',
        }
    this.setData({ exemptState: st, exemptNote: '', kbdH: 0 })
  },

  closeExempt() { this.setData({ exemptState: null, kbdH: 0 }) },
  confirmExempt() {
    const st = this.data.exemptState
    if (!st || !WARNING_WAIVER_URL) return
    const note = (this.data.exemptNote || '').trim()
    if (st.kind === 'course') {
      this._postWaiver({ kind: 'course', course_code: st.code,
                         course_name: st.name, note })
    } else {
      this._postWaiver({ kind: 'grade', course_name: st.name, note })
    }
    this.closeExempt()
  },

  // 类型A：缺修课 免修 / 平替（data-cat-index + data-index 定位 cats[].missing；
  // other_missing 段用 data-other-index）
  waiveCourse(e) {
    const { index, catIndex, otherIndex, mode } = e.currentTarget.dataset
    let it
    if (otherIndex != null && otherIndex !== '') {
      it = this.data.otherMissing[Number(otherIndex)]
    } else {
      const cat = this.data.cats[Number(catIndex)]
      it = cat && cat.missing[Number(index)]
    }
    if (!it || !WARNING_WAIVER_URL) return
    // 2026-09-07 用户改裁：failed（挂科未过）行提供**免修**入口（管理员特殊处理，
    // 后端 L1 已改——挂科代表行允许豁免注入）；平替（用旧课替代）对挂科行仍禁
    if (it.failed && mode === 'replace') {
      wx.showToast({ title: '该课挂科未过，暂不支持平替（可免修特殊处理）', icon: 'none' })
      return
    }
    if (mode === 'replace') {
      if (!this.data.reasonUnmatched.length) {
        wx.showToast({ title: '已修课程中无可用未匹配旧课（无法平替，可选免修）', icon: 'none' })
        return
      }
      this.setData({ pickState: { op: 'replace', code: it.code, name: it.name } })
      return
    }
    // 缺修清单免修：打开大字号确认面板（挂科必修与从未修提示差异）
    const tip = it.failed
      ? `《${it.name}》（${it.code}）${(it.failTip || '挂科未过')}。豁免后该方案课视为已修：`
        + '缺修消除、类别差额同步减少，下次运行选课检查时生效。'
      : `《${it.name}》（${it.code}）成绩单与选课均无记录。豁免后视为已修：`
        + '缺修消除、类别差额同步减少，下次运行选课检查时生效。'
    this.setData({
      exemptState: { kind: 'course', code: it.code, name: it.name, tip,
                     placeholder: '备注依据（选填，如：旧版方案已修 / 学校批准免修）' },
      exemptNote: '', kbdH: 0,
    })
  },

  chooseReplace(e) {
    const src = this.data.reasonUnmatched[e.currentTarget.dataset.index]
    const pick = this.data.pickState
    if (!src || !pick) return
    // C2：挂科未过源课不可作平替来源（灰显行；点击仅提示）
    if (src.usable === false) {
      wx.showToast({ title: '该课挂科未过，不可作平替/认可源（可走免修）', icon: 'none' })
      return
    }
    wx.showModal({
      title: '平替确认',
      content: `用已修《${src.course_name}》平替《${pick.name || pick.code}》：`
               + '该方案课豁免并视为已修，豁免将在下次运行选课检查时生效。',
      success: (res) => {
        if (!res.confirm) return
        this._postWaiver({
          kind: 'course', course_code: pick.code, course_name: pick.name,
          grade_source: src.course_name, note: '',
        })
        this.closePick()
      },
    })
  },

  // 类型B：未匹配旧课 → 类别 picker + 备注 → POST(kind=credit)
  waiveCredit(e) {
    const u = this.data.reasonUnmatched[e.currentTarget.dataset.index]
    if (!u) return
    // C2：挂科未过旧课 usable=false —— 灰显禁用；点击仅提示（后端同样拒绝）
    if (u.usable === false) {
      wx.showToast({ title: '该课挂科未过，不可作平替/认可源（可走免修）', icon: 'none' })
      return
    }
    this.setData({
      pickState: { op: 'credit', course: u },
      pickCatIdx: CREDIT_CATEGORIES.indexOf('专业选修课程'),
      pickNote: '', kbdH: 0,
    })
  },

  onPickCat(e) { this.setData({ pickCatIdx: Number(e.detail.value) }) },
  onPickNote(e) { this.setData({ pickNote: e.detail.value }) },
  onExemptNote(e) { this.setData({ exemptNote: e.detail.value }) },
  onKbd(e) { this.setData({ kbdH: Number((e.detail && e.detail.height) || 0) }) },
  onKbdHide() { this.setData({ kbdH: 0 }) },

  confirmCredit() {
    const pick = this.data.pickState
    if (!pick || pick.op !== 'credit') return
    const cat = CREDIT_CATEGORIES[this.data.pickCatIdx]
    if (!cat) return
    this._postWaiver({
      kind: 'credit', course_name: pick.course.course_name,
      category: cat, note: this.data.pickNote.trim(),
    })
    this.closePick()
  },

  closePick() { this.setData({ pickState: null, kbdH: 0 }) },

  _postWaiver(payload) {
    if (!WARNING_WAIVER_URL) return
    wx.request({
      url: WARNING_WAIVER_URL,
      method: 'POST',
      header: { 'X-API-Key': WARNING_API_KEY, 'Content-Type': 'application/json' },
      data: Object.assign({ grade: this.data.grade, student_id: this.data.sid },
                           payload),
      success: (r) => {
        if (r.statusCode === 200 && r.data && r.data.ok !== false) {
          wx.showToast({ title: '已提交豁免/认可，下次重跑选课检查生效', icon: 'none' })
          this._afterWaiverChange()
          return
        }
        const msg = this._apiErr(r)
        wx.showToast({ title: msg ? `操作失败：${msg}` : '操作失败，请重试', icon: 'none' })
      },
      fail: () => {
        wx.showToast({ title: '请检查网络或联系管理员', icon: 'none' })
      },
    })
  },

  // 撤销豁免（记录在详情页"豁免记录"节；成功后刷新本页全部状态）
  waiverDelete(e) {
    const id = e.currentTarget.dataset.id
    if (!WARNING_WAIVER_URL) return
    wx.showModal({
      title: '撤销豁免',
      content: '撤销后豁免不再生效（下次重跑选课检查恢复原判定），确定撤销该条记录？',
      success: (res) => {
        if (!res.confirm) return
        wx.request({
          url: WARNING_WAIVER_URL,
          method: 'DELETE',
          header: { 'X-API-Key': WARNING_API_KEY, 'Content-Type': 'application/json' },
          data: { id },
          success: (r) => {
            if (r.statusCode === 200 && r.data && r.data.ok !== false) {
              wx.showToast({ title: '已撤销豁免', icon: 'none' })
              this._afterWaiverChange()
            } else {
              const msg = this._apiErr(r)
              wx.showToast({ title: msg || `撤销失败（HTTP ${r.statusCode}）`, icon: 'none' })
            }
          },
          fail: () => {
            wx.showToast({ title: '请检查网络或联系管理员', icon: 'none' })
          },
        })
      },
    })
  },

  _afterWaiverChange() {
    // 本页豁免/撤销后：student 端点重拉（unmatched 消耗集 / waived 标注 / waivers 同步刷新）
    this._loadStudent()
  },

  // 后端错误提取（message / detail 字符串 / pydantic detail 数组）
  _apiErr(r) {
    const d = r && r.data
    if (!d) return ''
    if (d.message) return String(d.message)
    if (typeof d.detail === 'string') return d.detail
    if (Array.isArray(d.detail)) return d.detail.map((x) => x.msg).join(';')
    return ''
  },

  toggleCourses() { this.setData({ coursesOpen: !this.data.coursesOpen }) },
  toggleGrp(e) {
    const i = e.currentTarget.dataset.index
    const g = this.data.reasonMatched[i]
    if (!g) return
    this.setData({ [`reasonMatched[${i}].open`]: !g.open })
  },

  noop() {},
})
