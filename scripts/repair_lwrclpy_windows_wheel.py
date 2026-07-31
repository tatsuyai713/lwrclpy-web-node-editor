from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


REPLACEMENTS = {
    "lwrclpy/_vendor/lib/libcrypto-3-x64.dll": "libcrypto-3.dll",
    "lwrclpy/_vendor/lib/libssl-3-x64.dll": "libssl-3.dll",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Repair a Windows lwrclpy wheel so the bundled OpenSSL DLL names "
            "contain Python's signed libcrypto/libssl DLLs."
        )
    )
    parser.add_argument("wheel", type=Path, help="Input lwrclpy Windows .whl")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Output wheel path. Defaults to overwriting via a .repaired.whl sibling.")
    parser.add_argument("--python-dll-dir", type=Path, default=None, help="Directory containing libcrypto-3.dll and libssl-3.dll")
    parser.add_argument("--no-signature-check", action="store_true", help="Do not require Authenticode status Valid for replacement DLLs")
    args = parser.parse_args(argv)

    wheel = args.wheel.resolve()
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise SystemExit(f"wheel not found or not a .whl file: {wheel}")
    if "win" not in wheel.name.lower():
        raise SystemExit(f"refusing to repair a non-Windows-looking wheel: {wheel.name}")

    dll_dir = (args.python_dll_dir or find_python_dll_dir()).resolve()
    replacements = {target: dll_dir / source for target, source in REPLACEMENTS.items()}
    for target, source in replacements.items():
        if not source.is_file():
            raise SystemExit(f"replacement DLL for {target} not found: {source}")
        if not args.no_signature_check and not authenticode_valid(source):
            raise SystemExit(f"replacement DLL is not Authenticode-valid: {source}")

    output = args.output.resolve() if args.output else wheel.with_name(wheel.name.removesuffix(".whl") + ".repaired.whl")
    if output == wheel:
        with tempfile.NamedTemporaryFile(prefix=wheel.stem + ".", suffix=".whl", delete=False, dir=str(wheel.parent)) as tmp:
            temp_output = Path(tmp.name)
        write_repaired_wheel(wheel, temp_output, replacements)
        shutil.move(str(temp_output), str(wheel))
    else:
        write_repaired_wheel(wheel, output, replacements)
    print(f"Repaired wheel: {output}")
    return 0


def find_python_dll_dir() -> Path:
    candidates = [
        Path(sys.base_prefix) / "DLLs",
        Path(sys.base_prefix),
        Path(sys.exec_prefix) / "DLLs",
        Path(sys.exec_prefix),
        Path(sys.executable).resolve().parent / "DLLs",
        Path(sys.executable).resolve().parent,
    ]
    for candidate in candidates:
        if (candidate / "libcrypto-3.dll").is_file() and (candidate / "libssl-3.dll").is_file():
            return candidate
    raise SystemExit("could not find Python DLLs directory containing libcrypto-3.dll and libssl-3.dll")


def authenticode_valid(path: Path) -> bool:
    if os.name != "nt":
        return True
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-AuthenticodeSignature -LiteralPath {powershell_quote(str(path))}).Status",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return False
    return completed.stdout.strip() == "Valid"


def powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def write_repaired_wheel(input_wheel: Path, output_wheel: Path, replacements: dict[str, Path]) -> None:
    with zipfile.ZipFile(input_wheel, "r") as source:
        names = set(source.namelist())
        missing = sorted(target for target in replacements if target not in names)
        if missing:
            raise SystemExit("wheel does not contain expected lwrclpy DLLs: " + ", ".join(missing))
        record_name = find_record_name(source)
        entries: dict[str, bytes] = {}
        infos: dict[str, zipfile.ZipInfo] = {}
        for info in source.infolist():
            if info.filename == record_name:
                continue
            infos[info.filename] = clone_info(info)
            if info.filename in replacements:
                entries[info.filename] = replacements[info.filename].read_bytes()
            else:
                entries[info.filename] = source.read(info.filename)

    entries[record_name] = render_record(entries, record_name)
    infos[record_name] = zipfile.ZipInfo(record_name)
    infos[record_name].compress_type = zipfile.ZIP_DEFLATED

    output_wheel.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_wheel, "w", compression=zipfile.ZIP_DEFLATED) as dest:
        for name, data in entries.items():
            dest.writestr(infos.get(name, zipfile.ZipInfo(name)), data)


def clone_info(info: zipfile.ZipInfo) -> zipfile.ZipInfo:
    cloned = zipfile.ZipInfo(info.filename, info.date_time)
    cloned.comment = info.comment
    cloned.extra = info.extra
    cloned.internal_attr = info.internal_attr
    cloned.external_attr = info.external_attr
    cloned.create_system = info.create_system
    cloned.compress_type = zipfile.ZIP_DEFLATED
    return cloned


def find_record_name(wheel: zipfile.ZipFile) -> str:
    records = [name for name in wheel.namelist() if name.endswith(".dist-info/RECORD")]
    if len(records) != 1:
        raise SystemExit(f"expected exactly one RECORD file, found {len(records)}")
    return records[0]


def render_record(entries: dict[str, bytes], record_name: str) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    for name in sorted(entries):
        if name == record_name:
            writer.writerow([name, "", ""])
            continue
        data = entries[name]
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")
        writer.writerow([name, f"sha256={digest}", str(len(data))])
    return output.getvalue().encode("utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
