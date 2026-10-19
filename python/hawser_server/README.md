# hawser-server

`hawserd`: the server for [Hawser](https://pypi.org/project/hawser-ai/). It gives you a REST API,
a web app for running documents and reviewing results, and background workers that resume runs
after a crash. It stores data in Postgres (or SQLite) and S3-compatible storage (or local files).

## Try it

```bash
pip install hawser-server
export OPENROUTER_API_KEY=...     # or ANTHROPIC_API_KEY / OPENAI_API_KEY
hawserd serve --dev
```

`--dev` uses SQLite and a local folder, runs a worker in the same process and prints an API key.
Then:

- **Web app:** http://127.0.0.1:8080/app (sign in with the printed key)
- **API docs:** http://127.0.0.1:8080/docs
- **CLI and SDK:** run the two `export` lines it prints; `hawser` commands and `hawser.run()`
  then use the server.

`--dev` is for trying things out on your own machine. Don't expose it to a network.

## Run it for real

```bash
hawserd migrate                                  # create or upgrade the database
hawserd admin create-tenant acme                 # prints the tenant id
hawserd admin create-key --tenant ten_... --name ci   # prints the API key once
hawserd serve                                    # the API and web app
hawserd worker                                   # run one or more workers
```

Configuration, Docker Compose, deployment and backups:
https://github.com/hawser-ai/hawser-ai/tree/main/docs/operator
