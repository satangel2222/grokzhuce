"""邮箱服务类 - 支持 freemail API / mail.tm / mail.gw 多后端"""
import os
import re
import time
import random
import string
import requests
from dotenv import load_dotenv


# mail.tm 优先（mail.gw 域名经测试不能可靠收到 x.ai 邮件）
MAILTM_APIS = [
    "https://api.mail.tm",
    # "https://api.mail.gw",  # 域名不可靠，暂时禁用
]


class EmailService:
    """自动选择后端：有 WORKER_DOMAIN 用 freemail，否则用 mail.tm/mail.gw"""

    def __init__(self):
        load_dotenv()
        self.worker_domain = os.getenv("WORKER_DOMAIN", "").strip()
        self.freemail_token = os.getenv("FREEMAIL_TOKEN", "").strip()

        if self.worker_domain and self.freemail_token:
            self.backend = "freemail"
            self.base_url = f"https://{self.worker_domain}"
            self.headers = {"Authorization": f"Bearer {self.freemail_token}"}
            print("[*] 邮箱后端: freemail")
        else:
            self.backend = "mailtm"
            self._all_domains = []  # [(api_url, domain), ...]
            self._mailtm_accounts = {}  # email -> {id, token, password, api}
            self._init_domains()
            print(f"[*] 邮箱后端: mail.tm/mail.gw ({len(self._all_domains)} 个域名)")

    def _init_domains(self):
        """从所有 API 源获取可用域名"""
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

    def _random_username(self, length=12):
        return ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(length))

    # ── mail.tm/mail.gw 方法 ──

    def _create_mailtm_account(self):
        if not self._all_domains:
            return None, None

        # 随机选择一个 (api, domain) 对
        api_url, domain = random.choice(self._all_domains)
        username = self._random_username()
        address = f"{username}@{domain}"
        password = self._random_username(16)

        try:
            res = requests.post(
                f"{api_url}/accounts",
                json={"address": address, "password": password},
                timeout=10
            )
            if res.status_code not in (200, 201):
                print(f"[-] 创建邮箱失败 ({domain}): {res.status_code}")
                return None, None

            account_id = res.json().get("id")

            token_res = requests.post(
                f"{api_url}/token",
                json={"address": address, "password": password},
                timeout=10
            )
            if token_res.status_code != 200:
                print(f"[-] 获取token失败 ({domain}): {token_res.status_code}")
                return None, None

            token = token_res.json().get("token")
            self._mailtm_accounts[address] = {
                "id": account_id,
                "token": token,
                "password": password,
                "api": api_url,
            }
            return address, address

        except Exception as e:
            print(f"[-] 创建邮箱异常 ({domain}): {e}")
            return None, None

    def _fetch_mailtm_code(self, email, max_attempts=30):
        account = self._mailtm_accounts.get(email)
        if not account:
            print(f"[-] 未找到账号: {email}")
            return None

        api_url = account["api"]
        headers = {"Authorization": f"Bearer {account['token']}"}

        for attempt in range(max_attempts):
            try:
                res = requests.get(
                    f"{api_url}/messages",
                    headers=headers,
                    timeout=10
                )
                if res.status_code != 200:
                    if attempt % 10 == 0:
                        print(f"    [mail] 消息列表状态: {res.status_code}")
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

                    # 先检查主题 — x.ai 格式: "1B9-67R xAI confirmation code" (字母+数字混合)
                    subject = msg.get("subject", "")
                    code_match = re.search(r'\b([A-Z0-9]{3})-([A-Z0-9]{3})\b', subject)
                    if code_match:
                        code = code_match.group(1) + code_match.group(2)
                        return code

                    # 后备: 获取完整邮件正文
                    try:
                        detail = requests.get(
                            f"{api_url}/messages/{msg_id}",
                            headers=headers,
                            timeout=10
                        )
                        if detail.status_code == 200:
                            text = detail.json().get("text", "")
                            code_match = re.search(r'\b([A-Z0-9]{3})-([A-Z0-9]{3})\b', text)
                            if code_match:
                                return code_match.group(1) + code_match.group(2)
                    except:
                        pass

            except Exception as e:
                if attempt % 10 == 0:
                    print(f"    [mail] 错误: {e}")
            time.sleep(2)

        print(f"[-] {max_attempts*2}秒内未收到验证码 ({email})")
        return None

    def _delete_mailtm_account(self, email):
        account = self._mailtm_accounts.pop(email, None)
        if not account:
            return False
        try:
            res = requests.delete(
                f"{account['api']}/accounts/{account['id']}",
                headers={"Authorization": f"Bearer {account['token']}"},
                timeout=10
            )
            return res.status_code in (200, 204)
        except:
            return False

    # ── 统一接口 ──

    def create_email(self):
        """创建临时邮箱，返回 (jwt/email, email)"""
        if self.backend == "freemail":
            try:
                res = requests.get(
                    f"{self.base_url}/api/generate",
                    headers=self.headers,
                    timeout=10
                )
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

    def fetch_verification_code(self, email, max_attempts=30):
        """轮询获取验证码"""
        if self.backend == "freemail":
            for _ in range(max_attempts):
                try:
                    res = requests.get(
                        f"{self.base_url}/api/emails",
                        params={"mailbox": email},
                        headers=self.headers,
                        timeout=10
                    )
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
        if self.backend == "freemail":
            try:
                res = requests.delete(
                    f"{self.base_url}/api/mailboxes",
                    params={"address": address},
                    headers=self.headers,
                    timeout=10
                )
                return res.status_code == 200 and res.json().get("success")
            except:
                return False
        else:
            return self._delete_mailtm_account(address)
