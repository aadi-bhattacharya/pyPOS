"""Build-time identity. The tagged release workflow stamps this file.

Updates remain disabled for local/source builds and workflow_dispatch binaries.
Never use a runtime environment variable to choose an update publisher.
"""
REPOSITORY = ""
ENABLED = False
