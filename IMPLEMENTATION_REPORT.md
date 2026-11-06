# Lambda Tutor Implementation Report

## 1. Overview

Lambda Tutor is an interactive lambda-calculus teaching tool implemented from
the supplied specification. It evaluates lambda terms locally, produces
structured traces, checks a core simply typed lambda calculus (STLC), and can
send traces to Dartmouth for grounded explanations.

The local evaluator and type checker are deterministic. Dartmouth is used as
an explanation service; it is not responsible for deciding whether a term
reduces correctly or whether a type is valid.

## 2. Repository deliverables

### Core implementation

- [lambda_tutor.py](./lambda_tutor.py)
  - Surface parser for lambda expressions and types
  - Named-variable lambda terms
  - Application, literals, and conditionals
  - Lazy and full beta-reduction
  - Structured reduction traces
  - STLC inference, unification, and diagnostics
  - Interactive REPL
  - Dartmouth HTTP client
  - Offline explanation fallback
  - Interactive Dartmouth model selection
  - TLS certificate-bundle support

### Prompt templates

- [prompts/explain_reduction.md](./prompts/explain_reduction.md)
- [prompts/explain_typing.md](./prompts/explain_typing.md)
- [prompts/explain_type_error.md](./prompts/explain_type_error.md)
- [prompts/follow_up.md](./prompts/follow_up.md)

The prompts constrain the model to explain supplied traces and diagnostics
instead of inventing evaluation or typing facts.

### Tests

- [tests/test_lambda_tutor.py](./tests/test_lambda_tutor.py)

The tests cover parsing, reduction, typing, model selection, stale-model
detection, and Dartmouth model endpoint construction.

### Project configuration

- [Makefile](./Makefile)
- [.env.example](./.env.example)
- [.gitignore](./.gitignore)
- [README.md](./README.md)

The `.env` file is intentionally local and ignored by Git. The repository
contains only the safe `.env.example` template.

## 3. Untyped lambda calculus

The tutor supports:

- Lambda abstraction, such as `\x. x`
- Application, such as `(\x. x) 4`
- Integer literals
- Boolean literals
- Conditional expressions
- Parenthesized expressions
- Left-associative application
- Full reduction
- Lazy reduction

Example:

```text
λ> (\x. x) 4
=> 4 (1 steps)
```

Each reduction trace includes:

- Input expression
- Evaluation strategy
- Step indexes
- Redex function
- Redex argument
- Result after each step
- Reduction depth
- Parent path
- Substitution metadata
- Final result

The trace can be printed as JSON with:

```text
:trace
```

## 4. Typed lambda calculus

The implementation includes the core typed-tutor functionality requested in
the lecture feedback.

### Supported type features

- Base types:
  - `Int`
  - `Bool`
  - `Unit`
- Function types, such as `Int -> Int`
- Typed lambda parameters, such as:

  ```text
  \x:Int. x
  ```

- Integer literals
- Boolean literals
- `if` expressions
- Application type checking
- Type-variable unification
- Unbound-variable detection
- Type mismatch detection
- Non-function application detection
- Infinite-type/self-application detection
- Cannot-infer diagnostics for unresolved types
- Rule-based correction suggestions

Example:

```text
λ> :type \x:Int. x
Int -> Int
```

Typed mode can be toggled with:

```text
:typed
```

When enabled, expressions are type-checked before evaluation.

The local checker is authoritative. The LLM may explain a type result or
diagnostic, but it does not invent the type, error location, or suggested
correction.

## 5. Interactive REPL

Start the tutor with:

```bash
python3.10 lambda_tutor.py
```

Available commands:

| Command | Behavior |
| --- | --- |
| `:type EXPR` | Infer a type or print a structured type diagnostic |
| `:trace` | Print the latest reduction trace |
| `:explain` | Explain the latest trace |
| `:follow-up QUESTION` | Ask a follow-up about the latest trace |
| `:mode lazy` | Use lazy reduction |
| `:mode full` | Use full reduction |
| `:typed` | Toggle type checking before evaluation |
| `:models` | Refresh and choose a Dartmouth model |
| `:help` | Show command help |
| `:quit` | Exit |

Example:

```text
λ> (\x. x) 4
=> 4 (1 steps)
λ> :explain
0. Apply (λx. x) to 4 to get 4.
The computation ends at 4.
```

## 6. Dartmouth integration

The client uses Dartmouth's OpenAI-compatible chat-completions endpoint:

```text
https://chat.dartmouth.edu/api/chat/completions
```

The application reads:

| Variable | Purpose |
| --- | --- |
| `DARTMOUTH_CHAT_API_KEY` | Dartmouth bearer credential |
| `DARTMOUTH_CHAT_URL` | Optional chat endpoint override |
| `DARTMOUTH_MODEL` | Optional selected model ID |
| `DARTMOUTH_CA_BUNDLE` | Optional trusted PEM CA bundle path |

The model-list endpoint is derived as:

```text
https://chat.dartmouth.edu/api/models
```

### Interactive model selection

If a key is configured and `DARTMOUTH_MODEL` is empty, startup fetches the
models available to the account and displays a numbered list:

```text
Available Dartmouth models:
  1. ...
  2. ...
Select a model number (or q to cancel):
```

