# 星桥 AI · 可配置的中转站助手

用于 New API 的客户门户与 Windows WorkBuddy 配置助手。服务运营者可以配置品牌、New API 服务地址、对外 API 地址及积分换算；用户可通过浏览器授权客户端、查看余额、同步模型并生成 WorkBuddy 配置。

本项目与 WorkBuddy、New API 及其运营方无隶属关系。积分仅是运营者定义的展示单位，API 调用、余额校验与计费由 New API 执行。本项目不提供免费上游账号、积分、支付通道或盈利保证。

## 支持范围

- 支持本项目提供的门户协议及与本项目适配的 New API；**不支持把任意第三方 OpenAI 兼容 API 地址直接填进助手就获取其余额和账号**。其他中转站需要部署本门户或实现同样的门户协议。
- 门户提供账户登录、余额与消费记录、兑换码、模型列表和个人 API Key 配置。可选注册通过独立的受限辅助服务调用 New API；默认关闭注册。在线支付尚未实现。
- Windows x64 客户端要求 Windows 与 .NET Framework 4.x。手动配置服务地址时仅接受 HTTPS 根地址；HTTP 只允许用于回环地址本地测试。服务地址必须是**可信运营者的门户地址**，不是 API Key，也不是 `/v1` 地址。
- 客户端凭证使用 Windows DPAPI 保存，按门户地址隔离；退出当前账号后才能切换服务。网页登录通过设备授权及验证码核对完成，不在客户端保存密码。更换服务前建议先退出原站以撤销原凭证。
- 不保证第三方中转站、其他 WorkBuddy 版本或所有模型能力兼容；应先测试登录、模型配置、实际请求和扣费。

## 运营者配置

复制 `settings.example.json` 到本机的 `settings.json`，按实际环境填写。真实 `settings.json` 不应提交公开仓库。主要字段：

| 字段 | 作用 |
| --- | --- |
| `brand` | 门户品牌名称；客户端登录后会显示服务方名称 |
| `upstream_url` | 后端访问 New API 的地址，通常为本机或专用内网地址 |
| `public_url` | 用户访问本门户的根地址，正式环境必须使用 HTTPS |
| `api_base_url` | 提供给 WorkBuddy 的 OpenAI 兼容 API 根地址，正式环境必须使用 HTTPS |
| `quota_per_point` | 每 1 显示积分对应多少 New API 额度；正整数，默认 5000 |
| `secure_cookie` | 正式 HTTPS 环境设为 `true` |
| `registration_enabled` | 默认 `false`，仅安全部署注册辅助服务后启用 |
| `state_path` | 门户会话数据库位置，需设置受限访问权限 |

例如 New API 安装中如果 500,000 额度等于其显示的 $1，设置 `quota_per_point: 5000` 则该 $1 显示为 100 积分；设置 `10000` 则显示 50 积分。**500,000 只是示例，实际取决于你的 New API 设置。** 改该值只改变展示和兑换码到账积分的换算，不会修改用户原有 New API 额度，也不会改变 New API 模型费率。模型倍率和分组定价应在 New API 管理后台配置。

`model-capabilities.json` 默认是空对象。运营者可按模型 ID 添加 `name`、`description`、`supports_tool_call`、`supports_images`、`context_length`、`max_output_tokens` 和 `recommended`；未声明的能力不会被臆测。以实际上游能力为准。

### 本地测试

```powershell
cd relay-companion-open-source
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item settings.example.json settings.json
.venv\Scripts\python -m uvicorn app:app --host 127.0.0.1 --port 8400
```

复制前请确认 `settings.json` 尚不存在；New API 默认地址为 `http://127.0.0.1:3000`。打开 `http://127.0.0.1:8400`。正式部署需配置 HTTPS、反向代理、进程管理、最小权限、受限数据库访问及备份；请不要把示例地址当作生产设置。

### 注册辅助服务

`provisioner.py` 为可选的 Unix socket 注册辅助服务，仅实现创建普通用户的接口。默认读取 `/etc/starbridge-portal/provisioning.json`；可通过环境变量 `PROVISIONER_SETTINGS` 指定路径。该文件需由运营者自行创建，并包含 `username`、`password` 与 `upstream_url`。它是敏感文件，必须使用专用低权限服务账号和受限目录，不能纳入仓库或下载包。

只有在确保 Unix socket **仅允许门户服务账号访问**、网络监听已关闭、辅助服务和凭证文件权限正确、风险与管理员授权经过评估后，才可将 `registration_enabled` 设为 `true`。部署方式取决于主机和 New API 权限配置，示例代码不是即装即用的生产安全方案。

## 构建与使用 Windows 助手

在 Windows 上使用 Windows PowerShell 和 .NET Framework 编译器：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\desktop\build.ps1
```

输出为 `dist/Starbridge-Assistant.exe`，该文件默认为**未签名测试构建**。单独下载 EXE 后，首次打开需填写兼容门户的 HTTPS 根地址并保存，再通过浏览器登录。门户生成的 ZIP 包可以包含不含凭证的 `portal-profile.json` 作为初始地址；用户仍可以更换为其他兼容门户。

配置前程序会备份 WorkBuddy 配置；检测到同名模型指向其他服务时停止，避免覆盖其他提供商的配置。配置信息包含个人 API Key，不应分享。配置完成后保存当前工作并重启 WorkBuddy。

## 测试

```powershell
python -m unittest discover -s tests -p test_portal.py
node tests/test_frontend.cjs
```

可执行文件支持 `--self-test <新测试目录>`，覆盖积分显示、地址校验、合并备份和 DPAPI。测试数据不连接真实 New API。正式发布前还应使用真实环境完成注册、登录、模型配置、实际推理、余额耗尽、支付及权限测试；其中在线支付当前尚未实现。

## 开源和签名

源码使用 [MIT License](LICENSE)。`SECURITY.md` 和 `PRIVACY.md` 描述默认安全边界与数据流；第三方声明见 `THIRD_PARTY.md`。发布前应确认全部素材、依赖及贡献内容可合法采用相应许可证，清理不应公开的数据，并由维护者审查。

SignPath Foundation 为符合要求的开源项目提供申请途径；**开放源码不等于自动获得免费签名**。维护者需要有公开且已发布的合格开源项目、可追溯的构建、维护与审查流程，满足其隐私、安全及签名政策，并通过审核；条件可能变化，申请前请查看 [SignPath 条件](https://signpath.org/terms)。它的证书发行者为 SignPath Foundation，不是个人名字。仅在其批准后才能在项目页面标注其提供签名，不应提前声称已经获得。

本项目使用 GitHub Actions 测试并构建 Windows 客户端。当前发布的客户端尚未签名；SignPath 申请和审核完成前，Windows 仍可能显示安全警告。
