# API 细节与错误码速查

## 微信读书

统一入口:`POST https://i.weread.qq.com/api/agent/gateway`

鉴权 Header:`Authorization: Bearer $WEREAD_API_KEY`,Key 格式 `wrk-xxxxxxxx`。
Key 绑定用户身份(vid),需要用户身份的接口自动注入,**无需手动传 vid**。

### 取Key 的三级兜底

Windows 上 `setx` 写入的用户级环境变量,**已启动的进程读不到**,需重启客户端才继承。
`yx_api._wr_key()` 因此做了三级兜底:

1. 进程环境变量 `WEREAD_API_KEY`
2. 注册表 `HKCU\Environment` 的 `WEREAD_API_KEY`(用 `winreg` 读)
3. 文件 `~/.config/weread-skill/token`

注册表读取代码:
```python
import winreg
with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
    v, _ = winreg.QueryValueEx(key, 'WEREAD_API_KEY')
```

排查时注意:**注册表有值但进程读不到是正常现象**,不要误判为"没配置"。

### 请求格式铁律

每次请求 body **必须带 `skill_version`**,且**业务参数平铺在顶层**。

正确:
```json
{"api_name":"/user/notebooks","count":100,"skill_version":"1.0.3"}
```

错误(参数包在 params 里,后端收不到):
```json
{"api_name":"/user/notebooks","params":{"count":100},"skill_version":"1.0.3"}
```

### 本流程用到的接口

#### `/shelf/sync` — 书架同步

无参数。用于定位 bookId。

回包:`books[]`(电子书)、`albums[]`(有声书/专辑)、`mp`(文章收藏入口)、`archive[]`(书单)。
划线/想法统计口径:`books[].finishReading == 1` 表示读完。

#### `/book/bookmarklist` — 单本书划线

参数:`bookId`(必填)。

回包:
- `updated[]` — 划线数组
  - `markText` — **划线原文,内含 `\n` 表示书的段落边界**
  - `chapterUid` / `range` / `createTime` / `bookmarkId`
- `chapters[]` — 章节信息
  - `chapterUid` / `chapterIdx` / `title`
- `book` — 书籍信息

⚠️ 该接口已过滤书签(type=0),只返回划线(type=1),**导不出书签内容**。

#### `/review/list/mine` — 个人想法与点评

参数:`bookid`(注意是**小写 d**)、`count`、`synckey`(翻页游标,默认 0)。

回包:
- `reviews[].review` 数组
  - `content` — 想法正文
  - `abstract` — **对应的划线原文**
  - `range` + `chapterUid` — **配对键**
  - `chapterName` / `chapterIdx` / `createTime` / `star` / `reviewId`
- `totalCount` / `hasMore` / `synckey`

翻页循环:用返回的 `synckey` 作为下次请求的 `synckey`,直到 `hasMore` 为 0。
注意防死循环:`synckey` 未变化时跳出。

### 笔记统计口径

- **统计** 总笔记数 = `reviewCount + noteCount + bookmarkCount`
- **导出** 内容 = 划线 + 想法/点评(书签导不出)
- `noteCount` 是划线数,不是单本书总笔记数

---

## 印象笔记

Base:`https://app.yinxiang.com`
鉴权 Header:`auth: <token>`,所有请求 body 带 `"source":"skill"`

### 接口清单

| 用途 | 方法 | 路径 |
|---|---|---|
| 列出笔记本 | POST | `/third/ai-chat-note/grpc-api/search/listNoteBooks` |
| 列出标签 | POST | `/third/ai-chat-note/grpc-api/search/listTags` |
| 搜索笔记 | POST | `/third/ai-chat-note/grpc-api/search/searchNotesByFilter` |
| 笔记详情 | POST | `/third/ai-chat-note/grpc-api/search/getNoteDetail` |
| 创建笔记 | POST | `/third/third-party-note-service/restful/v1/createNoteFromMCP` |
| 更新笔记 | POST | `/third/third-party-note-service/restful/v1/updateNoteFromMCP` |

### 搜索笔记

```json
{
  "keyword": "关键词",
  "notebookName": "3-阅读｜Reading",
  "notebookGuid": "...",
  "title": "标题关键词",
  "tagNames": ["标签"],
  "startTime": 1782835200000,
  "endTime": 1785513599999,
  "source": "skill",
  "resultSpec": {"includeContent": false, "includeResources": false,
                 "includeTags": true, "includeResourceContent": false}
}
```

⚠️ **时间戳是 UTC 毫秒**。用户说「最近」固定按最近 3 天。

