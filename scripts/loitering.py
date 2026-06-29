import cv2
import time # 🔴 新增导入
from ultralytics import YOLO

def process_loitering_video(input_video_path, output_video_path, model_path, stay_limit=10):
    model = YOLO(model_path)
    cap = cv2.VideoCapture(input_video_path)
    
    fps = int(cap.get(cv2.CAP_PROP_FPS))
    if fps == 0: fps = 25
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_video_path, fourcc, fps, (width, height))
    enter_times = {}

    while cap.isOpened():
        success, frame = cap.read()
        if not success: break

        # 🔴 修改：去掉 classes=[0] 的硬性限制，防止你自己训练的模型把人的 ID 变成了别的
        results = model.track(frame, persist=True, verbose=False)
        
        current_frame = cap.get(cv2.CAP_PROP_POS_FRAMES)
        video_current_time = current_frame / fps

        if results[0].boxes.id is not None:
            boxes = results[0].boxes.xyxy.cpu().numpy()
            track_ids = results[0].boxes.id.int().cpu().tolist()

            for box, track_id in zip(boxes, track_ids):
                x1, y1, x2, y2 = map(int, box)

                if track_id not in enter_times:
                    enter_times[track_id] = video_current_time
                
                stay_duration = video_current_time - enter_times[track_id]
                color = (0, 255, 0)
                text = f"ID:{track_id} {stay_duration:.1f}s"

                if stay_duration > stay_limit:
                    color = (0, 0, 255)
                    text = f"ALARM! ID:{track_id} Loitering!"

                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(frame, text, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        out.write(frame)
        # 🔴 新增：给网络线程喘息的机会，防假死！
        time.sleep(0.001)

    cap.release()
    out.release()
    return output_video_path