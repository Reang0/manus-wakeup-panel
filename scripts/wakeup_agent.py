#!/usr/bin/env python3
"""
Manus 沙箱自动唤醒 Agent - 多实例管理版
=========================================
判断逻辑（修复版）：
  - 检查最近一条消息的时间戳
  - 若距今超过 IDLE_MINUTES 分钟没有任何活动，才发送唤醒消息
  - 避免沙箱活跃时重复发消息浪费额度
"""

import json
import os
import sys
import time
import logging
import fcntl
from datetime import datetime, timezone
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
STATE_FILE  = BASE_DIR / "state.json"
LOCK_FILE   = BASE_DIR / "run.lock"
LOG_FILE    = Path("/var/log/manus_wakeup.log")

BASE_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE.touch(exist_ok=True)

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

# ── 常量 ────────────────────────────────────
MANUS_API_BASE    = "https://api.manus.ai"
MAX_FAILURES      = 5     # 连续失败 N 次后自动注销
DEFAULT_IDLE_MIN  = 10    # 默认空闲阈值（分钟）
SETTINGS_FILE     = BASE_DIR / "settings.json"


def load_settings():
    """Load global settings, return defaults if not found."""
    defaults = {"idle_minutes": DEFAULT_IDLE_MIN}
    if not SETTINGS_FILE.exists():
        return defaults
    try:
        data = json.loads(SETTINGS_FILE.read_text())
        defaults.update(data)
        return defaults
    except Exception:
        return defaults


def save_settings(settings):
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2, ensure_ascii=False))


def get_idle_minutes():
    """Get current idle threshold in minutes."""
    return int(load_settings().get("idle_minutes", DEFAULT_IDLE_MIN))
# ════════════════════════════════════════════════════════
#  HTTP 工具
# ════════════════════════════════════════════════════════

