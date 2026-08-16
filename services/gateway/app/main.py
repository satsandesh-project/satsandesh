from fastapi import FastAPI

app = FastAPI(
    title="SatSandesh Gateway",
    version="0.1.0",
    description=(
        "Single front door for SatSandesh clients. Week 1 skeleton only — no "
        "routes, no proxying to services/ai/ or the chat backbone yet."
    ),
)
