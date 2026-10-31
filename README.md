# Quota Desk

一个 Windows 桌面额度看板，集中查看 Antigravity 和 Codex 多个账号的剩余额度，并切换客户端账号。

当前版本是 **0.3.0**，主线仅保留原朴素界面作为正式界面。源码托管于 [GitHub](https://github.com/qingxu620/Quota-Desk)，可以在本地安装、运行和测试，尚未发布到 PyPI。

## 能做什么

- **独立服务页面**：Antigravity、Codex 分为两页，界面简洁。
- **多账号管理**：保存或更新当前登录账号、导入已有账号文件、登录绑定新账号，数量不限于三个。
- **每 60 秒后台刷新**：同时刷新两页；切页使用已有数据，F5 可手动刷新。
- **托盘常驻与提醒**：关闭窗口默认收起到托盘，继续刷新；双击图标打开，右键可刷新、设置或退出。支持低额度、额度恢复和登录失效提醒。
- **账号整理**：卡片中的「管理」可重命名、设置分组或删除看板账号；支持按名称、邮箱、分组、套餐搜索，以及按额度、重置时间或名称排序。
- **区分真实状态**：实时额度、Argon 历史记录、过期记录和查询失败分别显示，未知值不会当作满额。
- **本地保存**：账号凭证使用 Windows DPAPI 加密。数据默认放在 `%LOCALAPPDATA%\QuotaDesk`，与源码分开。
- **离线演示**：不用登录，不读取真实账号，也不请求额度接口。

## 安装和启动

需要 Windows 10 / 11，以及带 Tk 的 Python 3.11 或更高版本。官方 Windows Python 安装包默认包含 Tk。

下载源码后，在项目目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
```

先打开离线演示：

```powershell
.\.venv\Scripts\python.exe -m quota_desk --demo
```

正常使用：

```powershell
# 打开看板
.\.venv\Scripts\python.exe -m quota_desk

# 创建桌面快捷方式
.\.venv\Scripts\python.exe -m quota_desk --install-shortcuts
```

安装后也提供 `quota-desk` 和 `quota-desk-gui` 两个入口。尚未提供独立 EXE 安装包。

## 添加和切换账号

窗口中选择对应服务，点击 **「+ 新增账号」**：

1. **保存 / 更新当前登录账号**：先在对应官方客户端登录，再保存到看板。相同身份更新凭证，不会重复添加。
2. **从账号文件导入**：支持原始登录凭证 JSON，以及旧看板带 `cred` 或 `auth` 字段的 `accN.json`。导入后在本地加密保存。
3. **登录其他账号**：重启对应客户端，由你在客户端或浏览器完成登录，看板等待并保存新账号。

切换账号会请求客户端正常关闭，写入目标账号后再启动。客户端未退出时不会替换账号；登录超时或启动失败会尝试恢复原账号。正在执行任务时，请先处理客户端中的工作再切换。

账号较多时可在当前页面滚动查看。

点击账号卡片中的 **「管理」** 可以修改名称和分组。分组可以直接输入，留空表示未分组。删除只移除该账号的看板记录及本地保存凭证，不会注销或删除官方账号，也不会修改当前客户端的登录状态。

搜索、分组和排序使用已有数据，不会额外查询接口。额度排序使用已知实时窗口的最低剩余百分比；重置排序使用最早的实时窗口重置时间。历史额度不参与这两种排序，未知值排在最后。

## 托盘和提醒

关闭窗口默认收起到 Windows 托盘，后台仍按配置的间隔刷新两页。双击托盘图标可重新打开；右键菜单可打开对应服务页面、刷新全部额度、打开设置或彻底退出。托盘不可用时，关闭窗口直接退出。

同一数据目录下再次启动桌面入口，会打开已有看板，避免重复常驻和重复提醒。

在窗口右上角 **「设置」** 中，可以关闭收起到托盘、开关各类提醒，并调整低额度阈值（默认 **20%**）：

- 实时额度达到或低于阈值时提醒；相同低额度状态不在每次刷新时重复提醒。
- 曾经低于阈值的窗口，后续实测恢复到阈值以上时提醒。
- 查询明确返回登录失效时提醒；网络故障、限流或权限错误不会直接当作登录失效。

提醒使用 Windows 原生通知区功能，显示受系统通知和勿扰设置影响。历史记录、未知值、失败查询及离线演示不触发额度提醒。状态去重在本次运行内有效；重启看板后会重新检查。设置保存在数据目录的 desktop.json，不包含登录凭证。离线演示不会读取或保存该设置。

## 其他用法

以下命令使用已安装本项目的 Python：

```powershell
# 在终端查看额度
python -m quota_desk --cli

# 临时调整刷新间隔（30 到 3600 秒）
python -m quota_desk --refresh-seconds 90

# 使用独立数据目录
python -m quota_desk --data-dir "D:\QuotaDeskData"

# 保存当前账号；agy 表示 Antigravity
python -m quota_desk account import-current agy --name "我的账号"
python -m quota_desk account import-current codex --name "工作账号"

# 输出不含邮箱和令牌的诊断摘要
python -m quota_desk --diagnostics
```

需要长期配置时，将 `config.example.toml` 复制到数据目录并命名为 `config.toml`。可以配置刷新间隔、客户端位置和 Codex 启动命令。也可通过 `QUOTA_DESK_HOME` 指定数据目录。

## 接口和当前限制

- 这是非官方工具。目前多账号额度查询使用客户端内部接口，接口变化时可能需要更新适配器；接口失败会显示失败状态。
- Argon 的本机实时数据仅对应当前登录的 Antigravity 账号。其他账号显示最近记录，超过重置时间后显示未知，不推测成 100%。
- Antigravity 的非当前账号凭证可能过期。仓库不分发第三方 OAuth 客户端密钥；未配置匹配的 OAuth 信息时，需要先在官方客户端重新登录，再刷新或保存当前账号。
- Codex 目前面向 Windows 商店版客户端。安装位置、进程名或认证方式变化时，可能需要在配置中调整。API Key 登录不等同于 ChatGPT 订阅额度账号。
- DPAPI 加密文件绑定原 Windows 用户环境，不是跨电脑通用的账号导出格式。备份整个数据目录，并在兼容的原用户环境恢复。
- 如果账号操作意外中断，数据目录中的加密恢复记录可用 `python -m quota_desk account recover agy` 或 `account recover codex` 恢复。该操作也会重启对应客户端。

## 开发与测试

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
```

测试使用合成账号；Windows 下另外验证 DPAPI 和独立测试目标的凭据读写。客户端重启、真实登录和真实账号切换不在自动测试中执行。Linux CI 仅验证离线逻辑，不代表已支持 Linux 客户端操作。

Windows 托盘与窗口交互测试需要交互式桌面；设置环境变量 QUOTA_DESK_DESKTOP_TESTS=1 后运行测试可启用。无桌面的 CI 默认跳过这两项。

提交问题时优先附上版本、复现步骤和 `--diagnostics` 输出。请勿提交账号文件、令牌、真实额度缓存或带个人资料的截图。

计划优先完善独立 EXE 发布、接口兼容性和更多安装环境。欢迎[提交问题](https://github.com/qingxu620/Quota-Desk/issues)和改进。

## 许可

[MIT](LICENSE) · Quota Desk contributors

## 初代版本

[初代经典版归档](https://github.com/qingxu620/Quota-Desk/releases/tag/v0.1.0-classic)保留历史源码供下载和回溯，主线不再包含经典界面代码与旧图标。旧命令中的 `--simple` 参数仍可兼容使用。
