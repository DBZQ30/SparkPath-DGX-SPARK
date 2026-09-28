// scripts/new-instance.js —— 生成新助手实例骨架（配置 + 专属目录）
// 用法: node scripts/new-instance.js <id> --title "我的助手" [--appid wx1234567890abcdef]
// 示例: node scripts/new-instance.js my-assistant --title "我的助手"
'use strict'

const fs = require('fs')
const path = require('path')

const ROOT = path.resolve(__dirname, '..')
const INSTANCES_DIR = path.join(ROOT, 'config', 'instances')

function parseFlag(args, flag) {
  const i = args.indexOf(flag)
  return i >= 0 ? args[i + 1] : undefined
}

function main() {
  const args = process.argv.slice(2)
  const id = args.find((a) => !a.startsWith('--'))
  const title = parseFlag(args, '--title')
  const appid = parseFlag(args, '--appid') || 'wx0000000000000000'

  if (!id || !title) {
    console.error('用法: node scripts/new-instance.js <id> --title "助手名称" [--appid wx...]')
    process.exit(1)
  }
  if (!/^[a-z][a-z0-9-]*$/.test(id)) {
    console.error(`[new-instance] ✗ id 格式: 小写字母开头，仅小写字母/数字/连字符（如 jwc）`)
    process.exit(1)
  }
  const file = path.join(INSTANCES_DIR, `${id}.js`)
  if (fs.existsSync(file)) {
    console.error(`[new-instance] ✗ 实例 ${id} 已存在: ${path.relative(ROOT, file)}`)
    process.exit(1)
  }

  const content = [
    `// config/instances/${id}.js —— ${title}`,
    'module.exports = {',
    `  APPID: '${appid}',  // TODO: 替换为该小程序的真实 AppID`,
    `  INSTANCE_ID: '${id}',`,
    `  ASSISTANT_TITLE: '${title}',`,
    `  WELCOME_TEXT: '你好，我是西安交通大学管理学院${title}。', // TODO: 补充正式欢迎语`,
    "  CHAT_PLACEHOLDER: '输入咨询问题', // TODO: 按需定制",
    "  API_BASE_URL: 'https://isom.xjtu.edu.cn/accapi/', // 与教务共享同一后端时保持不变；独立部署时修改",
    '  FEATURES: {',
    '    badge: true,',
    `    customPages: [], // 专属页面: ['pages/_ext/${id}/xxx']`,
    '  },',
    '  // UI_TEXT: { ... } // 角色文案定制（默认"教师"语境，见 scripts/build.js 的 DEFAULT_UI_TEXT）',
    '}',
    '',
  ].join('\n')

  fs.writeFileSync(file, content, 'utf8')
  fs.mkdirSync(path.join(ROOT, 'pages', '_ext', id), { recursive: true })
  fs.mkdirSync(path.join(ROOT, 'assets', 'instances', id), { recursive: true })

  console.log(`[new-instance] 已生成 ${id}（${title}）:`)
  console.log(`  ✓ ${path.relative(ROOT, file)}`)
  console.log(`  ✓ pages/_ext/${id}/（专属页面目录）`)
  console.log(`  ✓ assets/instances/${id}/（差异化静态资源目录）`)
  console.log('')
  console.log('下一步:')
  console.log(`  1. 编辑 config/instances/${id}.js 补全 AppID / 欢迎语`)
  console.log(`  2. npm run switch ${id} 开发验证`)
  console.log(`  3. 微信公众平台注册小程序 → 配 request 合法域名 → 上传密钥到 keys/<AppID>.key`)
  console.log(`  4. npm run release -- ${id} --version 1.0.0 发布`)
}

main()
