#!/bin/bash

# start.sh - 在 screen 会话中启动 HKUST 空调智能控制器

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 配置
SCREEN_SESSION="aircon"
APP_PORT=5000
APP_HOST="0.0.0.0"
FLASK_APP="app.py"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 打印带颜色的消息
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# 检查是否在 screen 中运行
if [ -n "$STY" ]; then
    print_warning "你在 screen 会话中运行此脚本。嵌套 screen 可能会导致问题。"
    read -p "是否继续？(y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 检查 screen 是否安装
if ! command -v screen &> /dev/null; then
    print_error "screen 未安装。请安装 screen:"
    echo "  Ubuntu/Debian: sudo apt install screen"
    echo "  CentOS/RHEL:   sudo yum install screen"
    echo "  macOS:         brew install screen"
    exit 1
fi

# 检查 Python 是否安装
if ! command -v python3 &> /dev/null; then
    print_error "Python3 未安装"
    exit 1
fi

# 检查依赖是否安装
print_info "检查 Python 依赖..."
if ! python3 -c "import flask, requests, schedule" 2>/dev/null; then
    print_warning "部分依赖未安装，正在尝试安装..."
    pip3 install -r requirements.txt
    if [ $? -ne 0 ]; then
        print_error "依赖安装失败，请手动运行: pip install -r requirements.txt"
        exit 1
    fi
fi

# 检查 app.py 是否存在
if [ ! -f "$FLASK_APP" ]; then
    print_error "找不到 $FLASK_APP，请确保在项目根目录运行此脚本"
    exit 1
fi

# 检查端口是否被占用
check_port() {
    if command -v lsof &> /dev/null; then
        if lsof -i :$APP_PORT -sTCP:LISTEN &> /dev/null; then
            return 0  # 端口被占用
        fi
    elif command -v netstat &> /dev/null; then
        if netstat -tlnp 2>/dev/null | grep -q ":$APP_PORT "; then
            return 0
        fi
    fi
    return 1  # 端口未被占用
}

if check_port; then
    print_warning "端口 $APP_PORT 已被占用"
    read -p "是否结束占用该端口的进程？(y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        if command -v lsof &> /dev/null; then
            PID=$(lsof -t -i :$APP_PORT)
            if [ -n "$PID" ]; then
                kill -9 $PID 2>/dev/null
                print_info "已结束进程 PID: $PID"
                sleep 1
            fi
        else
            print_error "无法自动结束进程，请手动关闭占用端口 $APP_PORT 的程序"
            exit 1
        fi
    else
        print_error "端口 $APP_PORT 被占用，请修改 app.py 中的端口配置"
        exit 1
    fi
fi

# 检查是否已有同名 screen 会话
if screen -ls | grep -q "\.${SCREEN_SESSION}\s"; then
    print_warning "已有名为 '$SCREEN_SESSION' 的 screen 会话正在运行"
    read -p "是否结束旧会话并重新启动？(y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        screen -S "$SCREEN_SESSION" -X quit 2>/dev/null
        sleep 1
        print_info "已结束旧会话"
    else
        print_info "你可以通过以下命令查看现有会话:"
        echo "  screen -r $SCREEN_SESSION"
        exit 1
    fi
fi

# 启动 screen 会话
print_info "正在启动 screen 会话: $SCREEN_SESSION"
print_info "Flask 应用将运行在 http://$APP_HOST:$APP_PORT"

# 创建 screen 会话并运行应用
screen -dmS "$SCREEN_SESSION" bash -c "
    cd '$SCRIPT_DIR'
    echo '==========================================='
    echo '  HKUST 空调智能控制器'
    echo '  访问地址: http://$APP_HOST:$APP_PORT'
    echo '  按 Ctrl+A+D 退出 screen (保持运行)'
    echo '  按 Ctrl+C 停止应用'
    echo '==========================================='
    echo ''
    python3 app.py
    echo ''
    echo '应用已停止，按任意键关闭此窗口...'
    read -n 1
"

if [ $? -eq 0 ]; then
    print_success "应用已在 screen 会话中启动！"
    echo ""
    echo "📌 常用命令:"
    echo "  screen -r $SCREEN_SESSION   # 进入会话"
    echo "  screen -ls                  # 查看所有会话"
    echo "  screen -S $SCREEN_SESSION -X quit  # 停止应用"
    echo ""
    echo "🌐 访问地址: http://$APP_HOST:$APP_PORT"
    echo ""
    
    # 询问是否立即进入 screen
    read -p "是否立即进入 screen 会话查看日志？(y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        screen -r "$SCREEN_SESSION"
    else
        print_info "你可以随时通过 'screen -r $SCREEN_SESSION' 进入会话"
    fi
else
    print_error "启动 screen 会话失败"
    exit 1
fi