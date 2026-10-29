"""Igor simple-dialog semantics over the trusted, bounded GUI rendezvous."""
from __future__ import annotations

import re
from typing import Any

import numpy as np

from materials_data_studio.tools.command_language import split


def interact(scope: Any, operation: str, flags: dict[str, str | None], rest: str) -> None:
    parts = split(rest)
    if operation == "prompt":
        if len(parts) not in {2, 4} or not re.fullmatch(r"[A-Za-z]\w*", parts[0]):
            raise ValueError('Use Prompt variable,"label" [,popup,"choices;..."]')
        value = scope.evaluate(parts[0])
        label = scope.evaluate(parts[1])
        if not isinstance(label, str) or not isinstance(value, (str, int, float, np.number)) or np.ndim(value):
            raise ValueError("Prompt requires a scalar/string variable and a text label")
        item: dict[str, Any] = {"name": parts[0], "label": label}
        if len(parts) == 4:
            options = scope.evaluate(parts[3])
            if parts[2].lower() != "popup" or not isinstance(options, str):
                raise ValueError("Prompt popup requires a semicolon-separated text list")
            choices = options.rstrip(";").split(";")
            if not choices or len(choices) > 256:
                raise ValueError("Prompt popup requires 1–256 choices")
            item.update(choices=choices, string_choice=isinstance(value, str))
        scope.prompts[parts[0].lower()] = item
        return
    callback = scope.owner._interaction
    if callback is None:
        raise RuntimeError("This Igor operation needs a live Graph Editor interaction session")
    if operation == "doalert":
        if len(parts) != 2:
            raise ValueError("DoAlert requires type,text")
        kind, text = scope.evaluate(parts[0]), scope.evaluate(parts[1])
        if kind not in {0, 1, 2} or not isinstance(text, str):
            raise ValueError("DoAlert type must be 0, 1 or 2 and text must be a string")
        title = scope.evaluate(flags["T"]) if flags.get("T") is not None else "Igor macro message"
        if not isinstance(title, str):
            raise ValueError("DoAlert title must be text")
        buttons = ["OK"] if kind == 0 else ["Yes", "No"] if kind == 1 else ["Yes", "No", "Cancel"]
        reply = callback("message", {"title": title, "text": text, "buttons": buttons})
        scope.set_global("V_flag", 3 if reply["status"] == "cancelled" else int(reply["button"]) + 1)
        return
    if operation != "doprompt" or not 2 <= len(parts) <= 11:
        raise ValueError("DoPrompt requires a title and 1–10 prompted variables")
    title = scope.evaluate(parts[0])
    if not isinstance(title, str):
        raise ValueError("DoPrompt title must be text")
    fields, definitions = [], []
    for name in parts[1:]:
        item = scope.prompts.get(name.lower())
        if item is None:
            raise ValueError("Declare Prompt before DoPrompt: " + name)
        value = scope.evaluate(name)
        field: dict[str, Any] = {"name": item["name"], "label": item["label"]}
        if "choices" in item:
            choices = item["choices"]
            selected = choices.index(value) if isinstance(value, str) and value in choices else 0 if isinstance(value, str) else int(value) - 1
            field.update(type="choice", choices=choices, value=max(0, min(len(choices) - 1, selected)))
        else:
            field.update(type="text" if isinstance(value, str) else "number",
                         value=value if isinstance(value, str) else float(value))
        fields.append(field)
        definitions.append(item)
    help_text = scope.evaluate(flags["HELP"]) if flags.get("HELP") is not None else ""
    if not isinstance(help_text, str):
        raise ValueError("DoPrompt help must be text")
    reply = callback("prompt", {"title": title, "fields": fields, "help": help_text})
    if reply["status"] == "cancelled":
        scope.set_global("V_flag", 1)
        return
    for item in definitions:
        value = reply["values"][item["name"]]
        if "choices" in item:
            value = item["choices"][value] if item["string_choice"] else value + 1
        scope.assign_value(item["name"], value)
    scope.set_global("V_flag", 0)
