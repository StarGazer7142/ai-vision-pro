import cv2
import json
import requests
from datetime import datetime
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent

# 加载 YOLO 模型
try:
    from ultralytics import YOLO
    model = YOLO('yolov8n.pt')
    print("YOLO 模型加载成功")
except Exception as e:
    print(f"YOLO 模型加载失败: {e}")
    exit(1)

# 打开摄像头
cap = cv2.VideoCapture(0)
if not cap.isOpened():
    print("无法打开摄像头")
    exit(1)
print("摄像头打开成功")

def normalize_bbox(x1, y1, x2, y2, width, height):
    return (
        max(0.0, min(1.0, x1 / width)),
        max(0.0, min(1.0, y1 / height)),
        max(0.0, min(1.0, x2 / width)),
        max(0.0, min(1.0, y2 / height)),
    )

def post_frame(payload, backend_url):
    try:
        print(f"发送到后端的数据: {json.dumps(payload, indent=2)}")
        resp = requests.post(
            f"{backend_url.rstrip('/')}/ingest/detections",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=1.5,
        )
        if resp.ok:
            print(f"后端响应: {resp.json()}")
            return resp.json()
        else:
            print(f"后端响应错误: {resp.status_code} - {resp.text}")
    except Exception as e:
        print(f"发送失败: {e}")
    return None

frame_idx = 0
try:
    while frame_idx < 5:  # 只运行5帧
        ret, frame = cap.read()
        if not ret:
            print("无法读取帧")
            break
        
        height, width = frame.shape[:2]
        
        # 运行 YOLO 检测
        result = model.track(
            frame,
            classes=[0],  # 只检测人
            conf=0.35,
            imgsz=640,
            persist=True,
            verbose=False,
        )[0]
        
        # 提取检测结果
        payload_dets = []
        boxes = getattr(result, "boxes", None)
        if boxes is not None and len(boxes) > 0:
            ids = boxes.id.tolist() if boxes.id is not None else [None] * len(boxes)
            xyxy_list = boxes.xyxy.tolist()
            conf_list = boxes.conf.tolist()
            cls_list = boxes.cls.tolist()
            
            for idx, (xyxy, conf, cls_id) in enumerate(zip(xyxy_list, conf_list, cls_list)):
                x1, y1, x2, y2 = [float(v) for v in xyxy]
                nx1, ny1, nx2, ny2 = normalize_bbox(x1, y1, x2, y2, width, height)
                track_id = None if ids[idx] is None else int(ids[idx])
                
                payload_dets.append({
                    "camera_id": "cam_fence",
                    "category": "person",
                    "confidence": float(conf),
                    "bbox": {"x1": nx1, "y1": ny1, "x2": nx2, "y2": ny2},
                    "track_id": track_id,
                    "class_id": int(cls_id),
                })
        
        # 构建 payload
        frame_payload = {
            "frame_id": f"cam_fence_{frame_idx}",
            "camera_id": "cam_fence",
            "timestamp": datetime.utcnow().isoformat(),
            "detections": payload_dets,
        }
        
        # 发送到后端
        print(f"\n第 {frame_idx+1} 帧")
        print(f"检测到 {len(payload_dets)} 个人")
        post_frame(frame_payload, "http://127.0.0.1:8000")
        
        frame_idx += 1
        
except KeyboardInterrupt:
    print("用户中断")
finally:
    cap.release()
    print("测试完成")