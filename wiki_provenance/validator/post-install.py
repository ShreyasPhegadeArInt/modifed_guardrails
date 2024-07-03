import subprocess
import sys

import nltk

# Install chromadb (and pysqlite3-binary on linux)
try:
    if sys.platform in ("linux", "linux2"):
        # If on linux, install pysqlite3-binary and follow steps as mentioned here:
        # https://gist.github.com/defulmere/8b9695e415a44271061cc8e272f3c300
        print("Installing `pysqlite3-binary`...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pysqlite3-binary"]
        )
        __import__("pysqlite3")
        sys.modules["sqlite3"] = sys.modules.pop("pysqlite3")

    # Install chromadb
    print("Installing `chromadb`...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "chromadb", "-q"])
    print("Successfully installed `chromadb`.")
except Exception as e:
    raise RuntimeError(f"Failed to install `chromadb`:\n{e}") from e


# Download NLTK data if not already present
try:
    nltk.data.find("tokenizers/punkt")
except LookupError:
    nltk.download("punkt")
print("NLTK stuff loaded successfully.")
