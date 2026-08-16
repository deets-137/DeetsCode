# Copy to config.py (gitignored) and edit. server.py creates it for you on
# first run, auto-filling MODEL with whatever llama-server already knows about.

# Leave empty to let the harness pick the first available model, or pin one by
# the id llama-server reports (a GGUF filename stem from --models-dir, or an
# "org/repo:quant" id for -hf downloads). The UI's model picker overrides this
# at runtime either way.
MODEL = ""

# llama-server's OpenAI-compatible endpoint. Router mode (llama-server started
# with no -m) serves every discovered model behind this one URL and hot-swaps
# by the request's model name.
LLM_BASE_URL = "http://localhost:8080/v1"

# Autostart: if llama-server isn't reachable at boot, the harness spawns it
# with these args and kills it again on exit. Set EXE to "" to disable and
# manage llama-server yourself. Add "--models-dir", r"C:\path\to\ggufs" to ARGS
# if your models aren't in the llama.cpp cache; per-model settings go in a
# --models-preset ini (see llama-server.example.ini). Don't pin -ngl: the
# default (`-ngl auto` + `--fit on`) packs whatever fits in VRAM and spills
# the rest to CPU. Pin -c though — unset ctx defaults to the model's trained
# max, and the fit logic will sacrifice GPU layers to fit that KV cache.
LLAMA_SERVER_EXE = "llama-server"
LLAMA_SERVER_ARGS = ["--port", "8080", "-c", "32768"]

TEMPERATURE = 0.65
HOST = "127.0.0.1"
PORT = 8000
