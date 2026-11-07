"""Launch a built executable with a disposable empty profile and verify clean exit."""
import argparse
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="AlarmClock-build-") as temp:
        profile = Path(temp) / "Smoke profile 日本語"
        env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
        subprocess.run([str(executable), "--data-dir", str(profile), "--smoke-test"], check=True, timeout=30, env=env)
        database = profile / "alarms.sqlite3"
        if not database.is_file():
            raise RuntimeError("Executable did not initialize its test profile")
        with sqlite3.connect(database) as connection:
            if connection.execute("SELECT count(*) FROM alarms").fetchone()[0]:
                raise RuntimeError("Smoke profile unexpectedly contains alarms")
        if (profile / "instance.lock").exists():
            raise RuntimeError("Executable left its instance lock behind")
    print("Executable launched and exited successfully with an isolated empty profile.")


if __name__ == "__main__":
    main()
