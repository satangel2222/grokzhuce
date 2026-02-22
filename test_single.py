"""单账号注册测试脚本"""
import os, json, random, string, time, re, struct
from urllib.parse import urljoin
from curl_cffi import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import requests as std_requests
from g import EmailService, TurnstileService, UserAgreementService, NsfwSettingsService

load_dotenv()

site_url = "https://accounts.x.ai"

def generate_random_name():
    length = random.randint(4, 6)
    return random.choice(string.ascii_uppercase) + ''.join(random.choice(string.ascii_lowercase) for _ in range(length - 1))

def generate_random_string(length=15):
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(length))

def encode_grpc_message(field_id, string_value):
    key = (field_id << 3) | 2
    value_bytes = string_value.encode('utf-8')
    length = len(value_bytes)
    payload = struct.pack('B', key) + struct.pack('B', length) + value_bytes
    return b'\x00' + struct.pack('>I', len(payload)) + payload

def encode_grpc_message_verify(email, code):
    p1 = struct.pack('B', (1 << 3) | 2) + struct.pack('B', len(email)) + email.encode('utf-8')
    p2 = struct.pack('B', (2 << 3) | 2) + struct.pack('B', len(code)) + code.encode('utf-8')
    payload = p1 + p2
    return b'\x00' + struct.pack('>I', len(payload)) + payload

