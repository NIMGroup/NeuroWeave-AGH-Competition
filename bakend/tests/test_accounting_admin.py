"""The accounting installer must preserve GPUs, cgroups and partition policy."""
from pathlib import Path
import importlib.util
import pytest

path = Path(__file__).resolve().parents[2] / "scripts/configure_slurm_accounting.py"
spec = importlib.util.spec_from_file_location("accounting_admin",path)
admin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admin)


def test_accounting_plan_is_idempotent_and_preserves_unrelated_cluster_configuration():
    original = """ClusterName=mycluster
AccountingStorageType=accounting_storage/none
JobAcctGatherType=jobacct_gather/none
NodeName=node01 CPUs=128 RealMemory=245000 Gres=gpu:1
PartitionName=all Nodes=mgr,node[01-03] Default=YES MaxTime=INFINITE State=UP
TaskPlugin=task/none
ProctrackType=proctrack/linuxproc
GresTypes=gpu
"""
    planned = admin.plan_conf(original)
    assert admin.plan_conf(planned) == planned
    for line in original.splitlines():
        if not line.startswith(("AccountingStorageType","JobAcctGatherType")):
            assert line in planned.splitlines()
    assert "AccountingStorageHost=mgr" in planned
    assert "JobAcctGatherType=jobacct_gather/linux" in planned
    assert "AccountingStorageEnforce" not in planned
    assert admin.cluster_name(planned) == "mycluster"


def test_duplicate_accounting_keys_stop_before_writing():
    with pytest.raises(RuntimeError,match="Duplicate"):
        admin.plan_conf("AccountingStorageHost=mgr\naccountingstoragehost=elsewhere\n")
