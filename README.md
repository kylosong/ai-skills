# ai-skills

自研 WorkBuddy / Claude Code 风格 Agent 技能集合。

## 技能列表

### vreading-note-auto v1.1.1

微信读书 → 印象笔记 读书笔记自动同步技能。

- 定位书籍 → 拉取划线与个人想法 → 去重 → 渲染微信读书原生风格 Markdown → 写入印象笔记目标笔记本
- 支持 dry-run 预览、增量更新、乐观锁冲突检测
- 鉴权全部走环境变量 / 本地文件（`WEREAD_API_KEY`、印象笔记 token），**不内置任何密钥**

详见 [vreading-note-auto/SKILL.md](vreading-note-auto/SKILL.md)。

## 安全说明

- 仓库内不含任何 token、密钥、cookie 等凭据，鉴权信息均通过环境变量或本地配置文件加载
- `.gitignore` 已屏蔽 `*.token`、`.env` 等敏感文件模式

## License

MIT
