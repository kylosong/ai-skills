---
name: vreading-note-auto
description: This skill should be used when the user wants to export WeRead (微信读书) highlights and thoughts into Evernote/印象笔记 as a formatted reading note. It handles the full pipeline - locating the book, fetching highlights and personal thoughts, deduplicating them, rendering WeRead-native Markdown, and writing to the correct notebook with tags. Use when the user says 把微信读书笔记同步到印象笔记 / 导出读书笔记 / 微信读书笔记存到印象笔记 / 整理成读书笔记 / sync weread notes to evernote, or asks to update an existing synced reading note.
version: 1.1.1
agent_created: true
metadata:
  primaryEnv: WEREAD_API_KEY
---

# 微信读书笔记 → 印象笔记 自动同步

把微信读书的划线与个人想法,去重、排版后写入印象笔记。

## 前置条件

两个凭据都必须在位:

| 凭据 | 来源 | 检查方式 |
|---|---|---|
| `WEREAD_API_KEY` | 微信读书开放平台,格式 `wrk-xxxxxxxx` | `yx_api._wr_key()` |
| 印象笔记 token | `~/.config/yinxiang-skill/token` 或 `YX_AUTH_TOKEN` | `yx_api._token()` |

`yx_api` 已内置三级兜底,直接调用即可,不要手动去读环境变量:

- `_wr_key()`:进程环境变量 → Windows 注册表 `HKCU\Environment` → `~/.config/weread-skill/token`
- `_token()`:token 文件 → `YX_AUTH_TOKEN`

**为什么需要注册表兜底**:Windows 上 `setx` 写入的用户级环境变量,**已启动的进程读不到**,
必须重启客户端才继承。注册表兜底让 skill 在同一会话里就能用,不必让用户重启。

任一缺失:微信读书侧提示配置 API Key;印象笔记侧提示用户说「授权印象笔记」。

## 核心工作流

### 第 1 步 · 定位书籍

用 `scripts/yx_api.py` 的 `find_book(title='书名')` 在书架里定位,拿到 `bookId`。

同名书多本时,列出候选让用户选,不要默认取第一本。

### 第 2 步 · 拉取笔记

```python
marks, reviews = fetch_notes(book_id)
```

`fetch_notes` 已内置 `/review/list/mine` 的翻页循环(用 `synckey` 游标),会自动取全。

### 第 3 步 · 生成正文

```python
md, stat = build(marks, reviews, title='无冕女王：孝庄', author='衍何文化')
```

参数用 `title` / `author`(也兼容 `book_title` / `book_author`)。

`build` 返回 `(markdown, stat)`,**必须把 stat 报给用户**——`paired` / `orphan_reviews` / `multi_para_marks` 这几个数字是质量关键信号(见下方验收标准)。

可选:`analysis_lines` 参数传入阅读分析段落(阅读轨迹、主线、锚点),插在统计信息之后、正文之前。

### 第 4 步 · 写入印象笔记

```python
nb = find_notebook('3-阅读｜Reading')
res = upsert_note(title, md, notebook_guid=nb['guid'],
                  tag_names=['阅读2026'], match_keyword='书名关键词',
                  dry_run=True)      # 先预演,不落盘
```

`upsert_note` 返回四种状态,按下表处理:

| status | 含义 | 该做什么 |
|---|---|---|
| `would_create` | 笔记本里没有同名笔记 | 去掉 `dry_run` 执行创建 |
| `unchanged` | 笔记已存在**且内容实质一致** | **不要重复录入**,直接告知用户"已存在,未重复录入" |
| `would_update` | 笔记已存在但内容有差异 | 向用户说明差异,确认后去掉 `dry_run` 执行 |
| `updated` / `created` | 写入成功且已校验 | 正常完成 |
| `conflict` | 找到多篇同名笔记 | 列出 `candidates` 让用户选哪一篇,**不要自己决定** |
| `drift` | 写入返回成功但内容没生效 | **不要重试**(会产生更多副本),报告冲突副本数量,让用户在客户端手动清理 |

确认无误后去掉 `dry_run=True` 再跑一次真正写入。

---

## ⚠️ 避免「冲突的修改」副本(重要)

**现象**:笔记列表里冒出「冲突的修改 《书名》(10-06 00:12)」,原笔记内容没变。

