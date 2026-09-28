// scripts/build.js —— 多实例组装引擎（switch-instance / release 共用）
// 根据 config/instances/<id>.js 生成/改写：
//   1. config/instance.js      （运行时配置，被 api.js / chatbot.js 等消费）
//   2. project.config.json     （改写 appid，其余字段保留）
//   3. app.json                （pages / tabBar / 全局标题）
//   4. assets/instances/<id>/**（镜像复制到项目根目录，覆盖同名文件，用于 tabBar 图标等差异化静态资源）
// 用法: node scripts/build.js <instanceId> [--dry-run]
'use strict'

const fs = require('fs')
const path = require('path')

const ROOT = path.resolve(__dirname, '..')
const INSTANCES_DIR = path.join(ROOT, 'config', 'instances')
const OUT_INSTANCE_FILE = path.join(ROOT, 'config', 'instance.js')
const PROJECT_CONFIG_FILE = path.join(ROOT, 'project.config.json')
const APP_JSON_FILE = path.join(ROOT, 'app.json')
const ASSETS_DIR = path.join(ROOT, 'assets', 'instances')

// 共享页面：所有实例共有的 app.json pages（新增共享页面在这里加）
const SHARED_PAGES = [
  'pages/chatbot/chatbot',
  'pages/methods/methods',
  'pages/user/user',
  'pages/list/list',
  'pages/profile/profile',
  'pages/phone-whitelist/phone-whitelist',
  'pages/student-archive/student-archive',   // 学生档案管理（011：学籍信息检索/编辑，仅 admin/owner）
  'pages/knowledge/knowledge',   // 知识库管理（知识库删除设计：按角色范围列表 / 删除 / 批量 / 孤儿扫描 / 操作历史）
  'pages/knowledge-preview/knowledge-preview',   // 原文只读预览（知识库删除设计 v1.5：从知识片段拼回文本）
  'pages/warning/warning',   // 学业预警模块（007 设计，FEATURES.warning + 管理员角色显隐）
  'pages/warning-detail/warning-detail',   // 预警详情页（详情 navigateTo 进入，返回回列表）
  'pages/plan/plan',   // 培养方案解读（004 设计，学生/教师/管理员，不对 guest 开放）
  'pages/plan-route/plan-route',   // 多路径个性化学业规划（010 设计：路线图/对比/专业选择/转专业）
  'pages/plan-manage/plan-manage',   // 培养方案管理（004 设计，仅 admin/owner：上传/队列进度/先修校对）
  'pages/doc-center/doc-center',   // 文件中心（007 设计，仅 admin/owner：统一上传 + 功能×年级×专业适用性）
  'pages/jxtz-sync/jxtz-sync',   // 教务通知同步管理（仅学业规划助手 jwc：FEATURES.jxtzSync 显隐入口）
  'pages/trace/trace',   // 执行轨迹（演示模式：暗色观测台；对话页「执行过程」折叠条进入）
]

// 默认 tabBar：实例配置里提供 TAB_BAR 字段可整体覆盖
// 2026-09-24 视觉改版（「学籍台账」）：文字与图标统一墨蓝/灰；「功能」改名「服务」
const DEFAULT_TAB_BAR = {
  color: '#5C6B7A',
  selectedColor: '#17324D',
  backgroundColor: '#ffffff',
  borderStyle: 'black',
  list: [
    { pagePath: 'pages/chatbot/chatbot', text: '对话', iconPath: 'images/tab-chat.png', selectedIconPath: 'images/tab-chat-active.png' },
    { pagePath: 'pages/methods/methods', text: '服务', iconPath: 'images/tab-tools.png', selectedIconPath: 'images/tab-tools-active.png' },
    { pagePath: 'pages/user/user', text: '我的', iconPath: 'images/tab-user.png', selectedIconPath: 'images/tab-user-active.png' },
  ],
}

// 默认功能开关：实例配置的 FEATURES 与其合并（实例字段优先）
const DEFAULT_FEATURES = {
  badge: true,     // 未读角标
  customPages: [], // 本实例专属页面（会追加进 app.json pages）
  // 手机号绑定方式：'manual' = 手动输入直接绑定（dev/未认证阶段）；
  //                 'wechat' = 微信 getPhoneNumber 授权（小程序已认证并开通权限后）
  phoneAuthMode: 'manual',
  jxtzSync: false,  // 教务通知同步管理（仅学业规划助手 jwc 开启入口）
  plan: false,      // 培养方案智能解读（004 设计；需实例提供 PLAN_API_BASE）
}

// 需要透传到 config/instance.js 的实例专有字段（未列出的字段不会写入运行时配置）。
// 新增一个"经由独立 BASE 派生的外部服务"时，必须在这里登记，否则页面 require 到 undefined。
const PASSTHROUGH_FIELDS = ['WARNING_API_BASE', 'WARNING_API_KEY', 'PLAN_API_BASE', 'PLAN_API_KEY']

