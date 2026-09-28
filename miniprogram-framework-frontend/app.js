const { METHODS_UNREAD } = require('config/api')
const { FEATURES } = require('config/instance')
const { KEYS, read } = require('utils/instance-keys')

App({
  globalData: {},

  onLaunch() { this._updateBadge() },
  onShow() { this._updateBadge() },

  _updateBadge() {
    if (FEATURES.badge === false) return // 实例关闭了未读角标
    const t = read('SESSION_TOKEN_KEY')
    if (!t) return
    wx.request({
      url: METHODS_UNREAD, method: 'GET', timeout: 10000,
      header: { 'Authorization': `Bearer ${t}` },
      success: (res) => {
        if (res.statusCode === 401) { wx.removeStorageSync(KEYS.SESSION_TOKEN_KEY); return }
        const n = (res.data && res.data.unread) || 0
        if (n > 0) wx.setTabBarBadge({ index: 1, text: String(n) })
        else wx.removeTabBarBadge({ index: 1 })
      },
      fail: () => {},
    })
  },
})
