// pages/trace/trace.js —— 执行轨迹（演示模式：暗色观测台）
//
// 数据源：默认回放内置录播数据（pages/trace/trace-fixture.js），也支持从本地缓存
// 读取「最近一次执行」（utils/trace 的 TRACE_KEY）——真实事件流接入后，同一份
// 播放逻辑由 tui_gateway 的 reasoning.available / skill.activate / tool.start /
// tool.complete 驱动，无需改动本页。
const { RUN_TRACE_URL } = require('../../config/api')
const { read } = require('../../utils/instance-keys')
const { DEMO_TRACE } = require('../../utils/trace-demo')
const { TRACE_KEY, STEP_DEFS, stepOf, formatClock } = require('../../utils/trace')

// 六拍步骤的角标（仅在进入该拍后显示）
const STEP_BADGE = { think: 'reasoning', reflect: 'reasoning', skill: '激活', flow: 'Bash', result: '完成' }

// Agent 拓扑的三个 skill 节点（与真实 skill 名一致）
const TOPO_NODES = [
  { key: 'training-plan-interpretation', label: '培养方案解读' },
  { key: 'multi-path-academic-planning', label: '多路径规划' },
  { key: 'academic-warning', label: '选课预警' },
]

const TICK_MS = 100          // 播放节拍（100ms 足够跟手，且不频繁 setData）
const PULSE_MS = 1800        // 命中 skill 后的脉冲时长（跑完即停，无常驻循环）
const TOKEN_BLOCKS = 20      // token 瀑布块数
const WAVE_BARS = 22         // 脑波条数

