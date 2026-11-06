#!/usr/bin/env python3
"""An LLM-assisted lambda-calculus tutor.

The evaluator and type checker are deliberately local and deterministic. The
language model receives only their structured trace and is never responsible
for deciding whether a term is valid.
"""
from __future__ import annotations

import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit


class TutorError(Exception):
    pass


@dataclass(frozen=True)
class Var:
    name: str


@dataclass(frozen=True)
class Int:
    value: int


@dataclass(frozen=True)
class Bool:
    value: bool


@dataclass(frozen=True)
class Abs:
    name: str
    body: "Term"
    annotation: Optional["Type"] = None


@dataclass(frozen=True)
class App:
    function: "Term"
    argument: "Term"


@dataclass(frozen=True)
class If:
    condition: "Term"
    then_branch: "Term"
    else_branch: "Term"


Term = Var | Int | Bool | Abs | App | If
Type = str | tuple["Type", "Type"] | tuple[str, int]


class Lexer:
    _pattern = re.compile(r"\s*(?:(->)|([()\\λ.:])|([A-Za-z_][A-Za-z0-9_']*)|(-?[0-9]+))")

    def __init__(self, source: str):
        self.tokens: list[str] = []
        position = 0
        while position < len(source):
            match = self._pattern.match(source, position)
            if not match:
                raise TutorError(f"Unexpected character at position {position}: {source[position]!r}")
            self.tokens.append(next(group for group in match.groups() if group is not None))
            position = match.end()
        self.index = 0

    def peek(self) -> Optional[str]:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def pop(self, expected: Optional[str] = None) -> str:
        token = self.peek()
        if token is None:
            raise TutorError("Unexpected end of expression")
        if expected is not None and token != expected:
            raise TutorError(f"Expected {expected!r}, got {token!r}")
        self.index += 1
        return token


class Parser:
    def __init__(self, source: str):
        self.lexer = Lexer(source)

    def parse(self) -> Term:
        term = self.term()
        if self.lexer.peek() is not None:
            raise TutorError(f"Unexpected token {self.lexer.peek()!r}")
        return term

    def term(self) -> Term:
        if self.lexer.peek() in ("\\", "λ"):
            return self.abstraction()
        if self.lexer.peek() == "if":
            self.lexer.pop()
            return If(self.term(), self.term(), self.term())
        first = self.atom()
        while self.lexer.peek() not in (None, ")"):
            if self.lexer.peek() in (".", ":", "->"):
                break
            first = App(first, self.atom())
        return first

    def atom(self) -> Term:
        token = self.lexer.peek()
        if token == "(":
            self.lexer.pop()
            value = self.term()
            self.lexer.pop(")")
            return value
        if token is None:
            raise TutorError("Expected an expression")
        self.lexer.pop()
        if token.isdigit() or (token.startswith("-") and token[1:].isdigit()):
            return Int(int(token))
        if token in ("true", "false"):
            return Bool(token == "true")
        return Var(token)

    def abstraction(self) -> Abs:
        self.lexer.pop()
        binder = self.lexer.pop()
        annotation = None
        if self.lexer.peek() == ":":
            self.lexer.pop()
            annotation = self.type_expr()
        self.lexer.pop(".")
        return Abs(binder, self.term(), annotation)

    def type_expr(self) -> Type:
        left = self.lexer.pop()
        if left not in ("Int", "Bool", "Unit"):
            raise TutorError(f"Unknown type {left!r}")
        result: Type = left
        if self.lexer.peek() == "->":
            self.lexer.pop()
            result = (result, self.type_expr())
        return result


def parse(source: str) -> Term:
    """Parse lambda notation, including ``\\x:Int. x`` and ``if c t e``."""
    # Accept the S-expression spelling from the specification as a convenience.
    source = re.sub(r"\(\s*lambda\s*\(\s*\(\s*([A-Za-z_]\w*)\s*:\s*(Int|Bool|Unit)\s*\)\s*\)\s*", r"\\\1:\2.", source)
    source = re.sub(r"\(\s*lambda\s*\(\s*([A-Za-z_]\w*)\s*\)\s*", r"\\\1.", source)
    return Parser(source).parse()


