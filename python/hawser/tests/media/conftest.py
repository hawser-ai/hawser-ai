import pytest

from hawser_synth import generate_invoice
from hawser_synth.invoice import GeneratedInvoice


@pytest.fixture(scope="session")
def invoice() -> GeneratedInvoice:
    return generate_invoice(7, layout=0)


@pytest.fixture(scope="session")
def scanned_invoice() -> GeneratedInvoice:
    return generate_invoice(7, layout=0, scanned=True)
