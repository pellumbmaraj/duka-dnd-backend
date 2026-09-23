import os
from pathlib import Path

from backend.admin import bootstrap_first_admin
from backend import create_app

# Load ignored, machine-local settings for `python main.py`; deployment environment
# variables still take precedence and are the recommended production configuration.
local_env = Path(__file__).resolve().with_name(".env")
if local_env.is_file():
    for line in local_env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            os.environ.setdefault(name.strip(), value.strip().strip("\"'"))

app = create_app()

if __name__ == "__main__":
    with app.app_context():
        bootstrap_first_admin()
    app.run()
