#!/usr/bin/env python3
"""
Manus 沙箱自动唤醒 Agent - 多实例管理版
=========================================
功能：
  - 支持多个 Task ID 同时管理（每个独立配置）
  - 失效检测：连续失败 N 次后自动从配置中注销该实例
  - 防重复：文件锁防止同一 Task ID 被并发执行
  - 状态持久化：记录每个实例的运行状态和失败次数
  - 统一日志：所有实例日志写入同一文件，带 Task ID 标识
"""

import json
import os
import sys
import time
import logging
import fcntl
import signal
from datetime import datetime
from pathlib import Path

try:
    import requests as _req
    USE_REQUESTS = True
except ImportError:
    import urllib.request
    import urllib.error
    USE_REQUESTS = False

# ── 路径配置 ────────────────────────────────────────────
BASE_DIR = Path("/etc/manus_wakeup")
CONFIG_FILE = BASE_DIR / "instances.json"
STATE_FILE = BASE_DIR / "state.json"
LOCK_FILE = BASE_DIR / "run.lock"
LOG_FILE = Path("/var/log/manus_wakeup.log")

BASE_DIR.mkdir(parents=True, exist_ok=True)

# ── 日志 ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ── 常量 ────────────────────────────────────────────────
MANUS_API_BASE = "https://api.manus.ai"
MAX_FAILURES = 5        # 连续失败 5 次后自动注销该实例
LOCK_TIMEOUT = 55       # 锁超时（秒），防止上次进程卡死


# ════════════════════════════════════════════════════════
#  HTTP 工具（兼容无 requests 环境）
# ════════════════════════════════════════════════════════

def http_get(url, headers, params=None):
    if USE_REQUESTS:
        r = _req.get(url, headers=headers, params=params or {}, timeout=15)
        return r.json()
    else:
        from urllib.parse import urlencode
        full_url = url + ("?" + urlencode(params) if params else "")
        req = urllib.request.Request(full_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())


def http_post(url, headers, data):
    if USE_REQUESTS:
        r = _req.post(url, headers=headers, json=data, timeout=30)
        return r.json()
    else:
        body = json.dumps(data).encode()
        req = urllib.request.Request(url, data=body, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())


# ════════════════════════════════════════════════════════
#  配置管理
# ════════════════════════════════════════════════════════

def load_config():
    if not CONFIG_FILE.exists():
        return {"instances": []}
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {"instances": []}


def save_config(config):
    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False))


def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


# ════════════════════════════════════════════════════════
#  核心唤醒逻辑
# ════════════════════════════════════════════════════════

def get_task_status(api_key, task_id):
    """获取任务当前状态"""
    try:
        data = http_get(
            f"{MANUS_API_BASE}/v2/task.listMessages",
            {"x-manus-api-key": api_key},
            {"task_id": task_id, "order": "desc", "limit": 5}
        )
        if not data.get("ok"):
            err = data.get("error", {})
            # task_not_found 或 unauthorized 视为永久失效
            if err.get("code") in ("task_not_found", "unauthorized", "forbidden"):
                return "INVALID"
            return None
        for m in data.get("messages", []):
            if m.get("type") == "status_update":
                return m["status_update"]["agent_status"]
        return "stopped"
    except Exception as e:
        logger.warning(f"[{task_id[:8]}] 获取状态异常: {e}")
        return None


def send_wakeup(api_key, task_id, message):
    """发送唤醒消息"""
    try:
        data = http_post(
            f"{MANUS_API_BASE}/v2/task.sendMessage",
            {"x-manus-api-key": api_key, "Content-Type": "application/json"},
            {"task_id": task_id, "message": {"role": "user", "content": message}}
        )
        if data.get("ok"):
            return True, None
        err = data.get("error", {})
        return False, err.get("code", "unknown")
    except Exception as e:
        return False, str(e)


def process_instance(instance, state):
    """处理单个实例的唤醒逻辑，返回更新后的 state 和是否应注销"""
    task_id = instance["task_id"]
    api_key = instance["api_key"]
    message = instance.get("message", "[自动唤醒] 请执行 bash /home/ubuntu/service_restart.sh")
    short_id = task_id[:8]

    # 初始化状态
    if task_id not in state:
        state[task_id] = {"failures": 0, "last_ok": None, "last_run": None, "status": "active"}

    s = state[task_id]
    s["last_run"] = datetime.now().isoformat()

    # 获取任务状态
    status = get_task_status(api_key, task_id)
    logger.info(f"[{short_id}] 任务状态: {status}")

    # 永久失效（Task 不存在或 API Key 无效）
    if status == "INVALID":
        logger.warning(f"[{short_id}] 任务已失效（不存在或无权限），自动注销此实例")
        s["status"] = "invalid"
        return state, True  # 标记为需要注销

    # 获取状态失败（网络问题等临时错误）
    if status is None:
        s["failures"] += 1
        logger.warning(f"[{short_id}] 获取状态失败，累计失败 {s['failures']}/{MAX_FAILURES} 次")
        if s["failures"] >= MAX_FAILURES:
            logger.error(f"[{short_id}] 连续失败 {MAX_FAILURES} 次，自动注销此实例")
            s["status"] = "auto_removed"
            return state, True
        return state, False

    # 任务正在运行，无需唤醒
    if status == "running":
        s["failures"] = 0
        s["last_ok"] = datetime.now().isoformat()
        s["status"] = "active"
        logger.info(f"[{short_id}] 运行中，跳过唤醒")
        return state, False

    # 任务已停止/休眠，发送唤醒消息
    logger.info(f"[{short_id}] 沙箱已休眠，发送唤醒消息...")
    ok, err_code = send_wakeup(api_key, task_id, message)

    if ok:
        s["failures"] = 0
        s["last_ok"] = datetime.now().isoformat()
        s["status"] = "active"
        logger.info(f"[{short_id}] 唤醒消息发送成功")
    else:
        s["failures"] += 1
        logger.warning(f"[{short_id}] 唤醒失败: {err_code}，累计 {s['failures']}/{MAX_FAILURES} 次")
        # 特定错误码直接注销
        if err_code in ("task_not_found", "unauthorized", "forbidden"):
            logger.error(f"[{short_id}] 永久性错误 [{err_code}]，自动注销")
            s["status"] = "invalid"
            return state, True
        if s["failures"] >= MAX_FAILURES:
            logger.error(f"[{short_id}] 连续失败 {MAX_FAILURES} 次，自动注销")
            s["status"] = "auto_removed"
            return state, True

    return state, False