Page({
  data: {
    query: '',
    running: false,
    paused: false,
    done: false,
    demo: false,
    demoNote: '',
    live: false,
    elapsedText: '00:00.0',
    steps: [],
    scrollTo: '',
    skills: [],
    tools: [],
    files: [],
    result: null,
    metrics: { tools: 0, skills: 0, tokens: 0, seconds: 0 },
    stamped: false,
    waveBars: [],
  },

  onLoad(options) {
    wx.setNavigationBarTitle({ title: '执行轨迹' })
    const live = !!(options && (options.live === '1' || options.live === 1))
    this.setData({ live })

    if (live) {
      // 实时模式：不播内置/缓存轨迹，改为轮询后端「当前 run」的事件缓冲
      this._trace = { id: 'live', query: '', events: [], durationMs: 0 }
      this._steps = this._buildSteps()
      this._prepare()
      this.setData({
        query: '',
        steps: this._steps.slice(),
        waveBars: this._buildWave(),
        demo: false,
        demoNote: '实时执行 · 运行中',
      })
      this._initCanvas()
      this._startLive()
      return
    }

    this._trace = this._resolveTrace(options)
    this._steps = this._buildSteps()
    this._prepare()

    this.setData({
      query: this._trace.query || '',
      steps: this._steps.slice(),
      waveBars: this._buildWave(),
      demo: !!this._trace.demo,
      demoNote: this._trace.demo
        ? (this._trace.answerSource === 'reply'
          ? '演示回放 · 除结论外均为内置样例（真实结论取自本次回答）'
          : '演示回放 · 全过程与数据均为内置样例')
        : '',
    })

    this._initCanvas()
    this.start()
  },

  onHide() { this._stop(); this._stopLive() },
  onUnload() { this._stop(); this._stopLive() },

  // ── 轨迹来源：本地缓存的「最近一次执行」优先，否则用内置录播数据 ──
  _resolveTrace(options) {
    const wantId = options && options.id
    let cached = null
    try { cached = wx.getStorageSync(TRACE_KEY) } catch (e) { cached = null }
    const usable = cached && Array.isArray(cached.events) && cached.events.length
    if (usable && (!wantId || cached.id === wantId)) return cached
    return DEMO_TRACE
  },

  _buildSteps() {
    return STEP_DEFS.map((d, i) => ({
      key: d.key,
      idx: i + 1,
      icon: d.icon,
      title: d.title,
      badge: STEP_BADGE[d.key] || '',
      state: 'wait',
      cls: 'wait',
      summary: '',
      startT: null,
      manualOpen: false,
      text: '',
      tokens: 0,
      blocks: [],
    }))
  },

  // 预计算：两段思考各自的总字数与目标 token 数（让流式 token 计数与结果一致）
  _prepare() {
    const events = this._trace.events || []
    const firstToolDone = events.find((e) => e.type === 'tool.done')
    const cut = firstToolDone ? firstToolDone.t : Infinity
    let thinkChars = 0
    let reflectChars = 0
    for (const e of events) {
      if (e.type !== 'reasoning') continue
      if (e.t <= cut) thinkChars += (e.text || '').length
      else reflectChars += (e.text || '').length
    }
    const totalTokens = (this._trace.events.find((e) => e.type === 'result') || {}).metrics
    const target = (totalTokens && totalTokens.tokens) || 1284
    this._thinkChars = thinkChars || 1
    this._reflectChars = reflectChars || 1
    this._thinkTokens = Math.round(target * 0.7)
    this._reflectTokens = target - this._thinkTokens
    this._sparkSeed = 0
  },

  _buildWave() {
    const bars = []
    for (let i = 0; i < WAVE_BARS; i++) {
      const h = 26 + Math.round(Math.abs(Math.sin(i * 1.7)) * 62)
      bars.push({ h, d: (i * 0.09).toFixed(2) })
    }
    return bars
  },

  // ── 播放引擎 ────────────────────────────────────────
  start() {
    this._stop()
    this._reset()
    this._t0 = Date.now()
    this._idx = 0
    this._ticks = 0
    this._hasToolRun = false
    this._activeSkill = ''
    this.setData({ running: true, paused: false, done: false })
    this._timer = setInterval(() => this._tick(), TICK_MS)
  },

  _stop() {
    if (this._timer) { clearInterval(this._timer); this._timer = null }
    if (this._cuTimer) { clearInterval(this._cuTimer); this._cuTimer = null }
    this._stopPulse()
  },

  // ── 实时模式（运行中进全屏）：轮询后端缓冲，事件到达即渲染 ──
  _startLive() {
    this._stop()
    this._stopLive()
    this._reset()
    this._liveApplied = 0
    this._liveT0 = Date.now()
    this.setData({ running: true, paused: false, done: false })
    const tick = () => {
      if (this.data.done) { this._stopLive(); return }
      const token = read('SESSION_TOKEN_KEY')
      wx.request({
        url: RUN_TRACE_URL,
        method: 'GET',
        timeout: 5000,
        header: token ? { 'Authorization': `Bearer ${token}` } : {},
        success: (res) => {
          if (res.statusCode < 200 || res.statusCode >= 300) return
          this._applyLive(res.data || {})
        },
      })
    }
    this._liveTimer = setInterval(tick, 1000)
    tick()
  },

  _stopLive() {
    if (this._liveTimer) { clearInterval(this._liveTimer); this._liveTimer = null }
  },

  // 把后端快照增量并入页面：只 _apply 尚未处理的新事件
  _applyLive(data) {
    if (data.query && !this.data.query) this.setData({ query: data.query })
    const events = Array.isArray(data.events) ? data.events : []
    this._trace.events = events
    this._trace.durationMs = data.elapsedMs || 0
    this._prepare()
    for (let i = this._liveApplied; i < events.length; i++) {
      this._apply(events[i])
    }
    this._liveApplied = events.length
    this.setData({
      steps: this._steps.slice(),
      elapsedText: formatClock(data.elapsedMs || 0),
    })
    if (!data.running) {
      this._stopLive()
      const totalMs = Number(this._trace.durationMs) || (Date.now() - this._liveT0)
      this.setData({ demoNote: '' })
      this._finish(totalMs)
    }
  },

  _reset() {
    this._skills = []
    this._tools = []
    this._files = []
    this._result = null
    this._stamped = false
    this._finalMetrics = null
    this._activeKey = ''
    this._metrics = { tools: 0, skills: 0, tokens: 0, seconds: 0 }
    this._steps = this._buildSteps()
    this.setData({
      steps: this._steps.slice(),
      skills: [],
      tools: [],
      files: [],
      result: null,
      metrics: this._metrics,
      stamped: false,
      paused: false,
      elapsedText: '00:00.0',
      })
  },


  _tick() {
    const el = Date.now() - this._t0
    const events = this._trace.events || []
    let touched = false
    while (this._idx < events.length && events[this._idx].t <= el) {
      this._apply(events[this._idx])
      this._idx++
      touched = true
    }
    if (touched) this.setData({ steps: this._steps.slice() })

    this._ticks++
    if (this._ticks % 2 === 0) this.setData({ elapsedText: formatClock(el) })

    if (this._idx >= events.length) this._finish(el)
  },

  // 单条事件 → UI 状态
  _apply(ev) {
    const stepKey = stepOf(ev, this._hasToolRun)
    if (stepKey) this._setActive(stepKey, ev.t)

    switch (ev.type) {
      case 'reasoning': {
        const st = this._step(stepKey)
        st.text += ev.text || ''
        const isThink = stepKey === 'think'
        const totalChars = isThink ? this._thinkChars : this._reflectChars
        const targetTokens = isThink ? this._thinkTokens : this._reflectTokens
        st.tokens = Math.min(targetTokens, Math.round(targetTokens * (st.text.length / totalChars)))
        st.blocks = this._blocksFor(st.text.length / totalChars)
        break
      }
      case 'skill': {
        // 一个 run 可能命中多个 skill：按名字去重后追加，列表展示
        const name = ev.name || ''
        if (name && !this._skills.some((s) => s.name === name)) {
          this._skills.push({ name, description: ev.description || '' })
        }
        this._activeSkill = name
        this.setData({ skills: this._skills.slice() })
        this._pulse()
        this._vibrate()
        break
      }
      case 'tool.start':
        this._tools.push({ title: ev.title || ev.tool || '工具调用', state: 'run', durationText: '' })
        this.setData({ tools: this._tools.slice() })
        break
      case 'tool.done': {
        const t = this._tools.find((x) => x.title === (ev.title || '') && x.state === 'run')
        if (t) {
          t.state = 'done'
          t.durationText = ev.duration_s != null ? `${ev.duration_s}s` : ''
        }
        this._hasToolRun = true
        this.setData({ tools: this._tools.slice() })
        break
      }
      case 'files':
        this._files = (ev.files || []).map((name) => ({ name }))
        this.setData({ files: this._files.slice() })
        break
      case 'result':
        this._result = ev
        this.setData({ result: ev })
        this._finalMetrics = this._resolveMetrics(ev)
        this._countUp(this._finalMetrics)
        this._stamped = true
        this.setData({ stamped: true })
        break
      default:
        break
    }
  },

  _step(key) {
    return this._steps.find((s) => s.key === key) || this._steps[0]
  },

  // 进入某一拍：此前全部标记完成（并生成一行摘要），其后保持未开始。
  // 时间轴是一条单向推进的主干；已完成步骤收成一行，避免页面无限变长。
  _setActive(key, t) {
    // 真流式思考会高频到达，_setActive 每拍都会被调用；若每次 setData scrollTo，
    // 滚动条会被持续钉在该步骤，用户无法手动上下滑。只在「进入新的一拍」时滚动。
    const entered = this._activeKey !== key
    this._activeKey = key
    const order = STEP_DEFS.map((d) => d.key)
    const at = order.indexOf(key)
    this._steps.forEach((s, i) => {
      if (i < at) {
        if (s.state !== 'done') {
          s.state = 'done'
          s.summary = this._summaryOf(s, t)
        }
      } else if (i === at) {
        if (s.state !== 'run') {
          s.state = 'run'
          s.startT = t
        }
      } else if (s.state !== 'wait') {
        s.state = 'wait'
      }
      s.cls = s.state === 'done' ? 'done' : (s.state === 'run' ? 'run' : 'wait')
    })
    if (entered) this.setData({ scrollTo: `step-${key}` })
  },

  // 已完成步骤的一行摘要（tokens / 步数 / 耗时 —— 收尾有账可查）
  _summaryOf(s, t) {
    const dur = s.startT != null && t != null ? (t - s.startT) / 1000 : 0
    const d = dur >= 0.3 ? `${dur.toFixed(1)}s` : ''
    switch (s.key) {
      case 'think':
        return [s.tokens ? `${s.tokens} tok` : '', d].filter(Boolean).join(' · ')
      case 'skill':
        if (!this._skills.length) return ''
        return this._skills.length > 1 ? `${this._skills.length} 个` : this._skills[0].name
      case 'flow':
        return [`${this._tools.length} 步`, d].filter(Boolean).join(' · ')
      case 'reflect':
        return d
      case 'result':
        return this._result && Array.isArray(this._result.artifacts)
          ? `${this._result.artifacts.length} 项产出`
          : ''
      default:
        return ''
    }
  },

  _blocksFor(ratio) {
    const filled = Math.round(Math.min(1, ratio) * TOKEN_BLOCKS)
    const out = []
    for (let i = 0; i < TOKEN_BLOCKS; i++) {
      out.push(i < filled - 1 ? 'on' : (i < filled ? 'fade' : ''))
    }
    return out
  },

  _finish(el) {
    this._stop()
    const totalMs = Number(this._trace.durationMs) || el
    // 真实轨迹的最后一条事件就是 result（后端不追加 end），_finish 会在同一拍
    // 紧跟 _apply(result) 触发；而 _stop() 会清掉数字跳跃的定时器，导致指标停在
    // 初始的 0。这里用最终值兜底，并让「耗时」与顶部总耗时保持一致。
    if (this._finalMetrics) {
      const m = Object.assign({}, this._finalMetrics)
      m.seconds = Math.round((totalMs / 1000) * 10) / 10
      this._finalMetrics = m
      this.setData({ metrics: m })
    }
    this._steps.forEach((s) => {
      if (s.state === 'run') {
        s.state = 'done'
        s.summary = this._summaryOf(s, totalMs) || s.summary
      }
      s.cls = s.state === 'done' ? 'done' : 'wait'
    })
    this.setData({
      steps: this._steps.slice(),
      running: false,
      done: true,
      elapsedText: formatClock(totalMs),
      scrollTo: 'step-result',
    })
    this._drawTopo()
  },


  // 指标：后端给了就用真值；没给（真实轨迹）就从事件本身算 ——
  // 工具数 / Skill 数 / 总耗时都能算出来，tokens 网关不产生 → 显示「—」而不是编一个。
  _resolveMetrics(ev) {
    const m = (ev && ev.metrics) || {}
    const events = this._trace.events || []
    return {
      tools: m.tools != null ? m.tools : events.filter((e) => e.type === 'tool.start').length,
      skills: m.skills != null ? m.skills : events.filter((e) => e.type === 'skill').length,
      tokens: m.tokens != null ? m.tokens : null,
      seconds: m.seconds != null
        ? m.seconds
        : Math.round(((Number(this._trace.durationMs) || 0) / 1000) * 10) / 10,
    }
  },

  // ── 指标数字跳表（收尾的「结算感」）──
  _countUp(target) {
    if (this._cuTimer) clearInterval(this._cuTimer)
    const t0 = Date.now()
    const DUR = 700
    this._cuTimer = setInterval(() => {
      const p = Math.min(1, (Date.now() - t0) / DUR)
      const e = 1 - Math.pow(1 - p, 3)
      const m = {}
      Object.keys(target).forEach((k) => {
        const v = target[k]
        if (v === null || v === undefined) { m[k] = null; return }
        const n = Number(v) || 0
        m[k] = n >= 100 ? Math.round(n * e) : Math.round(n * e * 10) / 10
      })
      this.setData({ metrics: m })
      if (p >= 1) { clearInterval(this._cuTimer); this._cuTimer = null }
    }, 60)
  },

  // ── Agent 拓扑（canvas 2d）───────────────────────────
  _initCanvas() {
    let query
    try {
      query = wx.createSelectorQuery().in(this)
      query.select('#topo').fields({ node: true, size: true }).exec((res) => {
        const item = res && res[0]
        if (!item || !item.node || !item.width) return
        const canvas = item.node
        const ctx = canvas.getContext('2d')
        let dpr = 2
        try {
          dpr = (wx.getWindowInfo ? wx.getWindowInfo().pixelRatio : 2) || 2
        } catch (e) { dpr = 2 }
        canvas.width = item.width * dpr
        canvas.height = item.height * dpr
        ctx.scale(dpr, dpr)
        this._canvas = canvas
        this._ctx = ctx
        this._cw = item.width
        this._ch = item.height
        this._drawTopo()
      })
    } catch (e) {
      // 拓扑画不出不影响其它内容
    }
  },

  _roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath()
    ctx.moveTo(x + r, y)
    ctx.lineTo(x + w - r, y)
    ctx.arcTo(x + w, y, x + w, y + r, r)
    ctx.lineTo(x + w, y + h - r)
    ctx.arcTo(x + w, y + h, x + w - r, y + h, r)
    ctx.lineTo(x + r, y + h)
    ctx.arcTo(x, y + h, x, y + h - r, r)
    ctx.lineTo(x, y + r)
    ctx.arcTo(x, y, x + r, y, r)
    ctx.closePath()
  },

  // pulse: undefined = 静态帧；0..1 = 脉冲位置
  _drawTopo(pulse) {
    const ctx = this._ctx
    if (!ctx) return
    const W = this._cw
    const H = this._ch
    ctx.clearRect(0, 0, W, H)

    const agentW = Math.min(W * 0.5, 160)
    const agentH = 34
    const ax = (W - agentW) / 2
    const ay = 4
    const nodeW = Math.min(W * 0.28, 100)
    const nodeH = 28
    const ny = H - nodeH - 4
    const xs = [W * 0.16, W * 0.5, W * 0.84]
    // 命中的 skill 可能不止一个：全部点亮；脉冲只跑最新命中的那个
    const hit = new Set((this._skills || []).map((s) => s.name))
    const pulseIdx = TOPO_NODES.findIndex((n) => n.key === this._activeSkill)
    const anyHit = hit.size > 0
    const midY = ay + agentH + 16

    // 连线 + 脉冲
    const self = this
    xs.forEach((nx, i) => {
      const on = hit.has(TOPO_NODES[i].key)
      ctx.beginPath()
      ctx.moveTo(W / 2, ay + agentH)
      ctx.lineTo(W / 2, midY)
      ctx.lineTo(nx, midY)
      ctx.lineTo(nx, ny)
      ctx.strokeStyle = on ? '#76B900' : '#243447'
      ctx.lineWidth = on ? 1.6 : 1
      ctx.stroke()

      if (i === pulseIdx && pulse != null) {
        const seg1 = midY - (ay + agentH)
        const seg2 = Math.abs(nx - W / 2)
        const seg3 = ny - midY
        const total = seg1 + seg2 + seg3
        let d = pulse * total
        let px = W / 2
        let py = ay + agentH + Math.min(d, seg1)
        if (d > seg1) {
          d -= seg1
          px = W / 2 + (nx - W / 2) * Math.min(1, d / (seg2 || 1))
          py = midY
          if (d > seg2) {
            d -= seg2
            py = midY + Math.min(d, seg3)
          }
        }
        ctx.beginPath()
        ctx.arc(px, py, 3.4, 0, Math.PI * 2)
        ctx.fillStyle = '#76B900'
        ctx.fill()
        self._roundRect(ctx, nx - nodeW / 2 - 5, ny - 5, nodeW + 10, nodeH + 10, 12)
        ctx.strokeStyle = 'rgba(118,185,0,' + (0.32 * (1 - Math.abs(pulse - 0.5) * 2)).toFixed(2) + ')'
        ctx.lineWidth = 1
        ctx.stroke()
      }
    })

    // Agent 节点
    this._roundRect(ctx, ax, ay, agentW, agentH, 9)
    ctx.fillStyle = '#1B2A3B'
    ctx.fill()
    ctx.strokeStyle = anyHit ? '#76B900' : '#243447'
    ctx.lineWidth = 1
    ctx.stroke()
    ctx.fillStyle = '#E6EDF5'
    ctx.font = '600 13px serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText('教务智能体', W / 2, ay + agentH / 2 - 2)
    ctx.fillStyle = anyHit ? '#76B900' : '#5C6E80'
    ctx.font = '9px sans-serif'
    ctx.fillText(anyHit ? 'active' : 'idle', W / 2, ay + agentH / 2 + 12)

    // skill 节点
    xs.forEach((nx, i) => {
      const on = hit.has(TOPO_NODES[i].key)
      this._roundRect(ctx, nx - nodeW / 2, ny, nodeW, nodeH, 8)
      ctx.fillStyle = on ? 'rgba(118,185,0,0.14)' : '#14202F'
      ctx.fill()
      ctx.strokeStyle = on ? '#76B900' : '#243447'
      ctx.lineWidth = 1
      ctx.stroke()
      ctx.fillStyle = on ? '#76B900' : '#5C6E80'
      ctx.font = '10px sans-serif'
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText(TOPO_NODES[i].label, nx, ny + nodeH / 2 - 1)
      if (on) {
        ctx.font = '8px sans-serif'
        ctx.fillText(TOPO_NODES[i].key, nx, ny + nodeH / 2 + 9)
      }
    })
  },

  // 命中 skill：脉冲沿连线跑一遍（事件驱动，跑完即停 —— 无常驻动画循环）
  _pulse() {
    const canvas = this._canvas
    if (!canvas || typeof canvas.requestAnimationFrame !== 'function') {
      this._drawTopo()
      return
    }
    this._stopPulse()
    const start = Date.now()
    const step = () => {
      const p = (Date.now() - start) / PULSE_MS
      if (p >= 1) {
        this._raf = null
        this._drawTopo()
        return
      }
      this._drawTopo(p)
      this._raf = canvas.requestAnimationFrame(step)
    }
    this._raf = canvas.requestAnimationFrame(step)
  },

  _stopPulse() {
    if (this._raf && this._canvas && typeof this._canvas.cancelAnimationFrame === 'function') {
      this._canvas.cancelAnimationFrame(this._raf)
    }
    this._raf = null
  },

  _vibrate() {
    try {
      if (wx.vibrateShort) wx.vibrateShort({ type: 'medium' })
    } catch (e) {
      // 部分机型/开发者工具不支持，忽略
    }
  },

  // ── 演示控制 ────────────────────────────────────────
  tapReplay() {
    this.start()
  },

  // 暂停 / 继续：现场可以停下来逐拍讲，或让用户停留细看
  tapPause() {
    if (this.data.done) return
    if (this.data.paused) {
      this._t0 = Date.now() - (this._pausedElapsed || 0)
      this._timer = setInterval(() => this._tick(), TICK_MS)
      this.setData({ paused: false, running: true })
      return
    }    this._pausedElapsed = Date.now() - this._t0
    this._stop()
    this.setData({ paused: true, running: false })
  },

  // 点步骤：就地展开 / 收起（暂停后可以逐拍细看，不影响播放进度）
  tapStep(e) {
    const key = e.currentTarget.dataset.key
    const i = this._steps.findIndex((s) => s.key === key)
    if (i < 0) return
    this._steps[i].manualOpen = !this._steps[i].manualOpen
    this.setData({ steps: this._steps.slice() })
  },

  // 下一拍：直接跳到下一个「拍点」（命中 skill / 工具 / 结果），现场控场用
  tapNextBeat() {
    if (this.data.done) return
    const events = this._trace.events || []
    const el = Date.now() - this._t0
    const next = events.find((e, i) => i >= this._idx &&
      (e.type === 'skill' || e.type === 'tool.start' || e.type === 'result'))
    const target = next ? next.t : (Number(this._trace.durationMs) || events.length ? (events[events.length - 1] || {}).t : el)
    this._t0 = Date.now() - Math.max(el + 30, target)
  },

  tapBack() {
    wx.navigateBack({ delta: 1 })
  },
})