The selected model applies to the current process. The `:models` command
refreshes the list and permits switching models without restarting.

If `.env` contains a model that Dartmouth no longer offers, startup detects
the stale value and opens the selector again.

## 7. Environment and credentials

Create a local environment file:

```bash
cp .env.example .env
```

Set the API key in `.env`:

```dotenv
DARTMOUTH_CHAT_API_KEY=your-dartmouth-key
DARTMOUTH_CHAT_URL=https://chat.dartmouth.edu/api/chat/completions
DARTMOUTH_MODEL=
DARTMOUTH_CA_BUNDLE=
```

Load the variables:

```bash
set -a
source .env
set +a
```

Real keys must not be placed in `.env.example`, source files, screenshots,
logs, or commits. `.env` is ignored by [.gitignore](./.gitignore).

## 8. TLS certificate handling

Live Dartmouth requests use certificate verification. TLS verification is
never disabled.

If Python reports `CERTIFICATE_VERIFY_FAILED`, install `certifi` into the
same Python environment:

```bash
python3.10 -m pip install certifi
```

Find the trusted bundle:

```bash
python3.10 -c "import certifi; print(certifi.where())"
```

Set it for the current shell:

```bash
export DARTMOUTH_CA_BUNDLE="$(python3.10 -c 'import certifi; print(certifi.where())')"
```

Or put the absolute path in `.env`:

```dotenv
DARTMOUTH_CA_BUNDLE=/absolute/path/to/site-packages/certifi/cacert.pem
```

Verify the path:

```bash
test -f "$DARTMOUTH_CA_BUNDLE" && echo "CA bundle found"
```

The application now reports clear configuration errors for missing or invalid
CA bundles instead of exposing an unhandled `FileNotFoundError`.

Both the model-list request and chat-completion request use the configured
verified TLS context.

## 9. Testing

Run the automated suite:

```bash
make test
```

Equivalent command:

```bash
python3.10 -m unittest discover -s tests -v
```

The final validation run passed:

```text
Ran 8 tests
OK
```

The test suite includes:

1. Identity reduction
2. Nested application reduction
3. Application parsing
4. Typed identity inference
5. Rejection of an unannotated type that cannot be inferred
6. Interactive model selection
7. Stale configured-model reselection
8. Dartmouth model endpoint construction

## 10. Manual Dartmouth connectivity test

After loading `.env` and selecting a valid model, send a small request:

```bash
curl --fail-with-body --silent --show-error \
  "$DARTMOUTH_CHAT_URL" \
  -H "Authorization: Bearer ${DARTMOUTH_CHAT_API_KEY}" \
  -H "Content-Type: application/json" \
  --data '{
    "model": "'"${DARTMOUTH_MODEL}"'",
    "messages": [
      {"role": "user", "content": "Reply with exactly: Dartmouth API is running"}
    ],
    "stream": false
  }'
```

To list models:

```bash
curl --fail-with-body --silent --show-error \
  "https://chat.dartmouth.edu/api/models" \
  -H "Authorization: Bearer ${DARTMOUTH_CHAT_API_KEY}"
```

An error such as:

```json
{"detail":"Model not found"}
```

means the endpoint and authentication were reached, but the selected model ID
is unavailable. Choose an ID from the `/api/models` response.

## 11. Troubleshooting history and fixes

### Model not found

The initial example model became unavailable. The project was changed to:

- Query Dartmouth's live model list
- Let the user select a model interactively
- Detect stale configured model IDs
- Refresh selection with `:models`

### Certificate verification failure

The Python installation did not have a usable default CA path. The project
was changed to:

- Support `certifi`
- Support `DARTMOUTH_CA_BUNDLE`
- Support `SSL_CERT_FILE`
- Keep TLS verification enabled
- Use the same verified context for model listing and chat requests
- Report missing or invalid bundle paths clearly

### Wrong model-list endpoint

The model-list URL was corrected from a derived `/api/chat/models` path to
Dartmouth's `/api/models` endpoint.

### Literal terminal escape sequences

Entering an arrow key as pasted text can produce an input such as `^[[C`.
That is not a lambda-calculus expression. Re-enter the expression normally.

## 12. Current limitations

- Full typing-derivation traces are not yet exposed as a dedicated REPL trace.
- Dartmouth currently explains the reduction trace; the typed checker remains
  local and authoritative.
- The parser uses named variables rather than exposing canonical De Bruijn
  notation directly.
- The implementation does not yet include the full evaluation harness,
  large demo corpus, or all benchmark cases described in the original
  specification.
- No dependency installation is required for offline mode.

## 13. Recommended first-time workflow

```bash
cd /Users/user_1/lambdatutor
python3.10 --version
python3.10 -m pip install certifi
cp .env.example .env
```

Edit `.env`, add the Dartmouth API key, then load it:

```bash
set -a
source .env
set +a
export DARTMOUTH_CA_BUNDLE="$(python3.10 -c 'import certifi; print(certifi.where())')"
```

Run tests:

```bash
make test
```

Start the tutor:

```bash
python3.10 lambda_tutor.py
```

Select a model and test:

```text
(\x. x) 4
:trace
:explain
:type \x:Int. x
:quit
```
