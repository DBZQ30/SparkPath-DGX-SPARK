// tests/setup.js —— jest setupFiles：注入 wx 全局桩
// 纯 Node 环境没有小程序运行时；被测共享模块（utils/instance-keys 等）在调用期
// 会触到 wx.getStorageSync / wx.request，这里给一个内存实现。
'use strict'

function createWxStub() {
  const storage = new Map()
  const requests = []
  const stub = {
    __storage: storage,
    __requests: requests,
    getStorageSync: (k) => (storage.has(k) ? storage.get(k) : ''),
    setStorageSync: (k, v) => storage.set(k, v),
    removeStorageSync: (k) => storage.delete(k),
    clearStorageSync: () => storage.clear(),
    // 请求桩：只记录，不真正发网络；未传 fail 回调时静默（调用方应处理）
    request: (opts) => {
      requests.push(opts)
      if (opts && typeof opts.fail === 'function') {
        opts.fail({ errMsg: 'request:fail stubbed in jest' })
      }
    },
  }
  return stub
}

global.wx = createWxStub()
