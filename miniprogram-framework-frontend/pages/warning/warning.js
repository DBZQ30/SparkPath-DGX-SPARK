// pages/warning/warning.js —— 选课合理性检查（2026-08-31 方向重构）
// 管理员：上传五类数据文件 → 上传选课后自动检查 → 选课检查名单（专业选修不足）
// 上传异步化：网络传输（onProgressUpdate 进度）→ 服务端解析（轮询 /status）→ 结果
const {
  WARNING_UPLOAD_URL, WARNING_STATUS_URL,
  WARNING_DELETE_URL, WARNING_CHECK_URL, WARNING_CHECK_RUN_URL,
  WARNING_GRADE_URL, WARNING_GRADE_CONFIRM_URL, WARNING_EXPORT_URL,
  WARNING_WAIVER_URL,
} = require('../../config/api')
const { WARNING_API_KEY } = require('../../config/instance')

// v1.7 标准专业名单（与后端 STANDARD_MAJORS 一致；上传子按钮 + 状态区"其他"判断）
const MAJORS = ['工商管理', '大数据管理与应用', '工业工程', '会计学（ACCA）']

const MODULES = [
  // v6.5：学籍名单放最前——检查名单权威基准（缺失时检查降级），其后为检查输入文件
  { hint: '学籍名单', desc: ' 1 份 xls/xlsx（学号/专业/学籍状态）', icon: '📋', ext: ['xls', 'xlsx'], majors: null },
  { hint: '培养方案', desc: '4 个专业各 1 份 docx', icon: '📘', ext: ['docx'], majors: MAJORS },
  { hint: '选课结果', desc: '1 份 xlsx', icon: '📗', ext: ['xlsx'], majors: null },
  { hint: '成绩单', desc: '4 个专业各 1 份 docx', icon: '📕', ext: ['docx'], majors: MAJORS },
  // 2026-09-07：通识课程信息表 = 全局单份（各年级共用通识池；学分权威，
  // 覆盖更新后下次选课检查生效；不随年级分区）
  { hint: '通识课程信息表', desc: '全校 1 份 xlsx，含通识课学分信息', icon: '🧾', ext: ['xlsx'], majors: null },
]

const MAX_FILE_SIZE = 10 * 1024 * 1024   // 与服务端上限一致（>10MB 拒绝）

// 模块中文名 → 服务端 file_type（/status 匹配用）
const TYPE_KEY = { '培养方案': 'plan', '选课结果': 'selection', '成绩单': 'grade',
                    '学籍名单': 'roster', '通识课程信息表': 'gen_ed' }
// 解析阶段轮询：3s 间隔，240s 上限（单份成绩单 OCR 实测 40-155s，留足余量）
const POLL_INTERVAL = 3000
const POLL_TIMEOUT = 900000

// 上传日志条目全局自增 id（v1.8.1）：并发上传时按 id 合并，避免闭包快照互相覆盖
let _ulid = 0

// v6.3 学期体系（与后端 service.SEMESTERS 一致；4 学年 × ≤3 学期）
const SEMESTERS = ['1-1', '1-2', '1-3', '2-1', '2-2', '2-3',
                   '3-1', '3-2', '3-3', '4-1', '4-2']
const AUTO_OPTION = '自动推断'   // picker 首项：发送空串 = 恢复文件推断
const SEM_OPTIONS = [AUTO_OPTION].concat(SEMESTERS)

// 学期编码比较（"X-Y"，调用方保证非空）：a < b → -1
function semCmp(a, b) {
  const pa = a.split('-').map(Number)
  const pb = b.split('-').map(Number)
  if (pa[0] !== pb[0]) return pa[0] < pb[0] ? -1 : 1
  if (pa[1] !== pb[1]) return pa[1] < pb[1] ? -1 : 1
  return 0
}

// v7：学分显示去尾零（20.0 → "20"，20.5 → "20.5"）

