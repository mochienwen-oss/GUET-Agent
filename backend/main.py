# main.py —— 稳定的 main:app 服务入口
from application.app import app


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8080)
