# AI视频识别信号平台

**AI-VISION PRO · 工业级智能视觉感知平台 · v0.3.0**

## 1. 项目定位
**AI-VISION PRO v0.3.0** —— 工业级智能视觉感知平台
本项目以 **后端 AI 视频识别信号输出** 为核心：
- 接入摄像头视频流（USB / RTSP / HTTP / 手机网络流）
- YOLO 目标检测（yolo_service.py）+ IoU 多目标跟踪（tracking_service.py）
- 越界检测（叉积法）+ 滞留检测（射线法）规则引擎（rules_engine.py）
- MJPEG 实时流 + 检测框/HUD 叠加（stream_service.py）
- MiMo 视频理解分析（mimo_video_client.py）
- Agent 智能问答（agent_orchestrator.py + agent_tools.py）
- 75 个 RESTful API 端点 · 10 张 SQLite 表 · 4 个前端页面

示例信号：
```json
{"是否滞留": 1, "滞留人数": 3}
```

## 2. 当前业务范围（config/rules.yaml 实际配置）
两个场景、三个摄像头、四条规则：
1. 园区内部围栏检测（campus_fence）
- 翻越围栏（fence_intrusion · boundary · high 级别）
- 围栏区域滞留（fence_dwell · dwell · medium 级别）
2. 仓库 + 码头检测（warehouse_dock）
- 码头滞留（dock_dwell_person · dwell · medium 级别）
- 仓库滞留（warehouse_dwell · dwell · medium 级别）

## 3. 核心架构（代码实现）
- 配置驱动：`config/rules.yaml` + `config/tracker.yaml` + `config/vision_backend.yaml`
- API 网关：`backend/app/api/routes.py`（75 个端点，FastAPI 0.109.2）
- 检测输入：`POST /ingest/detections`
- 跟踪层：`tracking_service.py`（IoU 贪心跟踪器，match_thresh=0.15）
- 规则引擎：`rules_engine.py`（boundary 叉积法 + dwell 射线法 + cooldown）
- 流媒体：`stream_service.py`（MJPEG + 检测框/HUD 叠加）
- 持久化：`storage_service.py`（SQLite WAL 模式，10 张表，自动 schema 迁移）
- 视觉后端：`vision_backend_service.py`（YOLO ↔ MiMo 热切换）
- Agent：`agent_orchestrator.py` + `agent_tools.py`（意图识别 + 工具调用 + LLM 兜底）
- 信号输出：`GET /signals/output/{scene_id}?lang=cn`
- 视频回放：`replay_service.py`（定位 + ffmpeg 裁剪 + 降级）

## 4. 启动方式
### 4.0 首次环境准备（新电脑）
```powershell
cd D:\Project
.\setup_env.bat
```

### 4.0.1 Agent 云模型本地配置（推荐）
在项目根目录创建 `.env`（可先复制 `.env.example`）：
```env
API_KEY="sk-你的密钥"
BASE_URL="https://api.deepseek.com/v1"
MODEL_NAME="deepseek-chat"
AGENT_ENABLE_LLM="1"
```
说明：
- `.env` 会被后端自动读取（优先于系统环境变量）
- `.env` 已加入 Git 忽略，不会被提交
- Windows 可执行 `attrib +h .env` 隐藏文件
- 需要本机加密可执行 `cipher /e .env`（EFS）

### 4.1 推荐：分终端前台启动
```powershell
cd D:\Project
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```
```powershell
cd D:\Project
.\.venv\Scripts\python.exe -m http.server 5500 --directory frontend\static
```
访问：
- http://127.0.0.1:5500/index.html
- http://127.0.0.1:5500/module.html?scene=campus_fence
- http://127.0.0.1:5500/debug.html

说明：
- 监控页视频流已叠加识别框与置信度
- 默认主体模式仅显示人员
- 页面可切换到扩展预览，辅助显示人、车辆、常见动物

### 4.2 一键启动
```powershell
start_all_dev.bat
```

交付模式（组员拿包后）：
```powershell
start_delivery.bat
```

