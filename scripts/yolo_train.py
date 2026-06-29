import argparse
from ultralytics import YOLO

def main():
    # 1. 定义参数解析器
    parser = argparse.ArgumentParser(description="YOLOv8 训练脚本升级版")
    parser.add_argument('--data', type=str, required=True, help='数据集配置文件路径')
    parser.add_argument('--weights', type=str, default='models/yolov8n.pt', help='权重文件')
    parser.add_argument('--epochs', type=int, default=50, help='训练轮数')
    parser.add_argument('--imgsz', type=int, default=640, help='图片尺寸')
    # 新增下面这两个参数支持
    parser.add_argument('--batch', type=int, default=16, help='批次大小')
    parser.add_argument('--workers', type=int, default=0, help='数据读取线程数(Windows建议为0)')

    args = parser.parse_args()

    # 2. 加载模型
    model = YOLO(args.weights)

    # 3. 开始训练
    # 这里将所有参数传递给 model.train
    model.train(
        data=args.data,
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        workers=args.workers,
        device=0  # 强制指定使用第一块显卡(RTX 3050)
    )

if __name__ == "__main__":
    main()