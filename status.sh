#!/bin/bash

# status.sh - 查看HKUST空调智能控制器运行状态

SESSION_NAME="aircon"
BLUE='\033[0;34m'
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   HKUST 空调智能控制器 - 状态检查   ${NC}"
echo -e "${BLUE}========================================${NC}"

# 检查 screen 会话
if screen -list | grep -q "\.${SESSION_NAME}"; then
    echo -e "${GREEN}✅ Screen 会话: 运行中${NC}"
    screen -list | grep "\.${SESSION_NAME}"
else
    echo -e "${RED}❌ Screen 会话: 未运行${NC}"
fi

# 检查进程
if pgrep -f "python.*app.py" > /dev/null; then
    echo -e "${GREEN}✅ Python 进程: 运行中${NC}"
    ps aux | grep "python.*app.py" | grep -v grep
else
    echo -e "${RED}❌ Python 进程: 未运行${NC}"
fi

# 检查端口
if command -v netstat &> /dev/null; then
    if netstat -tlnp 2>/dev/null | grep -q ":5000"; then
        echo -e "${GREEN}✅ 端口 5000: 已监听${NC}"
    else
        echo -e "${RED}❌ 端口 5000: 未监听${NC}"
    fi
elif command -v ss &> /dev/null; then
    if ss -tlnp 2>/dev/null | grep -q ":5000"; then
        echo -e "${GREEN}✅ 端口 5000: 已监听${NC}"
    else
        echo -e "${RED}❌ 端口 5000: 未监听${NC}"
    fi
else
    echo -e "${YELLOW}⚠️  无法检查端口状态 (netstat/ss 未找到)${NC}"
fi

echo ""
echo -e "${BLUE}📌 日志文件: logs/app.log${NC}"
echo -e "${BLUE}📌 最后10条日志:${NC}"
tail -n 10 logs/app.log 2>/dev/null || echo -e "${YELLOW}(日志文件不存在)${NC}"