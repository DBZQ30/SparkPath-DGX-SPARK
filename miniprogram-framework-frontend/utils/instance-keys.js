// utils/instance-keys.js —— 实例命名的本地存储键
// 所有页面 / App 统一从这里取存储键，键名前缀由 config/instance.js 的 INSTANCE_ID 派生，
// 保证各实例的本地缓存互不污染（架构原则：共享代码零硬编码）。
//
// 历史：早期版本在共享代码里硬编码了 'mba_admission_*' 前缀。该前缀的迁移逻辑已随
// 多助手清理一并移除（2026-09，本仓库只保留本科新生学业规划智能助手）；若仍有极早期客户端带着
// 旧键，重新登录一次即可，不影响数据。
'use strict'

const { INSTANCE_ID } = require('../config/instance')

// 实例前缀（即命名空间）：jwc-assistant
const PREFIX = INSTANCE_ID

// 键名（按实例派生）
const KEYS = {
  TEMP_USER_ID_KEY: `${PREFIX}_temp_user_id`,
  OPENID_KEY: `${PREFIX}_openid`,
  USER_ID_KEY: `${PREFIX}_user_id`,
  SESSION_ID_KEY: `${PREFIX}_session_id`,
  SESSION_TOKEN_KEY: `${PREFIX}_session_token`,
  USERNAME_KEY: `${PREFIX}_username`,
}

// 读键
function read(key) {
  return wx.getStorageSync(KEYS[key])
}

module.exports = { PREFIX, KEYS, read }
