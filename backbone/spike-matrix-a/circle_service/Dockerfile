# Built from the REPO ROOT as context (see docker-compose.yml), not from
# this directory -- the image needs backbone/interfaces.py, the contract
# this service implements, which lives outside this directory.
FROM python:3.11-slim

WORKDIR /app

COPY backbone/spike-matrix-a/circle_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backbone/interfaces.py ./interfaces.py
COPY backbone/spike-matrix-a/circle_service/ ./

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
