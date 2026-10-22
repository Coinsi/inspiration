"""Official assistant-ui present vocabulary, persisted as data, never executable code."""

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import RootModel, model_validator

SCHEMA = json.loads(Path(__file__).with_name("generative-ui-schema.json").read_text("utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA)
COMPONENTS = frozenset(SCHEMA["properties"]["_type"]["enum"])


def validate_tree(tree):
    count = 0

    def visit(value, depth=0):
        nonlocal count
        count += 1
        if count > 2000 or depth > 20:
            raise ValueError("界面结构过大或嵌套过深")
        if isinstance(value, list):
            for child in value:
                visit(child, depth + 1)
        elif isinstance(value, dict):
            for key, child in value.items():
                if key in ("__proto__", "constructor", "prototype"):
                    raise ValueError("界面包含非法属性")
                if key == "_type" and (not isinstance(child, str) or child not in COMPONENTS):
                    raise ValueError("未知界面组件")
                if key in ("_action", "$action"):
                    if not isinstance(child, dict) or child.get("type") != "reply":
                        raise ValueError("界面只支持 reply 动作，业务操作需使用现有工具")
                if key in ("src", "href", "url") and isinstance(child, str):
                    if not (
                        child.startswith(("https://", "http://"))
                        or (child.startswith("/") and not child.startswith("//"))
                    ):
                        raise ValueError("界面链接协议不受支持")
                if key == "background" and isinstance(child, str):
                    if "url(" in child.lower() or "@" in child or "\\" in child:
                        raise ValueError("界面背景不能加载外部资源")
                visit(child, depth + 1)

    visit(tree)
    error = next(VALIDATOR.iter_errors(tree), None)
    if error:
        raise ValueError(f"界面参数不符合官方组件协议: {error.message[:200]}")
    return tree


class PresentArgs(RootModel[dict[str, Any]]):
    @model_validator(mode="after")
    def valid_ui(self):
        validate_tree(self.root)
        return self

    @classmethod
    def model_json_schema(cls, **kwargs):
        return SCHEMA


def presented_blocks(steps):
    return [
        {"id": step["id"], "version": 1, "tree": step["arguments"]}
        for step in steps
        if step["tool"] == "present" and step["status"] == "succeeded"
    ]