**根因**:印象笔记的**乐观锁**。当同一篇笔记在两次写入之间被改动过
(客户端 App 同步、上一轮 `force=True` 连续覆盖、或刚写入后立刻再写),
服务端不会覆盖,而是把新内容另存为「冲突的修改」副本。

**实测**:今天对《趣说汉朝407年（一）》用 `force=True` 连续覆盖,产生了 1 份副本
(本机实测产生过 1 份,guid 已隐去)。

### 规避做法(已内建到 upsert_note)

1. **默认不 force**。内容有差异时先 `dry_run` 报告,用户确认再写。
2. **一次成型**——写入前把格式、分析段落全部确认好,别反复重写同一篇。
   今天的教训:为了调格式对同一篇写了 6 次,必然触发锁。
3. **写入后自动校验**(`_verify_written`):内容没生效就返回 `drift` 而非重试。
4. **冲突副本不自动删**——印象笔记 API 无删除能力,必须让用户在客户端手动清理。
5. 检测工具:`find_conflict_copies(title='《书名》')`

### 已修复:两处会伪造 `drift` 的误报(2026-10-17)

新建笔记后曾连续两本都返回 `drift`「检测到 1 份冲突副本」,实际并不存在副本。两个根因都已改掉:

1. **`contents_equal` 的兜底逻辑恒为 False** —— 原来是 `_diff_only_delete(na,nb) and _diff_only_delete(nb,na)`,
   但回读只会**少字不会多字**,反方向永远不成立,于是任何零星吞字都会掉进 `drift`。
   已改为单向 `_diff_only_delete(nb, na, 40)`(本地 md → 回读只丢 ≤40 字即视为相同)。
2. **`find_conflict_copies` 把目标笔记自身算成 1 份副本** —— 它用「标题含书名关键词」判断,
   而目标笔记本身就命中。已加「标题与目标完全相同时跳过」。

**复核副本的正确姿势**:按书名关键词搜笔记本,标题**唯一**即无副本;不要用关键词「冲突」去搜 ——
那是全文本匹配,会把正文里含「冲突」二字的正常笔记全捞出来(实测 42 篇),看不出真假。

`match_keyword` 用书名关键词。**不要带全角括号**——印象笔记搜索不支持标点:
实测「汉朝407（二）」命中 0 篇,「汉朝407」才命中。

`upsert_note` 已内置 `_keyword_candidates()` 降级:原关键词 → 去标点 → 逐步截短,
逐个尝试直到命中。所以带括号也能正常工作,但显式传干净的关键词更快。

### 第 5 步 · 报告

告知用户:笔记 ID、字符数、章节数、去重结果(几条想法配上了划线)、格式要点。
若 status 是 `unchanged`,要**明确说"已存在,未重复录入"**。

---

## 去重:同标题还要比内容

标题相同**不等于**同一篇笔记。`upsert_note` 的判定链:

```
按 match_keyword 在笔记本内搜
  ├─ 无同名 → created(新建)
  ├─ 多篇同名 → conflict(交用户选)
  └─ 唯一同名 → 拉详情比内容
        ├─ 一致 → unchanged(不重复录入)
        └─ 有差异 → updated / conflict
```

### 为什么不能直接比字符串

印象笔记回读返回的是**纯文本**:HTML 标签被剥离、Markdown 标记消失、单换行变空格。
实测本地 md 22,949 字符 vs 回读 21,789 字符,`==` 恒为 False,会**误判成"有差异"而白白覆盖**。

更麻烦的是**转换会吞零星字符**:实测《趣说汉朝407年（一）》丢失 2 处「姓名」
(因为「姓名」在某些解析路径下被当成 name 标签),相似度 0.9999。

### 判定策略:结构指纹优先

`contents_equal()` 先比结构指纹,结构一致即视为相同;结构变了才退到字符diff:

```python
content_signature()  # 章节数+ 章节标题序列 + 想法组数 + 阅读分析块 + 划线条数 + 首尾片段 + 长度
```

结构指纹可靠的原因:转换丢的是零星字符,**不会丢整章、整条划线或整个想法组**。

⚠️ 已知边界:在 3 万字笔记里删掉**仅 3 个字**,可能不改变任何统计量,会被判为相同。
现实中重新生成笔记的差异是几十字以上,不受影响。若要强制覆盖,用 `force=True`。

---

## 输出格式(用户已确认的最终版)

**微信读书原生导出风格**,不要自创样式:

````markdown
## 📖 阅读分析

**《书名》** — 作者

