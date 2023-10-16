from types import ModuleType
from typing import Any, Dict


def exec_module_for_new_dict(module: ModuleType) -> Dict[str, Any]:
    new_dict: Dict[str, Any] = {}
    with open(module.__spec__.origin, "r") as f:
        source_code = f.read()
    exec(source_code, new_dict)

    for field, obj in list(new_dict.items()):
        if obj is None:
            new_dict[field] = module.__dict__.get(field)
    for field, obj in module.__dict__.items():
        if field not in new_dict:
            new_dict[field] = obj

    return new_dict
