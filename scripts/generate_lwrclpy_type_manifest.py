from __future__ import annotations

import argparse
import json
from pathlib import Path


def discover_idl_types(idl_root: Path) -> dict[str, dict[str, list[str]]]:
    discovered: dict[str, dict[str, set[str]]] = {}
    for idl_path in idl_root.rglob("*.idl"):
        relative = idl_path.relative_to(idl_root)
        if len(relative.parts) < 3:
            continue
        package, kind = relative.parts[0], relative.parts[-2]
        if kind not in {"msg", "srv"}:
            continue
        name = idl_path.stem
        if kind == "srv":
            name = name.removesuffix("_Request").removesuffix("_Response")
        if name:
            discovered.setdefault(package, {}).setdefault(kind, set()).add(name)
    return {
        package: {
            kind: sorted(names)
            for kind, names in sorted(kinds.items())
        }
        for package, kinds in sorted(discovered.items())
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the editor datatype manifest from lwrclpy's IDL source tree."
    )
    parser.add_argument(
        "idl_root",
        type=Path,
        help="Path to lwrclpy/third_party/ros-data-types-for-fastdds/src",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("lwrclpy_web_node_editor/static/lwrclpy_message_types.json"),
    )
    args = parser.parse_args()

    idl_root = args.idl_root.resolve()
    types = discover_idl_types(idl_root)
    if not types:
        raise SystemExit(f"No msg/srv IDL files found under {idl_root}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "source": "lwrclpy/third_party/ros-data-types-for-fastdds/src/**/*.idl",
                "types": types,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    type_count = sum(len(names) for kinds in types.values() for names in kinds.values())
    print(f"Wrote {type_count} datatypes from {idl_root} to {args.output}")


if __name__ == "__main__":
    main()
