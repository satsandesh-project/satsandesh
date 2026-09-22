# THROWAWAY SPIKE — not wired into the project. Written for the Week 6 MT
# environment investigation (docs/MT_ENVIRONMENT_DECISION.md) to check whether
# `pip install indictranstoolkit` and `from IndicTransToolkit import
# IndicProcessor` work in a clean Linux container. Delete once the decision
# doc is reviewed, unless the team picks Docker as the Week 6 path.

FROM python:3.10-slim

RUN pip install --no-cache-dir indictranstoolkit "transformers==4.44.2"

CMD ["python", "-c", "from IndicTransToolkit import IndicProcessor; ip = IndicProcessor(inference=True); print('IMPORT AND INSTANTIATE OK:', type(ip))"]
