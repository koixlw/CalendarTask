# 安全策略

## 支持的版本

| 版本 | 状态 |
|---|---|
| 1.0.x | ✅ 修 bug |

## 怎么报告漏洞

**别开公开 issue。** 请发 [private advisory](https://github.com/koixlw/CalendarTask/security/advisories/new)，
或者在仓库的 Security 页点「Report a vulnerability」。

我会尽量在 7 天内回应。

## 需要注意的几点

### 凭据是怎么存的

界面里填的 Anthropic API key 和 CalDAV 密码，存在本机 SQLite 的 `settings` 表里，
用的是 base64 + XOR 混淆（固定密钥），**不是加密**。

- 能防的：随手打开 DB 看到明文
- 防不了的：拿到 DB 文件的人，一行代码就能还原

要更强的话，用环境变量：

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
```

环境变量优先于界面里存的 key。

### 数据在本地

所有条目、日历、笔记都存在 `%APPDATA%\PyCal\db\pycal.db`，只在你自己的机器上。
PyCal 不往任何第三方发数据 —— 唯一的对外请求是你自己配的 CalDAV 服务器和
Anthropic API（仅在你点「AI 润色」时）。

### 日志

API key 不写日志、不进异常信息。
