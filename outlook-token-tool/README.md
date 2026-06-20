# Outlook Token Tool

本工具用于在本机获取你自己 Outlook / Hotmail / Microsoft 邮箱的 Microsoft Graph `refresh_token`。

## 默认流程

个人 Outlook / Hotmail 不需要进 Entra Portal，也不需要手动创建 `Client ID`。

新版默认使用微软官方公开客户端：

```text
Microsoft Graph Command Line Tools
14d82eec-204b-4c2f-b7e8-296a70dab67e
```

使用方法：

1. 双击 `获取OutlookToken.bat`
2. 填已有 Outlook 邮箱，或点 `扫描本机 Outlook 已登录邮箱`
3. 保持默认免注册模式
4. 点击底部固定按钮 `网页登录并获取 Token`
5. 浏览器打开 `https://www.microsoft.com/link`
6. 工具会自动复制验证码，你粘贴并登录
7. 工具自动轮询并保存 token

成功后默认按邮箱保存：

```text
tokens_yourname@hotmail.com.json
tokens_yourname@hotmail.com.env
tokens_yourname@hotmail.com_combo.txt
```

其中 `_combo.txt` 是你要的格式：

```text
邮箱----密码----client_id----refresh_token
```

注意：密码不会从微软或 Outlook 里读取，必须由你在 GUI 的 `邮箱密码` 输入框手动填写，或命令行传 `--account-password`。

## 为什么不再让你进 Entra Portal

个人 Outlook / Hotmail 账号进入 `entra.microsoft.com` 可能报：

```text
AADSTS16000: User account from identity provider 'live.com' does not exist in tenant 'Microsoft Services'
```

根因是个人 Outlook 账号不是 Azure/Entra 租户账号。这个错误不是你操作错，而是入口不适合个人账号。

## Client ID 到底是什么

`Client ID` 属于应用，不属于邮箱。

已有 Outlook 邮箱本身没有 `Client ID`，不能从邮箱里查出来。Outlook 网页版或桌面版使用的是微软自己的应用身份，本工具不会读取或复用 Outlook 客户端里的登录态、token 或受保护凭证。

只有在你有自己的 Azure/Entra 应用时，才勾选：

```text
高级：使用自己的 Azure/Entra 应用 Client ID
```

这时 Redirect URI 填：

```text
http://localhost:8765/callback
```

## 命令行用法

默认 device-code 免注册：

```powershell
python .\get_outlook_token.py --account-email yourname@hotmail.com --account-password "你的密码"
```

刷新已有 token：

```powershell
python .\get_outlook_token.py --refresh --input .\tokens_yourname@hotmail.com.json
```

高级 auth-code 模式：

```powershell
python .\get_outlook_token.py --auth-code --client-id "你的_client_id"
```

## 网络策略

工具会自动尝试：

1. 直连
2. Python 环境代理
3. Windows 系统代理

如果环境变量里有坏代理，例如 `127.0.0.1:7890` 没有服务，工具会跳过失败通道并记录实际成功的网络通道。

## 安全提醒

`refresh_token` 等同长期登录凭证。不要发给别人，不要上传 GitHub、网盘或聊天软件。
