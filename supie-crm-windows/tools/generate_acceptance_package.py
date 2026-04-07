from __future__ import annotations

from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[1]
SRC_PPT_ASSETS = ROOT / "docs" / "20260403" / "ppt_assets"
SRC_DELIVERY_ASSETS = ROOT / "docs" / "20260403" / "delivery_assets"
OUT_DIR = ROOT / "docs" / "验收资料"
ASSET_DIR = OUT_DIR / "assets"


def ensure_dirs() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "01-项目管理与验收类").mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "02-产品使用类").mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "03-技术部署与环境类").mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "04-开发与技术实现类").mkdir(parents=True, exist_ok=True)


def copy_assets() -> None:
    mapping = {
        "login.png": SRC_DELIVERY_ASSETS / "01-login.png",
        "workbench.png": SRC_DELIVERY_ASSETS / "02-workbench.png",
        "dashboard.png": SRC_PPT_ASSETS / "02-dashboard.png",
        "customers.png": SRC_PPT_ASSETS / "04-customers.png",
        "opportunities.png": SRC_PPT_ASSETS / "05-opportunities.png",
        "contracts.png": SRC_PPT_ASSETS / "06-contracts.png",
        "projects.png": SRC_PPT_ASSETS / "07-projects.png",
        "project-detail.png": SRC_PPT_ASSETS / "08-project-detail.png",
        "receivables.png": SRC_PPT_ASSETS / "09-receivables.png",
        "invoices.png": SRC_PPT_ASSETS / "10-invoices.png",
        "approvals.png": SRC_PPT_ASSETS / "11-approvals.png",
        "users.png": SRC_DELIVERY_ASSETS / "06-users.png",
        "roles.png": SRC_DELIVERY_ASSETS / "07-roles.png",
    }
    for target_name, source in mapping.items():
        if source.exists():
            shutil.copy2(source, ASSET_DIR / target_name)


def write(path: Path, text: str) -> None:
    path.write_text(text.strip() + "\n", encoding="utf-8")


def rel_asset(name: str) -> str:
    return f"../assets/{name}"


def header(title: str, owner: str, collaborators: str, scope: str) -> str:
    return f"""# {title}

## 基本信息
- 编写负责人：{owner}
- 协作人员：{collaborators}
- 适用范围：{scope}
- 文档日期：2026-04-03

"""


def readme_text() -> str:
    return """# 验收资料目录

本目录用于存放 `项目过程管理系统` 的交付、验收、使用、部署和技术实现资料。

## 建议分工
| 类别 | 牵头角色 | 协作角色 | 主要职责 |
| --- | --- | --- | --- |
| 项目管理与验收类 | 项目经理 | 开发负责人、测试负责人、实施人员 | 统一交付口径、验收结论、变更版本、上线和回滚安排 |
| 产品使用类 | 产品经理 / 测试负责人 | 开发负责人 | 编写用户手册、管理员手册、快速入门、FAQ，并配真实截图 |
| 技术部署与环境类 | 开发负责人 | 运维 / 实施人员 | 输出部署文档、安装步骤、环境配置、账号密钥清单 |
| 开发与技术实现类 | 开发负责人 | 架构 / 后端 / 前端 | 输出架构、数据库、接口、编码规范说明 |

## 目录结构
- `01-项目管理与验收类/`
- `02-产品使用类/`
- `03-技术部署与环境类/`
- `04-开发与技术实现类/`
- `assets/`

## 资料使用顺序
1. 先看 `01-项目管理与验收类`，确认交付范围、验收口径和版本说明。
2. 再看 `02-产品使用类`，让普通使用人员能快速上手。
3. 然后看 `03-技术部署与环境类`，完成部署和环境交接。
4. 最后看 `04-开发与技术实现类`，补足架构、数据库、接口和编码规范。

## 截图说明
本目录下 `assets/` 中的截图均来自真实浏览器环境，可直接插入用户手册、管理员手册和快速入门指南。
"""


