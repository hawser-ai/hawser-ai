# Hawser

**Pull data out of documents and images with AI, and get proof for every value.**

You give Hawser a file (a PDF, a scan, a photo) and describe the data you want. An AI model reads
the file and fills in your fields, and Hawser checks the answer before you get it:

- **Every value comes with proof:** the page and the exact spot on the page where it was found.
- **No guessing:** a value that isn't in the document is marked `unknown`, never invented.
- **A clear verdict:** a result is `accepted` only when every check passes; otherwise it is
  `needs_review` and a person can fix and approve it.

Open source (Apache-2.0); runs on your own machine or server; works with Claude, GPT, Gemini and
local models.

## Install

```bash
pip install "hawser-ai[ocr]"
```

The command and the Python import are both `hawser`. `[ocr]` adds a local OCR engine for scans
and photos (on Linux it also needs `sudo apt-get install libgl1`). Python 3.12+.

## Try it (no API key needed)

```bash
mkdir my-project && cd my-project
hawser init          # creates a sample invoice, its schema and a saved model answer
hawser quickstart    # processes it offline, prints each field with its page and position
```

## Use it in your code

```python
import hawser
from pydantic import BaseModel


class Invoice(BaseModel):
    invoice_number: str
    vendor_name: str
    total: float


result = hawser.run(
    "Extract the invoice.",
    inputs=["invoice.pdf"],
    output_schema=Invoice,
    models="openrouter/anthropic/claude-haiku-5.5",  # needs OPENROUTER_API_KEY
)

if result.status == "accepted":
    print(result.parse_as(Invoice).total)
    for proof in result.evidence_for("/total"):
        print("Found on page", proof.page, "as", repr(proof.text))
else:
    for issue in result.validation.issues:  # status is needs_review, rejected or failed
        print(issue.field, issue.message)
```

Set `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY` or `OPENAI_API_KEY` for the provider you use;
`hawser models list` shows every model ID and its price.

## More

- **Step-by-step guide:** https://github.com/hawser-ai/hawser-ai#get-started
- **Documentation:** https://github.com/hawser-ai/hawser-ai/tree/main/docs
- **Measure accuracy on your own documents:** `hawser eval run` (included; see the
  [evaluations guide](https://github.com/hawser-ai/hawser-ai/blob/main/docs/concepts/evals.md))
- **Server and web app:** `pip install hawser-server` (see
  [hawser-server](https://pypi.org/project/hawser-server/))
- **Changelog:** https://github.com/hawser-ai/hawser-ai/blob/main/CHANGELOG.md
