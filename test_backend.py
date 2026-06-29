# 交互式测试后端服务
print("开始测试后端服务...")
try:
    from backend.app.main import app
    print("成功导入 app")
    
    # 尝试运行服务
    import uvicorn
    print("成功导入 uvicorn")
    
    print("准备启动服务...")
    # 这里不会实际启动服务，只是测试导入是否成功
    print("服务导入成功")
except Exception as e:
    print(f"启动失败: {e}")
    import traceback
    traceback.print_exc()

print("测试完成")