def project_delivery_text() -> str:
    return f"""{header(
        "项目交付说明书",
        "项目经理",
        "开发负责人、测试负责人、实施人员",
        "甲方验收、交付清单、部署环境、版本范围",
    )}## 一、文档目的
这份说明书的作用，是让甲方第一眼就知道“本次交付了什么、怎么部署、哪些功能在这个版本里、由谁负责后续支持”。

## 二、系统概述
项目过程管理系统覆盖项目类型、工作台、客户、商机、合同、项目、回款、开票、审批、用户和角色权限等功能。系统前台采用中文界面，适合项目、销售、财务和管理人员共同使用。

## 三、交付范围
| 模块 | 说明 |
| --- | --- |
| 工作台与项目总览 | 查看待办、关注事项、经营概览和异常提醒 |
| 客户、商机、合同 | 支撑销售经营、签约转化和数据沉淀 |
| 项目、任务、里程碑、风险 | 支撑交付管理、过程跟踪和项目结项 |
| 回款与开票 | 支撑财务协同、现金流和票据管理 |
| 审批中心 | 支撑赢单、签约、结项三类审批 |
| 用户、角色与权限 | 支撑多角色协作和数据边界控制 |

## 四、建议交付清单
1. 可运行系统及部署说明。
2. 用户手册、管理员手册、快速入门指南。
3. 项目验收报告、变更记录、版本说明。
4. 技术类文档，包括部署、架构、数据库、接口等。

## 五、版本与环境
- 运行端口：`3000`
- 默认账号：`admin / admin123`
- Web 技术栈：Flask 单体应用，Jinja 模板，Bootstrap 风格页面
- 数据库：当前支持 PostgreSQL，项目也保留本地数据库能力

## 六、建议截图
### 总览
![总览]({rel_asset("dashboard.png")})

### 工作台
![工作台]({rel_asset("workbench.png")})

### 项目详情
![项目详情]({rel_asset("project-detail.png")})

## 七、交付验收口径
- 业务主流程可跑通。
- 权限、审批、回款、开票等关键节点可查看可验证。
- 交付文档齐全、命名统一、截图真实。
- 甲方可按文档独立完成系统查看、基础配置和日常操作。

## 八、签字栏
| 角色 | 姓名 | 签字 | 日期 |
| --- | --- | --- | --- |
| 甲方代表 |  |  |  |
| 项目经理 |  |  |  |
| 开发负责人 |  |  |  |
| 测试负责人 |  |  |  |
"""


def acceptance_report_text() -> str:
    return header(
        "项目验收报告",
        "项目经理",
        "测试负责人、开发负责人、实施人员",
        "功能验收、性能验收、安全验收、签字确认",
    ) + """## 一、验收说明
本报告用于记录当前版本的验收结果，适合甲方最终签字前的复核使用。建议在正式签字前，由测试人员和项目经理再跑一次真实环境确认。

## 二、验收范围
| 验收项 | 验收内容 | 结论建议 |
| --- | --- | --- |
| 功能验收 | 登录、工作台、客户、商机、合同、项目、回款、开票、审批、用户和角色权限 | 已覆盖主流程 |
| 性能验收 | 列表、详情、审批、导出、搜索等常用动作是否可稳定响应 | 建议按现场环境复测 |
| 安全验收 | 用户权限、角色权限、敏感信息、账号密码管理 | 已具备基础控制能力 |
| 交付验收 | 文档、截图、部署资料是否齐全 | 可交付 |

## 三、功能验收要点
- 登录后可进入工作台和总览页。
- 客户、商机、合同、项目、回款、开票、审批等主链路可查看。
- 项目详情页可进入任务、里程碑、风险和进展协同。
- 用户管理、角色管理和审批中心可以支撑日常管理动作。

## 四、建议结论
当前版本已经具备阶段性验收条件，建议以“业务主流程可用、交付资料齐全、权限边界清晰”为通过标准。

## 五、双方签字
| 角色 | 姓名 | 签字 | 日期 |
| --- | --- | --- | --- |
| 甲方代表 |  |  |  |
| 项目经理 |  |  |  |
| 测试负责人 |  |  |  |
| 开发负责人 |  |  |  |
"""


