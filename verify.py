#!/usr/bin/env python3
"""Standalone entry point: ``python verify.py receipt.json --trusted-key HEX``.

Runs the independent verifier in ``src/gate_verify`` without installing the
package (it still needs ``cryptography``).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from gate_verify.verify import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