# ════════════════════════════════════════════════════════
#  主执行逻辑（带文件锁防并发）
# ════════════════════════════════════════════════════════

def run():
    """主运行函数"""
    # 文件锁：防止 cron 并发执行
    lock_fd = open(LOCK_FILE, 'w')
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        logger.info("另一个实例正在运行，跳过本次执行")
        return

    try:
        config = load_config()
        state = load_state()
        instances = config.get("instances", [])

        if not instances:
            logger.info("没有配置任何实例，退出")
            return

        logger.info(f"开始检查 {len(instances)} 个实例")
        to_remove = []

        for instance in instances:
            task_id = instance.get("task_id", "")
            if not task_id or not instance.get("api_key"):
                continue
            try:
                state, should_remove = process_instance(instance, state)
                if should_remove:
                    to_remove.append(task_id)
            except Exception as e:
                logger.error(f"[{task_id[:8]}] 处理异常: {e}")

        # 注销失效实例
        if to_remove:
            original_count = len(instances)
            config["instances"] = [i for i in instances if i["task_id"] not in to_remove]
            removed_count = original_count - len(config["instances"])
            logger.warning(f"已自动注销 {removed_count} 个失效实例: {[t[:8] for t in to_remove]}")
            save_config(config)

        save_state(state)
        logger.info(f"本次检查完成，活跃实例: {len(config['instances'])} 个")

    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


# ════════════════════════════════════════════════════════
#  管理命令
# ════════════════════════════════════════════════════════

def cmd_add(api_key, task_id, message=None):
    """添加一个实例"""
    config = load_config()
    # 检查是否已存在
    for inst in config["instances"]:
        if inst["task_id"] == task_id:
            print(f"Task ID {task_id[:8]}... 已存在，跳过添加")
            return
    config["instances"].append({
        "task_id": task_id,
        "api_key": api_key,
        "message": message or "[自动唤醒] 请执行 bash /home/ubuntu/service_restart.sh 重启所有服务",
        "added_at": datetime.now().isoformat()
    })
    save_config(config)
    print(f"✓ 已添加实例: {task_id[:8]}...")


def cmd_remove(task_id):
    """移除一个实例"""
    config = load_config()
    before = len(config["instances"])
    config["instances"] = [i for i in config["instances"] if i["task_id"] != task_id]
    if len(config["instances"]) < before:
        save_config(config)
        print(f"✓ 已移除实例: {task_id[:8]}...")
    else:
        print(f"未找到实例: {task_id}")


def cmd_list():
    """列出所有实例"""
    config = load_config()
    state = load_state()
    instances = config.get("instances", [])
    if not instances:
        print("当前没有配置任何实例")
        return
    print(f"\n{'Task ID':>12}  {'状态':>8}  {'失败次数':>8}  {'最后成功时间'}")
    print("-" * 60)
    for inst in instances:
        tid = inst["task_id"]
        s = state.get(tid, {})
        status = s.get("status", "unknown")
        failures = s.get("failures", 0)
        last_ok = s.get("last_ok", "从未")[:19] if s.get("last_ok") else "从未"
        print(f"  {tid[:12]}  {status:>8}  {failures:>8}  {last_ok}")
    print()


def cmd_status():
    """查看详细状态"""
    config = load_config()
    state = load_state()
    print(f"\n实例总数: {len(config.get('instances', []))}")
    print(f"配置文件: {CONFIG_FILE}")
    print(f"日志文件: {LOG_FILE}")
    print(f"最近日志:")
    try:
        lines = LOG_FILE.read_text().splitlines()[-10:]
        for line in lines:
            print(f"  {line}")
    except Exception:
        print("  (暂无日志)")


# ════════════════════════════════════════════════════════
#  入口
# ════════════════════════════════════════════════════════

if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] == "run":
        run()
    elif args[0] == "add" and len(args) >= 3:
        cmd_add(args[1], args[2], args[3] if len(args) > 3 else None)
    elif args[0] == "remove" and len(args) >= 2:
        cmd_remove(args[1])
    elif args[0] == "list":
        cmd_list()
    elif args[0] == "status":
        cmd_status()
    else:
        print("""
用法：
  python3 manus_wakeup_agent.py run              # 执行一次检查（cron 调用）
  python3 manus_wakeup_agent.py add <api_key> <task_id> [message]  # 添加实例
  python3 manus_wakeup_agent.py remove <task_id>  # 移除实例
  python3 manus_wakeup_agent.py list              # 列出所有实例
  python3 manus_wakeup_agent.py status            # 查看状态和日志
        """)
