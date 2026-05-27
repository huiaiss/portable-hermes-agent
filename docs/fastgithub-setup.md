# FastGithub 配置指南

## 是什么

FastGithub 是 GitHub 加速工具，解决国内访问 GitHub 打不开、git clone/push 失败等问题。

## 安装位置

`E:\fastgithub_win-x64\fastgithub_win-x64\`

## 启动方式

### 开机自动启动（已配置）

快捷方式已放入启动文件夹，开机自动运行。

### 手动启动

```cmd
sc start fastgithub
```

### 验证是否生效

```cmd
git ls-remote https://github.com/huiaiss/portable-hermes-agent.git HEAD
```

有输出 = 生效。

## 原理

- DNS over HTTPS：绕过 DNS 污染
- 本地反向代理：127.0.0.1:38457（DNS） + 127.0.0.1:443（HTTPS）
- 自动测速选最快 IP

## 故障排查

服务未运行：
```cmd
sc start fastgithub
```

SSL 证书错误：
```cmd
git config --global http.sslVerify false   # 临时方案
```

完全卸载重装：
```cmd
sc stop fastgithub
sc delete fastgithub
```
