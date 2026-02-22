"""调试邮件内容 - 看看 x.ai 验证码真实格式"""
import os, re, time, struct, string, random
from curl_cffi import requests as cffi_requests
import requests as std_requests
from dotenv import load_dotenv

load_dotenv()

site_url = "https://accounts.x.ai"
mailtm_api = "https://api.mail.tm"

def random_str(n=12):
    return ''.join(random.choice(string.ascii_lowercase + string.digits) for _ in range(n))

def encode_grpc_message(field_id, string_value):
    key = (field_id << 3) | 2
    value_bytes = string_value.encode('utf-8')
    length = len(value_bytes)
    payload = struct.pack('B', key) + struct.pack('B', length) + value_bytes
    return b'\x00' + struct.pack('>I', len(payload)) + payload

# 1. 获取 mail.tm 域名
print("[1] 获取 mail.tm 域名...")
res = std_requests.get(f"{mailtm_api}/domains", timeout=10)
domain = res.json().get("hydra:member", res.json().get("member", []))[0]["domain"]
print(f"    域名: {domain}")

# 2. 创建邮箱
print("[2] 创建邮箱...")
username = random_str()
address = f"{username}@{domain}"
password = random_str(16)
res = std_requests.post(f"{mailtm_api}/accounts", json={"address": address, "password": password}, timeout=10)
print(f"    状态: {res.status_code}")
if res.status_code not in (200, 201):
    print(f"    错误: {res.text}")
    exit(1)
account_id = res.json().get("id")

# 获取 token
res = std_requests.post(f"{mailtm_api}/token", json={"address": address, "password": password}, timeout=10)
token = res.json().get("token")
print(f"    邮箱: {address}")

# 3. 发送 x.ai 验证码
print("[3] 请求 x.ai 发送验证码...")
with cffi_requests.Session(impersonate="chrome120") as session:
    try:
        session.get(site_url, timeout=10)
    except:
        pass
    url = f"{site_url}/auth_mgmt.AuthManagement/CreateEmailValidationCode"
    data = encode_grpc_message(1, address)
    headers = {
        "content-type": "application/grpc-web+proto",
        "x-grpc-web": "1",
        "x-user-agent": "connect-es/2.1.1",
        "origin": site_url,
        "referer": f"{site_url}/sign-up?redirect=grok-com"
    }
    res = session.post(url, data=data, headers=headers, timeout=15)
    print(f"    gRPC 状态: {res.status_code}")
    print(f"    gRPC 响应 hex: {res.content.hex()}")

# 4. 等待并打印完整邮件
print("[4] 等待邮件到达...")
headers = {"Authorization": f"Bearer {token}"}
for attempt in range(60):
    time.sleep(2)
    res = std_requests.get(f"{mailtm_api}/messages", headers=headers, timeout=10)
    if res.status_code == 200:
        data = res.json()
        messages = data.get("hydra:member", data.get("member", []))
        if messages:
            msg = messages[0]
            print(f"\n=== 收到邮件 ===")
            print(f"  ID: {msg.get('id')}")
            print(f"  From: {msg.get('from', {}).get('address', 'unknown')}")
            print(f"  Subject: {msg.get('subject', 'N/A')}")
            print(f"  Intro: {msg.get('intro', 'N/A')}")

            # 获取完整内容
            msg_id = msg.get("id")
            detail = std_requests.get(f"{mailtm_api}/messages/{msg_id}", headers=headers, timeout=10)
            if detail.status_code == 200:
                msg_data = detail.json()
                print(f"\n--- text content ---")
                print(msg_data.get("text", "(empty)")[:500])
                print(f"\n--- html content (前500字) ---")
                html_content = ""
                html_list = msg_data.get("html", [])
                if isinstance(html_list, list):
                    html_content = " ".join(html_list)
                elif isinstance(html_list, str):
                    html_content = html_list
                print(html_content[:500])

                # 尝试提取验证码
                full_text = (msg_data.get("subject", "") + " " +
                            msg_data.get("text", "") + " " + html_content)
                print(f"\n--- 验证码提取尝试 ---")

                # 方法1: 3-3 格式
                m1 = re.search(r'(\d{3})-(\d{3})', full_text)
                if m1:
                    print(f"  3-3格式: {m1.group(0)} → {m1.group(1)}{m1.group(2)}")

                # 方法2: 6位数字
                m2 = re.findall(r'\b(\d{6})\b', full_text)
                if m2:
                    print(f"  6位数字: {m2}")

                # 方法3: "code" 或 "验证码" 附近的数字
                m3 = re.search(r'(?:code|验证码|verification)[:\s]*(\d{3,8})', full_text, re.I)
                if m3:
                    print(f"  code附近: {m3.group(1)}")

                # 方法4: "is" 后面的数字 (Your code is XXXXXX)
                m4 = re.search(r'(?:is|:)\s*(\d{3,8})', full_text, re.I)
                if m4:
                    print(f"  is/冒号后: {m4.group(1)}")
            break
    if attempt % 10 == 0:
        print(f"    等待中... ({attempt*2}s)")
else:
    print("[-] 60秒内未收到邮件")

# 清理
std_requests.delete(f"{mailtm_api}/accounts/{account_id}", headers={"Authorization": f"Bearer {token}"})
print("\n[*] 清理完成")
