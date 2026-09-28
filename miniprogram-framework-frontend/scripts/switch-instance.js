// scripts/switch-instance.js —— 开发时切换实例
// 用法: node scripts/switch-instance.js <instanceId>
//       不带参数时使用 config/instances/_default.js（当前默认=教务）
// 示例: node scripts/switch-instance.js jwc
'use strict'

const { buildInstance } = require('./build')

const id = process.argv[2] || process.env.INSTANCE || '_default'
const cfg = buildInstance(id)

console.log('\n[switch] 切换完成，当前实例摘要:')
console.log(`  ├─ 实例     : ${id}`)
console.log(`  ├─ 助手名称 : ${cfg.ASSISTANT_TITLE}`)
console.log(`  ├─ AppID    : ${cfg.APPID}`)
console.log(`  ├─ API      : ${cfg.API_BASE_URL}`)
console.log(`  ├─ 功能开关 : ${Object.keys(cfg.FEATURES).filter((k) => cfg.FEATURES[k] === true).join(', ') || '无'}`)
console.log('  └─ 提示     : 微信开发者工具若提示项目配置变更，点确定重载')
console.log('               切换了 AppID 的小程序，需在其后台确认 request 合法域名已配置')
