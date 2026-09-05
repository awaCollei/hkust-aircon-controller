#!/bin/bash

# stop.sh - 停止HKUST空调智能控制器

SESSION_NAME="aircon"
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}正在停止 screen 会话 '${SESSION_NAME}'...${NC}"

if screen -list | grep -q "\.${SESSION_NAME}"; then
    screen -S "$SESSION_NAME" -X quit
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✅ 服务已停止${NC}"
    else
        echo -e "${RED}❌ 停止失败${NC}"
        exit 1
    fi
else
    echo -e "${YELLOW}⚠️  没有找到运行中的会话 '${SESSION_NAME}'${NC}"
fi

# 检查是否还有进程在运行
if pgrep -f "python.*app.py" > /dev/null; then
    echo -e "${YELLOW}⚠️  发现残留的 Python 进程，正在清理...${NC}"
    pkill -f "python.*app.py"
    echo -e "${GREEN}✅ 已清理残留进程${NC}"
fi