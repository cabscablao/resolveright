"""Memory footprint: with Guardrails AI off the server must not load transformers or torch.

On the Render free plan (512 MB) loading them made the service run out of memory on the first
LLM call (about 230 MB extra). The check runs in a fresh interpreter, because the test process
may already have imported them.
"""

import os
import subprocess
import sys

PROBE = (
    "import sys, warnings; warnings.filterwarnings('ignore'); import app.server; "
    "print('torch' in sys.modules, 'transformers' in sys.modules and sys.modules['transformers'] is not None)"
)


def _loaded(guardrails_ai: str) -> str:
    """Import the server in a fresh process and report whether torch / transformers got loaded."""
    env = {**os.environ, "GUARDRAILS_AI_ENABLED": guardrails_ai, "LANGFUSE_ENABLED": "false"}
    out = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert out.returncode == 0, out.stderr[-500:]
    return out.stdout.strip().splitlines()[-1]


def test_server_import_skips_torch_when_guardrails_ai_is_off():
    """No torch and no usable transformers module after importing the server with the flag off."""
    assert _loaded("false") == "False False"
