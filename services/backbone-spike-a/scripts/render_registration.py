"""
Substitutes real tokens from .env into registration.yaml, producing
registration.local.yaml (gitignored) -- the file actually pasted into
Conduit's admin room. Keeps real secrets out of the committed template.
"""

from pathlib import Path
from typing import Dict

SERVICE_DIR = Path(__file__).resolve().parent.parent


def load_env(path: Path) -> Dict[str, str]:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key] = value
    return env


HEADER = (
    "# GENERATED FILE - contains real secrets, gitignored, do not commit.\n"
    "# Rendered from registration.yaml + .env by render_registration.py.\n\n"
)


def main() -> None:
    env = load_env(SERVICE_DIR / ".env")
    template = (SERVICE_DIR / "registration.yaml").read_text()

    # Drop the template's own leading comment block (it describes the
    # template, not this generated file) and replace it with an accurate one.
    _, _, body = template.partition("\nid:")
    rendered = HEADER + "id:" + body

    rendered = rendered.replace("REPLACE_WITH_AS_TOKEN_FROM_ENV", env["AS_TOKEN"]).replace(
        "REPLACE_WITH_HS_TOKEN_FROM_ENV", env["HS_TOKEN"]
    )

    out_path = SERVICE_DIR / "registration.local.yaml"
    out_path.write_text(rendered)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