> **笔记统计** ｜ 全书 143 条 = 划线 126 + 想法 17（去重后正文 127 条）
>
> **阅读时间** ｜ 2026.09.30 → 2026.10.03，实际有笔记的三天：09-30 / 10-02 / 10-03

**阅读轨迹**

- **09-30**｜9 条 · 明末女真人崛起
- **10-02**｜24 条 · 皇太极的诛心之局、皇权的棋局等

## 序回到起点，遥望汉人来时路

- 短划线直接一条

- 长划线首段，两空格缩进作为续行
  第二段
  第三段

- 划线内容（💭 **我的想法**：你的想法）
````

硬性规则:

| 规则 | 说明 |
|---|---|
| 章节标题 | `## 第 N 章 章节名`,**`第` 和 `章名` 之间无分隔符** |
| 划线 | `- 文本`(渲染为圆点),**不要用 `1.` 编号** |
| 想法 | 内联在原文末尾 `（💭 **我的想法**：...）`,**不要另起段落** |
| 「原文」小标题 | **不要加** |
| 章节分布表 | **不要加** |
| 分隔符 | **不要加** |
| 段落 | `markText` 内的 `\n` 是书的段落边界,**必须保留** |

---

## 三个必须避开的坑

### 坑 1 · 多段落必须首段带 `- `

`markText` 里含 `\n` 的划线(实测占比约 1/3),拆行后:

```markdown
- 首段内容
  后续段内容
```

**错误写法**(所有段落都缩进、没有首段 `- `):

```markdown
  首段内容
  后续段内容
```

→ Markdown 判定这不是列表项,**圆点完全不显示**。这是最容易反复踩的坑。

### 坑 2 · 想法与划线去重靠 (chapterUid, range)

`/review/list/mine` 每条 review 带 `abstract`(对应划线原文)和 `range`、`chapterUid`。

配对成功的划线**必须从独立列表剔除**,改以 `（💭 我的想法：...）` 内联。否则同一段内容出现两次。

### 坑 3 · 印象笔记会吞掉某些 Markdown 语法

| 写法 | 结果 |
|---|---|
| `---`(单独一行水平线) | ❌ 被吞 |
| `*`(孤立星号) | ❌ 被吞(被当成无序列表符号) |
| `· · ·` | ✅ 保留 |
| `> 引用` | ✅ 保留 |
| `- 列表项` | ✅ 保留 |
| `> ` 空引用行 | ✅ 保留(用于分隔引用块内的两行) |

**不要用分隔符**,当前格式本来就不需要。

---

## 验收标准

写入后必须核对,任一不达标要修正:

- [ ] `paired` = 想法总数 → 全部配对成功,`orphan_reviews` = 0
- [ ] `entries` = `纯划线 + 配对数`,无重复无遗漏
- [ ] 笔记标题与 `noteTitle` 完全一致
- [ ] 标签在 `tagList` 中,不只是正文里写了
- [ ] 抽查一条多段落划线,确认本地 md 里有 `- ` 开头
- [ ] 章节数 = 书籍实际章节数
- [ ] **重跑同一本书时,status 应为 `unchanged`**,不得产生第二篇

**关于回读的注意点**:印象笔记回读返回的是**纯文本**(HTML 标签已剥离),且会把单换行显示成空格。所以:

- 回读里看不到段落分隔 **不代表丢失**,本地 md 才是真值
- 校验渲染结构要靠**纯文本特征串**:`第 N 章`、`我的想法`、书名
- `c.count('<')` 恒为 0,不能用它判断

---

## 参考资料

- `references/api-quirks.md` —— 两个 API 的完整字段、错误码、翻页规则
- `references/format-recipes.md` —— 排版变体(A/B/C 三方案)与用户取舍记录

## 注意事项

- **印象笔记 API 不支持删除笔记**。验证新样式前先建临时笔记(标题加「【临时】」前缀),测完提醒用户手动删。
- **不要在不确认的情况下手改已有笔记内容**。`update_note` 传 `content` 会整体替换,需先跟用户确认。
- macOS 与 Windows 环境独立,`WEREAD_API_KEY` 和印象笔记 token 需各自配置,不同步。
- 新写入的用户级环境变量,**已启动的进程读不到**,提示用户重启客户端。
- 阅读轨迹必须**按真实数据统计**,不要凭印象写日期——曾因此写出不存在的「10-01」。
