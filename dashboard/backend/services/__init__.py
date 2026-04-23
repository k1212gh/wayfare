"""Shared backend services (stateful helpers used by multiple API routers).

Extraction started in refactor Step 2. Each submodule owns one axis of I/O:
- adb_service: ADB / emulator subprocess calls + device discovery.
- (future) tour_store: pipeline_state + cancellation state.
- (future) pipeline_service: _run_pipeline_sync orchestration.
"""
