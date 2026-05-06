"""``rearrange`` package marker.

The actual code lives under ``src/`` (split into ``core/``, ``services/``,
``utils/``). Layout follows the convention used by ``file_organizer/``: import
fully-qualified paths such as::

    from rearrange.src.core.steps import collect_orphan_items
    from rearrange.src.services.llm_gateway import LLMGateway

For this to work, the *parent* of this directory must be on ``sys.path``. The
back-compat shim ``file_rearrang.py`` handles that automatically when invoked
from the project root.
"""

__version__ = "0.1.0"