def requirements_text() -> str:
    return header(
        "需求规格说明书",
        "产品经理",
        "项目经理、测试负责人、开发负责人",
        "最终需求基线、功能边界、角色边界、验收依据",
    ) + """## 一、需求目标
本说明书的目标，是把当前系统“应该做什么”写清楚，作为后续开发、测试和验收的共同依据。

## 二、角色说明
| 角色 | 说明 |
| --- | --- |
| 管理员 | 系统维护、权限配置、全局兜底 |
| 管理层 | 查看经营结果和审批进度 |
| 销售 | 维护客户、商机、合同和回款相关业务 |
| 项目经理 | 推进项目交付、成员管理和结项审批 |
| 实施人员 | 参与项目执行和任务维护 |
| 财务 | 管理回款应收和开票 |
| 销售总监 | 管理销售线并负责赢单、签约审批 |
| 项目总监 | 管理项目线并负责结项审批 |

## 三、核心业务需求
| 模块 | 主要需求 |
| --- | --- |
| 工作台 | 显示待办、关注事项、经营指标和 AI 优先建议 |
| 客户管理 | 支持查询、导出、新增、详情和跟进记录 |
| 商机管理 | 支持阶段流转、赢单审批和列表查询 |
| 合同管理 | 支持签约审批、合同查询、与回款联动 |
| 项目管理 | 支持新建、详情、任务、里程碑、风险、进展和结项审批 |
| 回款管理 | 支持回款应收登记、查询与关联合同 |
| 开票管理 | 支持开票记录查询与管理 |
| 审批中心 | 统一处理赢单、签约、结项等流程 |
| 用户与角色 | 支持用户启停、角色配置和权限分配 |

## 四、非功能需求
- 页面必须使用中文，方便业务人员直接使用。
- 常用操作必须可在桌面浏览器中完成。
- 重要页面应有明确的提示、确认和错误反馈。
- 敏感操作必须受权限控制。
- 系统应保留导出和回收站等管理能力。

## 五、验收依据
1. 功能需求是否与当前页面和流程一致。
2. 关键角色是否能看到对应菜单和数据。
3. 业务主线是否可从客户一直走到项目、回款、开票和审批。
4. 交付资料是否与系统功能一致。
"""


def version_text() -> str:
    return header(
        "变更记录 / 版本说明",
        "项目经理",
        "开发负责人、测试负责人",
        "需求变更、迭代版本、修复记录、发布说明",
    ) + """## 一、版本编写原则
版本说明建议按时间线记录，不写“感觉变了很多”，而是写清楚“哪一天改了什么、为什么改、影响到哪里”。

## 二、建议时间线
| 日期 | 版本 | 主要内容 |
| --- | --- | --- |
| 2026-03-31 | V0.1 | 梳理客户、商机、合同、项目等四模块主流程，完成视觉初步统一 |
| 2026-04-01 | V0.2 | 优化详情页布局、列表页结构和项目管理子页面，提升可用性 |
| 2026-04-02 | V0.3 | 完成浏览器级测试、缺陷修复和回归验证 |
| 2026-04-03 | V1.0 | 整理权限矩阵、审批流程和演示材料，进入交付整理阶段 |

## 三、变更记录建议字段
| 字段 | 说明 |
| --- | --- |
| 版本号 | 例如 V1.0、V1.1 |
| 变更时间 | 具体日期 |
| 变更内容 | 本次改动点 |
| 变更原因 | 为什么要改 |
| 影响范围 | 涉及哪些模块 |
| 确认人 | 项目经理、产品、开发、测试 |

## 四、编写建议
- 尽量使用“对业务的影响”来描述变更，而不是只写技术术语。
- 版本说明要和实际发布包、截图、测试结果对应。
- 如果某个版本只是修复问题，也要写清楚修复对象和回归结果。
"""


