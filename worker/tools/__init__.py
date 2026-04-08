# Import all tool modules so their @register_tool decorators fire at package load time.
# This file is executed by Python whenever any submodule of worker.tools is imported
# (e.g. `from worker.tools.registry import get_tool`), which guarantees _REGISTRY is
# populated before execute_step ever calls get_tool().
from worker.tools import code_exec, web_search  # noqa: F401
