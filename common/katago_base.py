"""KataGo 子进程公共基座 + 可执行文件/模型/配置查找。

对弈线（play.py）和分析线（analysis.py）各自派生子类，
本模块只提供两者共有的：子进程启动、stderr 排水、自动重启。
"""
import os
import subprocess
import threading

# go-ai 项目根目录（本文件位于 common/ 下）
GOAI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class KatagoProcess:
    """KataGo 子进程基类：管理进程生命周期与互斥锁"""

    label = "引擎"  # 子类覆盖，仅用于日志

    def __init__(self, katago_path, model_path, config_path=None):
        self.katago_path = katago_path
        self.model_path = model_path
        self.config_path = config_path
        self.process = None
        self.lock = threading.Lock()

    def start(self):
        raise NotImplementedError

    def _spawn(self, cmd):
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        self.stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self.stderr_thread.start()

    def _drain_stderr(self):
        while self.process and self.process.poll() is None:
            try:
                line = self.process.stderr.readline()
                if not line:
                    break
            except:
                break

    def _restart(self):
        """重启引擎"""
        try:
            if self.process:
                self.process.terminate()
                self.process = None
            self.start()
            print(f"{self.label}自动重启成功")
        except Exception as e:
            print(f"{self.label}重启失败: {e}")


def find_katago():
    candidates = [
        "/opt/homebrew/bin/katago",
        "/usr/local/bin/katago",
        os.path.join(GOAI_ROOT, "katago-src", "cpp", "build", "katago"),
    ]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    result = subprocess.run(["which", "katago"], capture_output=True, text=True)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def find_model():
    for f in os.listdir(GOAI_ROOT):
        if f.endswith(".bin.gz"):
            return os.path.join(GOAI_ROOT, f)
    return None


def find_config():
    cfg = os.path.join(GOAI_ROOT, "gtp.cfg")
    if os.path.exists(cfg):
        return cfg
    return None


def find_analysis_config():
    cfg = os.path.join(GOAI_ROOT, "analysis.cfg")
    if os.path.exists(cfg):
        return cfg
    return None