def deployment_plan_text() -> str:
    return header(
        "上线部署方案 + 回滚方案",
        "开发负责人",
        "项目经理、测试负责人、实施人员",
        "上线步骤、责任人、时间节点、回滚流程、数据保护",
    ) + """## 一、部署前准备
1. 确认代码版本、数据库版本和部署环境。
2. 确认管理员账号、数据库账号和服务器权限。
3. 确认备份文件已完成，回滚方案可执行。
4. 确认端口 `3000` 未被其他服务占用。

## 二、推荐启动方式
### Windows
```powershell
cd D:\\Supie\\crm
python -m venv .venv
.\\.venv\\Scripts\\pip install -r requirements.txt
.\\.venv\\Scripts\\python app.py
```

### Linux
```bash
cd /workspace/crm
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

## 三、服务化运行
- 当前环境支持通过 `ops/service_runner.py` 或 Windows 服务方式启动。
- Linux 环境可使用 `scripts/service_control.sh` 管理启动、停止、重启和状态检查。
- Windows 环境可使用 `ops/windows_service.py` 注册服务。

## 四、上线步骤
1. 停止旧版本服务。
2. 备份数据库和上传文件。
3. 发布新代码。
4. 检查环境变量、数据库连接和配置文件。
5. 启动服务并访问 `http://127.0.0.1:3000` 验证登录页。
6. 逐页检查工作台、客户、项目、审批和财务页面。

## 五、回滚方案
### 适用场景
- 新版本无法登录。
- 页面报错或接口异常。
- 数据库迁移失败。
- 关键页面权限异常。

### 回滚步骤
1. 停止当前服务。
2. 恢复上一个稳定版本代码。
3. 恢复数据库备份或回滚迁移。
4. 重启服务。
5. 再次验证登录页、工作台和核心业务流程。

## 六、数据保障措施
- 上线前完成数据库备份。
- 上线后保留旧版本包和配置文件。
- 关键账号、密钥、证书独立保存。
"""


def user_manual_text() -> str:
    return header(
        "用户手册 / 操作手册",
        "产品经理 / 测试负责人",
        "开发负责人",
        "普通业务用户、项目经理、销售、财务、管理层",
    ) + f"""## 一、使用目标
这份手册给普通人看，目标是让第一次接触系统的人也能知道“先点哪里、再做什么、最后看到什么结果”。

## 二、登录与首页
1. 先打开登录页，输入账号、密码和验证码。
2. 登录后进入工作台或总览页。
3. 先看待办、关注事项，再看经营概览。

![登录页]({rel_asset("login.png")})

![工作台]({rel_asset("workbench.png")})

## 三、客户管理
客户管理用于维护客户信息、联系人和跟进记录。

### 常见操作
1. 在客户列表中筛选负责人、分级、状态和标签。
2. 点击客户名称进入客户详情。
3. 在详情页维护联系人和跟进记录。
4. 需要导出时使用右上角导出按钮。

![客户管理]({rel_asset("customers.png")})

## 四、商机管理
商机页面用于记录销售推进过程。

### 常见操作
1. 查看商机列表和阶段。
2. 进入详情页查看商机信息。
3. 商机达到赢单条件时提交审批。

## 五、合同管理
合同页面用于维护签约信息和后续履约。

### 常见操作
1. 在合同列表中查询合同编号或客户名称。
2. 进入合同详情查看关联合同信息。
3. 需要签约确认时发起审批。

## 六、项目管理
项目页面用于交付管理，是项目经理最常使用的页面。

### 常见操作
1. 在项目列表中查询项目。
2. 进入项目详情查看进度、阶段、成员、任务、里程碑和风险。
3. 需要提交结项时点击“提交结项审批”。
4. 在“进展汇报”里填写本周工作和问题。

![项目详情]({rel_asset("project-detail.png")})

## 七、回款与开票
1. 在回款列表查看计划金额和到账情况。
2. 在开票列表查看发票号码、类型和状态。
3. 财务人员以回款、开票页面为主进行日常处理。

![回款应收]({rel_asset("receivables.png")})

![开票管理]({rel_asset("invoices.png")})

## 八、审批中心
审批中心用于统一处理赢单、签约和结项审批。

### 常见操作
1. 打开审批列表查看待处理事项。
2. 查看审批详情和 AI 摘要。
3. 按业务情况选择同意或驳回。

![审批中心]({rel_asset("approvals.png")})

## 九、使用建议
- 先从工作台开始看，不要一上来就钻进细节页面。
- 遇到找不到功能时，先看左侧菜单和顶部标题。
- 如果提示无权限，说明当前角色不能做该动作，需要联系管理员。
"""


