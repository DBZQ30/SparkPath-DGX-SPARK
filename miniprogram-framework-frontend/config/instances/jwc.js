// config/instances/jwc.js —— 本科新生学业规划智能助手（当前线上实例）
// 唯一事实来源：改配置改这里，然后执行 node scripts/switch-instance.js jwc
//
// 密钥不入库：从同目录 gitignored 的 jwc.local.js 读取（本地开发/构建时提供），
// 模板见 jwc.local.js.example。未提供时为空字符串（对应功能入口自动隐藏）。
let SECRETS = {}
try {
  SECRETS = require('./jwc.local')
} catch (e) {
  SECRETS = {}
}

module.exports = {
  APPID: 'wx575fe5fb99d7b4de',
  INSTANCE_ID: 'jwc-assistant',
  ASSISTANT_TITLE: '本科学业规划智能助手',
  WELCOME_TEXT: '你好，我是西安交通大学管理学院本科学业规划智能助手，通过小程序为本科生、教师和教务管理员提供学业规划指导和教务咨询服务。我的知识来源于管理学院和学校官方发布的培养方案、课程信息、学分规定、政策文件等正式文档。',
  CHAT_PLACEHOLDER: '输入您的问题',
  // dgx 灰度路由：校园网关 -> acc-svr nginx -> 反向隧道 -> dgx 问答管线
  // 见 deploy/dgx/dgx-agentapi.conf 与 deploy/dgx/README.md
  API_BASE_URL: 'https://isom.xjtu.edu.cn/accapi/dgx-agentapi/',
  // 学业预警 HTTP 服务（007 设计）：dgx :8008，经 /accapi/dgx-warning 灰度路由暴露
  // 见 deploy/dgx/dgx-warning.conf。前端会拼出 /api/warning/status、/api/warning/upload 等。
  // WARNING_API_BASE 空值 = 功能未启用（前端隐藏入口）。
  WARNING_API_BASE: 'https://isom.xjtu.edu.cn/accapi/dgx-warning',
  WARNING_API_KEY: SECRETS.WARNING_API_KEY || '',
  // 培养方案智能解读 HTTP 服务（004 设计）：与学业预警同服务不同路由，
  // 经 /accapi/dgx-plan 灰度路由暴露（见 deploy/dgx/dgx-plan.conf）。
  // 空值 = 功能未启用（前端隐藏入口与页面）。
  PLAN_API_BASE: 'https://isom.xjtu.edu.cn/accapi/dgx-plan',
  PLAN_API_KEY: SECRETS.PLAN_API_KEY || '',
  FEATURES: {
    badge: true,     // 未读角标
    warning: true,   // 学业预警模块（管理员上传文件 + 触发计算，007 设计）
    plan: true,      // 培养方案智能解读（学生解读页 + 管理员管理页，004 设计）
    planRoute: true, // 多路径个性化学业规划（学生学业规划页，010 设计）
    docCenter: true, // 文件中心（管理员统一上传与适用性管理，007 设计）
    jxtzSync: true,  // 教务通知同步管理（仅学业规划助手）
    customPages: [], // 本实例专属页面，如 ['pages/_ext/jwc/xxx']
    // 当前小程序未开通 getPhoneNumber 权限（errno 102），用手动绑定；
    // 认证并开通权限后改为 'wechat'
    phoneAuthMode: 'manual',
  },
  UI_TEXT: { // 教务语境的角色文案（与 scripts/build.js 的 DEFAULT_UI_TEXT 一致，此处显式列出便于阅读）
    ROLE_TEACHER: '教师',
    APPLY_TEACHER_DESC: '申请成为教师',
    PENDING_TEACHER_AUTH: '待审核的教师申请列表',
    AUTH_TEACHER_LABEL: '教师',
    ALREADY_TEACHER: '已是教师',
  },
}
