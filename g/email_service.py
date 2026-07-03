"""邮箱服务类 - 支持 duckmail / freemail / mail.tm 多后端"""
import os
import re
import time
import random
import string
import requests
from dotenv import load_dotenv

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    curl_requests = None

DUCKMAIL_API = "https://api.duckmail.sbs"
DUCKMAIL_DOMAINS = ["duckmail.sbs", "baldur.edu.kg"]

MAILTM_APIS = [
    "https://api.mail.tm",
]


class EmailService:
    """优先级: duckmail > freemail > mail.tm"""

    def __init__(self):
        load_dotenv()
        self.worker_domain = os.getenv("WORKER_DOMAIN", "").strip()
        self.freemail_token = os.getenv("FREEMAIL_TOKEN", "").strip()
        self.duckmail_domain = os.getenv("DUCKMAIL_DOMAIN", "").strip() or random.choice(DUCKMAIL_DOMAINS)

        # 优先 duckmail（需要 curl_cffi）
        use_duckmail = os.getenv("USE_DUCKMAIL", "1").strip()
        if use_duckmail == "1" and curl_requests:
            self.backend = "duckmail"
            self._duckmail_accounts = {}
            print(f"[*] 邮箱后端: duckmail ({self.duckmail_domain})")
        elif self.worker_domain and self.freemail_token:
            self.backend = "freemail"
            self.base_url = f"https://{self.worker_domain}"
            self.headers = {"Authorization": f"Bearer {self.freemail_token}"}
            print("[*] 邮箱后端: freemail")
        else:
            self.backend = "mailtm"
            self._all_domains = []
            self._mailtm_accounts = {}
            self._init_domains()
            print(f"[*] 邮箱后端: mail.tm ({len(self._all_domains)} 个域名)")

    def _random_username(self, length=12):
        return ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(length))

    def _random_password(self, length=14):
        lower = string.ascii_lowercase
        upper = string.ascii_uppercase
        digits = string.digits
        special = "!@#$%"
        pwd = [random.choice(lower), random.choice(upper),
               random.choice(digits), random.choice(special)]
        all_chars = lower + upper + digits + special
        pwd += [random.choice(all_chars) for _ in range(length - 4)]
        random.shuffle(pwd)
        return "".join(pwd)

    # ── DuckMail 方法 (curl_cffi 必须) ──

    def _duckmail_request(self, method, url, **kwargs):
        """DuckMail 请求需要 curl_cffi 模拟浏览器 TLS 指纹"""
        session = curl_requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        kwargs.setdefault("impersonate", "chrome124")  # 本机 curl_cffi 0.7.4 最高支持 chrome124(chrome131 未支持会抛异常)
        kwargs.setdefault("timeout", 15)
        return getattr(session, method)(url, **kwargs)

    def _create_duckmail_account(self):
        domain = self.duckmail_domain
        username = self._random_username(random.randint(8, 13))
        address = f"{username}@{domain}"
        password = self._random_password()

        try:
            # 1. 创建账号
            res = self._duckmail_request("post", f"{DUCKMAIL_API}/accounts",
                                         json={"address": address, "password": password})
            if res.status_code not in (200, 201):
                print(f"[-] DuckMail 创建邮箱失败: {res.status_code} - {res.text[:200]}")
                return None, None

            # 2. 获取 mail token
            time.sleep(0.5)
            token_res = self._duckmail_request("post", f"{DUCKMAIL_API}/token",
                                               json={"address": address, "password": password})
            if token_res.status_code == 200:
                mail_token = token_res.json().get("token")
                if mail_token:
                    self._duckmail_accounts[address] = {
                        "token": mail_token,
                        "password": password,
                    }
                    return address, address

            print(f"[-] DuckMail token 获取失败: {token_res.status_code}")
            return None, None
        except Exception as e:
            print(f"[-] DuckMail 异常: {e}")
            return None, None

    def _fetch_duckmail_code(self, email, max_attempts=40):
        account = self._duckmail_accounts.get(email)
        if not account:
            return None

        headers = {"Authorization": f"Bearer {account['token']}"}
        seen_ids = set()

        for attempt in range(max_attempts):
            try:
                res = self._duckmail_request("get", f"{DUCKMAIL_API}/messages", headers=headers)
                if res.status_code != 200:
                    time.sleep(3)
                    continue

                data = res.json()
                messages = data.get("hydra:member") or data.get("member") or data.get("data") or []

                for msg in messages:
                    msg_id = msg.get("id") or msg.get("@id")
                    if not msg_id or msg_id in seen_ids:
                        continue
                    seen_ids.add(msg_id)

                    # 先查 subject
                    subject = msg.get("subject", "")
                    code = self._extract_code(subject)
                    if code:
                        return code

                    # 查邮件正文
                    mid = str(msg_id).split("/")[-1] if "/" in str(msg_id) else str(msg_id)
                    detail_res = self._duckmail_request("get", f"{DUCKMAIL_API}/messages/{mid}",
                                                        headers=headers)
                    if detail_res.status_code == 200:
                        detail = detail_res.json()
                        content = detail.get("text") or detail.get("html") or ""
                        code = self._extract_code(content)
                        if code:
                            return code

                if not messages and attempt % 5 == 0:
                    print(f"    [duckmail] 等待邮件... ({attempt*3}s)")
            except Exception as e:
                if attempt % 10 == 0:
                    print(f"    [duckmail] 错误: {e}")
            time.sleep(3)

        return None

    def _extract_code(self, content):
        """提取验证码: Grok 格式 XXX-XXX 或 6位数字"""
        if not content:
            return None
        # Grok 格式: MM0-SF3
        m = re.search(r'\b([A-Z0-9]{3})-([A-Z0-9]{3})\b', content)
        if m:
            return m.group(1) + m.group(2)
        # 6位数字
        m = re.search(r'(?<![&#\d])(\d{6})(?![&#\d])', content)
        if m and m.group(1) != "177010":
            return m.group(1)
        return None

    # ── mail.tm 方法 ──

    def _init_domains(self):
        for api_url in MAILTM_APIS:
            try:
                res = requests.get(f"{api_url}/domains", timeout=10)
                if res.status_code == 200:
                    data = res.json()
                    members = data.get("hydra:member", data.get("member", []))
                    for m in members:
                        self._all_domains.append((api_url, m["domain"]))
            except Exception as e:
                print(f"    [warn] {api_url} 获取域名失败: {e}")
        if not self._all_domains:
            print("[-] 所有邮箱 API 都不可用!")

    def _create_mailtm_account(self):
        if not self._all_domains:
            return None, None
        api_url, domain = random.choice(self._all_domains)
        username = self._random_username()
        address = f"{username}@{domain}"
        password = self._random_username(16)
        try:
            res = requests.post(f"{api_url}/accounts",
                                json={"address": address, "password": password}, timeout=10)
            if res.status_code not in (200, 201):
                return None, None
            account_id = res.json().get("id")
            token_res = requests.post(f"{api_url}/token",
                                      json={"address": address, "password": password}, timeout=10)
            if token_res.status_code != 200:
                return None, None
            token = token_res.json().get("token")
            self._mailtm_accounts[address] = {"id": account_id, "token": token, "password": password, "api": api_url}
            return address, address
        except Exception as e:
            print(f"[-] 创建邮箱异常 ({domain}): {e}")
            return None, None

    def _fetch_mailtm_code(self, email, max_attempts=30):
        account = self._mailtm_accounts.get(email)
        if not account:
            return None
        api_url = account["api"]
        headers = {"Authorization": f"Bearer {account['token']}"}
        for attempt in range(max_attempts):
            try:
                res = requests.get(f"{api_url}/messages", headers=headers, timeout=10)
                if res.status_code != 200:
                    time.sleep(2)
                    continue
                data = res.json()
                messages = data.get("hydra:member", data.get("member", []))
                if not messages:
                    if attempt % 5 == 0:
                        print(f"    [mail] 等待邮件... ({attempt*2}s)")
                    time.sleep(2)
                    continue
                for msg in messages:
                    msg_id = msg.get("id")
                    if not msg_id:
                        continue
                    subject = msg.get("subject", "")
                    code = self._extract_code(subject)
                    if code:
                        return code
                    try:
                        detail = requests.get(f"{api_url}/messages/{msg_id}", headers=headers, timeout=10)
                        if detail.status_code == 200:
                            text = detail.json().get("text", "")
                            code = self._extract_code(text)
                            if code:
                                return code
                    except:
                        pass
            except Exception as e:
                if attempt % 10 == 0:
                    print(f"    [mail] 错误: {e}")
            time.sleep(2)
        return None

    def _delete_mailtm_account(self, email):
        account = self._mailtm_accounts.pop(email, None)
        if not account:
            return False
        try:
            res = requests.delete(f"{account['api']}/accounts/{account['id']}",
                                  headers={"Authorization": f"Bearer {account['token']}"}, timeout=10)
            return res.status_code in (200, 204)
        except:
            return False

    # ── 统一接口 ──

    def create_email(self):
        """创建临时邮箱，返回 (email, email)"""
        if self.backend == "duckmail":
            return self._create_duckmail_account()
        elif self.backend == "freemail":
            try:
                res = requests.get(f"{self.base_url}/api/generate", headers=self.headers, timeout=10)
                if res.status_code == 200:
                    email = res.json().get("email")
                    return email, email
                print(f"[-] 创建邮箱失败: {res.status_code} - {res.text}")
                return None, None
            except Exception as e:
                print(f"[-] 创建邮箱失败: {e}")
                return None, None
        else:
            return self._create_mailtm_account()

    def fetch_verification_code(self, email, max_attempts=40):
        """轮询获取验证码"""
        if self.backend == "duckmail":
            return self._fetch_duckmail_code(email, max_attempts)
        elif self.backend == "freemail":
            for _ in range(max_attempts):
                try:
                    res = requests.get(f"{self.base_url}/api/emails",
                                       params={"mailbox": email}, headers=self.headers, timeout=10)
                    if res.status_code == 200:
                        emails = res.json()
                        if emails and emails[0].get("verification_code"):
                            code = emails[0]["verification_code"]
                            return code.replace("-", "")
                except:
                    pass
                time.sleep(1)
            return None
        else:
            return self._fetch_mailtm_code(email, max_attempts)

    def delete_email(self, address):
        """删除邮箱"""
        if self.backend == "duckmail":
            self._duckmail_accounts.pop(address, None)
            return True
        elif self.backend == "freemail":
            try:
                res = requests.delete(f"{self.base_url}/api/mailboxes",
                                      params={"address": address}, headers=self.headers, timeout=10)
                return res.status_code == 200 and res.json().get("success")
            except:
                return False
        else:
            return self._delete_mailtm_account(address)
