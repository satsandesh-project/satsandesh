# MT pivot service (services/ai/mt): IndicTrans2 indic -> English.
# Repo root as build context. NEEDS HF_TOKEN at runtime (the model is gated;
# accept its terms once on Hugging Face) -- the process fails fast without it.
#
# torch comes from PyTorch's CPU wheel index: the default PyPI wheel drags in
# gigabytes of CUDA libraries this CPU deployment never uses. UNVERIFIED on the
# shared server: building this image has been checked, running it has not
# (no HF token there) -- infra/ai/README.md, "What was and was not proven".
FROM python:3.11-slim

WORKDIR /app
COPY services/ai/pyproject.toml /tmp/ai/pyproject.toml
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu "/tmp/ai[mt]"

COPY contracts/ai ./contracts/ai
COPY services/ai/__init__.py ./services/ai/__init__.py
COPY services/ai/mt ./services/ai/mt

ENV PYTHONPATH=/app HF_HOME=/models/hf MT_PORT=8004
EXPOSE 8004
CMD ["uvicorn", "services.ai.mt.app:app", "--host", "0.0.0.0", "--port", "8004"]
