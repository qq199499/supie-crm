# 项目过程管理系统

支持项目类型：

- 集成类项目
- 开发类项目
- 实施类项目

## 功能

- 中文页面
- 工作台与项目总览
- 项目、任务、里程碑、风险管理
- 客户、商机、合同、审批、回款、发票管理
- 项目附件上传与下载
- 用户、角色与权限管理

## 运行环境

Linux / macOS：

```bash
cd /workspace/crm
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Windows PowerShell：

```powershell
cd D:\Supie\crm
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python app.py
```

访问地址：

- http://127.0.0.1:3000

## 数据库

- 系统固定使用 PostgreSQL
- 如果项目根目录存在 `.env`，应用会优先从中加载数据库连接配置

应用优先读取 `PG_HOST`、`PG_PORT`、`PG_DATABASE`、`PG_USERNAME`、`PG_PASSWORD`，同时兼容 `PGHOST`、`PGPORT`、`PGDATABASE`、`PGUSER`、`PGPASSWORD`。

PostgreSQL 示例配置：

```powershell
$env:PG_HOST="192.168.0.103"
$env:PG_PORT="23083"
$env:PG_DATABASE="zqq_test"
$env:PG_USERNAME="zqq"
$env:PG_PASSWORD="qq199499"
```

兼容旧变量名时可写为：

```powershell
$env:PGHOST="192.168.0.103"
$env:PGPORT="23083"
$env:PGDATABASE="zqq_test"
$env:PGUSER="zqq"
$env:PGPASSWORD="qq199499"
```

当前环境已固化 PostgreSQL 配置，直接启动即可：

```bash
cd /workspace/crm
.venv/bin/python ops/service_runner.py
```

当前环境的 Web 端口已固定为 `3000`，可通过 `.env` 中的 `APP_PORT` 调整。

## 打包发布

项目提供两套解压即用的发布包，分别面向 Windows 和 Linux。Windows 包不再内置虚拟环境，只包含源码、脚本、文档和配置模板，首次部署时可直接双击 `install_windows.cmd` 完成一键安装。

### Windows

```powershell
cd D:\Supie\supie-crm-windows
.\scripts\package_windows_no_venv.ps1
```

输出目录：

- `dist\windows-no-venv\supie-crm-windows-no-venv.zip`

解压后可以先双击 `install_windows.cmd` 自动安装；如果想手动部署，也请先阅读 `docs\windows_install_guide.md`。

### Linux

```bash
cd /workspace/crm
bash scripts/package_linux.sh
```

输出目录：

- `dist/linux/supie-crm-linux.tar.gz`

解压后可直接运行包根目录下的 `start_linux.sh`。

### 发布包说明

- 发布包不包含 PostgreSQL，数据库仍需单独部署。
- 首次启动前请检查包根目录的 `.env`，按实际 PostgreSQL 地址、库名和账号修改。
- 包内的启动入口统一调用 `ops/service_runner.py`，与当前运行方式保持一致。

## 默认账号

- 用户名：`admin`
- 密码：`admin123`
- 角色：`管理员`

可通过环境变量修改默认账号，仅首次初始化时生效：

```powershell
$env:DEFAULT_ADMIN_USER="admin"
$env:DEFAULT_ADMIN_PASSWORD="admin123"
```

## 测试

```bash
cd /workspace/crm
.venv/bin/python -m unittest discover -s tests -v
```

## 服务重启与开机自启

统一服务控制脚本：

```bash
cd /workspace/crm
chmod +x scripts/service_control.sh
./scripts/service_control.sh restart
```

常用手动命令：

```bash
cd /workspace/crm
./scripts/service_control.sh start
./scripts/service_control.sh stop
./scripts/service_control.sh restart
./scripts/service_control.sh status
```

配置 Linux 开机自启（systemd）：

```bash
cd /workspace/crm
chmod +x scripts/service_control.sh deploy/install_autostart.sh
sudo ./deploy/install_autostart.sh
```

安装完成后，系统每次重启都会自动拉起服务。也可以直接用 `systemctl` 手动管理：

```bash
sudo systemctl restart supie-crm.service
sudo systemctl status supie-crm.service --no-pager
sudo systemctl stop supie-crm.service
sudo systemctl start supie-crm.service
```

目录整理说明：

- 用户手册已移动到 `docs/manuals/`
- 服务入口已移动到 `ops/`
- 一次性维护脚本已移动到 `tools/maintenance/`
- 数据导出归档到 `data/backups/`
- 运行日志与 PID 文件统一写入 `logs/`

## 目录

- `app.py`: 单体 Flask 应用入口
- `templates/`: Jinja 页面模板
- `static/`: 静态资源
- `docs/`: 项目文档
- `tests/`: 启动与迁移测试
- `crm.db`: 本地 SQLite 数据库

## Windows 服务

管理员 PowerShell 执行：

```powershell
cd D:\Supie\supie-crm-windows
.\install_windows.cmd
```

服务名：

- `supie_crm`

完整步骤请参考 `docs\windows_install_guide.md`。
