// pages/jxtz-sync —— 教务通知同步管理（仅学业规划助手，admin/owner）
// 开关 / 每日时间 / 立即同步 / 最近运行。文案不暴露 cron/job/systemd 等后端术语。
const { METHODS_JXTZ_SYNC } = require('../../config/api')
const { read } = require('../../utils/instance-keys')

Page({
  data: {
    loading: true,
    loadFailed: false,
    jobMissing: false,
    enabled: false,
    scheduleTime: '',
    nextRunAt: '',
    recentRuns: [],
    running: false,
    statusText: '',
    statusTone: '',
  },

  onLoad() {
    const app = getApp()
    let token = read('SESSION_TOKEN_KEY') || ''
    if (!token && app && app.globalData) token = app.globalData.sessionToken || ''
    this._token = token
    this._load()
  },

  onShow() { if (this._token) this._load() },

  _request(path, method, data, onOk, onErr) {
    wx.request({
      url: METHODS_JXTZ_SYNC + (path || ''),
      method: method || 'GET',
      timeout: path === '/run' ? 60000 : 15000,
      header: { 'Authorization': `Bearer ${this._token}`, 'content-type': 'application/json' },
      data: data || {},
      success: (res) => {
        if (res.statusCode >= 200 && res.statusCode < 300) { onOk(res.data || {}); return }
        onErr(res.statusCode, res.data || {})
      },
      fail: () => onErr(0, {}),
    })
  },

  _load() {
    if (!this._token) { this.setData({ loading: false }); return }
    this.setData({ loading: true, loadFailed: false, jobMissing: false })
    this._request('', 'GET', null, (d) => {
      this.setData({
        loading: false, loadFailed: false, jobMissing: false,
        enabled: !!d.enabled,
        scheduleTime: d.schedule_time || '',
        nextRunAt: this._fmt(d.next_run_at),
        recentRuns: (d.recent_runs || []).map((r) => this._decorate(r)),
      })
    }, (code) => {
      if (code === 404) { this.setData({ loading: false, jobMissing: true }); return }
      this.setData({ loading: false, loadFailed: true })
    })
  },

  // ISO（带时区）→ MM-DD HH:mm；解析失败原样返回
  _fmt(iso) {
    if (!iso) return ''
    const dt = new Date(iso)
    if (isNaN(dt.getTime())) return String(iso)
    const p = (n) => (n < 10 ? '0' + n : '' + n)
    return `${p(dt.getMonth() + 1)}-${p(dt.getDate())} ${p(dt.getHours())}:${p(dt.getMinutes())}`
  },

  _resultText(rec) {
    const ok = rec.ok || 0, fail = rec.fail || 0, skip = rec.skip || 0
    if (rec.status === 'no_new') return '无新增'
    if (rec.status === 'ok') return `新增 ${ok} 条` + (skip > 0 ? `，${skip} 条已在库` : '')
    if (rec.status === 'partial') return `同步完成，${fail} 条未入库`
    if (rec.status === 'failed') return '同步失败：' + (rec.error || '请查看服务器日志')
    return rec.status || '未知'
  },

  _decorate(rec) {
    const tone = (rec.status === 'ok' || rec.status === 'no_new') ? 'tag-ok'
      : (rec.status === 'partial' ? 'tag-partial' : 'tag-fail')
    return {
      ...rec,
      timeText: this._fmt(rec.run_at),
      resultText: this._resultText(rec),
      tone,
      error: rec.error || '',
      manual: rec.trigger === 'manual',
    }
  },

  onToggle(e) {
    const enabled = !!e.detail.value
    this._request('/toggle', 'POST', { enabled }, (d) => {
      this.setData({ enabled: !!d.enabled, nextRunAt: this._fmt(d.next_run_at) })
      wx.showToast({ title: enabled ? '已开启' : '已关闭', icon: 'none' })
    }, () => {
      wx.showToast({ title: '操作失败', icon: 'none' })
      this._load()   // 回滚 UI 状态
    })
  },

  onPickTime(e) {
    const time = e.detail.value
    this._request('/schedule', 'POST', { time }, (d) => {
      this.setData({ scheduleTime: d.schedule_time || time, nextRunAt: this._fmt(d.next_run_at) })
      wx.showToast({ title: '已更新', icon: 'success' })
    }, (code) => {
      wx.showToast({ title: code === 400 ? '时间格式不正确' : '更新失败', icon: 'none' })
    })
  },

  tapRun() {
    if (this.data.running) return
    this.setData({ running: true, statusText: '', statusTone: '' })
    this._request('/run', 'POST', null, (d) => {
      const rec = d.result || {}
      this.setData({ running: false, statusText: this._resultText(rec),
                     statusTone: this._tone(rec.status) })
      this._load()
    }, (code, d) => {
      if (code === 409) {
        this.setData({ running: false, statusText: '已有同步正在进行', statusTone: 'tag-partial' }); return
      }
      if (code === 504) {
        this.setData({ running: false, statusText: '同步超时（结果未知）', statusTone: 'tag-fail' })
        this._load(); return
      }
      if (code === 0) {
        this.setData({ running: false, statusText: '同步仍在进行，请稍后刷新查看结果',
                       statusTone: 'tag-partial' }); return
      }
      const detail = (d && d.detail) || ''
      this.setData({ running: false, statusText: '同步失败：' + (detail || '请查看最近运行'),
                     statusTone: 'tag-fail' })
      this._load()
    })
  },

  _tone(status) {
    if (status === 'ok' || status === 'no_new') return 'tag-ok'
    if (status === 'partial') return 'tag-partial'
    return 'tag-fail'
  },
})
