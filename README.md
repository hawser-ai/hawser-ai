# Hawser

**Pull data out of documents and images with AI, and get proof for every value.**

You give Hawser a file (a PDF, a scan, a photo) and describe the data you want. An AI model reads
the file and fills in your fields. Hawser then checks the answer before you get it:

- **Every value comes with proof.** You get the page and the exact spot on the page where each
  value was found, so anyone can check it.
- **No guessing.** If a value isn't in the document, Hawser marks it `unknown` instead of
  inventing one.
- **A clear verdict.** Each result is `accepted` only when all the checks pass. Otherwise it is
  `needs_review`, and a person can fix and approve it.

Hawser is open source (Apache-2.0) and runs on your own machine or server. It works with Claude,
GPT, Gemini and local models.

---

## How it works

```
 your file ──► 1. read ──► 2. extract ──► 3. check ──► 4. verdict
               text, or    an AI model    schema, rules,   accepted ──► use it
               OCR for     fills in your  and "where on    needs_review ──► a person fixes it
               scans       fields         the page?"
```

1. **Read.** Hawser reads the text layer of a PDF. For scans and photos it straightens the image
   and runs OCR.
2. **Extract.** A model of your choice fills in the fields you asked for.
3. **Check.** Hawser checks that the answer matches your schema, finds each value on the page,
   and applies any rules you add (for example, "line items must add up to the subtotal").
4. **Verdict.** The checks, not the model, decide whether the result is accepted.

---

## Get started

You need **Python 3.12 or newer**. The first two steps need no API key and cost nothing.

### Step 1: Install

```bash
pip install "hawser-ai[ocr]"
```

> **Before the first PyPI release**, install straight from GitHub instead:
>
> ```bash
> pip install "hawser-ai[ocr] @ git+https://github.com/hawser-ai/hawser-ai#subdirectory=python/hawser"
> ```

Check it worked:

```bash
hawser --version        # hawser 0.1.0
```

The package is called `hawser-ai`; the command and the Python import are both `hawser`.
`[ocr]` adds a local OCR engine for scans and photos. On Linux servers, OCR also needs one system
library: `sudo apt-get install libgl1`.

### Step 2: Try the example (no API key needed)

```bash
mkdir my-project && cd my-project
hawser init
hawser quickstart
```

`hawser init` creates a `quickstart/` folder containing a sample invoice (`invoice.pdf`), the
fields to extract (`invoice.schema.json`) and a saved model answer. `hawser quickstart` processes
the invoice using that saved answer, so it works offline and costs nothing.

You should see:

```
ACCEPTED  run run_01M59W8J...
{ "invoice_number": "INV-2026-00042", "vendor_name": "Northwind Components Ltd", "total": 15438.29, ... }

field            value                      status  page  bbox (pt)
/invoice_number  INV-2026-00042             found      1  108,97-180,104
/total           15438.29                   found      1  503,344-557,354
...
```

The `page` and `bbox` columns show exactly where on the page each value was found.

### Step 3: Add your model API key

Pick one provider and set its key:

```bash
export ANTHROPIC_API_KEY=...      # Claude, directly from Anthropic
export OPENROUTER_API_KEY=...     # or OpenRouter: Claude, GPT, Gemini and more with one key
export OPENAI_API_KEY=...         # or OpenAI
```

Run `hawser models list` to see every model ID and its price. Without an Anthropic key, choose a
model that matches your key (Step 4 shows how).

### Step 4: Extract data from your own document

Describe the fields you want as a Pydantic class, then call `hawser.run`. Save this as
`extract.py`:

```python
import hawser
from pydantic import BaseModel


# 1. Describe the data you want.
class Invoice(BaseModel):
    invoice_number: str
    vendor_name: str
    total: float


# 2. Run it on your file.
result = hawser.run(
    "Extract the invoice.",
    inputs=["quickstart/invoice.pdf"],          # replace with your own file
    output_schema=Invoice,
    models="openrouter/anthropic/claude-haiku-5.5",  # leave out to use Claude Opus (Anthropic key)
)

# 3. Use the result only if it passed the checks.
if result.status == "accepted":
    invoice = result.parse_as(Invoice)
    print("Total:", invoice.total)
    for proof in result.evidence_for("/total"):
        print("Found on page", proof.page, "as", repr(proof.text))
else:
    print("Status:", result.status)            # needs_review, rejected or failed
    for issue in result.validation.issues:
        print("-", issue.field, issue.message)
    print("Review it with: hawser review show", result.run_id)
```

