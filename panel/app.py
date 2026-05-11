#!/usr/bin/env python3
"""
Manus 沙箱唤醒管理面板 - 后端
"""
import json
import os
import subprocess
import sys
import time
import threading
import logging
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, jsonify, Response, stream_with_context

app = Flask(__name__)

# ── 路径配置 ────────────────────────────────────────────
BASE_DIR = Path("/etc/manus_wakeup")
CONFIG_FILE = BASE_DIR / "instances.json"
STATE_FILE = BASE_DIR / "state.json"
LOG_FILE = Path("/var/log/manus_wakeup.log")
AGENT_SCRIPT = Path("/usr/local/bin/manus_wakeup_agent.py")
BASE_DIR.mkdir(parents=True, exist_ok=True)

MANUS_API_BASE = "https://api.manus.ai"
SETTINGS_FILE = BASE_DIR / "settings.json"
DEFAULT_IDLE_MIN = 10

def load_settings():
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

# ── 工具函数 ────────────────────────────────────────────
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

def get_python():
    for cmd in ["python3", "python3.11", "python3.10", "python"]:
        if os.path.exists(f"/usr/bin/{cmd}") or os.path.exists(f"/usr/local/bin/{cmd}"):
            return cmd
    return "python3"

def write_log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} [PANEL] {msg}\n"
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line)
    except Exception:
        pass

# ── API 路由 ────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/instances", methods=["GET"])
def get_instances():
    config = load_config()
    state = load_state()
    result = []
    for inst in config.get("instances", []):
        tid = inst["task_id"]
        s = state.get(tid, {})
        result.append({
            "task_id": tid,
            "task_id_short": tid[:12] + "...",
            "api_key_short": inst["api_key"][:16] + "...",
            "message": inst.get("message", ""),
            "added_at": inst.get("added_at", ""),
            "status": s.get("status", "active"),
            "failures": s.get("failures", 0),
            "last_ok": s.get("last_ok", "")[:19] if s.get("last_ok") else "从未",
            "last_run": s.get("last_run", "")[:19] if s.get("last_run") else "从未",
        })
    return jsonify({"ok": True, "instances": result, "total": len(result)})

@app.route("/api/instances/add", methods=["POST"])
def add_instance():
    data = request.json or {}
    api_key = data.get("api_key", "").strip()
    task_id = data.get("task_id", "").strip()
    message = data.get("message", "").strip()

    if not api_key or not task_id:
        return jsonify({"ok": False, "error": "API Key 和 Task ID 不能为空"})

    # 验证 API Key + Task ID 是否有效
    try:
        import requests as req_lib
        url = f"{MANUS_API_BASE}/v2/task.listMessages"
        headers = {
            "x-manus-api-key": api_key,
            "User-Agent": "Mozilla/5.0 (compatible; ManusPanel/1.0)",
            "Accept": "application/json"
        }
        resp = req_lib.get(url, params={"task_id": task_id, "limit": 1}, headers=headers, timeout=10)
        result = resp.json()
        if not result.get("ok"):
            err = result.get("error", {})
            if isinstance(err, dict):
                msg = err.get("message", "无效的 API Key 或 Task ID")
            else:
                msg = str(err)
            return jsonify({"ok": False, "error": f"验证失败: {msg}"})
    except Exception as e:
        return jsonify({"ok": False, "error": f"验证请求失败: {str(e)}"})


    config = load_config()
    for inst in config["instances"]:
        if inst["task_id"] == task_id:
            return jsonify({"ok": False, "error": "该 Task ID 已存在"})

    config["instances"].append({
        "task_id": task_id,
        "api_key": api_key,
        "message": message or "[自动唤醒] 请执行 bash /home/ubuntu/service_restart.sh 重启所有服务",
        "added_at": datetime.now().isoformat()
    })
    save_config(config)
    write_log(f"添加实例: {task_id[:12]}")
    return jsonify({"ok": True, "message": f"实例 {task_id[:12]}... 添加成功"})

@app.route("/api/instances/remove", methods=["POST"])
def remove_instance():
    data = request.json or {}
    task_id = data.get("task_id", "").strip()
    if not task_id:
        return jsonify({"ok": False, "error": "Task ID 不能为空"})

    config = load_config()
    before = len(config["instances"])
    config["instances"] = [i for i in config["instances"] if i["task_id"] != task_id]
    if len(config["instances"]) == before:
        return jsonify({"ok": False, "error": "未找到该实例"})

    save_config(config)
    write_log(f"移除实例: {task_id[:12]}")
    return jsonify({"ok": True, "message": f"实例 {task_id[:12]}... 已移除"})

@app.route("/api/instances/clear_all", methods=["POST"])
def clear_all():
    """一键清除所有实例和配置"""
    config = load_config()
    count = len(config.get("instances", []))

    # 清空配置
    save_config({"instances": []})

    # 清空状态
    STATE_FILE.write_text("{}")

    # 移除 cron 任务
    try:
        result = subprocess.run(
            ["bash", "-c", "(crontab -l 2>/dev/null | grep -v manus_wakeup) | crontab -"],
            capture_output=True, text=True
        )
    except Exception:
        pass

    write_log(f"一键清除所有配置，共清除 {count} 个实例，cron 已移除")
    return jsonify({"ok": True, "message": f"已清除 {count} 个实例，cron 定时任务已停止"})