def admin_manual_text() -> str:
    return header(
        "管理员手册",
        "测试负责人 / 开发负责人",
        "项目经理、实施人员",
        "账号管理、角色配置、系统配置、运行监控",
    ) + f"""## 一、管理员职责
管理员主要负责“人、权限、配置、运行状态”四件事。

## 二、用户管理
在用户管理页面，管理员可以：
1. 新增用户。
2. 配置用户角色组合。
3. 启用或禁用用户。
4. 重置密码。

![用户管理]({rel_asset("users.png")})

## 三、角色与权限
在角色管理页面，管理员可以：
1. 查看系统内置角色和自定义角色。
2. 新建角色并分配权限。
3. 编辑角色权限。
4. 删除未绑定用户的自定义角色。

![角色与权限]({rel_asset("roles.png")})

## 四、权限配置建议
| 角色 | 建议职责 |
| --- | --- |
| 管理员 | 保留系统最高权限 |
| 管理层 | 只看全局，不做高频编辑 |
| 销售 | 客户、商机、合同、回款 |
| 项目经理 | 项目、任务、里程碑、风险、进展、结项审批 |
| 实施人员 | 任务协同与项目参与 |
| 财务 | 回款与开票 |

## 五、运行监控
- 检查服务是否能正常打开登录页。
- 检查关键菜单是否可见。
- 检查审批、导出、保存、删除等敏感操作是否都受权限控制。
- 定期备份数据库和文件。

## 六、常见管理员动作
1. 新员工入职后创建账号并配置角色。
2. 人员岗位变动后调整角色权限。
3. 项目阶段切换后检查相关审批权限是否正常。
4. 出现权限异常时优先检查用户角色组合。
"""


def quick_start_text() -> str:
    return header(
        "快速入门指南",
        "产品经理",
        "测试负责人",
        "第一次使用系统的业务人员",
    ) + f"""## 一、三分钟上手
1. 打开登录页，输入账号、密码和验证码。
2. 登录后先看工作台，确认今天要处理什么。
3. 去客户、项目或审批页面完成对应工作。

## 二、最常用的四步
### 第一步：登录
![登录页]({rel_asset("login.png")})

### 第二步：看工作台
![工作台]({rel_asset("workbench.png")})

### 第三步：找到业务对象
去客户、项目、回款、开票等列表页查找记录。

### 第四步：处理审批或维护记录
在详情页或审批中心完成提交、同意、驳回、编辑等操作。

## 三、常见提示
- 看不到菜单，多半是角色权限没配。
- 找不到记录，先用筛选条件查。
- 需要交接给别人时，先确认负责人字段。
"""


def faq_text() -> str:
    return header(
        "FAQ / 常见问题排查",
        "测试负责人",
        "项目经理、开发负责人",
        "用户常见问题、排查方法、解决方案",
    ) + """## 一、登录问题
### 1. 登录时验证码不通过怎么办？
先确认验证码大小写是否一致，再刷新页面重试。

### 2. 登录后页面空白怎么办？
先检查浏览器是否真的打开了 `3000` 端口的系统页面，再检查账号是否正常。

## 二、权限问题
### 3. 为什么我看不到某个菜单？
大概率是当前角色没有菜单权限，需要管理员给你补角色。

### 4. 为什么我能看列表，不能编辑？
列表可见不代表可编辑，操作权限和查看权限是分开的。

## 三、数据问题
### 5. 为什么列表里找不到某条记录？
先确认筛选条件、负责人、状态和分页，再确认你是否有数据权限。

### 6. 为什么导出后数量不对？
导出通常会跟随当前筛选条件，先检查查询条件是否过滤过多。

## 四、流程问题
### 7. 为什么审批按钮是灰色的？
说明当前状态不允许审批，或者你没有审批权限。

### 8. 为什么项目无法结项？
先检查项目进度、风险、成员和遗留事项是否已经处理。

## 五、系统问题
### 9. 页面显示异常或布局错乱怎么办？
优先刷新页面，再检查浏览器缩放、缓存和当前版本。

### 10. 出现 500 错误怎么办？
先确认是不是非法输入或空数据，若重复发生，需要把页面截图和操作步骤发给开发负责人。
"""