// 2026-09-06：名单行触发摘要（details 同源；老批次无 details 时按行字段/消息兜底）
// 2026-09-22：缺修课并入类别差额展示 → chip 改"缺修 N 门"（具体课名留详情页）
function rowSummary(s) {
  const det = s.details || {}
  const parts = []
  ;(det.cats || []).forEach((c) => parts.push({
    text: `${c.cat} 差 ${fmtN(c.gap)}`, cls: 'sum-gap',
  }))
  // 缺修门数 = 各 cats[].missing 之和 + other_missing（老批次回退顶层 missing）
  let miss = 0
  ;(det.cats || []).forEach((c) => { miss += (c.missing || []).length })
  miss += (det.other_missing || []).length
  if (!miss && (det.missing || []).length) miss = det.missing.length   // 老批次兼容
  if (miss) parts.push({ text: `缺修 ${miss} 门`, cls: 'sum-miss' })
  if (!parts.length && s.category) {
    if (s.category === '缺修必修课') {
      const n = (s.message || '').match(/《/g)
      parts.push({ text: n && n.length ? `缺修 ${n.length} 门` : '缺修',
                   cls: 'sum-miss' })
    } else {
      parts.push({ text: `${s.category} 差 ${fmtN(s.gap)}`, cls: 'sum-gap' })
    }
  }
  return parts
}
function fmtN(n) {
  const v = Number(n)
  if (!isFinite(v)) return ''
  return Number.isInteger(v) ? String(v) : String(Math.round(v * 10) / 10)
}

