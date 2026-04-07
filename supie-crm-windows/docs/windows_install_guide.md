# Windows 服务注册与启动指南

本文说明如何把这个项目注册成 Windows 服务，并通过“服务”来控制项目的启动、停止和开机自启。

本指南适用于 Windows 发布包，尤其是 `supie-crm-windows-no-venv.zip`。这个包**不自带虚拟环境**，但已经包含服务注册脚本和完整源码，只要先准备好 Python，就可以完成服务注册。

## 1. 先理解整体运行方式

这个项目在 Windows 上的运行链路是：

```text
Windows 服务
 -> 启动项目内的 Python 解释器
 -> 运行 `ops/windows_service.py`
 -> 再由它拉起 `ops/service_runner.py`
 -> `service_runner.py` 调用 `app.py` 里的 `init_db()`
 -> `waitress` 监听 `http://127.0.0.1:3000`
```

所以你在服务管理器里看到的，是 `supie_crm` 这个服务，而不是直接看到 `app.py`。

## 2. 准备环境

### 2.1 安装 Python

建议安装 Python 3.11 或更高版本。

安装时请勾选 `Add python.exe to PATH`，这样后面可以直接在 PowerShell 里调用 `python`。

安装完成后，打开 PowerShell 检查：

```powershell
python --version
python -m pip --version
```

如果这两条命令失败，先不要继续，说明 Python 没有正确加入 `PATH`。

### 2.2 解压项目到固定目录

建议解压到类似下面的目录：

```powershell
D:\Supie\supie-crm-windows
```

项目根目录里至少应该有这些内容：

- `app.py`
- `ops\windows_service.py`
- `ops\service_runner.py`
- `requirements.txt`
- `install_windows.cmd`
- `start_windows.cmd`
- `docs\windows_install_guide.md`

如果这些文件缺失，先确认你解压的是完整包。

## 3. 创建虚拟环境

打开管理员 PowerShell，进入项目根目录：

```powershell
cd D:\Supie\supie-crm-windows
```

然后创建虚拟环境：

```powershell
python -m venv .venv
```

创建成功后，目录里会出现 `.venv` 文件夹。

如果你想确认虚拟环境已经生成，可以检查：

```powershell
Test-Path .\.venv\Scripts\python.exe
```

返回 `True` 就表示虚拟环境可用。

## 4. 安装依赖

推荐始终使用虚拟环境里的 `python`，不要直接用系统 `pip.exe`。

先升级 `pip`：

```powershell
.\.venv\Scripts\python -m pip install --upgrade pip
```

再安装项目依赖：

```powershell
.\.venv\Scripts\python -m pip install -r requirements.txt
```

如果这一步报错，先处理依赖问题，不要急着注册服务。

## 5. 配置数据库

这个项目固定使用 PostgreSQL。

在项目根目录检查 `.env` 文件，至少确认下面这些配置是对的：

```ini
PG_HOST=127.0.0.1
PG_PORT=5432
PG_DATABASE=supie_crm
PG_USERNAME=supie_crm
PG_PASSWORD=your_password
APP_PORT=3000
```

这几项分别代表：

- `PG_HOST`：PostgreSQL 地址
- `PG_PORT`：PostgreSQL 端口
- `PG_DATABASE`：数据库名
- `PG_USERNAME`：数据库用户名
- `PG_PASSWORD`：数据库密码
- `APP_PORT`：Web 服务端口，默认 `3000`

数据库还要满足这几个条件：

- PostgreSQL 服务已经启动
- 数据库已经创建
- 用户名和密码可以正常登录
- 机器允许当前这台 Windows 访问数据库

如果你的 PostgreSQL 不在本机，把地址和端口改成实际值即可。

## 6. 先做一次手动启动验证

在注册成 Windows 服务之前，建议先手动跑一次，确认项目本身是能启动的。

运行：

```powershell
.\.venv\Scripts\python ops\service_runner.py
```

如果这一步成功，浏览器访问：

- `http://127.0.0.1:3000`