def deployment_text() -> str:
    return header(
        "部署文档",
        "开发负责人",
        "运维 / 实施人员",
        "服务器、中间件、数据库、端口、依赖组件",
    ) + """## 一、系统运行方式
系统基于 Flask 单体应用，页面通过 Jinja 模板渲染，静态资源放在 `static/`，主要业务入口放在 `routes_system.py`、`routes_business.py` 和 `routes_projects.py` 中。

## 二、运行环境
- Web 访问地址：`http://127.0.0.1:3000`
- 默认账号：`admin / admin123`
- 数据库：默认支持 PostgreSQL，当前环境可按项目配置直接启动

## 三、启动命令
### Windows
```powershell
cd D:\\Supie\\crm
python -m venv .venv
.\\.venv\\Scripts\\pip install -r requirements.txt
.\\.venv\\Scripts\\python app.py
```

### Linux
```bash
cd /workspace/crm
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

## 四、服务控制
- `scripts/service_control.sh start`
- `scripts/service_control.sh stop`
- `scripts/service_control.sh restart`
- `scripts/service_control.sh status`

## 五、部署检查项
1. 数据库连接是否正确。
2. 端口 `3000` 是否可访问。
3. 登录页是否可打开。
4. 工作台和关键业务页面是否能正常渲染。
5. 用户、角色和审批是否可正常使用。
"""


def install_steps_text() -> str:
    return header(
        "安装部署步骤",
        "开发负责人 / 实施人员",
        "项目经理",
        "自动化部署脚本 + 手动部署步骤",
    ) + """## 一、Windows 安装步骤
1. 安装 Python 和基础依赖。
2. 进入项目目录。
3. 创建虚拟环境并安装依赖。
4. 启动应用并检查登录页。

## 二、Linux 安装步骤
1. 准备 Python 环境。
2. 创建虚拟环境。
3. 安装依赖。
4. 运行服务并检查端口。

## 三、常用验证
- `http://127.0.0.1:3000/login`
- `http://127.0.0.1:3000/workbench`
- `http://127.0.0.1:3000/customers`

## 四、注意事项
- 如果数据库连接错误，先检查环境变量。
- 如果页面报错，先检查日志和最近改动。
- 上线后至少检查一次登录、列表和审批页。
"""


def env_config_text() -> str:
    return header(
        "环境配置清单",
        "运维 / 实施人员",
        "开发负责人、项目经理",
        "生产、测试、备份环境参数、路径和端口",
    ) + """## 一、环境清单
| 项目 | 生产环境 | 测试环境 | 备份环境 |
| --- | --- | --- | --- |
| 数据库类型 | PostgreSQL | PostgreSQL | PostgreSQL |
| Web 端口 | 3000（示例） | 3000（示例） | 同生产备份 |
| 配置文件 | `.env` | `.env.test`（如有） | 独立备份 |
| 日志目录 | `logs/` | `logs/` | 归档目录 |

## 二、关键变量
- `PG_HOST`
- `PG_PORT`
- `PG_DATABASE`
- `PG_USERNAME`
- `PG_PASSWORD`
- `APP_PORT`

## 三、交接建议
敏感配置如账号、密码、证书和密钥建议单独加密保存，不要直接写进公开文档。
"""


def architecture_text() -> str:
    return header(
        "系统架构设计文档",
        "开发负责人",
        "架构 / 后端 / 前端",
        "总体架构、模块划分、技术栈、运行方式",
    ) + """## 一、总体架构
系统采用单体 Flask 架构，前端通过 Jinja 模板渲染，核心业务按路由文件拆分，便于维护和交接。

```mermaid
flowchart LR
    Browser[Browser] --> App[FlaskApp]
    App --> SysRoutes[routes_system]
    App --> BizRoutes[routes_business]
    App --> ProjRoutes[routes_projects]
    SysRoutes --> Templates[templates]
    BizRoutes --> Templates
    ProjRoutes --> Templates
    App --> Static[static]
    App --> DB[(PostgreSQL/SQLite)]
```

## 二、模块划分
| 模块 | 说明 |
| --- | --- |
| 系统模块 | 登录、工作台、用户、角色、权限 |
| 业务模块 | 客户、商机、合同、审批、回款、开票 |
| 项目模块 | 项目、任务、里程碑、风险、进展、结项 |
| 支撑模块 | 导出、附件、AI 辅助、日志 |

## 三、技术栈
- 后端：Python + Flask
- 前端：Jinja 模板 + Bootstrap 风格组件
- 数据：PostgreSQL 为主，保留本地数据库能力
- 运行：`app.py` / `ops/service_runner.py`

## 四、权限设计
权限分为菜单权限、操作权限和数据权限三层，能够让不同岗位看到不同菜单、执行不同动作、访问不同数据。

## 五、交付建议
- 架构图、模块图和权限图建议放到甲方技术说明中。
- 如果后续做二开，建议保留路由分层和模板复用规范。
"""


