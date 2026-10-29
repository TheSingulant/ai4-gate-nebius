import pytest

from gate_demo.authorizers.reference import ReferenceAuthorizer
from gate_demo.clock import ManualClock
from gate_demo.gate import Gate
from gate_demo.workspace import Workspace


@pytest.fixture
def clock():
    return ManualClock()


@pytest.fixture
def authorizer(clock):
    return ReferenceAuthorizer(clock=clock)


@pytest.fixture
def workspace(tmp_path):
    ws = Workspace.create(base_dir=tmp_path)
    yield ws
    ws.cleanup()


@pytest.fixture
def gate(authorizer, workspace):
    return Gate(authorizer, workspace)


@pytest.fixture
def signer_hex(authorizer):
    return authorizer.public_keys()["receipt_signer"]["public_key_hex"]


@pytest.fixture
def approver_hex(authorizer):
    return authorizer.public_keys()["approvers"][0]["public_key_hex"]
