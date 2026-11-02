"""Build argv from worker-owned command definitions; never invoke a shell."""

import re


def build_command(definition, task):
    command = definition.get("command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(v, str) and v for v in command)
    ):
        raise ValueError(
            "Command must be a nonempty list of executable and fixed arguments"
        )
    defaults = definition.get("defaults", {})
    parameters = task.get("parameters", {})
    if not isinstance(defaults, dict) or not isinstance(parameters, dict):
        raise ValueError("Command defaults and task parameters must be mappings")
    parameters = {**defaults, **parameters}
    required_parameters = definition.get("required_parameters", [])
    if not isinstance(required_parameters, list) or not all(
        isinstance(name, str) for name in required_parameters
    ):
        raise ValueError("required_parameters must be a list of parameter names")
    for name in required_parameters:
        if parameters.get(name) is None or parameters[name] == "":
            raise ValueError(f"Missing command parameter: {name}")
    argv = list(command)
    for mapping, values, required in (
        (definition.get("context_args", {}), task, True),
        (definition.get("parameter_args", {}), parameters, False),
    ):
        if not isinstance(mapping, dict):
            raise ValueError("context_args and parameter_args must be mappings")
        for flag, name in mapping.items():
            if not isinstance(flag, str) or not re.fullmatch(
                r"[A-Za-z][A-Za-z0-9-]*", flag
            ):
                raise ValueError("Invalid command option name")
            if not isinstance(name, str):
                raise ValueError(
                    "Command option mappings must reference parameter names"
                )
            value = values.get(name)
            if value is None:
                if required:
                    raise ValueError(f"Missing task field: {name}")
                continue
            if isinstance(value, bool):
                if value:
                    argv.append("--" + flag)
            elif isinstance(value, (str, int, float)):
                # One argv entry prevents values beginning with '-' becoming options.
                argv.append(f"--{flag}={value}")
            else:
                raise ValueError(f"Command parameter {name} must be a scalar")
    return argv