Page({
  data: {
    modules: MODULES,
    enabled: !!(WARNING_UPLOAD_URL && WARNING_API_KEY),
    files: [],          // 文件状态列表（/status）
    checkSummary: '',   // 选课检查摘要
    checkList: [],      // 选课检查名单（专业选修不足学生）
    checkCount: 0,
    checkedAt: '',
    hasCheck: false,    // 是否有选课文件（无则显示引导提示）
    loading: false,
    uploadLog: [],      // 逐文件上传结果 [{name, state, progress, ok, msg}]（并发独立状态机）
    grades: [],         // 年级列表（/status 返回；tab 数据源）
    activeGrade: '',    // 当前选中年级（未定则取 grades[0]）
    exemptCourses: {},  // 豁免课程清单（{专业: [课程名]}，后端原始结构）
    exemptList: [],     // 豁免清单渲染用 [{major, names}]（WXML 不支持方法调用，预拼接）
    hasExempt: false,
    notSelected: [],    // 名单内本学期未选课学生（selection-check 返回；警示区渲染）
    notSelectedCount: 0,
    downloading: false,
    // v6.3 学期配置状态行（loadCheck 成功后填充；空 = 状态行隐藏）
    semOptions: SEM_OPTIONS,
    semState: null,     // {current, auto, covered, confirm}（selection-check 响应 4 字段）
    semLabel: '',       // picker 显示：已配置学期 or 自动（文件推断）
    semIdx: 0,          // picker 选中 index（0 = 自动推断）
    coveredLabel: '',   // 成绩覆盖到：covered_sem（人工确认生效时带"（已确认）"后缀）
    prevSem: '',        // 当前学期前一学期（needConfirm 时显示/提交）
    needConfirm: false, // 需人工确认 prevSem 已出成绩（auto 与 confirm 均未覆盖 prev）
    waiverList: [],         // 豁免记录（GET waiver?grade=，页面底部折叠区）
    waiversOpen: false,     // 豁免记录折叠（默认收起）
  },

  onLoad() {
    if (!this.data.enabled) return
    this.loadStatus()
    this.loadCheck()
    this.loadWaivers()   // v7：豁免记录（页面底部折叠区）
  },

  onShow() {
    if (this.data.enabled) {
      this.loadStatus()
      this.loadCheck()
      this.loadWaivers()
    }
  },

  onUnload() {
    if (this._pollTimer) clearTimeout(this._pollTimer)
  },

  onPullDownRefresh() {
    if (this.data.enabled) {
      this.loadStatus()
      this.loadCheck()
      this.loadWaivers()
    }
    wx.stopPullDownRefresh()
  },

  _fail() {
    wx.showToast({ title: '请检查网络或联系管理员', icon: 'none' })
  },

  // ── 文件上传（v1.8 并发：解析在服务端 _OP_LOCK 内排队，上传可同时发起多个；
  //    各文件独立状态机；v1.7 专业分区：majors 类型每专业子按钮传 major_hint）
  tapChoose(e) {
    if (!this.data.enabled) return
    const { hint, major, ext } = e.currentTarget.dataset
    wx.chooseMessageFile({
      count: 1,             // v1.7：每专业入口一次 1 份
      type: 'file',
      extension: ext || [], // 按模块扩展名过滤（基础库 2.10.0+；本实例 3.16.2）
      success: (res) => {
        const picked = res.tempFiles || []
        let pending = 0
        const doneOne = () => {
          pending -= 1
          if (pending === 0) this.loadStatus()   // 本次选择全部结束 → 刷新状态区
        }
        picked.forEach((f) => {
          // P2：大小预校验（>10MB 与服务端上限一致，直接跳过不发请求）
          if (f.size > MAX_FILE_SIZE) {
            this._pushLog({ name: f.name, state: 'failed', progress: 0,
                            ok: false, msg: '文件超过 10MB 上限' })
            return
          }
          pending += 1
          this.uploadOne(hint, major || '', f, doneOne)
        })
        if (pending === 0) this.loadStatus()
      },
      fail: () => {},
    })
  },

  // 失败文件单独重试：移除旧条目后按原 path/hint/major 重新上传
  tapRetry(e) {
    const id = e.currentTarget.dataset.id
    const old = this.data.uploadLog.find((it) => it.id === id)
    if (!old || !old.path || !old.hint) return
    this.setData({ uploadLog: this.data.uploadLog.filter((it) => it.id !== id) })
    this.uploadOne(old.hint, old.major || '', { name: old.name, path: old.path },
                   () => this.loadStatus())
  },

  // v1.8.1 并发隔离：每条日志带唯一 id，更新一律按 id 合并进"当前"列表。
  // 旧实现每个 uploadOne 各自持有数组快照，先发起的文件完成时 setData 会整体
  // 覆盖 uploadLog，抹掉后加入的条目——即"第一份解析完成后其余进度条消失"。
  _pushLog(entry) {
    const item = Object.assign({ id: ++_ulid }, entry)
    this.setData({ uploadLog: this.data.uploadLog.concat([item]) })
    return item.id
  },

  _updateLog(id, patch) {
    this.setData({
      uploadLog: this.data.uploadLog.map(
        (it) => (it.id === id ? Object.assign({}, it, patch) : it)),
    })
  },

  uploadOne(hint, major, file, next) {
    // 每文件状态机：uploading（网络传输，进度条）→ parsing（服务端解析，轮询）
    //             → done / failed（按 id 合并写 uploadLog，进入下一文件）
    const id = this._pushLog({ name: file.name, path: file.path, hint, major,
                               state: 'uploading', progress: 0, ok: false, msg: '' })
    const update = (patch) => this._updateLog(id, patch)
    const done = (ok, msg) => {
      update({ state: ok ? 'done' : 'failed', progress: 100, ok, msg })
      next()
    }
    // 前端轮询窗口到期 ≠ 失败：后台 daemon 线程仍在解析（成绩单 OCR 逐张调 VL，
    // 单份可达数分钟）。用中性状态收尾，不给"重试"入口，避免误重传把队列撑更长。
    const pending = (msg) => {
      update({ state: 'pending', progress: 100, ok: false, msg })
      next()
    }
    const attempt = (retried) => {
      update({ state: 'uploading', progress: 0, ok: false, msg: '' })
      wx.uploadFile({
        url: WARNING_UPLOAD_URL,
        filePath: file.path,
        name: 'file',
        // v1.8：orig_name 传原始文件名（微信上传发送临时 hash 名，后端以此入库/回传）
        // v6：grade_hint 必传（文件归入当前选中年级分区）
        formData: Object.assign(
          { type_hint: hint, orig_name: file.name },
          // 通识课程信息表为全局单份（不随年级分区）；其余文件带当前年级
          hint === '通识课程信息表' ? {}
            : { grade_hint: this.data.activeGrade },
          major ? { major_hint: major } : {}),
        header: { 'X-API-Key': WARNING_API_KEY },
        timeout: 120000,   // M1：120s 超时（仅覆盖网络传输阶段；解析已异步化）
        onProgressUpdate: (p) => {
          if (p && p.progress != null) update({ progress: p.progress })
        },
        success: (res) => {
          let body = {}
          try { body = JSON.parse(res.data) } catch (e) {}
          if (res.statusCode === 401) return done(false, 'API Key 失效，请检查配置')
          if (body.parsed_status === 'rejected') return done(false, body.message || '文件被拒绝')
          // done = 判重命中（同一文件已在库），并未执行解析——与"解析完成"区分展示
          if (body.parsed_status === 'done') {
            return done(true, body.message || '已在库（同一文件此前已上传，无需重复解析）')
          }
          if (body.parsed_status === 'parsing') {
            // v1.8：按响应回传的落库 file_name 匹配（与 file.name 可能不同——临时名）
            return this.pollParsed(hint, major, body.file_name || file.name, update, done, pending)
          }
          if (!retried) return attempt(true)   // 未知响应 → 重试 1 次
          return done(false, body.message || `HTTP ${res.statusCode}`)
        },
        fail: (err) => {
          console.error('[warning] uploadFile fail:', err)
          if (!retried) return attempt(true)   // 网络类失败 → 重试 1 次
          const msg = (err && err.errMsg) || ''
          if (msg.includes('timeout')) return done(false, '上传超时，请重试')
          if (msg.includes('domain')) return done(false, '接口域名未在微信后台配置')
          return done(false, msg || '网络错误或超时')
        },
      })
    }
    attempt(false)
  },

  // 解析阶段轮询：/status 按 (file_type, major, 落库 file_name) 匹配（v1.7 + v1.8）
  pollParsed(fileType, major, serverFileName, update, done, pending) {
    const ft = TYPE_KEY[fileType] || ''
    update({ state: 'parsing', msg: '解析入库中…' })
    let waited = 0
    const schedule = () => {
      if (waited >= POLL_TIMEOUT) {
        const msg = '后台解析中，稍后下拉刷新状态查看'
        return pending ? pending(msg) : done(false, msg)
      }
      waited += POLL_INTERVAL
      this._pollTimer = setTimeout(tick, POLL_INTERVAL)
    }
    const tick = () => {
      this.fetchStatus((files) => {
        if (!files) return schedule()
        const row = files
          .filter((g) => g.file_type === ft)
          .flatMap((g) => g.items)
          .find((it) => it.major === (major || null) && it.file_name === serverFileName)
        if (!row || (row.parsed_status !== 'done' && row.parsed_status !== 'failed')) return schedule()
        if (row.parsed_status === 'done') return done(true, row.note || '入库成功')
        return done(false, row.error || '解析失败，请检查文件内容后重传')
      })
    }
    tick()
  },

  // ── 状态（v6 年级分区：按 activeGrade 过滤文件；grades 列表维护 tab）──
  fetchStatus(cb) {
    if (!WARNING_STATUS_URL) return
    wx.request({
      url: WARNING_STATUS_URL + (this.data.activeGrade ? `?grade=${encodeURIComponent(this.data.activeGrade)}` : ''),
      header: { 'X-API-Key': WARNING_API_KEY },
      success: (res) => {
        if (res.statusCode === 200 && res.data && res.data.files) {
          const files = this._decorateFiles(res.data.files)
          const grades = res.data.grades || []
          const first = !this.data.activeGrade && grades.length
          this.setData({ files, missingTip: this._missingTip(files), grades })
          if (first) {
            // 首屏：status 返回后才知年级列表，此处补齐 activeGrade 并
            // 重新拉取按年级过滤的文件列表 + 加载对应检查
            this.setData({ activeGrade: grades[0] })
            this.loadCheck()
            this.loadWaivers()   // v7：豁免记录同样依赖 activeGrade，首屏补齐后加载
            this.fetchStatus()
          }
          if (cb) cb(files)
        } else if (cb) {
          cb(null)
        }
      },
      fail: () => {
        if (cb) cb(null)      // 轮询路径静默重试
        else this._fail()     // 手动刷新路径提示
      },
    })
  },

  // 标记每行是否标准专业（majors 名单内；非标准 → "其他"区样式）
  _decorateFiles(files) {
    return files.map((g) => ({
      ...g,
      items: g.items.map((it) => ({
        ...it,
        majorIsStandard: !!it.major && g.majors.indexOf(it.major) !== -1,
      })),
    }))
  },

  // 缺文件汇总提示（v6.1）：标准 majors 无 done 记录 → 提示；
  // 选课结果（majors 空）整组无文件 → 同样提示（2026-09-01 用户反馈）
  _missingTip(files) {
    const parts = []
    const rowOf = (g, m) => g.items.find((x) => x.major === m)
    for (const g of files) {
      if (!g.majors || !g.majors.length) {
        const it = g.items && g.items[0]
        // v1.8.1：解析中（记录已存在但未 done）不算"缺少"，避免"已上传却显示未上传"
        if (it && it.file_name && it.parsed_status === 'parsing') {
          parts.push(`${g.type_name}解析中`)
        } else if (!it || !it.file_name || it.parsed_status === 'failed') {
          parts.push(`${g.type_name}缺少`)
        }
        continue
      }
      const missing = g.majors.filter((m) => {
        const it = rowOf(g, m)
        return !it || !it.file_name || it.parsed_status === 'failed'
      })
      const parsing = g.majors.filter((m) => {
        const it = rowOf(g, m)
        return it && it.file_name && it.parsed_status === 'parsing'
      })
      if (missing.length) parts.push(`${g.type_name}缺少：${missing.join('、')}`)
      if (parsing.length) parts.push(`${g.type_name}解析中：${parsing.join('、')}`)
    }
    return parts.join('；')
  },

  loadStatus() {
    this.fetchStatus()
  },

  // ── 删除（v1.7 与上传分区一一对应：按 file_type+major 删该分区全部记录）──
  tapDelete(e) {
    if (!WARNING_DELETE_URL) {
      wx.showToast({ title: '删除接口未配置，请检查 api.js 版本', icon: 'none' })
      return
    }
    const ft = e.currentTarget.dataset.ftype
    const major = e.currentTarget.dataset.major || ''
    const group = this.data.files.find((f) => f.file_type === ft)
    if (!group) return
    const row = group.items.find((it) => it.major === (major || null))
    if (!row || !row.file_name) return
    const scope = major ? `「${major}」` : `「${group.type_name}」`
    wx.showModal({
      title: '删除文件',
      content: `确定删除${scope}文件 ${row.file_name}？将删除该分区全部记录（含历史版本，不可恢复），需重新上传才能计算。`,
      success: (res) => {
        if (!res.confirm) return
        wx.request({
          url: WARNING_DELETE_URL,
          method: 'POST',
          header: { 'X-API-Key': WARNING_API_KEY, 'Content-Type': 'application/json' },
          // v6：删除限当前年级分区；仅 activeGrade 非空时附 grade（空字符串会触发后端"全删"语义）
          data: Object.assign({ file_type: ft },
                              this.data.activeGrade ? { grade: this.data.activeGrade } : {},
                              major ? { major: major } : {}),
          success: (r) => {
            if (r.statusCode === 200) {
              wx.showToast({ title: (r.data && r.data.message) || '删除成功', icon: 'none' })
              this.loadStatus()
              return
            }
            // 非 200：显示后端具体原因（422 detail / 404 detail），不误报成功
            const detail = r.data && (r.data.message
              || (Array.isArray(r.data.detail) ? r.data.detail.map((d) => d.msg).join(';')
                                                : r.data.detail))
            wx.showToast({ title: detail ? `删除失败：${detail}` : `删除失败（HTTP ${r.statusCode}）`,
                           icon: 'none' })
          },
          fail: () => this._fail(),
        })
      },
    })
  },

  // ── 选课检查（2026-08-31：上传选课后自动检查；此处为手动重跑入口）──
  tapRunCheck() {
    if (this.data.loading || !this.data.enabled) return
    this.setData({ loading: true })
    wx.request({
      url: WARNING_CHECK_RUN_URL,
      method: 'POST',
      header: { 'X-API-Key': WARNING_API_KEY },
      data: { grade: this.data.activeGrade },   // v6：检查按年级分区执行
      timeout: 60000,
      success: (res) => {
        this.setData({ loading: false })
        if (res.statusCode === 200 && res.data && res.data.message) {
          this.setData({ checkSummary: res.data.message })
          this.loadCheck()
        } else if (res.statusCode === 401) {
          this._fail()
        } else {
          this.setData({ checkSummary: (res.data && res.data.message) || '检查失败' })
        }
      },
      fail: () => { this.setData({ loading: false }); this._fail() },
    })
  },

  // ── 检查名单（v6：按年级加载；年级未定前不发请求，由 fetchStatus 首屏补齐后触发）──
  loadCheck() {
    if (!WARNING_CHECK_URL || !this.data.activeGrade) return
    wx.request({
      url: WARNING_CHECK_URL + `?grade=${encodeURIComponent(this.data.activeGrade)}`,
      header: { 'X-API-Key': WARNING_API_KEY },
      success: (res) => {
        if (res.statusCode === 200 && res.data) {
          // v7：行内并存结构化触发明细（details JSON；老批次缺省时兜底空对象）
          const students = (res.data.students || []).map((s) => ({
            ...s, details: s.details || {},
            // 2026-09-06：行内不再显示"应/已修/已选/差"四要素原文（属详情上下文），
            // 改为触发摘要 chips——每触发维度一枚：类别差 N / 缺修必修课 N 门
            summary: rowSummary(s),
          }))
          const notSelected = (res.data.not_selected || []).map((s) => ({ ...s }))
          const exemptCourses = res.data.exempt_courses || {}
          const exemptList = Object.keys(exemptCourses).map((major) => ({
            major, names: (exemptCourses[major] || []).join('、'),
          }))
          this.setData(Object.assign({
            checkList: students,
            checkCount: students.length,
            checkSummary: res.data.summary || this.data.checkSummary || '',
            checkedAt: res.data.checked_at || '',
            exemptCourses,
            exemptList,
            hasExempt: exemptList.length > 0,
            notSelected,
            notSelectedCount: notSelected.length,
            hasCheck: !!(res.data.source_file_id),
          }, this._semState(res.data)))
        }
      },
      fail: () => {},
    })
  },

  // v6.3：读年级学期 4 字段 → 状态行展示数据
  // current = API current_semester（未配置空串 → picker 显示"自动（文件推断）"）
  // coveredLabel = 生效覆盖 covered_sem；auto < covered（人工确认生效）加"（已确认）"
  // needConfirm：current 有前一项 prev，且成绩单自动检测（covered_auto）与人工确认
  // （confirm_sem）均未覆盖 prev → 提示管理员确认 prev 已出成绩
  _semState(res) {
    const cur = res.current_semester || ''
    const auto = res.covered_auto || ''
    const covered = res.covered_sem || ''
    const confirm = res.confirm_sem || ''
    const i = cur ? SEMESTERS.indexOf(cur) : -1
    const semIdx = i >= 0 ? i + 1 : 0       // picker index（0 = 自动推断/未知值）
    const prev = i > 0 ? SEMESTERS[i - 1] : ''
    const uncovered = (sem, prevSem) => !sem || semCmp(sem, prevSem) < 0
    let coveredLabel = covered || '—'
    if (covered && (!auto || semCmp(auto, covered) < 0)) coveredLabel += '（已确认）'
    return {
      semState: { current: cur, auto, covered, confirm },
      semLabel: cur || '自动（文件推断）',
      semIdx,
      coveredLabel,
      prevSem: prev,
      needConfirm: !!(prev && uncovered(auto, prev) && uncovered(confirm, prev)),
    }
  },

  // ── 年级 tab 切换（v6）：全部数据按年级重载 ──
  tapGrade(e) {
    const g = e.currentTarget.dataset.grade
    if (g === this.data.activeGrade) return
    this.setData({
      activeGrade: g,
      checkList: [], checkCount: 0, checkSummary: '', checkedAt: '', hasCheck: false,
      exemptCourses: {}, exemptList: [], hasExempt: false,
      notSelected: [], notSelectedCount: 0,
      semState: null,   // v6.3：学期状态行等 loadCheck 返回后显示
      // v7：豁免记录随年级重载（旧年级残留清空）
      waiverList: [], waiversOpen: false,
    })
    this.loadStatus()
    this.loadCheck()
    this.loadWaivers()
  },

  // ── 添加年级（后端校验格式/重名，ok=false 时仅提示不切换）──
  tapAddGrade() {
    wx.showModal({
      title: '添加年级',
      editable: true,
      placeholderText: '如 2026级',
      success: (res) => {
        if (!res.confirm || !res.content) return
        const name = (res.content || '').trim()
        wx.request({
          url: WARNING_GRADE_URL,
          method: 'POST',
          header: { 'X-API-Key': WARNING_API_KEY, 'Content-Type': 'application/json' },
          data: { name },
          success: (r) => {
            wx.showToast({ title: (r.data && r.data.message) || '添加成功', icon: 'none' })
            if (r.data && r.data.ok !== false) {
              // 新年级无任何检查数据：与 tapGrade 一致清空旧年级检查态再加载
              this.setData({
                activeGrade: name,
                checkList: [], checkCount: 0, checkSummary: '', checkedAt: '', hasCheck: false,
                exemptCourses: {}, exemptList: [], hasExempt: false,
                notSelected: [], notSelectedCount: 0,
                semState: null,
                // v7：新年级清空豁免记录残留（与 tapGrade 同款）
                waiverList: [], waiversOpen: false,
              })
              this.loadStatus()
              this.loadCheck()
              this.loadWaivers()
            }
          },
          fail: () => this._fail(),
        })
      },
    })
  },

  // ── 删除年级（毕业年级滚出；仅移出注册表，历史分区数据保留）──
  tapDeleteGrade(e) {
    const grade = e.currentTarget.dataset.grade
    if (!grade || !WARNING_GRADE_URL) return
    wx.showModal({
      title: '移出年级',
      content: `确认把「${grade}」移出年级列表？历史数据保留，仅不再出现在各功能页的年级选项中。`,
      confirmText: '移出', confirmColor: '#e64340',
      success: (res) => {
        if (!res.confirm) return
        wx.request({
          url: `${WARNING_GRADE_URL}?name=${encodeURIComponent(grade)}`,
          method: 'DELETE',
          header: { 'X-API-Key': WARNING_API_KEY },
          success: (r) => {
            wx.showToast({ title: (r.data && r.data.message) || '已移出', icon: 'none' })
            if (r.data && r.data.ok !== false) {
              const remaining = (this.data.grades || []).filter((g) => g !== grade)
              if (this.data.activeGrade === grade) {
                this.setData({ grades: remaining, activeGrade: remaining[0] || '' })
              } else {
                this.setData({ grades: remaining })
              }
              this.loadStatus()
              this.loadCheck()
              this.loadWaivers()
            }
          },
          fail: () => this._fail(),
        })
      },
    })
  },

  // ── 年级当前学期（v6.3）：picker 选中 → 确认 → PUT（空串 = 恢复文件推断）──
  onSemChange(e) {
    const grade = this.data.activeGrade
    const idx = Number(e.detail.value)
    if (!grade || !WARNING_GRADE_URL || !this.data.semOptions[idx]) return
    const val = idx === 0 ? '' : SEMESTERS[idx - 1]
    wx.showModal({
      title: '设置当前学期',
      content: `确定将「${grade}」当前学期设为「${idx === 0 ? AUTO_OPTION : val}」？`,
      success: (res) => {
        if (!res.confirm) return
        wx.request({
          url: WARNING_GRADE_URL,
          method: 'PUT',
          header: { 'X-API-Key': WARNING_API_KEY, 'Content-Type': 'application/json' },
          data: { name: grade, current_semester: val },
          success: (r) => {
            if (r.statusCode !== 200) return this._fail()
            wx.showToast({ title: (r.data && r.data.message) || '已更新', icon: 'none' })
            if (r.data && r.data.ok !== false) this.loadCheck()   // 成功 → 刷新状态行
          },
          fail: () => this._fail(),
        })
      },
    })
  },

  // ── 人工确认成绩覆盖（v6.3）：后端校验成绩单确有 prevSem 整批记录后才生效 ──
  onConfirmSem() {
    const grade = this.data.activeGrade
    const prev = this.data.prevSem
    if (!grade || !prev || !WARNING_GRADE_CONFIRM_URL) return
    wx.showModal({
      title: '确认成绩已出',
      content: `确认「${grade}」成绩单已出至 ${prev}？将校验成绩单确有该学期记录，通过后 ${prev} 及以前无成绩课程不再豁免。`,
      success: (res) => {
        if (!res.confirm) return
        wx.request({
          url: WARNING_GRADE_CONFIRM_URL,
          method: 'POST',
          header: { 'X-API-Key': WARNING_API_KEY, 'Content-Type': 'application/json' },
          data: { name: grade, sem: prev },
          success: (r) => {
            if (r.statusCode !== 200) return this._fail()
            wx.showToast({ title: (r.data && r.data.message) || '已提交', icon: 'none' })
            if (r.data && r.data.ok !== false) this.loadCheck()   // 校验通过 → 刷新状态行
          },
          fail: () => this._fail(),
        })
      },
    })
  },

  // ── 下载选课检查报告（xlsx，需当前年级已有检查结果）──
  tapExport() {
    if (this.data.downloading || !this.data.hasCheck) return
    this.setData({ downloading: true })
    wx.downloadFile({
      url: WARNING_EXPORT_URL + (this.data.activeGrade ? `?grade=${encodeURIComponent(this.data.activeGrade)}` : ''),
      header: { 'X-API-Key': WARNING_API_KEY },
      success: (res) => {
        this.setData({ downloading: false })
        if (res.statusCode !== 200) return wx.showToast({ title: '暂无报告或下载失败', icon: 'none' })
        wx.openDocument({ filePath: res.tempFilePath, fileType: 'xlsx', showMenu: true })
      },
      fail: () => {
        this.setData({ downloading: false })
        wx.showToast({ title: '下载失败，请检查网络', icon: 'none' })
      },
    })
  },

  // ══ 预警详情入口（2026-09-06：弹层 → 独立详情页）══
  // 行数据经 eventChannel 传给详情页；栈 [warning, warning-detail]——
  // 详情页原生导航栏返回必然回到本页（学业预警），不会落到首界面
  tapReason(e) {
    const idx = e.currentTarget.dataset.index
    const s = this.data.checkList[idx]
    if (!s || !this.data.activeGrade) return
    wx.navigateTo({
      url: '/pages/warning-detail/warning-detail'
        + `?grade=${encodeURIComponent(this.data.activeGrade)}`
        + `&sid=${encodeURIComponent(s.student_id)}`,
      success: (res) => {
        if (res && res.eventChannel) {
          res.eventChannel.emit('studentRow', { ...s, details: s.details || {} })
        }
      },
      fail: (err) => {
        // 可诊断提示：页面未注册（app.json 未重建）与其它失败分开展示
        const msg = (err && err.errMsg) || ''
        const tip = msg.indexOf('not found') >= 0 || msg.indexOf('does not exist') >= 0
          ? '详情页未注册：请在本项目执行 npm run switch（或重建 app.json）后重新编译'
          : '打开详情失败，请重试'
        wx.showToast({ title: tip, icon: 'none' })
      },
    })
  },

  // 豁免记录（GET /waiver?grade=；页面底部折叠区展示 + 撤销入口）
  loadWaivers() {
    if (!WARNING_WAIVER_URL || !this.data.activeGrade) return
    wx.request({
      url: WARNING_WAIVER_URL + `?grade=${encodeURIComponent(this.data.activeGrade)}`,
      header: { 'X-API-Key': WARNING_API_KEY },
      success: (res) => {
        if (res.statusCode !== 200 || !res.data || !Array.isArray(res.data.waivers)) return
        const names = {}
        this.data.checkList.forEach((s) => { names[s.student_id] = s.name || '' })
        const waiverList = res.data.waivers.map((w) => {
          const who = names[w.student_id]
            ? `${names[w.student_id]} ${w.student_id}` : w.student_id
          const time = (w.created_at || '').slice(0, 16)
          if (w.kind === 'course') {
            const src = w.grade_source ? `平替《${w.grade_source}》` : '免修'
            return {
              id: w.id, who, time,
              label: `课程豁免《${w.course_name || w.course_code || ''}》（${src}）`,
              sub: w.note ? `备注：${w.note}` : '',
            }
          }
          if (w.kind === 'grade') {
            return {
              id: w.id, who, time,
              label: `旧课豁免《${w.course_name || ''}》`,
              sub: w.note ? `备注：${w.note}` : '',
            }
          }
          return {
            id: w.id, who, time,
            label: `学分认可《${w.course_name || ''}》→ ${w.category || ''} ${fmtN(w.credit)} 学分`,
            sub: w.note ? `备注：${w.note}` : '',
          }
        })
        this.setData({ waiverList })
      },
      fail: () => {},   // 记录区为辅助信息，拉取失败静默
    })
  },

  // 撤销豁免（物理删除；成功后刷新记录）
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
          fail: () => this._fail(),
        })
      },
    })
  },

  _afterWaiverChange() { this.loadWaivers() },

  // 后端错误提取（message / detail 字符串 / pydantic detail 数组）
  _apiErr(r) {
    const d = r && r.data
    if (!d) return ''
    if (d.message) return String(d.message)
    if (typeof d.detail === 'string') return d.detail
    if (Array.isArray(d.detail)) return d.detail.map((x) => x.msg).join(';')
    return ''
  },

  toggleWaivers() { this.setData({ waiversOpen: !this.data.waiversOpen }) },
})