Run it:

```bash
python extract.py
```

```
Total: 15438.29
Found on page 1 as 'Total Due: $15,438.29'
```

That run cost about $0.0003 with Claude Haiku. To see every step, field and cost of a run, use
`hawser inspect <run_id>`.

**What the status means:**

| Status | Meaning | What to do |
|---|---|---|
| `accepted` | Every check passed. | Use the data. |
| `needs_review` | A required value is missing, or a check failed. | A person reviews it (Step 5). |
| `rejected` | It failed and review was turned off. | Check `result.validation.issues`. |
| `failed` | Something broke (a bad file, a provider error). | Check `result.error`. |

### Step 5: Review results that need a person

Say your schema asks for a field that isn't in the document. Hawser won't guess it: the run ends
`needs_review`. Fix it from the command line:

```bash
hawser review list                                   # runs waiting for review
hawser review show run_01M59...                      # which fields have problems, and why
hawser review edit run_01M59... --set /bank_account_iban=GB29NWBK60161331926819
hawser review approve run_01M59...                   # the run is now accepted
```

Your correction is saved as a new revision. The model's original answer is kept unchanged.

### Step 6 (optional): Run it as a server with a web app

To call Hawser from other services, or to review results in a browser, run the server:

```bash
pip install hawser-server
hawserd serve --dev
```

It prints an API key. Open **http://127.0.0.1:8080/app** to run documents, inspect results and
review them in the browser. To point the CLI and SDK at the server, use the two `export` lines it
prints (`HAWSER_SERVER_URL` and `HAWSER_API_KEY`).

`--dev` keeps everything in a local folder and is for trying things out only. For a real
deployment (Postgres, S3-compatible storage and a background worker), clone this repository and
run:

```bash
export ANTHROPIC_API_KEY=...     # the worker calls the model, so it needs the key
make up                          # docker compose: Postgres, storage, server, worker
```

See the [operator guide](docs/operator/) before exposing it to a network.

---

## Prefer the command line?

Everything in Step 4 also works without writing Python:

```bash
hawser run -i "Extract the invoice." -f my-invoice.pdf \
  --schema quickstart/invoice.schema.json \
  --model openrouter/anthropic/claude-haiku-5.5
```

## Going further

| I want to... | Read |
|---|---|
| Add my own checks, e.g. "totals must add up" | [Validation](docs/concepts/validation.md) |
| Set cost limits, retries and review rules | [Policy](docs/concepts/policy.md) |
| Package a repeatable, versioned workflow | [Skills](docs/concepts/skills.md) |
| Process scans, photos and images | [Images and OCR](docs/concepts/images.md) |
| Choose or switch models | [Models](docs/concepts/models.md) |
| Measure accuracy on my own documents | [Evaluations](docs/concepts/evals.md) |
| Run Hawser as a service | [Server](docs/concepts/server.md) · [Operator guide](docs/operator/) |

The reference workflow (invoice ↔ purchase-order matching) scores 100% field accuracy on its
60-document benchmark, scans included: see the [benchmark](docs/benchmarks/invoice-po.md). All
documentation is listed in the [docs index](docs/README.md).

## Project status

Version 0.1 handles documents and images end to end. Audio and video are next; see the
[roadmap](docs/roadmap.md). The APIs may still change before 1.0, and every change is recorded
in the [changelog](CHANGELOG.md).

## Contributing

Bug reports, ideas and pull requests are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) explains
how to set up the repository (`make setup`, `make check`) and how the code is organised. Tests
never call a paid model: they replay saved responses.

## License

Apache-2.0. See [LICENSE](LICENSE).
