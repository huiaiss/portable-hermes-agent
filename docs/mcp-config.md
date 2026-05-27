# MCP 服务器配置参考

## 当前已配置的 MCP 服务

| 服务 | 用途 |
|------|------|
| `kimi-code` | Kimi 代码库分析（256K 上下文） |
| `luma-mcp` | Luma 图像理解 |
| `plugin-context7` | Context7 文档查询 |
| `plugin-playwright` | 浏览器自动化测试 |
| `talk-sql` | 数据库操作（PostgreSQL/MySQL/SQLite） |
| `zai-vision` | 图像/视频分析、图表识别、UI 对比 |

## Claude Code 配置位置

`~/.claude/settings.json`

## 常用操作

### 网页截图/测试
```
用 Playwright 打开 http://localhost:3000
截图看看首页
```

### 代码库分析
```
用 Kimi 分析 d:/auto-video-platform 的架构
```

### 数据库操作
```
用 talk-sql 查 users 表的前 10 条记录
```

### 文档查询
```
查 React 19 的 use() hook 用法
```
