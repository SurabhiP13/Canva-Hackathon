from fastapi import FastAPI, UploadFile
import uvicorn
import asyncio
import shutil

app = FastAPI()

@app.post("/ocr")
async def ocr_endpoint(file: UploadFile):
    # Save uploaded file temporarily
    tmp_path = f"/tmp/{file.filename}"
    with open(tmp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    from receipt_client import run_ocr
    response = await run_ocr(tmp_path)
    return response

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
