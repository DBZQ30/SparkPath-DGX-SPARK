// utils/grades.js —— 年级注册表读取（唯一事实来源：warning 服务的 grade_master）
//
// 培养方案解读 / 学业规划 / 培养方案管理 / 入库文件与适用年级 / 文件中心
// 的年级选项统一从这里取，不再各自硬编码；注册表不可用时回退内置默认值，
// 保证离线/未启用预警时页面仍可用。
'use strict'

const { WARNING_GRADES_URL } = require('../config/api')
const { WARNING_API_KEY } = require('../config/instance')

// 仅作「注册表不可用」时的兜底，不是事实来源；请勿在此增删年级。
const FALLBACK_GRADES = ['2023级', '2024级', '2025级', '2026级']

function fetchGrades(cb) {
  if (!WARNING_GRADES_URL) { cb(FALLBACK_GRADES.slice()); return }
  wx.request({
    url: WARNING_GRADES_URL,
    header: { 'X-API-Key': WARNING_API_KEY },
    success: (res) => {
      const grades = (res.data && res.data.grades) || []
      cb(grades.length ? grades.slice() : FALLBACK_GRADES.slice())
    },
    fail: () => cb(FALLBACK_GRADES.slice()),
  })
}

module.exports = { FALLBACK_GRADES, fetchGrades }
