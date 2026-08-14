# Copy to config.py (gitignored) and edit. server.py creates it for you on
# first run, auto-filling MODEL with whatever Ollama already has installed.

# Leave empty to let the harness pick the first installed model, or pin a
# fully-qualified tag ("llama3.1:8b", "qwen3:8b", "lfm2.5:latest"). A bare
# name without the tag resolves by substring match in Ollama and is brittle.
# The UI's model picker overrides this at runtime either way.
MODEL = ""

OLLAMA_BASE_URL = "http://localhost:11434/v1"
TEMPERATURE = 0.65
HOST = "127.0.0.1"
PORT = 8000
