#!/bin/bash
# Manus 管理面板 - 一键卸载脚本

GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'

echo -e "${RED}正在卸载 Manus 沙箱唤醒管理面板...${NC}"

# 停止并删除 systemd 服务
systemctl stop manus-panel 2>/dev/null || true
systemctl disable manus-panel 2>/dev/null || true
rm -f /etc/systemd/system/manus-panel.service
systemctl daemon-reload 2>/dev/null || true

# 删除 cron 任务
(crontab -l 2>/dev/null | grep -v "manus_wakeup") | crontab - 2>/dev/null || true

# 删除文件
rm -rf /opt/manus-panel
rm -f /usr/local/bin/manus_wakeup_agent.py
rm -f /usr/local/bin/manus_wakeup.py
rm -rf /etc/manus_wakeup
rm -f /var/log/manus_wakeup.log

echo -e "${GREEN}✓ 卸载完成，所有配置和数据已清除${NC}"
