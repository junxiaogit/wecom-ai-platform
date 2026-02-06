# test_deep.py
import requests
import json
import time

# 1. 定义接口地址
url = "http://127.0.0.1:8000/api/analyze/deep_analysis"

# 2. 构造请求参数
payload = {
    "limit": 20,
    # 填入你之前查到的真实群ID，确保能查到数据
    "room_id": "wroCqZGwAAbmMa5peF0zU6LA-3RXcq8A", 
    # 🚀 关键参数：开启后，系统会把这 20 条数据存入向量库！
    "do_vectorize": True 
}

print(f"📡 正在请求智能体 Agent... (URL: {url})")
print("⏳ 正在进行：[RAG检索] -> [工单提取] -> [情感风控] -> [知识库构建]...")

try:
    start_time = time.time()
    response = requests.post(url, json=payload, timeout=60) # 分析比较耗时，超时设长一点
    
    if response.status_code == 200:
        data = response.json()
        print(f"\n✅ 分析成功！(耗时: {time.time() - start_time:.2f}s)")
        print("=" * 50)
        
        # 1. 打印结构化结果
        ai_result = data.get("ai_analysis", {})
        print(json.dumps(ai_result, indent=2, ensure_ascii=False))
        
        print("=" * 50)
        # 2. 检查知识库状态
        if data.get("knowledge_base_updated"):
            print("📚 [知识库]：后台任务已提交，聊天记录正在存入向量库...")
        
    else:
        print(f"❌ 请求失败 (Code {response.status_code}): {response.text}")

except Exception as e:
    print(f"❌ 发生错误: {e}")