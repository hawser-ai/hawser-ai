from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hawser.config import HawserConfig, RecordMode
from hawser.gateway import Gateway
from hawser.models.common import JsonSchema
from hawser.storage import SqlBackend, SQLiteBackend
from hawser.storage.schema import apply_migrations
from hawser.structured import inline_refs, to_json_schema
from hawser.testing import build_envelope
from hawser_server.app import create_app
from hawser_server.config import ServerConfig
from hawser_server.migrations import ALL_MIGRATIONS
from hawser_server.services import Services
from hawser_server.testing import LiveServer, ServicesFactory, static_gateway
from hawser_server.worker import Worker
from hawser_synth import Invoice, generate_invoice
from hawser_synth.invoice import GeneratedInvoice

# -- documents ---------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def invoice() -> GeneratedInvoice:
    return generate_invoice(11, layout=0)


@pytest.fixture(scope="session")
def invoice_schema() -> JsonSchema:
    return inline_refs(to_json_schema(Invoice))


@pytest.fixture
def invoice_pdf(tmp_path: Path, invoice: GeneratedInvoice) -> Path:
    p = tmp_path / "invoice.pdf"
    p.write_bytes(invoice.pdf)
    return p


@pytest.fixture(scope="session")
def truth_envelope(invoice: GeneratedInvoice, invoice_schema: JsonSchema) -> Any:
    return build_envelope(
        invoice_schema, invoice.truth.model_dump(mode="json"), source_texts=invoice.source_texts
    )


@pytest.fixture(scope="session")
def unknown_envelope(invoice: GeneratedInvoice, invoice_schema: JsonSchema) -> Any:
    return build_envelope(
        invoice_schema,
        invoice.truth.model_dump(mode="json"),
        source_texts=invoice.source_texts,
        unknown={"/vendor_name"},
    )


# -- databases ---------------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_admin_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str | None]:
    """A Postgres to create test databases in: ``HAWSER_TEST_DATABASE_URL`` or an embedded one."""
    env = os.environ.get("HAWSER_TEST_DATABASE_URL")
    if env:
        yield env
        return
    if os.environ.get("HAWSER_SKIP_PG"):
        yield None
        return
    try:
        import pgserver
    except ImportError:
        yield None
        return
    pgdata = tmp_path_factory.mktemp("pg")
    server = pgserver.get_server(str(pgdata))  # pyright: ignore[reportPrivateImportUsage]
    try:
        yield server.get_uri()
    finally:
        server.cleanup()


def _fresh_pg_database(admin_url: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    from hawser_server.db import connect_kwargs

    name = f"hawser_test_{uuid.uuid4().hex[:10]}"
    import pg8000.dbapi

    conn = pg8000.dbapi.connect(**connect_kwargs(admin_url))
    conn.autocommit = True
    conn.cursor().execute(f'CREATE DATABASE "{name}"')
    conn.close()
    parts = urlsplit(admin_url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


@pytest.fixture
def pg_url(pg_admin_url: str | None) -> str:
    if pg_admin_url is None:
        pytest.skip("no Postgres available (install pgserver or set HAWSER_TEST_DATABASE_URL)")
    return _fresh_pg_database(pg_admin_url)


@pytest.fixture(params=["sqlite", "postgres"])
def backend(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[SqlBackend]:
    if request.param == "sqlite":
        be: SqlBackend = SQLiteBackend(tmp_path / "meta.db")
    else:
        from hawser_server.db import PostgresBackend

        be = PostgresBackend(request.getfixturevalue("pg_url"))
    apply_migrations(be, ALL_MIGRATIONS)
    try:
        yield be
    finally:
        be.close()


# -- services, app, worker ---------------------------------------------------------------------


@pytest.fixture
def make_services(tmp_path: Path) -> Iterator[ServicesFactory]:
    created: list[Services] = []

    def factory(
        *,
        output: Any = None,
        gateway: Gateway | None = None,
        backend: SqlBackend | None = None,
        database_url: str | None = None,
        skill_paths: list[Path] | None = None,
        **config: Any,
    ) -> Services:
        n = len(created)
        runtime = HawserConfig(
            home=tmp_path / f"home{n}", record_mode=RecordMode.live, skill_paths=skill_paths or []
        )
        cfg = ServerConfig(
            database_url=database_url or f"sqlite:///{tmp_path / 'hawserd.db'}",
            artifacts_dir=tmp_path / "store",
            runtime=runtime,
            poll_interval_s=0.02,
            lease_seconds=5,
            **config,
        )
        svc = Services(
            cfg,
            backend=backend,
            gateway=gateway or static_gateway(output),
            migrate=True,
        )
        created.append(svc)
        return svc

    yield factory
    for s in created:
        s.close()


@pytest.fixture
def services(make_services: ServicesFactory, truth_envelope: Any) -> Services:
    return make_services(output=truth_envelope)


@pytest.fixture
def api_key(services: Services) -> str:
    tenant = services.auth.create_tenant("acme")
    _, token = services.auth.create_key(tenant.id, "test")
    return token


@pytest.fixture
def client(services: Services, api_key: str) -> Iterator[TestClient]:
    with TestClient(create_app(services), headers={"Authorization": f"Bearer {api_key}"}) as c:
        yield c


@pytest.fixture
def worker(services: Services) -> Iterator[Worker]:
    w = Worker(services, concurrency=2, lease_seconds=5, poll_s=0.02)
    w.start()
    try:
        yield w
    finally:
        w.stop()


@pytest.fixture
def live_server(services: Services) -> Iterator[LiveServer]:
    srv = LiveServer(services).start()
    try:
        yield srv
    finally:
        srv.stop()
