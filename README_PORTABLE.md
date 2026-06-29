# Portable Package Guide

This package is prepared for moving the project to another Windows PC and reproducing the current workflow.

## Recommended install path

Use an ASCII-only path on the new computer, for example:

```powershell
D:\AI_Vision_Project
```

Avoid Chinese folder names, the Desktop, and very deep nested paths on the first run.

## What this package includes

- Source code: `backend`, `frontend`, `scripts`, `config`
- Model files: `models`
- Runtime database: `data\runtime\ai_platform.db`
- Current uploaded test videos: `data\uploads\videos`
- Generated replay clips: `data\replay_clips`
- Startup scripts: `setup_env.bat`, `start_all_dev.bat`, `start_delivery.bat`
- Reference docs: `README.md`, `docs\NEW_PC_SETUP.md`

## What is intentionally not bundled

- `.venv`
- Large local logs
- Training cache and temporary outputs that are not needed for runtime recovery

The new computer should recreate `.venv` locally by running `setup_env.bat`.

## First run on the new computer

1. Extract the zip to a path like `D:\AI_Vision_Project`.
2. Install Python `3.10.x` or `3.11.x` 64-bit if the machine does not already have it.
3. Open PowerShell in the project root.
4. Run:

```powershell
.\setup_env.bat
.\start_all_dev.bat
```

5. Open:

- `http://127.0.0.1:5500/index.html`
- `http://127.0.0.1:8000/health`

## Environment file

This package may contain the current `.env` for direct recovery on another machine.

If the package will be shared beyond your own devices, review or replace `.env` before forwarding it.

If `.env` is missing or you want a clean setup, copy `.env.example` to `.env` and fill the required values.

## Current replay clip location

Replay clips are now generated under the project path instead of the Windows temp directory:

```powershell
data\replay_clips
```

## Recommended verification after startup

1. Confirm the backend health page returns `{"status":"ok"}`.
2. Open the monitor matrix page and verify the frontend loads.
3. Upload a video and confirm alerts, replay, and MiMo analysis are available.
4. Open `module.html` and check that the `滞留阈值` button is visible.

## Troubleshooting

- If ports `8000` or `5500` are occupied, close the conflicting program and rerun `start_all_dev.bat`.
- If `pip install` fails on the new computer, use a stable internet connection and rerun `setup_env.bat`.
- If MiMo analysis is unavailable, verify `MIMO_API_KEY` and related entries in `.env`.

