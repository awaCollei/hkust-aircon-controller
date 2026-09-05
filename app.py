# app.py
import threading
import time
import logging
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple

from flask import Flask, render_template, request, jsonify
import requests
import schedule

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = 'your-secret-key-here-change-in-production'

# ============================================
# 配置
# ============================================
class Config:
    BASE_URL = "https://w5.ab.ust.hk/njggt/api/app/prepaid"
    STATUS_ENDPOINT = f"{BASE_URL}/ac-status"
    TOGGLE_ENDPOINT = f"{BASE_URL}/toggle-status"
    TIMEOUT = 10
    AUTO_REFRESH_INTERVAL = 60  # 秒


# ============================================
# 空调API客户端
# ============================================
class AirConClient:
    """HKUST空调API客户端"""
    
    def __init__(self, token: str):
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://w5.ab.ust.hk/njggt/app/home"
        })
    
    def _request(self, method: str, url: str, **kwargs) -> Dict[str, Any]:
        """统一请求处理"""
        try:
            response = self.session.request(method, url, timeout=Config.TIMEOUT, **kwargs)
            data = response.json()
            
            # 检查业务状态码
            meta = data.get('meta', {})
            if meta.get('code') != 200:
                error_msg = meta.get('message', 'Unknown error')
                logger.error(f"API业务错误: {error_msg}")
                return {'success': False, 'error': error_msg, 'data': data}
            
            return {'success': True, 'data': data.get('data', {})}
        except requests.exceptions.Timeout:
            logger.error("请求超时")
            return {'success': False, 'error': '请求超时'}
        except requests.exceptions.RequestException as e:
            logger.error(f"请求异常: {e}")
            return {'success': False, 'error': str(e)}
        except Exception as e:
            logger.error(f"未知错误: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_status(self) -> Dict[str, Any]:
        """获取空调状态"""
        result = self._request('GET', Config.STATUS_ENDPOINT)
        if result['success']:
            return result['data'].get('ac_status', {})
        return {'error': result['error']}
    
    def set_status(self, status: int) -> Dict[str, Any]:
        """
        设置空调状态
        status: 0=关闭, 1=开启
        """
        logger.info(f"设置空调状态: {'开启' if status == 1 else '关闭'}")
        result = self._request('POST', Config.TOGGLE_ENDPOINT, json={"toggle": {"status": status}})
        return result


# ============================================
# 调度器管理器
# ============================================
class SchedulerManager:
    """定时任务管理器"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        
        self._running = False
        self._enabled = False
        self._thread: Optional[threading.Thread] = None
        self._jobs: Dict[str, Dict] = {}
        self._current_token: Optional[str] = None
        self._log_callback = None
        self._loop_count = 0  # 当前循环次数
        self._max_loops = 0   # 最大循环次数
        self._task_counter = 0  # 任务计数器
        self._execution_lock = threading.Lock()  # 执行锁
        
        logger.info("调度器管理器初始化完成")
    
    def set_log_callback(self, callback):
        """设置日志回调"""
        self._log_callback = callback
    
    def _log(self, message: str, level: str = 'info'):
        """记录日志"""
        if self._log_callback:
            self._log_callback(message, level)
        else:
            logger.info(message)
    
    def _get_current_status(self) -> Tuple[bool, Dict]:
        """获取当前空调状态"""
        if not self._current_token:
            return False, {'error': '没有Token'}
        
        client = AirConClient(self._current_token)
        status = client.get_status()
        
        if isinstance(status, dict) and 'error' in status:
            return False, status
        
        is_on = status.get('DisconnectRelay', False)
        return is_on, status
    
    def start(self, token: str, on_delay: int, off_delay: int, loop_count: int = 0) -> Dict[str, Any]:
        """
        启动定时任务
        on_delay: 关闭后多久开启（分钟），0表示不开启
        off_delay: 开启后多久关闭（分钟），0表示不关闭
        loop_count: 循环次数，0表示无限循环
        """
        self._current_token = token
        
        # 检查是否有有效任务
        if on_delay <= 0 and off_delay <= 0:
            return self.stop()
        
        # 获取当前状态
        is_on, status = self._get_current_status()
        if isinstance(status, dict) and 'error' in status:
            return {'success': False, 'error': f'无法获取状态: {status["error"]}'}
        
        # 清除旧任务
        self._clear_jobs()
        self._enabled = True
        self._loop_count = 0
        self._max_loops = loop_count if loop_count > 0 else 0
        self._task_counter = 0
        
        # 构建任务列表
        tasks = []
        job_id_prefix = f"job_{int(time.time())}"
        
        if is_on:
            # 当前开启 → 先执行关闭任务
            if off_delay > 0:
                self._add_job(f"{job_id_prefix}_off", '关闭', off_delay, False)
                tasks.append(f"每{off_delay}分钟关闭")
            if on_delay > 0:
                self._add_job(f"{job_id_prefix}_on", '开启', on_delay, True)
                tasks.append(f"每{on_delay}分钟开启")
        else:
            # 当前关闭 → 先执行开启任务
            if on_delay > 0:
                self._add_job(f"{job_id_prefix}_on", '开启', on_delay, True)
                tasks.append(f"每{on_delay}分钟开启")
            if off_delay > 0:
                self._add_job(f"{job_id_prefix}_off", '关闭', off_delay, False)
                tasks.append(f"每{off_delay}分钟关闭")
        
        # 启动调度器线程
        self._start_worker()
        
        loop_text = f"循环{loop_count}次" if loop_count > 0 else "无限循环"
        task_desc = ', '.join(tasks)
        self._log(f"定时控制已启动: {task_desc} ({loop_text}) (当前状态: {'开启' if is_on else '关闭'})")
        
        return {
            'success': True,
            'message': f"定时控制已启动: {task_desc}",
            'tasks': tasks,
            'enabled': True,
            'current_status': '开启' if is_on else '关闭',
            'loop_count': loop_count,
            'loop_text': loop_text
        }
    
    def _add_job(self, job_id: str, job_type: str, delay: int, action: bool):
        """添加单个定时任务 - 使用 schedule 内置重复机制"""
        
        def task():
            if not self._enabled or not self._current_token:
                return
            
            # 检查循环次数
            with self._execution_lock:
                if self._max_loops > 0 and self._loop_count >= self._max_loops:
                    self._log(f"⏹️ 已达到设定循环次数 ({self._max_loops}次)，自动停止定时", 'warning')
                    self.stop()
                    return
                
                # 执行任务
                self._execute_task(action, delay, job_type)
                
                # 增加循环计数（每次任务执行算一次循环）
                self._loop_count += 1
                
                # 检查是否达到最大循环次数
                if self._max_loops > 0 and self._loop_count >= self._max_loops:
                    self._log(f"⏹️ 已完成 {self._max_loops} 次循环，自动停止定时", 'warning')
                    self.stop()
        
        # schedule.every(delay).minutes 会自动重复执行
        schedule.every(delay).minutes.do(task).tag(job_id)
        self._jobs[job_id] = {
            'type': job_type,
            'delay': delay,
            'action': action,
            'tag': job_id
        }
        self._log(f"📌 已调度任务: {job_type}空调 (每{delay}分钟)", 'info')
    
    def _execute_task(self, action: bool, delay: int, job_type: str):
        """执行定时任务"""
        if not self._enabled:
            self._log("⏸️ 定时控制已暂停", 'warning')
            return
        
        if not self._current_token:
            self._log("❌ 没有有效的Token", 'error')
            return
        
        action_str = "开启" if action else "关闭"
        
        try:
            client = AirConClient(self._current_token)
            status = client.get_status()
            
            if isinstance(status, dict) and 'error' in status:
                self._log(f"❌ 获取状态失败: {status['error']}", 'error')
                return
            
            is_on = status.get('DisconnectRelay', False)
            
            # 如果已经是目标状态，跳过
            if (action and is_on) or (not action and not is_on):
                self._log(f"⏸️ 空调已经是{action_str}状态，跳过", 'info')
                return
            
            # 执行操作
            target = 1 if action else 0
            result = client.set_status(target)
            
            if result.get('success'):
                self._log(f"✅ 定时任务执行成功: {action_str}空调 (延迟{delay}分钟)", 'success')
            else:
                self._log(f"❌ 定时任务执行失败: {result.get('error')}", 'error')
                
        except Exception as e:
            self._log(f"❌ 任务执行异常: {e}", 'error')
    
    def _clear_jobs(self):
        """清除所有任务"""
        for job_id in list(self._jobs.keys()):
            schedule.clear(job_id)
        self._jobs.clear()
    
    def stop(self) -> Dict[str, Any]:
        """停止所有定时任务"""
        self._clear_jobs()
        self._enabled = False
        self._loop_count = 0
        self._max_loops = 0
        self._log("定时控制已停止", 'info')
        return {'success': True, 'message': '定时控制已停止', 'enabled': self._enabled}
    
    def get_status(self) -> Dict[str, Any]:
        """获取调度器状态"""
        jobs = []
        for key, info in self._jobs.items():
            job_list = schedule.get_jobs(key)
            if job_list:
                job = job_list[0]
                jobs.append({
                    'id': key,
                    'type': info['type'],
                    'delay': info['delay'],
                    'next_run': str(job.next_run) if job.next_run else 'N/A'
                })
        
        # 计算剩余循环次数
        remaining = self._max_loops - self._loop_count if self._max_loops > 0 else 0
        
        return {
            'running': self._running,
            'enabled': self._enabled and len(jobs) > 0,
            'jobs': jobs,
            'loop_count': self._loop_count,
            'max_loops': self._max_loops,
            'remaining_loops': remaining if remaining > 0 else 0,
            'is_looping': self._max_loops > 0
        }
    
    def _start_worker(self):
        """启动调度器工作线程"""
        if self._running:
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._thread.start()
        logger.info("调度器工作线程已启动")
    
    def _worker_loop(self):
        """调度器工作循环"""
        logger.info("调度器开始运行")
        while self._running:
            try:
                schedule.run_pending()
                time.sleep(1)
            except Exception as e:
                logger.error(f"调度器错误: {e}")
                time.sleep(5)


# ============================================
# 全局调度器实例
# ============================================
scheduler = SchedulerManager()


# ============================================
# 日志管理
# ============================================
class Logger:
    _logs = []
    _max_logs = 100
    
    @classmethod
    def add(cls, message: str, level: str = 'info'):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = {
            'time': timestamp,
            'message': message,
            'level': level
        }
        cls._logs.append(log_entry)
        
        # 限制日志数量
        if len(cls._logs) > cls._max_logs:
            cls._logs = cls._logs[-cls._max_logs:]
        
        # 写入文件
        try:
            with open('aircon.log', 'a', encoding='utf-8') as f:
                f.write(f"[{timestamp}] [{level}] {message}\n")
        except:
            pass
    
    @classmethod
    def get_logs(cls):
        return cls._logs


# 设置调度器日志回调
scheduler.set_log_callback(Logger.add)


# ============================================
# 辅助函数
# ============================================
def format_status(status: Dict[str, Any]) -> Dict[str, Any]:
    """格式化状态数据"""
    is_on = status.get('DisconnectRelay', False)
    return {
        'device_name': status.get('DeviceName', 'Unknown'),
        'time': status.get('Time', ''),
        'is_on': is_on,
        'status_text': '开启' if is_on else '关闭',
        'voltage': status.get('V', 0),
        'current': status.get('I', 0),
        'power': status.get('P', 0),
        'power_factor': status.get('PF', 0),
        'kwh': status.get('kWhImport', 0),
        'raw': status
    }


def calculate_end_time(on_delay: int, off_delay: int, loop_count: int) -> Dict[str, Any]:
    """
    计算结束时间
    返回：总分钟数、天、时、分、结束时间描述
    """
    if loop_count <= 0 or (on_delay <= 0 and off_delay <= 0):
        return {
            'total_minutes': 0,
            'days': 0,
            'hours': 0,
            'minutes': 0,
            'end_time_str': '无限循环'
        }
    
    # 每次循环的时间 = on_delay + off_delay
    cycle_minutes = on_delay + off_delay
    if cycle_minutes == 0:
        return {
            'total_minutes': 0,
            'days': 0,
            'hours': 0,
            'minutes': 0,
            'end_time_str': '无效配置'
        }
    
    # 总分钟数
    total_minutes = cycle_minutes * loop_count
    
    # 转换为天、时、分
    days = total_minutes // 1440
    hours = (total_minutes % 1440) // 60
    minutes = total_minutes % 60
    
    # 计算结束时间
    now = datetime.now()
    end_time = now + timedelta(minutes=total_minutes)
    
    # 判断是今天、明天还是后天
    today = now.date()
    end_date = end_time.date()
    day_diff = (end_date - today).days
    
    if day_diff == 0:
        day_desc = "今天"
    elif day_diff == 1:
        day_desc = "明天"
    elif day_diff == 2:
        day_desc = "后天"
    else:
        day_desc = f"{day_diff}天后"
    
    end_time_str = f"{day_desc} {end_time.strftime('%H:%M')}"
    
    return {
        'total_minutes': total_minutes,
        'days': days,
        'hours': hours,
        'minutes': minutes,
        'end_time_str': end_time_str,
        'end_time': end_time.strftime('%Y-%m-%d %H:%M:%S')
    }


# ============================================
# Flask路由
# ============================================
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/status', methods=['POST'])
def get_status():
    """获取空调状态"""
    data = request.json
    token = data.get('token')
    
    if not token:
        return jsonify({'error': 'Token不能为空'}), 400
    
    client = AirConClient(token)
    status = client.get_status()
    
    if isinstance(status, dict) and 'error' in status:
        return jsonify({'error': status['error']}), 500
    
    return jsonify(format_status(status))


@app.route('/api/toggle', methods=['POST'])
def toggle_ac():
    """切换空调状态"""
    data = request.json
    token = data.get('token')
    action = data.get('action')  # 'on' 或 'off'
    
    if not token:
        return jsonify({'error': 'Token不能为空'}), 400
    
    if action not in ['on', 'off']:
        return jsonify({'error': '无效的操作'}), 400
    
    status_code = 1 if action == 'on' else 0
    action_text = '开启' if action == 'on' else '关闭'
    
    client = AirConClient(token)
    result = client.set_status(status_code)
    
    if result.get('success'):
        Logger.add(f"手动{action_text}空调", 'success')
        return jsonify({'success': True, 'message': f'{action_text}成功'})
    else:
        return jsonify({'error': result.get('error', f'{action_text}失败')}), 500


@app.route('/api/schedule', methods=['POST'])
def set_schedule():
    """设置定时任务"""
    data = request.json
    token = data.get('token')
    on_delay = int(data.get('on_delay', 0) or 0)
    off_delay = int(data.get('off_delay', 0) or 0)
    loop_count = int(data.get('loop_count', 0) or 0)
    
    if not token:
        return jsonify({'error': 'Token不能为空'}), 400
    
    result = scheduler.start(token, on_delay, off_delay, loop_count)
    
    if result.get('success'):
        return jsonify(result)
    else:
        return jsonify({'error': result.get('error', '启动定时失败')}), 500


@app.route('/api/schedule/clear', methods=['POST'])
def clear_schedule():
    """清除所有定时任务"""
    result = scheduler.stop()
    return jsonify(result)


@app.route('/api/schedule/status', methods=['GET'])
def get_schedule_status():
    """获取调度器状态"""
    return jsonify(scheduler.get_status())


@app.route('/api/schedule/calculate', methods=['POST'])
def calculate_schedule():
    """计算定时结束时间"""
    data = request.json
    on_delay = int(data.get('on_delay', 0) or 0)
    off_delay = int(data.get('off_delay', 0) or 0)
    loop_count = int(data.get('loop_count', 0) or 0)
    
    result = calculate_end_time(on_delay, off_delay, loop_count)
    return jsonify(result)


@app.route('/api/logs', methods=['GET'])
def get_logs():
    """获取日志"""
    return jsonify({'logs': Logger.get_logs()})


# ============================================
# 启动
# ============================================
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10721, debug=True, use_reloader=False)