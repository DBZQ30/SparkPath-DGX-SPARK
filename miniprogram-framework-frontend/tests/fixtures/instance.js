// tests/fixtures/instance.js —— config/instance.js 的 jest 替身
// 真文件是 scripts/build.js 的构建产物（gitignored，克隆后 npm run switch 才生成），
// 且内容随开发者当前切换的实例变化；单测需要确定性的输入，故用 moduleNameMapper
// 把所有对 config/instance 的 require 映射到这里。
// 注意：这里是受控夹具——API_BASE_URL 故意带尾斜杠（测 api.js 去斜杠逻辑），
// PLAN_API_BASE 留空（测「功能未启用 → URL 为空串」的派生分支）。
'use strict'

module.exports = {
  INSTANCE_ID: 'jest-instance',
  ASSISTANT_TITLE: 'Jest 测试实例',
  WELCOME_TEXT: '你好',
  CHAT_PLACEHOLDER: '输入问题',
  API_BASE_URL: 'https://api.example.test/dgx-agentapi/',
  WARNING_API_BASE: 'https://api.example.test/warning/',
  WARNING_API_KEY: '',
  PLAN_API_BASE: '',
  PLAN_API_KEY: '',
  FEATURES: {
    badge: true,
    customPages: [],
    phoneAuthMode: 'manual',
    jxtzSync: false,
    plan: false,
  },
  UI_TEXT: {},
}
