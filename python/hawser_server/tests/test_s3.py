from __future__ import annotations

from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

from hawser.config import HawserConfig, RecordMode
from hawser.hashing import content_hash, scoped_hash
from hawser.models import PathInput, RunRequest, StorageInput
from hawser.storage import artifact_key
from hawser_server.config import ServerConfig
from hawser_server.services import Services
from hawser_server.storage import S3ArtifactStore
from hawser_server.testing import static_gateway


@mock_aws
def test_s3_artifact_store_round_trip() -> None:
    client = boto3.client("s3", region_name="us-east-1")
    store = S3ArtifactStore("hawser-test", client=client, presign_ttl_s=120)
    store.ensure_bucket()
    key = artifact_key(
        "ten_00000000000000000000000000",
        scoped_hash("ten_00000000000000000000000000", content_hash(b"hello")),
    )
    assert not store.exists(key)
    store.put_bytes(key, b"hello")
    store.put_bytes(key, b"ignored")  # content-addressed: first write wins
    assert store.exists(key)
    assert store.get_bytes(key) == b"hello"
    url = store.url(key, ttl_s=600)
    assert "hawser-test" in url
    assert "Expires" in url and "Signature" in url
    client.put_object(Bucket="hawser-test", Key="in/doc.txt", Body=b"Doc\n")
    assert store.read_uri("s3://hawser-test/in/doc.txt") == b"Doc\n"
    with pytest.raises(ValueError, match="s3://"):
        store.read_uri("https://x")


@mock_aws
def test_services_with_s3_and_storage_inputs(
    tmp_path: Path, truth_envelope: Any, invoice_pdf: Path
) -> None:
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="inputs")
    client.put_object(Bucket="inputs", Key="invoice.pdf", Body=invoice_pdf.read_bytes())
    cfg = ServerConfig(
        database_url=f"sqlite:///{tmp_path / 'db'}",
        s3_bucket="artifacts",
        artifacts_proxy=False,
        runtime=HawserConfig(home=tmp_path / "home", record_mode=RecordMode.live),
    )
    svc = Services(cfg, gateway=static_gateway(truth_envelope), s3_client=client, migrate=True)
    svc.bootstrap()  # creates the artifacts bucket
    tenant = svc.auth.create_tenant("acme").id
    req = RunRequest(
        instruction="Extract the invoice.",
        inputs=[StorageInput(uri="s3://inputs/invoice.pdf", name="invoice.pdf")],
        output_schema={"type": "object", "properties": {"invoice_number": {"type": "string"}}},
    )
    run, _ = svc.engine.create_run(req, tenant_id=tenant)
    result = svc.engine.execute(run)
    assert result.status.value in ("accepted", "needs_review"), result.error
    original = result.artifacts[0]
    art = svc.meta.get_artifact(original.artifact_id)
    assert art is not None
    assert (
        svc.store.get_bytes(artifact_key(tenant, art.meta.scoped_hash)) == invoice_pdf.read_bytes()
    )
    assert svc.s3 is not None
    assert "Signature" in svc.s3.url(artifact_key(tenant, art.meta.scoped_hash))
    # path inputs are still rejected server-side; storage inputs need the reader
    assert PathInput(path="x").kind == "path"
    svc.close()