def database_text() -> str:
    return header(
        "数据库设计文档",
        "开发负责人",
        "后端、测试、实施人员",
        "表结构、关系、索引、导入导出和数据字典",
    ) + """## 一、设计说明
以下内容按业务实体拆分，适合作为数据库说明草案。正式交付时，建议由研发根据实际数据库导出补齐字段类型和索引细节。

## 二、核心数据实体
| 实体 | 作用 | 典型关系 |
| --- | --- | --- |
| users | 系统用户 | 与 roles 通过 user_roles 关联 |
| roles | 角色定义 | 与 users 多对多 |
| customers | 客户信息 | 关联 contacts、follow-ups、opportunities、contracts |
| contacts | 联系人 | 隶属客户 |
| follow-ups | 跟进记录 | 归属客户和销售人员 |
| opportunities | 商机 | 关联客户和合同前流程 |
| contracts | 合同 | 关联客户和回款、开票 |
| projects | 项目 | 关联客户、合同和项目成员 |
| project_members | 项目成员 | 关联项目和用户 |
| tasks | 任务 | 归属项目 |
| milestones | 里程碑 | 归属项目 |
| risks | 风险 | 归属项目 |
| receivables | 回款应收 | 关联合同和项目 |
| invoices | 开票记录 | 关联合同和回款 |
| approvals | 审批单 | 关联商机、合同或项目 |
| attachments | 附件 | 关联业务对象 |
| activity_logs | 操作日志 | 记录管理员和业务操作 |

## 三、建议数据字段
- 主键：`id`
- 创建时间：`created_at`
- 更新时间：`updated_at`
- 状态字段：`status`
- 负责人字段：`owner`、`manager`、`applicant`、`approver`
- 业务编号字段：`contract_no`、`invoice_code` 等

## 四、数据交付建议
1. 导出实际表结构。
2. 按实体补充字段说明。
3. 标明主键、外键和索引。
4. 补充 SQL 脚本和初始化脚本。
"""


def api_text() -> str:
    return header(
        "接口文档",
        "开发负责人",
        "后端、测试、前端",
        "页面接口、审批接口、AI 辅助接口、导出接口",
    ) + """## 一、接口编写原则
接口文档应写明“接口做什么、怎么调用、返回什么、谁能调用”，这样测试和对接人员才好用。

## 二、主要接口清单
| 模块 | 方法 | 路径 | 说明 |
| --- | --- | --- | --- |
| 系统 | GET/POST | `/login` | 登录 |
| 系统 | GET | `/logout` | 退出登录 |
| 系统 | GET | `/workbench` | 工作台 |
| 系统 | GET | `/workbench/ai/priorities` | 工作台 AI 优先建议 |
| 用户 | GET/POST | `/users` | 用户管理 |
| 用户 | POST | `/users/<id>/toggle-active` | 启停用户 |
| 用户 | POST | `/users/<id>/reset-password` | 重置密码 |
| 用户 | POST | `/users/<id>/role` | 配置角色 |
| 角色 | GET/POST | `/roles` | 角色列表与创建 |
| 角色 | GET/POST | `/roles/<id>/edit` | 编辑角色 |
| 客户 | GET/POST | `/customers` | 客户列表与新增 |
| 客户 | GET | `/customers/<id>` | 客户详情 |
| 商机 | GET/POST | `/opportunities` | 商机列表与新增 |
| 商机 | GET | `/opportunities/<id>` | 商机详情 |
| 合同 | GET/POST | `/contracts` | 合同列表与新增 |
| 合同 | GET/POST | `/contracts/<id>` | 合同详情与编辑 |
| 项目 | GET/POST | `/projects` | 项目列表与新增 |
| 项目 | GET | `/projects/<id>` | 项目详情 |
| 项目 | POST | `/projects/<id>/progress` | 新增进展 |
| 项目 | POST | `/projects/<id>/submit-close-approval` | 提交结项审批 |
| 审批 | GET | `/approvals` | 审批列表 |
| 审批 | POST | `/approvals/<id>/approve` | 同意审批 |
| 审批 | POST | `/approvals/<id>/reject` | 驳回审批 |
| 审批 | POST | `/approvals/<id>/ai/summary` | 审批 AI 摘要 |
| 回款 | GET/POST | `/receivables` | 回款应收 |
| 开票 | GET/POST | `/invoices` | 开票管理 |
| 导出 | GET | `*/export` | 各列表导出 |

## 三、调用说明
- 页面类接口主要返回 HTML。
- AI 类接口返回 JSON。
- 列表导出接口返回文件。
- 敏感操作一般需要登录和权限校验。

## 四、接口交付建议
正式交付时建议补充：
1. 请求参数表。
2. 返回值示例。
3. 错误码说明。
4. 鉴权方式。
5. 调用示例。
"""


