import traceback

try:
    print("Importing app...")
    from backend.app.main import app
    print("App imported successfully")
    
    print("Starting server...")
    import uvicorn
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8000, log_level="info")
    
except Exception as e:
    print(f"Error: {e}")
    traceback.print_exc()