如果这里都不通，说明问题不在 Windows 服务，而在数据库、依赖或配置。

## 7. 安装 Windows 服务

### 7.1 用管理员 PowerShell 执行安装

进入项目根目录：

```powershell
cd D:\Supie\supie-crm-windows
```

执行安装命令：

```powershell
.\.venv\Scripts\python ops\windows_service.py --startup auto install
```

这条命令会做两件事：

- 把项目注册为 Windows 服务
- 把启动类型设为 `Automatic`，也就是开机自动启动

安装后，服务名是：

- `supie_crm`

### 7.2 安装后服务是怎么启动的

当前这套实现不是直接让 `pythonservice.exe` 处理业务，而是注册成一个 Windows 服务后，由服务进程直接启动：

```text
D:\Supie\supie-crm-windows\.venv\Scripts\python.exe
 -> D:\Supie\supie-crm-windows\ops\windows_service.py
 -> D:\Supie\supie-crm-windows\ops\service_runner.py
```

这样做的好处是注册和启动逻辑更直观，也方便你后面通过服务管理器控制项目。

## 8. 启动 Windows 服务

安装完成后，启动服务：

```powershell
.\.venv\Scripts\python ops\windows_service.py start
```

也可以直接用系统命令启动：

```powershell
sc start supie_crm
```

如果服务已经在运行，系统会提示当前状态。

## 9. 检查服务状态

查看服务状态：

```powershell
Get-Service supie_crm
```

或者看更详细的状态：

```powershell
sc queryex supie_crm
```

正常情况下你会看到：

- `Status : Running`
- `StartType : Automatic`

## 10. 验证 Web 是否真的起来了

服务显示运行，不代表页面一定已经可用，所以要再验证一次端口。

访问本地地址：

- `http://127.0.0.1:3000`

或者在 PowerShell 里测试：

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:3000
```

如果返回 `200`，说明项目已经正常对外提供服务。

## 11. 以后如何用 Windows 服务控制项目

### 11.1 启动

```powershell
sc start supie_crm
```

### 11.2 停止

```powershell
sc stop supie_crm
```

### 11.3 查看状态

```powershell
sc query supie_crm
```

### 11.4 设置开机自启

如果你安装时已经用了 `--startup auto install`，通常已经是自动启动。

如果想再次确认：

```powershell
sc qc supie_crm
```

## 12. 如果要重新安装服务

如果你之前装过同名服务，建议先停止再重新安装。

先停止：

```powershell
sc stop supie_crm
```

然后卸载：

```powershell
.\.venv\Scripts\python ops\windows_service.py remove
```

再重新安装：

```powershell
.\.venv\Scripts\python ops\windows_service.py --startup auto install
```

## 13. 日志在哪里看

如果服务启动异常，先看项目日志目录：

- `logs\service.log`

另外也可以看系统服务状态：

```powershell
sc queryex supie_crm
```

常见问题一般是下面几类：

- Python 没装好或不在 `PATH`
- 虚拟环境 `.venv` 没创建成功
- 依赖没有安装完成
- PostgreSQL 连接失败
- 数据库不存在
- 3000 端口被别的程序占用

## 14. 最短可执行流程

如果你只想按最少步骤操作，直接照下面顺序执行即可：

```powershell
cd D:\Supie\supie-crm-windows
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python ops\windows_service.py --startup auto install
.\.venv\Scripts\python ops\windows_service.py start
```

## 15. 一次性完整排查顺序

如果你是第一次部署，建议按这个顺序逐步确认：

1. 安装 Python 并确认 `python --version` 正常。
2. 解压项目到固定目录。
3. 创建 `.venv`。
4. 安装依赖。
5. 配置 `.env` 和 PostgreSQL。
6. 手动运行 `ops/service_runner.py` 验证项目本体可启动。
7. 执行 `install` 把项目注册成 Windows 服务。
8. 执行 `start` 启动服务。
9. 用 `Get-Service supie_crm` 或 `sc queryex supie_crm` 检查状态。
10. 浏览器访问 `http://127.0.0.1:3000` 验证页面。
