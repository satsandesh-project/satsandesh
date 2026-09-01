# Built from the REPO ROOT as context (see docker-compose.yml), not from
# this directory -- the image needs backbone/interfaces.py, the contract
# the gateway's backbone client implements.
FROM python:3.11-slim

WORKDIR /app

COPY gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The backbone contract. The gateway depends on this abstraction only,
# never on a concrete backbone's internals -- see backbone/interfaces.py
# and ADR 0002 for why that boundary exists.
COPY backbone/interfaces.py ./interfaces.py
COPY gateway/ ./

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
