# Manus 沙箱自动唤醒管理面板

> 自动检测 Manus 沙箱休眠状态，每分钟发送唤醒消息并重启服务，支持多实例管理、失效自动注销、实时日志查看。

![License](https://img.shields.io/badge/license-MIT-blue)
![Python](https://img.shields.io/badge/python-3.8%2B-green)
![Platform](https://img.shields.io/badge/platform-Linux-lightgrey)

---

## 功能特性

- **自动唤醒**：每分钟检测沙箱状态，休眠时自动发送唤醒消息
- **多实例管理**：同时管理多个不同 API Key / Task ID 的沙箱
- **失效自动注销**：连续失败 5 次或 Task ID 不存在时，自动从列表移除
- **防重复执行**：文件锁机制，防止 cron 并发触发时重复执行
- **Web 管理面板**：可视化管理所有实例，无需命令行操作
- **实时日志**：SSE 流式推送，浏览器实时查看运行日志
- **一键清除**：清空所有实例、状态、停止 cron，带二次确认
- **开机自启**：systemd 服务，服务器重启后面板自动恢复

---

## 目录结构

```
manus-wakeup-panel/
├── panel/
│   ├── app.py              # Flask 后端（管理面板 API）
│   └── templates/
│       └── index.html      # 前端管理界面
├── scripts/
│   ├── wakeup_agent.py     # 核心唤醒 Agent（cron 调用）
│   ├── install.sh          # 一键安装脚本（完整版，含面板）
│   ├── install_lite.sh     # 轻量安装脚本（仅唤醒脚本）
│   └── uninstall.sh        # 一键卸载脚本
└── README.md
```

---

## 快速开始

### 方式一：一键安装完整面板（推荐）

在你的服务器上以 **root** 权限执行：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/YOUR_USERNAME/manus-wakeup-panel/main/scripts/install.sh)
```

安装完成后，浏览器打开 `http://你的服务器IP:7788` 即可使用管理面板。

### 方式二：轻量安装（仅唤醒脚本，无面板）

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/YOUR_USERNAME/manus-wakeup-panel/main/scripts/install_lite.sh)
```

---

## 使用教程

### 第一步：获取 Manus API Key

1. 登录 [manus.im](https://manus.im)
2. 进入 **Settings → API**
3. 点击 **Create API Key** 生成密钥
4. 复制保存（格式：`sk-xxxxxxxx...`）

### 第二步：获取 Task ID

Task ID 是你与 Manus 对话的唯一标识符。

- 在 Manus 对话页面，URL 中或任务列表中可以找到
- 格式示例：`xUGt4WyQr7KPHVWCtiGvaI`

### 第三步：添加实例

打开管理面板（`http://服务器IP:7788`），在左侧表单填入：

| 字段 | 说明 |
|------|------|
| **Manus API Key** | 第一步获取的密钥 |
| **Task ID** | 第二步获取的对话 ID |
| **唤醒指令** | 可选，留空使用默认指令 |

点击 **添加实例**，系统会自动验证 API Key 和 Task ID 是否有效。

### 第四步：启用定时唤醒

点击控制面板中的 **▶ 启用定时唤醒**，cron 每分钟自动执行一次唤醒检查。

---

## 管理面板说明

### 实例状态

| 状态 | 含义 |
|------|------|
| 🟢 `active` | 正常运行 |
| 🔴 `invalid` | Task ID 不存在或 API Key 无效，已自动注销 |
| 🟡 `auto_removed` | 连续失败 5 次，已自动注销 |

### API 接口

面板提供以下 REST API，可供外部调用：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/instances` | 获取所有实例列表 |
| POST | `/api/instances/add` | 添加实例 |
| POST | `/api/instances/remove` | 移除实例 |
| POST | `/api/instances/clear_all` | 清除所有实例和配置 |
| GET | `/api/cron/status` | 查看 cron 状态 |
| POST | `/api/cron/enable` | 启用 cron |
| POST | `/api/cron/disable` | 禁用 cron |
| POST | `/api/run_now` | 立即执行一次唤醒检查 |
| GET | `/api/logs` | 获取日志（`?lines=100`） |
| GET | `/api/logs/stream` | SSE 实时日志流 |
| POST | `/api/logs/clear` | 清空日志 |

---

## 命令行使用（无面板）

```bash
# 添加实例
python3 /usr/local/bin/manus_wakeup_agent.py add <api_key> <task_id> [唤醒消息]

# 移除实例
python3 /usr/local/bin/manus_wakeup_agent.py remove <task_id>

# 列出所有实例
python3 /usr/local/bin/manus_wakeup_agent.py list

# 查看状态和最近日志
python3 /usr/local/bin/manus_wakeup_agent.py status

# 手动执行一次唤醒检查
python3 /usr/local/bin/manus_wakeup_agent.py run
```

---

## 常用运维命令

```bash
# 查看面板服务状态
systemctl status manus-panel

# 重启面板
systemctl restart manus-panel

# 查看实时日志
tail -f /var/log/manus_wakeup.log

# 查看 cron 任务
crontab -l | grep manus

# 手动执行一次唤醒
python3 /usr/local/bin/manus_wakeup_agent.py run
```

---

## 卸载

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/YOUR_USERNAME/manus-wakeup-panel/main/scripts/uninstall.sh)
```

卸载将删除：
- systemd 服务
- cron 定时任务
- 所有配置文件（`/etc/manus_wakeup/`）
- 日志文件（`/var/log/manus_wakeup.log`）
- 安装的脚本文件

---

## 文件说明

| 文件 | 说明 |
|------|------|
| `panel/app.py` | Flask 后端，提供 REST API 和静态文件服务 |
| `panel/templates/index.html` | 前端管理界面，纯 HTML/CSS/JS，无需构建 |
| `scripts/wakeup_agent.py` | 核心唤醒逻辑，由 cron 每分钟调用 |
| `scripts/install.sh` | 一键安装脚本，自动配置 systemd + cron |
| `scripts/install_lite.sh` | 轻量版安装，仅配置 cron 唤醒脚本 |
| `scripts/uninstall.sh` | 完整卸载脚本 |

---

## 工作原理

```
你的服务器（cron 每分钟）
    ↓
wakeup_agent.py 调用 Manus API
    ↓
检查 Task 状态
    ├── running → 跳过（沙箱正常运行）
    └── stopped/waiting → 发送唤醒消息
            ↓
        Manus 收到消息，沙箱唤醒
            ↓
        执行服务重启脚本
            ↓
        代理/Web 服务恢复在线
```

---

## 注意事项

- 本工具依赖 **Manus API**，需要有效的 API Key
- 沙箱唤醒后服务不会自动恢复，需要在唤醒消息中包含重启指令
- 建议在唤醒消息中指定具体的重启脚本路径
- API Key 请妥善保管，不要泄露到公开环境

---

## License

MIT License
