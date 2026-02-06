import os
import time
import jwt
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

app_id = os.getenv("TB_APP_ID")
app_secret = os.getenv("TB_APP_SECRET")

if not app_id or not app_secret:
    print("❌ 错误：未找到 .env 配置，请检查文件！")
else:
    # 生成 Token
    payload = {
        "_appId": app_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600
    }
    
    try:
        # 编码
        token = jwt.encode(payload, app_secret, algorithm="HS256")
        
        # 兼容旧版本 PyJWT (如果是 bytes 就转 string)
        if isinstance(token, bytes):
            token = token.decode("utf-8")
            
        # 打印完整 Token (没有任何省略号)
        print(token)
        
    except Exception as e:
        print(f"❌ 生成失败: {e}")