### 4.3 若 bat 运行失败，按手动命令启动
先创建环境并安装依赖：
```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

再分别启动后端和前端：
```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```
```powershell
.\.venv\Scripts\python.exe -m http.server 5500 --directory frontend\static
```

新电脑迁移细化说明见：
- `docs/NEW_PC_SETUP.md`

## 5. 主要接口
### 5.1 健康与配置
- `GET /health`
- `GET /config/rules`
- `GET /config/scenes`
- `POST /config/reload`（需调试token）

### 5.2 检测输入与事件
- `POST /ingest/detections`
- `GET /alerts`
- `GET /alerts/scene/{scene_id}`
- `GET /alerts/history`

### 5.3 信号输出
- `GET /signals/scenes`
- `GET /signals/scenes/{scene_id}`
- `GET /signals/history/{scene_id}`
- `GET /signals/output/{scene_id}?lang=cn|en`

### 5.4 运行状态与视频流
- `GET /runtime/status`
- `GET /runtime/ingest/recent`
- `GET /stream/{camera_id}`

### 5.5 调试接口
- `POST /debug/login`
- `GET /debug/ping`
- `POST /debug/simulate`
- `POST /debug/upload-video?camera_id=cam_fence`（multipart，字段名 `file`）
- `POST /debug/bind-video?camera_id=cam_fence&video_path=data/uploads/videos/...`

### 5.6 最小 Agent 接口（只读）
- `POST /agent/chat`
- `GET /agent/status`（查看是否启用模型、是否识别到密钥）

请求示例：
```json
{
  "query": "总结一下当前状态",
  "scene_id": "campus_fence",
  "camera_id": "cam_fence",
  "limit": 20
}
```

说明：
- 该接口为只读，不会修改规则配置和运行参数
- 当前核心仅保留 3 项：运行状态、告警摘要、回放定位参数建议
- 架构为混合式：本地工具执行 + 可选云端模型决策（模型不可用时自动回退本地）
- 响应中 `agent_mode` 用于标识当前模式（如 `local_fallback`、`hybrid_llm`）

Agent 可选模型配置（不配也能运行）：
- 推荐：项目根目录 `.env`
  - `API_KEY=...`
  - `BASE_URL=...`
  - `MODEL_NAME=...`
  - `AGENT_ENABLE_LLM=1`
- 兼容旧变量（环境变量方式）：`DEEPSEEK_API_KEY` / `AGENT_API_KEY` / `AGENT_BASE_URL` / `AGENT_MODEL`

## 6. 调试账号
默认：
- 用户名：`debug`
- 密码：`123456`

可通过环境变量覆盖：
- `DEBUG_USERNAME`
- `DEBUG_PASSWORD`
- `DEBUG_TOKEN_HOURS`

## 7. 数据与训练
当前建议：先完成链路联调，再做定向训练。

本地视频全链路验证（无需训练）：
```powershell
python scripts\webcam_pipeline.py --camera-id cam_fence --source data\uploads\videos\cam_fence\your_video.mp4 --no-view
```

多类别识别示例：
```powershell
python scripts\webcam_pipeline.py --camera-id cam_fence --classes person,vehicle,animal
```

默认主体模式（仅人）：
```powershell
python scripts\webcam_pipeline.py --camera-id cam_fence
```

训练入口：
```powershell
python scripts\yolo_train.py --data data\dataset.yaml --weights yolov8n.pt --epochs 50
```

数据模板：
- `data/dataset.template.yaml`

## 8. 自动化测试
```powershell
python -m unittest discover -s backend\tests -p "test_*.py" -v
```

当前测试覆盖：
- IoU 跟踪器轨迹稳定性
- 滞留规则触发与信号聚合
- 认证与权限（10个用例）
- 设备管理（8个用例）
- 规则引擎（10个用例）
- 告警管理（7个用例）
- 视频回放（6个用例）
- 流媒体（4个用例）
- 前端页面（10个用例）
- 安全测试（8个用例）
- 接口兼容性（6个用例）

**合计：73 个测试用例，通过率 100%**

## 9. 项目文档
项目交付文档位于 `docs/` 目录：

| 文档 | 文件 | 说明 |
| --- | --- | --- |
| 需求分析报告 | `docs/需求分析报告.docx` | 功能/非功能需求、验收标准 |
| 系统设计书 | `docs/项目系统设计书.docx` | 架构、数据库、接口、安全设计 |
| 测试报告 | `docs/测试报告.docx` | 功能测试、性能测试、准确率 |
| 接口说明 | `docs/接口说明.docx` | 60+ API 端点详细文档 |
| 用户手册 | `docs/用户手册.docx` | 系统使用指南 |
| 项目总结报告 | `docs/项目总结报告.docx` | 开发历程、问题与经验 |
| 完整项目文档 | `docs/AI-VISION_PRO_完整项目文档.md` | 一站式项目文档 |
