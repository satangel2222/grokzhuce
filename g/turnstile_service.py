"""
Turnstile验证服务类 — 支持 CapSolver / YesCaptcha / 本地 Solver
"""
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()


class TurnstileService:
    """Turnstile验证服务类（优先级：CapSolver > YesCaptcha > 本地 Solver）"""

    def __init__(self, solver_url="http://127.0.0.1:5072"):
        self.capsolver_key = os.getenv('CAPSOLVER_KEY', '').strip()
        self.yescaptcha_key = os.getenv('YESCAPTCHA_KEY', '').strip()
        self.solver_url = solver_url

        if self.capsolver_key:
            self.backend = "capsolver"
            self.api_url = "https://api.capsolver.com"
            print("[*] Turnstile 后端: CapSolver (云端API)")
        elif self.yescaptcha_key:
            self.backend = "yescaptcha"
            self.api_url = "https://api.yescaptcha.com"
            print("[*] Turnstile 后端: YesCaptcha (云端API)")
        else:
            self.backend = "local"
            print(f"[*] Turnstile 后端: 本地 Solver ({solver_url})")

    def create_task(self, siteurl, sitekey):
        """创建Turnstile验证任务"""
        if self.backend == "capsolver":
            url = f"{self.api_url}/createTask"
            payload = {
                "clientKey": self.capsolver_key,
                "task": {
                    "type": "AntiTurnstileTaskProxyLess",
                    "websiteURL": siteurl,
                    "websiteKey": sitekey
                }
            }
            response = requests.post(url, json=payload, timeout=15)
            response.raise_for_status()
            data = response.json()
            if data.get('errorId') != 0:
                raise Exception(f"CapSolver创建任务失败: {data.get('errorDescription')}")
            return data['taskId']

        elif self.backend == "yescaptcha":
            url = f"{self.api_url}/createTask"
            payload = {
                "clientKey": self.yescaptcha_key,
                "task": {
                    "type": "TurnstileTaskProxyless",
                    "websiteURL": siteurl,
                    "websiteKey": sitekey
                }
            }
            response = requests.post(url, json=payload, timeout=15)
            response.raise_for_status()
            data = response.json()
            if data.get('errorId') != 0:
                raise Exception(f"YesCaptcha创建任务失败: {data.get('errorDescription')}")
            return data['taskId']

        else:
            # 本地 Solver
            url = f"{self.solver_url}/turnstile?url={siteurl}&sitekey={sitekey}"
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            return response.json()['taskId']

    def get_response(self, task_id, max_retries=30, initial_delay=5, retry_delay=2):
        """获取Turnstile验证响应"""
        # 云端 API 通常更快，减少初始等待
        if self.backend in ("capsolver", "yescaptcha"):
            time.sleep(3)
        else:
            time.sleep(initial_delay)

        api_key = self.capsolver_key if self.backend == "capsolver" else self.yescaptcha_key

        for attempt in range(max_retries):
            try:
                if self.backend in ("capsolver", "yescaptcha"):
                    url = f"{self.api_url}/getTaskResult"
                    payload = {
                        "clientKey": api_key,
                        "taskId": task_id
                    }
                    response = requests.post(url, json=payload, timeout=15)
                    response.raise_for_status()
                    data = response.json()

                    if data.get('errorId') != 0:
                        print(f"    [{self.backend}] 获取结果失败: {data.get('errorDescription')}")
                        return None

                    if data.get('status') == 'ready':
                        token = data.get('solution', {}).get('token')
                        if token:
                            return token
                        print(f"    [{self.backend}] 返回结果中没有token")
                        return None
                    elif data.get('status') == 'processing':
                        time.sleep(retry_delay)
                    else:
                        time.sleep(retry_delay)

                else:
                    # 本地 Solver
                    url = f"{self.solver_url}/result?id={task_id}"
                    response = requests.get(url, timeout=15)
                    response.raise_for_status()
                    data = response.json()
                    captcha = data.get('solution', {}).get('token', None)

                    if captcha:
                        if captcha != "CAPTCHA_FAIL":
                            return captcha
                        else:
                            return None
                    else:
                        time.sleep(retry_delay)

            except Exception as e:
                if attempt % 10 == 0:
                    print(f"    [{self.backend}] 获取响应异常: {e}")
                time.sleep(retry_delay)

        return None