def coding_text() -> str:
    return header(
        "代码注释 / 规范说明",
        "开发负责人",
        "后端、前端、测试",
        "代码交付、注释要求、命名规范、关键逻辑说明",
    ) + """## 一、代码规范
- 路由文件按系统、业务、项目分层。
- 模板文件按页面类型组织，减少重复代码。
- 关键逻辑必须写注释，尤其是权限判断、审批逻辑和数据转换。
- 文件、函数、变量命名要保持一致。

## 二、注释要求
1. 关键分支必须有注释。
2. 复杂数据处理必须解释为什么这样写。
3. 权限判断和异常处理必须说明原因。
4. 对外交付源码时，关键函数要标明输入、输出和副作用。

## 三、测试配合
- 每个关键功能改动后，最好有对应测试或回归步骤。
- 变更说明要和测试结果一起保存。
- 用户手册里的操作路径，要和实际代码路径一致。

## 四、交付建议
如果要把源码交给甲方技术团队，建议同时提供：
1. 代码目录说明。
2. 关键配置说明。
3. 启动和测试命令。
4. 关键业务流程注释。
"""


def build_package() -> None:
    ensure_dirs()
    copy_assets()
    write(OUT_DIR / "README.md", readme_text())
    write(OUT_DIR / "01-项目管理与验收类" / "项目交付说明书.md", project_delivery_text())
    write(OUT_DIR / "01-项目管理与验收类" / "项目验收报告.md", acceptance_report_text())
    write(OUT_DIR / "01-项目管理与验收类" / "需求规格说明书.md", requirements_text())
    write(OUT_DIR / "01-项目管理与验收类" / "变更记录与版本说明.md", version_text())
    write(OUT_DIR / "01-项目管理与验收类" / "上线部署方案与回滚方案.md", deployment_plan_text())

    write(OUT_DIR / "02-产品使用类" / "用户手册.md", user_manual_text())
    write(OUT_DIR / "02-产品使用类" / "管理员手册.md", admin_manual_text())
    write(OUT_DIR / "02-产品使用类" / "快速入门指南.md", quick_start_text())
    write(OUT_DIR / "02-产品使用类" / "FAQ常见问题排查.md", faq_text())

    write(OUT_DIR / "03-技术部署与环境类" / "部署文档.md", deployment_text())
    write(OUT_DIR / "03-技术部署与环境类" / "安装部署步骤.md", install_steps_text())
    write(OUT_DIR / "03-技术部署与环境类" / "环境配置清单.md", env_config_text())

    write(OUT_DIR / "04-开发与技术实现类" / "系统架构设计文档.md", architecture_text())
    write(OUT_DIR / "04-开发与技术实现类" / "数据库设计文档.md", database_text())
    write(OUT_DIR / "04-开发与技术实现类" / "接口文档.md", api_text())
    write(OUT_DIR / "04-开发与技术实现类" / "代码注释与规范说明.md", coding_text())


def main() -> int:
    build_package()
    print(OUT_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
