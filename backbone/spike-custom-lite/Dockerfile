# Built from the REPO ROOT as context (see docker-compose.yml), not from
# this directory -- the image needs backbone/interfaces.py, which lives
# one level up and so is unreachable from a directory-scoped context.
FROM python:3.11-slim

WORKDIR /app

COPY backbone/spike-custom-lite/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The backbone contract, copied next to the code that implements it so
# `from interfaces import CircleBackbone` resolves the same way in the
# image as it does on the host.
COPY backbone/interfaces.py ./interfaces.py
COPY backbone/spike-custom-lite/ ./

CMD ["python", "run.py"]