def pretty(term: Term) -> str:
    if isinstance(term, Var):
        return term.name
    if isinstance(term, Int):
        return str(term.value)
    if isinstance(term, Bool):
        return "true" if term.value else "false"
    if isinstance(term, Abs):
        annotation = f":{format_type(term.annotation)}" if term.annotation else ""
        return f"(λ{term.name}{annotation}. {pretty(term.body)})"
    if isinstance(term, App):
        return f"({pretty(term.function)} {pretty(term.argument)})"
    return f"(if {pretty(term.condition)} {pretty(term.then_branch)} {pretty(term.else_branch)})"


def format_type(type_: Optional[Type]) -> str:
    if type_ is None:
        return "?"
    if isinstance(type_, tuple) and len(type_) == 2 and type_[0] == "var":
        return f"α{type_[1]}"
    if isinstance(type_, tuple):
        left, right = type_
        left_text = f"({format_type(left)})" if isinstance(left, tuple) and left[0] != "var" else format_type(left)
        return f"{left_text} -> {format_type(right)}"
    return type_


def substitute(term: Term, name: str, replacement: Term) -> Term:
    if isinstance(term, Var):
        return replacement if term.name == name else term
    if isinstance(term, (Int, Bool)):
        return term
    if isinstance(term, Abs):
        return term if term.name == name else Abs(term.name, substitute(term.body, name, replacement), term.annotation)
    if isinstance(term, App):
        return App(substitute(term.function, name, replacement), substitute(term.argument, name, replacement))
    return If(substitute(term.condition, name, replacement), substitute(term.then_branch, name, replacement),
              substitute(term.else_branch, name, replacement))


@dataclass
class Step:
    index: int
    before: str
    redex_func: str
    redex_arg: str
    after: str
    depth: int
    parent_path: list[str]
    subst: dict[str, Any]


def reduce_once(term: Term, strategy: str, depth: int = 0, path: Optional[list[str]] = None) -> tuple[Term, Optional[tuple[Term, Term, Term, int, list[str]]]]:
    path = path or []
    if isinstance(term, App) and isinstance(term.function, Abs):
        return substitute(term.function.body, term.function.name, term.argument), (term.function, term.argument, term, depth, path)
    if strategy == "lazy":
        if isinstance(term, App):
            new_function, event = reduce_once(term.function, strategy, depth, path + ["function"])
            if event:
                return App(new_function, term.argument), event
        return term, None
    if isinstance(term, App):
        new_function, event = reduce_once(term.function, strategy, depth, path + ["function"])
        if event:
            return App(new_function, term.argument), event
        new_argument, event = reduce_once(term.argument, strategy, depth, path + ["argument"])
        if event:
            return App(term.function, new_argument), event
    if isinstance(term, Abs):
        body, event = reduce_once(term.body, strategy, depth + 1, path + ["body"])
        if event:
            return Abs(term.name, body, term.annotation), event
    return term, None


def evaluate(term: Term, strategy: str = "full", max_steps: int = 1000) -> dict[str, Any]:
    if strategy not in ("lazy", "full"):
        raise TutorError("strategy must be lazy or full")
    current = term
    steps: list[dict[str, Any]] = []
    for index in range(max_steps):
        next_term, event = reduce_once(current, strategy)
        if not event:
            break
        function, argument, _, depth, path = event
        steps.append({"index": index, "before": pretty(current), "redex_func": pretty(function),
                      "redex_arg": pretty(argument), "after": pretty(next_term), "depth": depth,
                      "parent_path": path, "subst": {"binder": function.name, "arg_shifted_from": None,
                      "arg_shifted_to": None, "body_decremented": None}})
        current = next_term
    else:
        raise TutorError(f"reduction exceeded {max_steps} steps; likely a divergent term")
    return {"kind": "reduction", "input": pretty(term), "normalized": pretty(term),
            "strategy": strategy, "steps": steps, "result": pretty(current), "time_ms": 0.0}