def _headers(api_key):
    return {
        "x-manus-api-key": api_key,
        "User-Agent": "Mozilla/5.0 (compatible; ManusWakeupAgent/2.0)",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

def http_get(url, api_key, params=None):
    if USE_REQUESTS:
        r = _req.get(url, headers=_headers(api_key), params=params or {}, timeout=15)
        return r.json()
    else:
        from urllib.parse import urlencode
        full_url = url + ("?" + urlencode(params) if params else "")
        req = urllib.request.Request(full_url, headers=_headers(api_key))
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

def http_post(url, api_key, data):
    if USE_REQUESTS:
        r = _req.post(url, headers=_headers(api_key), json=data, timeout=30)
        return r.json()
    else:
        body = json.dumps(data).encode()
        req = urllib.request.Request(url, data=body, headers=_headers(api_key), method='POST')
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
#  核心判断逻辑：基于最近消息时间戳
# ════════════════════════════════════════════════════════

def get_last_message_time(api_key, task_id):
    """
    获取最近一条消息的时间戳（UTC）。
    返回 (datetime 或 None, is_invalid)
    - is_invalid=True 表示 Task 不存在或 API Key 无效，需要注销
    """
    try:
        data = http_get(
            f"{MANUS_API_BASE}/v2/task.listMessages",
            api_key,
            {"task_id": task_id, "order": "desc", "limit": 1}
        )
        if not data.get("ok"):
            err = data.get("error", {})
            code = err.get("code", "") if isinstance(err, dict) else ""
            if code in ("task_not_found", "unauthorized", "forbidden"):
                return None, True
            return None, False

        messages = data.get("messages", [])
        if not messages:
            return None, False

        # 解析时间戳（支持毫秒 Unix 时间戳 和 ISO 字符串）
        ts = messages[0].get("timestamp") or messages[0].get("created_at") or messages[0].get("createdAt")
        if not ts:
            return None, False

        # 毫秒级 Unix 时间戳（数字或数字字符串）
        try:
            ts_num = int(ts)
            # 判断是毫秒还是秒
            if ts_num > 1e12:
                ts_num = ts_num / 1000.0
            dt = datetime.fromtimestamp(ts_num, tz=timezone.utc)
            return dt, False
        except (ValueError, TypeError):
            pass

        # ISO 字符串格式
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                dt = datetime.strptime(str(ts)[:26], fmt)
                return dt.replace(tzinfo=timezone.utc), False
            except Exception:
                continue

        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            return dt, False
        except Exception:
            pass

        return None, False

    except Exception as e:
        logger.warning(f"[{task_id[:8]}] 获取消息时间异常: {e}")
        return None, False


def needs_wakeup(api_key, task_id, idle_min=None):
    """
    判断是否需要发送唤醒消息。
    返回 (bool: 是否需要唤醒, bool: 是否需要注销)
    """
    if idle_min is None:
        idle_min = get_idle_minutes()

    last_time, is_invalid = get_last_message_time(api_key, task_id)

    if is_invalid:
        return False, True

    if last_time is None:
        # 获取失败，保守起见不发消息
        logger.warning(f"[{task_id[:8]}] 无法获取最近消息时间，跳过本次")
        return False, False

    now_utc = datetime.now(timezone.utc)
    idle_seconds = (now_utc - last_time).total_seconds()
    idle_minutes = idle_seconds / 60

    logger.info(f"[{task_id[:8]}] 最近消息距今 {idle_minutes:.1f} 分钟（阈值 {idle_min} 分钟）")

    if idle_minutes >= idle_min:
        logger.info(f"[{task_id[:8]}] 超过阈值，需要唤醒")
        return True, False
    else:
        logger.info(f"[{task_id[:8]}] 沙箱活跃，无需唤醒")
        return False, False


def send_wakeup(api_key, task_id, message):
    """发送唤醒消息"""
    try:
        data = http_post(
            f"{MANUS_API_BASE}/v2/task.sendMessage",
            api_key,
            {"task_id": task_id, "message": {"role": "user", "content": message}}
        )
        if data.get("ok"):
            return True, None
        err = data.get("error", {})
        code = err.get("code", "unknown") if isinstance(err, dict) else str(err)
        return False, code
    except Exception as e:
        return False, str(e)


# ════════════════════════════════════════════════════════
#  处理单个实例
# ════════════════════════════════════════════════════════

def process_instance(instance, state):
    task_id = instance["task_id"]
    api_key = instance["api_key"]
    message = instance.get("message", "[自动唤醒] 请执行 bash /home/ubuntu/service_restart.sh 重启所有服务")
    short_id = task_id[:8]

    if task_id not in state:
        state[task_id] = {"failures": 0, "last_ok": None, "last_run": None, "status": "active"}

    s = state[task_id]
    s["last_run"] = datetime.now().isoformat()

    idle_min = get_idle_minutes()
    wakeup_needed, is_invalid = needs_wakeup(api_key, task_id, idle_min)

    if is_invalid:
        logger.warning(f"[{short_id}] Task 已失效，自动注销")
        s["status"] = "invalid"
        return state, True

    if not wakeup_needed:
        s["failures"] = 0
        s["status"] = "active"
        return state, False

    # 发送唤醒消息
    logger.info(f"[{short_id}] 发送唤醒消息...")
    ok, err_code = send_wakeup(api_key, task_id, message)

    if ok:
        s["failures"] = 0
        s["last_ok"] = datetime.now().isoformat()
        s["status"] = "active"
        logger.info(f"[{short_id}] 唤醒消息发送成功")
    else:
        s["failures"] += 1
        logger.warning(f"[{short_id}] 唤醒失败: {err_code}，累计 {s['failures']}/{MAX_FAILURES} 次")
        if err_code in ("task_not_found", "unauthorized", "forbidden"):
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
    lock_fd = open(LOCK_FILE, 'w')
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        logger.info("另一个实例正在运行，跳过本次执行")
        return

    try:
        config = load_config()
        state  = load_state()
        instances = config.get("instances", [])

        if not instances:
            logger.info("没有配置任何实例，退出")
            return

        idle_min = get_idle_minutes()
        logger.info(f"开始检查 {len(instances)} 个实例（空闲阈值: {idle_min} 分钟）")
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

        if to_remove:
            config["instances"] = [i for i in instances if i["task_id"] not in to_remove]
            logger.warning(f"已自动注销 {len(to_remove)} 个失效实例")
            save_config(config)

        save_state(state)
        logger.info(f"检查完成，活跃实例: {len(config['instances'])} 个")

    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


# ════════════════════════════════════════════════════════
#  管理命令
# ════════════════════════════════════════════════════════

def cmd_add(api_key, task_id, message=None):
    config = load_config()
    for inst in config["instances"]:
        if inst["task_id"] == task_id:
            print(f"Task ID {task_id[:8]}... 已存在")
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
    config = load_config()
    before = len(config["instances"])
    config["instances"] = [i for i in config["instances"] if i["task_id"] != task_id]
    if len(config["instances"]) < before:
        save_config(config)
        print(f"✓ 已移除: {task_id[:8]}...")
    else:
        print(f"未找到: {task_id}")

def cmd_list():
    config = load_config()
    state  = load_state()
    instances = config.get("instances", [])
    if not instances:
        print("当前没有配置任何实例")
        return
    print(f"\n{'Task ID':>12}  {'状态':>8}  {'失败次数':>8}  {'最后成功时间'}")
    print("-" * 60)
    for inst in instances:
        tid = inst["task_id"]
        s = state.get(tid, {})
        print(f"  {tid[:12]}  {s.get('status','unknown'):>8}  {s.get('failures',0):>8}  {(s.get('last_ok','从未') or '从未')[:19]}")
    print()

def cmd_status():
    config = load_config()
    print(f"\n实例总数: {len(config.get('instances', []))}")
    print(f"空闲阈值: {get_idle_minutes()} 分钟（可用 set-threshold <分钟数> 修改）")
    print(f"日志文件: {LOG_FILE}")
    try:
        lines = LOG_FILE.read_text().splitlines()[-10:]
        print("最近日志:")
        for line in lines:
            print(f"  {line}")
    except Exception:
        print("  (暂无日志)")


# ════════════════════════════════════════════════════════
#  入口
# ════════════════════════════════════════════════════════

def cmd_set_threshold(minutes):
    try:
        m = int(minutes)
        if m < 1:
            print("错误：阈值最小为 1 分钟")
            return
        settings = load_settings()
        settings["idle_minutes"] = m
        save_settings(settings)
        print(f"✓ 空闲阈值已设置为 {m} 分钟")
    except ValueError:
        print(f"错误：无效的分钟数: {minutes}")


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
    elif args[0] == "set-threshold" and len(args) >= 2:
        cmd_set_threshold(args[1])
    elif args[0] == "get-threshold":
        print(f"当前空闲阈值: {get_idle_minutes()} 分钟")
    else:
        print("""
用法：
  python3 wakeup_agent.py run                              # 执行一次检查
  python3 wakeup_agent.py add <api_key> <task_id> [msg]   # 添加实例
  python3 wakeup_agent.py remove <task_id>                 # 移除实例
  python3 wakeup_agent.py list                             # 列出所有实例
  python3 wakeup_agent.py status                           # 查看状态
  python3 wakeup_agent.py set-threshold <分钟数>             # 设置空闲阈值（默认 10 分钟）
  python3 wakeup_agent.py get-threshold                    # 查看当前阈值
        """)
