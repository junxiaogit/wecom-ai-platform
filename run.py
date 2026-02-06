# run.py
import uvicorn
from fastapi import FastAPI
from app.api.endpoints import router as api_router

app = FastAPI(title="WeCom AI Platform", description="企业微信会话内容智能分析后端")

# 注册路由
app.include_router(api_router, prefix="/api/analyze")

if __name__ == "__main__":
    # Reload=True 意味着你改了代码，服务会自动重启，不用手动关了再开
    uvicorn.run("run:app", host="0.0.0.0", port=8000, reload=True)