@dataclass
class TypeDiagnostic(Exception):
    kind: str
    expected: Optional[Type]
    actual: Optional[Type]
    location: str
    context: list[tuple[str, Type]]
    path: list[str]
    suggestion: str

    def __post_init__(self) -> None:
        Exception.__init__(self, self.suggestion)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "expected": format_type(self.expected), "actual": format_type(self.actual),
                "location": self.location, "context": [{"name": n, "type": format_type(t)} for n, t in self.context],
                "path": self.path, "suggestion": self.suggestion}


class TypeChecker:
    def __init__(self):
        self.next_var = 0
        self.subst: dict[int, Type] = {}

    def fresh(self) -> Type:
        result = ("var", self.next_var)
        self.next_var += 1
        return result

    def resolve(self, type_: Type) -> Type:
        if isinstance(type_, tuple) and len(type_) == 2 and type_[0] == "var" and type_[1] in self.subst:
            self.subst[type_[1]] = self.resolve(self.subst[type_[1]])
            return self.subst[type_[1]]
        if isinstance(type_, tuple) and len(type_) == 2 and type_[0] != "var":
            return (self.resolve(type_[0]), self.resolve(type_[1]))
        return type_

    def unify(self, left: Type, right: Type, term: Term, context: list[tuple[str, Type]], path: list[str]) -> None:
        left, right = self.resolve(left), self.resolve(right)
        if left == right:
            return
        if isinstance(left, tuple) and left[0] == "var":
            if self.occurs(left, right):
                raise self.error("infinite-type", left, right, term, context, path)
            self.subst[left[1]] = right
            return
        if isinstance(right, tuple) and right[0] == "var":
            return self.unify(right, left, term, context, path)
        raise self.error("mismatch", left, right, term, context, path)

    def occurs(self, variable: Type, type_: Type) -> bool:
        type_ = self.resolve(type_)
        return type_ == variable or (isinstance(type_, tuple) and type_[0] != "var" and
                                     (self.occurs(variable, type_[0]) or self.occurs(variable, type_[1])))

    def error(self, kind: str, expected: Type, actual: Type, term: Term, context: list[tuple[str, Type]], path: list[str]) -> TypeDiagnostic:
        if kind == "not-a-function":
            suggestion = f"This subterm has type {format_type(actual)}, but application requires a function type; wrap it in a lambda."
        elif kind == "infinite-type":
            suggestion = "The expression requires an infinite type; self-application is forbidden in STLC."
        elif kind == "unbound-var":
            suggestion = "The variable is free; add a lambda binder for it."
        else:
            suggestion = "Change the literal or annotation to match the expected type."
        return TypeDiagnostic(kind, expected, actual, pretty(term), context, path, suggestion)

    def infer(self, term: Term, context: list[tuple[str, Type]], path: Optional[list[str]] = None) -> Type:
        path = path or []
        if isinstance(term, Int):
            return "Int"
        if isinstance(term, Bool):
            return "Bool"
        if isinstance(term, Var):
            for name, type_ in context:
                if name == term.name:
                    return self.resolve(type_)
            raise self.error("unbound-var", None, None, term, context, path)  # type: ignore[arg-type]
        if isinstance(term, Abs):
            parameter = term.annotation or self.fresh()
            return (parameter, self.infer(term.body, [(term.name, parameter), *context], path + ["body"]))
        if isinstance(term, App):
            function_type = self.infer(term.function, context, path + ["function"])
            argument_type = self.infer(term.argument, context, path + ["argument"])
            result = self.fresh()
            self.unify(function_type, (argument_type, result), term.function, context, path + ["function"])
            return self.resolve(result)
        condition = self.infer(term.condition, context, path + ["condition"])
        self.unify(condition, "Bool", term.condition, context, path + ["condition"])
        then_type = self.infer(term.then_branch, context, path + ["then"])
        else_type = self.infer(term.else_branch, context, path + ["else"])
        self.unify(then_type, else_type, term, context, path)
        return self.resolve(then_type)

    def check(self, term: Term) -> Type:
        result = self.resolve(self.infer(term, []))
        if self.contains_type_variable(result):
            raise TutorError("cannot infer; please annotate the lambda parameter")
        return result

    def contains_type_variable(self, type_: Type) -> bool:
        type_ = self.resolve(type_)
        if isinstance(type_, tuple) and type_[0] == "var":
            return True
        return isinstance(type_, tuple) and (
            self.contains_type_variable(type_[0]) or self.contains_type_variable(type_[1])
        )