def main():
    print("=== 单账号注册测试 ===")

    # 初始化服务
    email_service = EmailService()
    turnstile_service = TurnstileService()
    user_agreement_service = UserAgreementService()
    nsfw_service = NsfwSettingsService()

    # 获取 Action ID
    print("[1/8] 获取 Action ID...")
    config = {
        "site_key": "0x4AAAAAAAhr9JGVDZbrZOo0",
        "action_id": None,
        "state_tree": "%5B%22%22%2C%7B%22children%22%3A%5B%22(app)%22%2C%7B%22children%22%3A%5B%22(auth)%22%2C%7B%22children%22%3A%5B%22sign-up%22%2C%7B%22children%22%3A%5B%22__PAGE__%22%2C%7B%7D%2C%22%2Fsign-up%22%2C%22refresh%22%5D%7D%5D%7D%2Cnull%2Cnull%5D%7D%2Cnull%2Cnull%5D%7D%2Cnull%2Cnull%2Ctrue%5D"
    }

    start_url = f"{site_url}/sign-up"
    with requests.Session(impersonate="chrome120") as s:
        html = s.get(start_url).text
        key_match = re.search(r'sitekey":"(0x4[a-zA-Z0-9_-]+)"', html)
        if key_match:
            config["site_key"] = key_match.group(1)
        tree_match = re.search(r'next-router-state-tree":"([^"]+)"', html)
        if tree_match:
            config["state_tree"] = tree_match.group(1)
        soup = BeautifulSoup(html, 'html.parser')
        js_urls = [urljoin(start_url, script['src']) for script in soup.find_all('script', src=True) if '_next/static' in script['src']]
        for js_url in js_urls:
            js_content = s.get(js_url).text
            match = re.search(r'7f[a-fA-F0-9]{40}', js_content)
            if match:
                config["action_id"] = match.group(0)
                print(f"    Action ID: {config['action_id']}")
                break

    if not config["action_id"]:
        print("[-] 未找到 Action ID，退出")
        return

    # 创建邮箱
    print("[2/8] 创建临时邮箱...")
    jwt, email = email_service.create_email()
    if not email:
        print("[-] 创建邮箱失败")
        return
    print(f"    邮箱: {email}")
    password = generate_random_string()

    impersonate = "chrome120"
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    with requests.Session(impersonate=impersonate) as session:
        try:
            session.get(site_url, timeout=10)
        except:
            pass

        # 发送验证码
        print("[3/8] 发送验证码...")
        url = f"{site_url}/auth_mgmt.AuthManagement/CreateEmailValidationCode"
        data = encode_grpc_message(1, email)
        headers = {
            "content-type": "application/grpc-web+proto",
            "x-grpc-web": "1",
            "x-user-agent": "connect-es/2.1.1",
            "origin": site_url,
            "referer": f"{site_url}/sign-up?redirect=grok-com"
        }
        res = session.post(url, data=data, headers=headers, timeout=15)
        if res.status_code != 200:
            print(f"[-] 发送失败: {res.status_code}")
            email_service.delete_email(email)
            return
        print(f"    状态: {res.status_code} OK")

        # 获取验证码
        print("[4/8] 等待验证码...")
        verify_code = email_service.fetch_verification_code(email, max_attempts=45)
        if not verify_code:
            print("[-] 未收到验证码")
            email_service.delete_email(email)
            return
        print(f"    验证码: {verify_code}")

        # 验证验证码
        print("[5/8] 验证验证码...")
        url = f"{site_url}/auth_mgmt.AuthManagement/VerifyEmailValidationCode"
        data = encode_grpc_message_verify(email, verify_code)
        headers = {
            "content-type": "application/grpc-web+proto",
            "x-grpc-web": "1",
            "x-user-agent": "connect-es/2.1.1",
            "origin": site_url,
            "referer": f"{site_url}/sign-up?redirect=grok-com"
        }
        res = session.post(url, data=data, headers=headers, timeout=15)
        if res.status_code != 200:
            print(f"[-] 验证失败: {res.status_code}")
            email_service.delete_email(email)
            return
        print("    验证成功")

        # 解 Turnstile
        print("[6/8] 解 Turnstile 验证码...")
        task_id = turnstile_service.create_task(site_url, config["site_key"])
        print(f"    Task ID: {task_id}")
        token = turnstile_service.get_response(task_id)
        if not token:
            print("[-] Turnstile 求解失败")
            email_service.delete_email(email)
            return
        print(f"    Token: {token[:20]}...")

        # 注册
        print("[7/8] 提交注册...")
        headers = {
            "user-agent": ua,
            "accept": "text/x-component",
            "content-type": "text/plain;charset=UTF-8",
            "origin": site_url,
            "referer": f"{site_url}/sign-up",
            "cookie": f"__cf_bm={session.cookies.get('__cf_bm', '')}",
            "next-router-state-tree": config["state_tree"],
            "next-action": config["action_id"]
        }
        payload = [{
            "emailValidationCode": verify_code,
            "createUserAndSessionRequest": {
                "email": email,
                "givenName": generate_random_name(),
                "familyName": generate_random_name(),
                "clearTextPassword": password,
                "tosAcceptedVersion": "$undefined"
            },
            "turnstileToken": token,
            "promptOnDuplicateEmail": True
        }]

        res = session.post(f"{site_url}/sign-up", json=payload, headers=headers)
        print(f"    注册响应: {res.status_code}")

        if res.status_code == 200:
            match = re.search(r'(https://[^"\s]+set-cookie\?q=[^:"\s]+)1:', res.text)
            if not match:
                print("[-] 未找到 set-cookie URL")
                print(f"    响应前300字: {res.text[:300]}")
                email_service.delete_email(email)
                return

            verify_url = match.group(1)
            session.get(verify_url, allow_redirects=True)
            sso = session.cookies.get("sso")
            sso_rw = session.cookies.get("sso-rw")

            if not sso:
                print("[-] 未获取到 SSO Cookie")
                email_service.delete_email(email)
                return

            print(f"    SSO Token: {sso[:30]}...")

            # Accept ToS + NSFW
            print("[8/8] 设置 ToS + NSFW...")
            tos_result = user_agreement_service.accept_tos_version(
                sso=sso, sso_rw=sso_rw or "", impersonate=impersonate, user_agent=ua)
            tos_ok = tos_result.get("ok")
            print(f"    ToS: {'OK' if tos_ok else 'FAIL - ' + str(tos_result.get('error'))}")

            nsfw_result = nsfw_service.enable_nsfw(
                sso=sso, sso_rw=sso_rw or "", impersonate=impersonate, user_agent=ua)
            nsfw_ok = nsfw_result.get("ok")
            print(f"    NSFW: {'OK' if nsfw_ok else 'FAIL - ' + str(nsfw_result.get('error'))}")

            unhinged_result = nsfw_service.enable_unhinged(sso)
            unhinged_ok = unhinged_result.get("ok")
            print(f"    Unhinged: {'OK' if unhinged_ok else 'FAIL - ' + str(unhinged_result.get('error', ''))}")

            # 保存
            os.makedirs("keys", exist_ok=True)
            with open("keys/test_single.txt", "w") as f:
                f.write(sso + "\n")
            print(f"\n[+] 注册成功！SSO 已保存到 keys/test_single.txt")

            # 自动导入到 grok2api
            grok2api_url = os.getenv("GROK2API_URL", "").strip()
            grok2api_key = os.getenv("GROK2API_KEY", "").strip()
            grok2api_pool = os.getenv("GROK2API_POOL", "ssoSuper").strip()
            if grok2api_url and grok2api_key:
                print(f"\n[9/9] 自动导入到 grok2api...")
                try:
                    r = std_requests.get(
                        f"{grok2api_url}/v1/admin/tokens",
                        headers={"Authorization": f"Bearer {grok2api_key}"},
                        timeout=10
                    )
                    existing = r.json() if r.status_code == 200 else {}
                    pool = existing.get(grok2api_pool, [])
                    raw = [t if isinstance(t, str) else t.get("token", "") for t in pool]
                    if sso not in raw:
                        raw.append(sso)
                    r = std_requests.post(
                        f"{grok2api_url}/v1/admin/tokens",
                        headers={"Authorization": f"Bearer {grok2api_key}", "Content-Type": "application/json"},
                        json={grok2api_pool: raw},
                        timeout=10
                    )
                    if r.status_code == 200:
                        print(f"    ✓ 已导入 {grok2api_url} → {grok2api_pool} 池")
                    else:
                        print(f"    ✗ 导入失败: {r.status_code}")
                except Exception as e:
                    print(f"    ✗ 导入异常: {e}")
        else:
            print(f"[-] 注册失败: {res.status_code}")
            print(f"    响应: {res.text[:300]}")

        email_service.delete_email(email)
        print("[*] 清理完成")

if __name__ == "__main__":
    main()