@app.route("/api/cron/status", methods=["GET"])
def cron_status():
    """检查 cron 是否已配置"""
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        has_cron = "manus_wakeup" in result.stdout
        cron_line = ""
        for line in result.stdout.splitlines():
            if "manus_wakeup" in line:
                cron_line = line
                break
        return jsonify({"ok": True, "active": has_cron, "cron_line": cron_line})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/cron/enable", methods=["POST"])
def cron_enable():
    """启用 cron 定时任务"""
    python = get_python()
    # 先清除旧的
    subprocess.run(
        ["bash", "-c", "(crontab -l 2>/dev/null | grep -v manus_wakeup) | crontab -"],
        capture_output=True
    )
    # 添加新的
    cron_line = f"* * * * * {python} {AGENT_SCRIPT} run >> {LOG_FILE} 2>&1"
    result = subprocess.run(
        ["bash", "-c", f'(crontab -l 2>/dev/null; echo "{cron_line}") | crontab -'],
        capture_output=True, text=True
    )
    write_log("启用 cron 定时任务")
    return jsonify({"ok": True, "message": "cron 定时任务已启用（每分钟执行）"})

@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify({"ok": True, "settings": load_settings()})

@app.route("/api/settings", methods=["POST"])
def update_settings():
    data = request.json or {}
    settings = load_settings()
    if "idle_minutes" in data:
        try:
            m = int(data["idle_minutes"])
            if m < 1:
                return jsonify({"ok": False, "error": "阈值最小为 1 分钟"})
            settings["idle_minutes"] = m
        except (ValueError, TypeError):
            return jsonify({"ok": False, "error": "无效的分钟数"})
    save_settings(settings)
    write_log(f"更新设置: 空闲阈值={settings.get('idle_minutes')} 分钟")
    return jsonify({"ok": True, "message": "设置已保存", "settings": settings})

@app.route("/api/cron/disable", methods=["POST"])
def cron_disable():
    """禁用 cron 定时任务"""
    subprocess.run(
        ["bash", "-c", "(crontab -l 2>/dev/null | grep -v manus_wakeup) | crontab -"],
        capture_output=True
    )
    write_log("禁用 cron 定时任务")
    return jsonify({"ok": True, "message": "cron 定时任务已停止"})

@app.route("/api/run_now", methods=["POST"])
def run_now():
    """立即执行一次唤醒检查"""
    python = get_python()
    try:
        result = subprocess.run(
            [python, str(AGENT_SCRIPT), "run"],
            capture_output=True, text=True, timeout=60
        )
        output = result.stdout + result.stderr
        write_log("手动触发唤醒检查")
        return jsonify({"ok": True, "output": output})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "执行超时"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/logs", methods=["GET"])
def get_logs():
    """获取最近日志"""
    lines = int(request.args.get("lines", 100))
    try:
        if not LOG_FILE.exists():
            return jsonify({"ok": True, "logs": "暂无日志"})
        all_lines = LOG_FILE.read_text(errors="replace").splitlines()
        recent = all_lines[-lines:]
        return jsonify({"ok": True, "logs": "\n".join(recent), "total_lines": len(all_lines)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/api/logs/stream")
def stream_logs():
    """SSE 实时日志流"""
    def generate():
        # 先发送最近 50 行历史日志
        try:
            if LOG_FILE.exists():
                lines = LOG_FILE.read_text(errors="replace").splitlines()[-50:]
                for line in lines:
                    yield f"data: {line}\n\n"
        except Exception:
            pass

        # 持续 tail -f 并加入心跳防断连
        proc = None
        try:
            # 确保日志文件存在
            LOG_FILE.touch(exist_ok=True)
            proc = subprocess.Popen(
                ["tail", "-f", "-n", "0", str(LOG_FILE)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1
            )
            last_heartbeat = time.time()
            while True:
                # 非阻塞读取
                import select
                rlist, _, _ = select.select([proc.stdout], [], [], 5.0)
                if rlist:
                    line = proc.stdout.readline()
                    if line:
                        yield f"data: {line.rstrip()}\n\n"
                        last_heartbeat = time.time()
                else:
                    # 每 5 秒发送心跳保持连接
                    yield f": heartbeat\n\n"
                    last_heartbeat = time.time()
        except GeneratorExit:
            pass
        except Exception as e:
            yield f"data: [日志流错误] {e}\n\n"
        finally:
            if proc:
                try:
                    proc.terminate()
                except Exception:
                    pass

    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})

@app.route("/api/logs/clear", methods=["POST"])
def clear_logs():
    try:
        LOG_FILE.write_text("")
        return jsonify({"ok": True, "message": "日志已清空"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7788))
    write_log(f"管理面板启动，端口 {port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
