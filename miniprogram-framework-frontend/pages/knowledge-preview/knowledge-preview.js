// pages/knowledge-preview —— 原文只读预览（设计 §4.7.2 / §5，v1.5）
// 入库后不保留原始文件，这里展示的是从知识片段按顺序拼回的文本（最多前 2 万字）。
const { METHODS_KNOWLEDGE_CONTENT } = require('../../config/api')
const { read } = require('../../utils/instance-keys')

Page({
  data: {
    loading: true,
    loadFailed: false,
    displayName: '',
    text: '',
    truncated: false,
  },

  onLoad(options) {
    const app = getApp()
    let token = read('SESSION_TOKEN_KEY') || ''
    if (!token && app && app.globalData) token = app.globalData.sessionToken || ''
    this._token = token
    this._scope = decodeURIComponent(options.scope || '')
    this._filename = decodeURIComponent(options.filename || '')
    if (!token || !this._scope || !this._filename) {
      this.setData({ loading: false, loadFailed: true }); return
    }
    this._load()
  },

  _load() {
    this.setData({ loading: true, loadFailed: false })
    wx.request({
      url: METHODS_KNOWLEDGE_CONTENT, method: 'GET', timeout: 30000,
      header: { 'Authorization': `Bearer ${this._token}` },
      data: { scope: this._scope, filename: this._filename },
      success: (res) => {
        const d = res.data || {}
        if (res.statusCode === 404) {
          // 文件已被删除：提示后返回列表（列表页 onShow 会刷新）
          wx.showToast({ title: '文件已不存在，已为你刷新', icon: 'none' })
          setTimeout(() => wx.navigateBack(), 1200)
          return
        }
        if (res.statusCode < 200 || res.statusCode >= 300) {
          this.setData({ loading: false, loadFailed: true }); return
        }
        this.setData({
          loading: false, loadFailed: false,
          displayName: d.display_name || this._filename,
          text: d.text || '',
          truncated: !!d.truncated,
        })
      },
      fail: () => this.setData({ loading: false, loadFailed: true }),
    })
  },

  retry() { this._load() },
})
