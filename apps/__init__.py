"""Apps — the apps-over-panels primitive (app_harness.md).

An app is a folder under apps/<name>/ that declares one or more panels,
owns shared per-instance state, and installs/uninstalls atomically. See
apps/loader.py for discovery and apps/context.py for the harness_ctx
handed to app panel handlers.
"""
