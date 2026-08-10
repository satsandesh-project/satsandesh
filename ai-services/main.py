from fastapi import FastAPI

app = FastAPI(title="SatSandesh AI Services")


@app.get("/health")
def health():
    return {"status": "ok", "service": "ai-services"}
