# 测试导入后端核心模块
print("开始测试导入模块...")
try:
    from backend.app.services.rules_engine import engine
    print("成功导入 rules_engine")
except Exception as e:
    print(f"导入 rules_engine 失败: {e}")

try:
    from backend.app.api.routes import router
    print("成功导入 routes")
except Exception as e:
    print(f"导入 routes 失败: {e}")

try:
    from backend.app.core.logging import configure_logging
    print("成功导入 logging")
except Exception as e:
    print(f"导入 logging 失败: {e}")

print("测试完成")