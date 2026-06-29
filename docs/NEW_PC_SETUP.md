# New PC Setup

## 1. Recommended path
Put the project in an ASCII-only folder, for example:

```powershell
D:\AI_Video_Platform
```

Avoid desktop folders, Chinese paths, and deeply nested folders on the first try.

## 2. Install Python first
Install Python `3.10.x` or `3.11.x` 64-bit.

During installation, make sure both options are enabled:
- `Add Python to PATH`
- `Install launcher for all users (py)`

## 3. Fast path
Open PowerShell in the project root and run:

```powershell
copy .env.example .env
```

Edit `.env` and set:

```env
API_KEY="sk-your-key"
BASE_URL="https://api.deepseek.com/v1"
MODEL_NAME="deepseek-chat"
AGENT_ENABLE_LLM="1"
```

Then run:

```powershell
.\setup_env.bat
.\start_delivery.bat
```

If these two scripts work, you are done.

## 4. Manual fallback
If the bat scripts still fail, do not stop there. Run the commands below one by one in PowerShell.

### 4.1 Create virtual environment
```powershell
py -3.10 -m venv .venv
```

If `py -3.10` is unavailable, try:

```powershell
python -m venv .venv
```

### 4.2 Upgrade pip tools
```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
```

### 4.3 Install dependencies
```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 4.4 Start backend
Open the first PowerShell window:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
```

### 4.5 Start frontend
Open the second PowerShell window:

```powershell
.\.venv\Scripts\python.exe -m http.server 5500 --directory frontend\static
```

### 4.6 Open pages
- `http://127.0.0.1:5500/index.html`
- `http://127.0.0.1:8000/health`

## 5. Common failure points
- `Python not found`
  Install Python 3.10 or 3.11 again and enable PATH.
- `No module named venv`
  Reinstall full Python from python.org instead of a stripped environment.
- `pip install` timeout or fail
  Check network, or prepare an offline `vendor\wheels` directory.
- Port `8000` or `5500` is already in use
  Stop the conflicting program and retry.

## 6. Project completeness check
These files must exist after copying:

```powershell
backend\app\main.py
frontend\static\index.html
requirements.txt
config\rules.yaml
models\yolov8n.pt
```
