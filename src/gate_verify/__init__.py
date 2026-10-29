"""Independent offline verifier for gate receipts.

This package deliberately imports nothing from ``gate_demo``. It re-implements
canonical encoding and every check from RECEIPT_SPEC.md, so a bug in the gate
cannot hide itself from the verifier.
"""