// 默认界面文案：实例配置的 UI_TEXT 与其合并（实例字段优先）
// 页面通过 config/instance.js 的 UI_TEXT 读取，wx:if / 标签 map 中使用
const DEFAULT_UI_TEXT = {
  ROLE_TEACHER: '教师',                 // 教师角色的显示名（角色标签/变更身份弹窗）
  APPLY_TEACHER_DESC: '申请成为教师',       // 申请认证卡片的描述
  PENDING_TEACHER_AUTH: '待审核的教师申请列表',     // 管理员审核列表描述
  AUTH_TEACHER_LABEL: '教师',             // "申请{{}}认证"弹窗标题中的角色名
  ALREADY_TEACHER: '已是教师',            // 已是教师角色时的 toast
}

const REQUIRED_FIELDS = ['APPID', 'INSTANCE_ID', 'ASSISTANT_TITLE', 'WELCOME_TEXT', 'CHAT_PLACEHOLDER', 'API_BASE_URL']

function fail(message) {
  console.error(`[build] ✗ ${message}`)
  process.exit(1)
}

function listInstances() {
  return fs.readdirSync(INSTANCES_DIR)
    .filter((f) => f.endsWith('.js'))
    .map((f) => f.replace(/\.js$/, ''))
    // 排除 _default 与本地密钥覆盖文件（*.local.js，gitignored，不是实例）
    .filter((id) => id !== '_default' && !id.endsWith('.local'))
    .sort()
}

function loadInstance(id) {
  const file = path.join(INSTANCES_DIR, `${id}.js`)
  if (!fs.existsSync(file)) {
    fail(`找不到实例配置 ${id}（期望 ${path.relative(ROOT, file)}）。\n      可用实例: ${listInstances().join(', ')}`)
  }
  let cfg
  try {
    cfg = require(file)
  } catch (err) {
    fail(`加载实例配置失败: ${err.message}`)
  }
  for (const key of REQUIRED_FIELDS) {
    if (!cfg[key]) fail(`实例 ${id} 缺少必填字段 ${key}`)
  }
  return {
    ...cfg,
    FEATURES: { ...DEFAULT_FEATURES, ...(cfg.FEATURES || {}) },
    UI_TEXT: { ...DEFAULT_UI_TEXT, ...(cfg.UI_TEXT || {}) },
    TAB_BAR: cfg.TAB_BAR || DEFAULT_TAB_BAR,
  }}

function writeFile(file, content) {
  const existing = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : null
  if (existing === content) {
    console.log(`  = ${path.relative(ROOT, file)}（内容未变化，跳过写入）`)
    return
  }
  // 原子写入：先写临时文件再替换，避免 DevTools 文件监听读到半写状态
  const tmp = `${file}.tmp`
  fs.writeFileSync(tmp, content, 'utf8')
  fs.renameSync(tmp, file)
  console.log(`  ✓ ${path.relative(ROOT, file)}`)
}

