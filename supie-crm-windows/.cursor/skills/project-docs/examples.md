# 项目文档示例

## 1. README 片段示例

````markdown
## 运行环境

Windows PowerShell：

```powershell
cd D:\Supie\supie-crm-windows
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python app.py
```

访问地址：

- http://127.0.0.1:3000
````

## 2. 安装指南片段示例

````markdown
## 6. 先做一次手动启动验证

在注册成 Windows 服务之前，建议先手动跑一次，确认项目本身是能启动的。

运行：

```powershell
.\.venv\Scripts\python ops\service_runner.py
```

如果页面无法访问，优先检查数据库、依赖和配置，而不是先排查服务注册。
````

## 3. FAQ 片段示例

````markdown
### 启动后浏览器打不开 3000 端口

现象：
- 服务显示已运行，但浏览器访问 `http://127.0.0.1:3000` 失败。

原因：
- 数据库连接失败
- 依赖未安装完成
- 端口被其他程序占用

解决方法：
1. 检查 `.env` 中的 PostgreSQL 配置。
2. 确认 `.venv` 已创建并已安装依赖。
3. 检查 3000 端口是否被占用。

验证：
- 再次访问 `http://127.0.0.1:3000`
````

## 4. 文档写作提示

- 先写结论，再写步骤。
- 每个步骤尽量能被直接执行或验证。
- 术语统一使用仓库中的现有命名。
- 不知道的内容不要补脑，直接标记“待确认”。

