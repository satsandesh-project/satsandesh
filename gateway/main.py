import os

import psycopg
from fastapi import FastAPI, HTTPException

import circles
from backbone_client import HttpCircleBackbone

app = FastAPI(title="SatSandesh Gateway")

DATABASE_URL = os.environ.get("DATABASE_URL")

# The one place the gateway names a concrete backbone. Everything else --
# circles.py in particular -- sees only the CircleBackbone interface, so
# resolving ADR 0002 means changing this line and BACKBONE_URL, not the
# route code.
circles.set_backbone(HttpCircleBackbone())
app.include_router(circles.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/db-check")
def db_check():
    """Confirms Postgres is reachable and db/init/001_init.sql ran."""
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT note FROM schema_check ORDER BY id LIMIT 1;")
                row = cur.fetchone()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail=f"database unreachable: {exc}")

    if row is None:
        raise HTTPException(status_code=500, detail="schema_check table is empty — init did not run")

    return {"status": "ok", "schema_check": row[0]}