function buildInstance(instanceId, { dryRun = false } = {}) {
  const cfg = loadInstance(instanceId)
  const tag = dryRun ? ' [dry-run]' : ''
  console.log(`\n[build] 组装实例: ${cfg.ASSISTANT_TITLE} (${instanceId})${tag}`)

  // 1. config/instance.js
  const instanceObject = {
    INSTANCE_ID: cfg.INSTANCE_ID,
    ASSISTANT_TITLE: cfg.ASSISTANT_TITLE,
    WELCOME_TEXT: cfg.WELCOME_TEXT,
    CHAT_PLACEHOLDER: cfg.CHAT_PLACEHOLDER,
    API_BASE_URL: cfg.API_BASE_URL,
    // 经由独立 BASE 派生的外部服务（007 学业预警 / 009 培养方案解读）；
    // 空值 = 功能未启用。字段清单见 PASSTHROUGH_FIELDS（新增服务务必登记）。
    ...Object.fromEntries(PASSTHROUGH_FIELDS.map((k) => [k, cfg[k] || ''])),
    FEATURES: cfg.FEATURES,
    UI_TEXT: cfg.UI_TEXT,
  }
  // 密钥缺失是最常见的"页面全 401/空白"根因：BASE 配了但 KEY 为空 → 明确告警
  const missingKeys = []
  if (cfg.PLAN_API_BASE && !cfg.PLAN_API_KEY) missingKeys.push('PLAN_API_KEY')
  if (cfg.WARNING_API_BASE && !cfg.WARNING_API_KEY) missingKeys.push('WARNING_API_KEY')
  if (missingKeys.length) {
    console.log(`  ⚠ ${missingKeys.join('/')} 为空 —— 相关接口会返回 401（页面空白）`)
    console.log(`    请确认 config/instances/${instanceId}.local.js 存在且导出 { WARNING_API_KEY, PLAN_API_KEY }，`)
    console.log('    Windows 注意扩展名别存成 .js.txt；填好后重新执行 npm run switch。')
  }
  const instanceJs = [
    '// 本文件由 scripts/build.js 自动生成，请勿手动修改。',
    `// 源配置: config/instances/${instanceId}.js —— 改配置请改源文件后重新执行切换脚本。`,
    '// Per-mini-program public settings. Keep secrets on the server, never here.',
    'module.exports = ' + JSON.stringify(instanceObject, null, 2),
    '',
  ].join('\n')
  if (dryRun) {
    console.log(`  (dry-run) 将写入 config/instance.js（INSTANCE_ID=${cfg.INSTANCE_ID}）`)
  } else {
    writeFile(OUT_INSTANCE_FILE, instanceJs)
  }

  // 2. project.config.json（只改 appid，保留其余字段；文件缺失时按模板生成）
  //    该文件是构建产物、不入库：克隆后第一次 switch 时由这里兜底创建
  let projectConfig
  if (fs.existsSync(PROJECT_CONFIG_FILE)) {
    projectConfig = JSON.parse(fs.readFileSync(PROJECT_CONFIG_FILE, 'utf8'))
  } else {
    console.log(`  ! ${path.relative(ROOT, PROJECT_CONFIG_FILE)} 不存在，按模板生成`)
    projectConfig = {
      appid: cfg.APPID,
      libVersion: '3.16.2',
      setting: {
        es6: true, postcss: false, minified: false, enhance: true,
        minifyWXSS: true, minifyWXML: true, useCompilerPlugins: false,
        disableUseStrict: false, babelSetting: { ignore: [], disablePlugins: [], outputPath: '' },
      },
      compileType: 'miniprogram',
      packOptions: { ignore: [], include: [] },
      editorSetting: {},
    }
  }
  const appidChanged = projectConfig.appid !== cfg.APPID
  projectConfig.appid = cfg.APPID
  // 关闭「过滤无依赖文件」：DevTools 的增量依赖分析会把新加入 app.json 的页面误判为无依赖，
  // 导致运行/上传时报「已被代码依赖分析忽略，无法被其他模块引用」。这是官方给的规避开关。
  projectConfig.setting = {
    ...(projectConfig.setting || {}),
    ignoreDevUnusedFiles: false,
    ignoreUploadUnusedFiles: false,
  }
  if (dryRun) {
    console.log(`  (dry-run) project.config.json appid → ${cfg.APPID}${appidChanged ? '' : '（不变）'}`)
  } else {
    writeFile(PROJECT_CONFIG_FILE, JSON.stringify(projectConfig, null, 2)) // 与仓库基线保持字节一致（无末尾换行）
  }

  // 3. app.json（共享页 + 实例专属页，tabBar 可覆盖，全局标题用实例标题）
  const appJson = {
    pages: SHARED_PAGES.concat(
      (cfg.FEATURES.customPages || []).filter((p) => !SHARED_PAGES.includes(p))
    ),
    window: {
      navigationBarTextStyle: 'black',
      navigationBarTitleText: cfg.ASSISTANT_TITLE,
      navigationBarBackgroundColor: '#ffffff',
    },
    tabBar: cfg.TAB_BAR,
    style: 'v2',
    sitemapLocation: 'sitemap.json',
  }

  // 2.5 校验：所有注册页面必须真实存在（防止 customPages / SHARED_PAGES 拼错路径）
  for (const p of appJson.pages) {
    if (!fs.existsSync(path.join(ROOT, `${p}.js`))) {
      fail(`app.json pages 引用了不存在的页面: ${p}（期望文件 ${p}.js；检查 FEATURES.customPages 拼写，或先创建页面再声明）`)
    }
  }

  if (dryRun) {
    console.log(`  (dry-run) app.json（pages=${appJson.pages.length}，标题=${cfg.ASSISTANT_TITLE}）`)
  } else {
    writeFile(APP_JSON_FILE, JSON.stringify(appJson, null, 2) + '\n')
  }

  // 4. 差异化静态资源镜像复制
  const srcDir = path.join(ASSETS_DIR, instanceId)
  if (fs.existsSync(srcDir)) {
    // 防护：镜像按相对路径覆盖项目根文件，禁止覆盖构建产物
    const protectedRel = [
      path.relative(ROOT, APP_JSON_FILE),
      path.relative(ROOT, PROJECT_CONFIG_FILE),
      path.relative(ROOT, OUT_INSTANCE_FILE),
    ]
    const conflicts = []
    const walk = (dir) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name)
        if (entry.isDirectory()) walk(full)
        else if (protectedRel.includes(path.relative(srcDir, full))) conflicts.push(path.relative(srcDir, full))
      }
    }
    walk(srcDir)
    if (conflicts.length) {
      fail(`assets/instances/${instanceId}/ 含与构建产物同名的文件（${conflicts.join('、')}），已拒绝覆盖。请改用其他文件名。`)
    }
    if (dryRun) {
      console.log(`  (dry-run) 将复制 assets/instances/${instanceId}/** → 项目根目录`)
    } else {
      fs.cpSync(srcDir, ROOT, { recursive: true })
      console.log(`  ✓ assets/instances/${instanceId}/** → 项目根目录`)
    }
  }

  return cfg
}

if (require.main === module) {
  const args = process.argv.slice(2)
  const dryRun = args.includes('--dry-run')
  const id = args.find((a) => !a.startsWith('--')) || process.env.INSTANCE || '_default'
  buildInstance(id, { dryRun })
}

module.exports = { buildInstance, listInstances, loadInstance }