def explain(trace: dict[str, Any], mode: str = "reduction", question: Optional[str] = None) -> str:
    """Ask Dartmouth, or provide a deterministic explanation without credentials."""
    prompt_file = {"reduction": "prompts/explain_reduction.md", "typing": "prompts/explain_typing.md",
                   "type_error": "prompts/explain_type_error.md", "follow_up": "prompts/follow_up.md"}[mode]
    root = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(root, prompt_file), encoding="utf-8") as prompt_handle:
        system = prompt_handle.read()
    user = json.dumps(trace, indent=2)
    if question:
        user += f"\n\nStudent question: {question}"
    key = os.getenv("DARTMOUTH_CHAT_API_KEY")
    if not key:
        if mode == "reduction":
            steps = trace.get("steps", [])
            if not steps:
                return "This expression is already in normal form."
            return "\n".join(f"{step['index']}. Apply {step['redex_func']} to {step['redex_arg']} to get {step['after']}."
                             for step in steps) + f"\nThe computation ends at {trace['result']}."
        return "No Dartmouth API key is configured; the structured tutor trace is available above."
    model = os.getenv("DARTMOUTH_MODEL") or choose_model()
    if not model:
        raise TutorError("No Dartmouth model selected")
    payload = json.dumps({"model": model,
                          "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                          "stream": False}).encode()
    request = urllib.request.Request(os.getenv("DARTMOUTH_CHAT_URL", "https://chat.dartmouth.edu/api/chat/completions"),
                                     data=payload, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60, context=ssl_context()) as response:
                result = json.load(response)
            return result["choices"][0]["message"]["content"]
        except (urllib.error.HTTPError, urllib.error.URLError, KeyError, IndexError) as error:
            if attempt == 2:
                raise TutorError(f"Dartmouth request failed: {error}") from error
            time.sleep(2 ** attempt)
    raise TutorError("Dartmouth request failed")


def fetch_models() -> list[dict[str, Any]]:
    """Fetch models available to the configured Dartmouth API key."""
    key = os.getenv("DARTMOUTH_CHAT_API_KEY")
    if not key:
        raise TutorError("DARTMOUTH_CHAT_API_KEY is not set")
    endpoint = os.getenv("DARTMOUTH_CHAT_URL", "https://chat.dartmouth.edu/api/chat/completions")
    parts = urlsplit(endpoint)
    path = parts.path
    api_root = path[:path.find("/api/") + len("/api/")] if "/api/" in path else "/api/"
    models_url = urlunsplit((parts.scheme, parts.netloc, api_root + "models", "", ""))
    request = urllib.request.Request(models_url, headers={"Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(request, timeout=30, context=ssl_context()) as response:
            response_body = response.read()
            try:
                result = json.loads(response_body)
            except json.JSONDecodeError as error:
                raise TutorError(
                    f"Dartmouth model endpoint returned non-JSON data (HTTP {response.status})"
                ) from error
    except urllib.error.URLError as error:
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            raise TutorError(
                "TLS certificate verification failed. Install Python's certificates "
                "or set DARTMOUTH_CA_BUNDLE to a trusted CA bundle; certificate "
                "verification is intentionally not disabled."
            ) from error
        raise TutorError(f"Could not retrieve Dartmouth models: {error}") from error
    except urllib.error.HTTPError as error:
        raise TutorError(f"Could not retrieve Dartmouth models: {error}") from error
    models = result.get("data")
    if not isinstance(models, list):
        raise TutorError("Dartmouth returned an invalid model list")
    valid_models = [model for model in models if isinstance(model, dict) and model.get("id")]
    if not valid_models:
        raise TutorError("Dartmouth returned no selectable models")
    return valid_models


def ssl_context() -> ssl.SSLContext:
    """Create a verified TLS context, optionally using an explicit CA bundle."""
    bundle = os.getenv("DARTMOUTH_CA_BUNDLE") or os.getenv("SSL_CERT_FILE")
    if not bundle:
        return ssl.create_default_context()
    if not os.path.isfile(bundle):
        raise TutorError(f"CA bundle does not exist: {bundle}")
    try:
        return ssl.create_default_context(cafile=bundle)
    except ssl.SSLError as error:
        raise TutorError(f"CA bundle is not valid: {bundle}") from error


def choose_model() -> Optional[str]:
    """Interactively select and retain a Dartmouth model for this process."""
    if not os.getenv("DARTMOUTH_CHAT_API_KEY"):
        return None
    models = fetch_models()
    print("\nAvailable Dartmouth models:")
    for index, model in enumerate(models, 1):
        label = model["id"]
        if model.get("name") and model["name"] != label:
            label += f" ({model['name']})"
        print(f"  {index}. {label}")
    while True:
        selection = input("Select a model number (or q to cancel): ").strip()
        if selection.lower() == "q":
            return None
        try:
            selected = models[int(selection) - 1]["id"]
        except (ValueError, IndexError):
            print(f"Please enter a number from 1 to {len(models)}, or q.")
            continue
        os.environ["DARTMOUTH_MODEL"] = selected
        print(f"Selected Dartmouth model: {selected}")
        return selected


def ensure_model_selection() -> None:
    """Validate a configured model and prompt when it is missing or stale."""
    if not os.getenv("DARTMOUTH_CHAT_API_KEY"):
        return
    configured = os.getenv("DARTMOUTH_MODEL")
    models = fetch_models()
    available = {model["id"] for model in models}
    if configured in available:
        return
    if configured:
        print(f"Configured Dartmouth model is unavailable: {configured}")
    choose_model()


def repl() -> None:
    mode, typed, last = "full", False, None
    print("Lambda Tutor. Type :help for commands.")
    try:
        ensure_model_selection()
    except TutorError as error:
        print(f"Dartmouth model selection unavailable: {error}")
    while True:
        try:
            line = input("λ> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line == ":quit":
            return
        if line == ":help":
            print(":type EXPR, :trace, :explain, :follow-up QUESTION, :models, :mode lazy|full, :typed, :quit")
            continue
        if line == ":models":
            try:
                choose_model()
            except TutorError as error:
                print(f"error: {error}")
            continue
        try:
            if line.startswith(":mode "):
                mode = line.split(maxsplit=1)[1]
                if mode not in ("lazy", "full"):
                    raise TutorError("mode must be lazy or full")
                continue
            if line == ":typed":
                typed = not typed
                print(f"typed mode {'on' if typed else 'off'}")
                continue
            if line.startswith(":type "):
                checker = TypeChecker()
                try:
                    print(format_type(checker.check(parse(line[6:]))))
                except TypeDiagnostic as diagnostic:
                    print(json.dumps(diagnostic.as_dict(), indent=2))
                continue
            if line == ":trace":
                print(json.dumps(last, indent=2) if last else "No trace yet.")
                continue
            if line == ":explain":
                print(explain(last) if last else "No trace yet.")
                continue
            if line.startswith(":follow-up "):
                print(explain(last or {}, "follow_up", line[11:]))
                continue
            term = parse(line)
            if typed:
                checker = TypeChecker()
                inferred = checker.check(term)
                print(f"type: {format_type(inferred)}")
            last = evaluate(term, mode)
            print(f"=> {last['result']} ({len(last['steps'])} steps)")
        except TutorError as error:
            print(f"error: {error}")


if __name__ == "__main__":
    repl()
