import json
import requests
from datetime import datetime

# 模拟一个检测到人的场景
def test_signal_update():
    # 构建检测数据
    frame_payload = {
        "frame_id": "test_frame_1",
        "camera_id": "cam_fence",
        "timestamp": datetime.utcnow().isoformat(),
        "detections": [
            {
                "camera_id": "cam_fence",
                "category": "person",
                "confidence": 0.95,
                "bbox": {"x1": 0.1, "y1": 0.1, "x2": 0.2, "y2": 0.2},
                "track_id": 1,
                "class_id": 0
            }
        ]
    }
    
    # 发送到后端
    print("发送检测数据到后端...")
    try:
        response = requests.post(
            "http://127.0.0.1:8000/ingest/detections",
            data=json.dumps(frame_payload),
            headers={"Content-Type": "application/json"},
            timeout=1.5
        )
        print(f"后端响应状态码: {response.status_code}")
        if response.ok:
            print("后端响应数据:")
            print(json.dumps(response.json(), indent=2))
        else:
            print(f"后端响应错误: {response.text}")
    except Exception as e:
        print(f"发送失败: {e}")
    
    # 获取信号状态
    print("\n获取信号状态...")
    try:
        response = requests.get("http://127.0.0.1:8000/signals/scenes/campus_fence")
        print(f"信号状态响应状态码: {response.status_code}")
        if response.ok:
            print("信号状态数据:")
            print(json.dumps(response.json(), indent=2))
        else:
            print(f"信号状态获取错误: {response.text}")
    except Exception as e:
        print(f"获取信号状态失败: {e}")

if __name__ == "__main__":
    test_signal_update()