回包:`data.total`(总数,不要用列表长度代替)、`data.noteDetailList[]`。
列表项字段:`noteGuid`、`noteTitle`、`tagList`、`content`。

### 笔记详情

⚠️ **参数名是 `guid`,不是 `noteGuid`**。用错会返回:

```json
{"status": {"code": 8400, "msg": "invalid guid"}}
```

⚠️ **必须带 `resultSpec`**,否则拿不到正文:

```json
{"guid": "xxx", "source": "skill",
 "resultSpec": {"includeContent": true, "includeResources": false,
                "includeTags": true, "includeResourceContent": false}}
```

回包:`data.dataDetail.{noteTitle, content, tagList}`

### 创建笔记

```json
{"title": "《书名》", "content": "markdown 正文", "notebookGuid": "...",
 "tagNames": ["阅读2026"], "source": "skill"}
```

- `content` 支持 Markdown,服务端转 HTML
- 不存在的标签会自动创建
- 成功返回 `{"code":0,"noteGuid":"..."}`

### 更新笔记

```json
{"noteGuid": "...", "content": "新正文", "source": "skill"}
```

⚠️ 传 `content` 会**整体替换**原内容,不是追加。
⚠️ 标签传 `tagNames` 时必须是**最终全集**(不是增量)。
清空标签传 `"clearTags": true`,传空数组 `[]` 无效。

### 错误码

| code | 含义 | 处理 |
|---|---|---|
| 8400 | invalid guid | 检查是否用了 `noteGuid` 而非 `guid` |
| 415 | HTTP 415 | 检查是否用错接口路径(如 `searchNote` 而非 `searchNotesByFilter`) |
| 1107 | 无结果 | 正常,告知用户没找到 |

### Markdown 转换行为

写入时 Markdown → HTML,**回读返回纯文本**:

- HTML 标签被剥离,`content.count('<')` 恒为 0
- **单换行会被显示成空格** → 回读看不到段落分隔,不代表内容丢失
- 孤立 `*` 和 `---` 被吞
- `> ` 空引用行可用,适合在引用块内强制换行

⚠️ **本地 md 文件才是真值**。校验渲染结构用纯文本特征串,不要依赖回读。

### ⚠️ 回读内容不能直接做字符串比较

同一篇笔记,本地 md 与回读内容长度不同——实测 **22,949 vs 21,789**。

原因:
- Markdown 标记(`##` `**` `-` `> `)被剥离
- HTML 标签被剥离
- 单换行变空格

所以 `local == remote` 恒为 `False`,直接比会**误判成"内容有差异"而白白覆盖已有笔记**。

必须先归一化:剥离标签与 Markdown 语法,只保留可见文字再比较。
`yx_api.normalize_content()` / `yx_api.contents_equal()` 已实现,直接用。

### 更新笔记的副作用

`update_note` 传 `content` 是**整体替换**,不是追加。
因此在写入前必须先判断是否真需要更新——同标题同内容时应直接跳过。


## 乐观锁与「冲突的修改」副本

印象笔记对笔记写入采用**乐观锁**。

**现象**:笔记列表出现「冲突的修改 《书名》(10-06 00:12)」,原笔记内容不变。

**触发条件**(满足其一即可能发生):
- 同一篇笔记在短时间内被**反复写入**(实测连续 force=True 覆盖 6 次,必现)
- 写入期间客户端 App / 其他端也在修改同一篇
- 刚写入完立刻再写(服务端索引尚未同步)

**行为**:不覆盖原笔记,而是把新内容另存为冲突副本。副本标题格式:


**规避**:
1. 默认不 force,先 dry_run 确认再写
2. 一次成型,避免对同一篇反复重写
3. 写入后校验内容是否生效,未生效**不要重试**(重试会产生更多副本)
4. 副本无法通过 API 删除(印象笔记无删除接口),必须让用户在客户端手动清理

**检测**:

已产生的副本(需手动清理):
-  guid 


## 搜索不支持标点符号

印象笔记的 `searchNotesByFilter` **不支持全角括号等标点**:

|关键词 | 命中 |
|---|---|
| `汉朝407（二）` | 0 篇 |
| `汉朝407` | 2 篇 |
| `趣说汉朝` | 2 篇 |
| `汉朝` | 8 篇 |

**规避**:`upsert_note` 用 `_keyword_candidates()` 做降级:
原关键词 → 去掉 `《》（）()〔〕【】[]` → 取前 3 / 前 2 字,逐个尝试直到命中。

因此 `match_keyword` 传带括号的书名也能工作,但显式传干净关键词更快。

另:`data.total` 有时返回字符串而非数字,拼接输出时用 `%s` 而非 `%d`。
