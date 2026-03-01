#!/usr/bin/env python
"""Batch-run tecs processing on zipped RINEX directories.

Usage example:
    python process_rinex.py \
        --root N:\\RINEX \
        --cfg n:\\tec-suite\\tecs.cfg \
        --tecs n:\\tec-suite\\tecs.py

The script will scan the root directory for subfolders whose names
consist only of digits ("day folders"). Within each day folder it looks
for ``*.zip`` archives. Each archive is uncompressed into a sibling
folder named after the archive (without ``.zip``), the generated
path is written into both ``obsDir`` and ``navDir`` variables of the
configuration file and then the tecs script is invoked via the same
Python interpreter that runs this program.  After tecs finishes the
next archive is processed.  The loop continues until all archives in
all eligible day folders have been processed.

The pattern for identifying a day directory is deliberately permissive
(e.g. "1", "01", "001", "1234" all match) to accommodate the variety
mentioned in the requirement.

This script updates the config file in-place.  You may want to keep a
backup or use version control if you need the original values preserved.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed


DAY_RE = re.compile(r"^\d+$")


def is_day_dir(name: str) -> bool:
    """Return True if *name* looks like a day-number directory."""
    return bool(DAY_RE.fullmatch(name))


def update_cfg(cfg_path: Path, new_dir: Path, out_dir: Path | None = None) -> None:
    """Set `obsDir` and `navDir` in the configuration file to *new_dir`.

    If *out_dir* is provided, set `outDir` to that absolute path so
    `tecs` will write outputs to the intended local folder even when
    using a temporary config file in another directory.
    """

    text = cfg_path.read_text().splitlines(keepends=True)
    out_lines: list[str] = []
    for line in text:
        stripped = line.strip()
        if stripped.startswith("obsDir"):
            out_lines.append(f"obsDir = {new_dir}\n")
        elif stripped.startswith("navDir"):
            out_lines.append(f"navDir = {new_dir}\n")
        elif out_dir is not None and stripped.startswith("outDir"):
            out_lines.append(f"outDir = {out_dir}\n")
        else:
            out_lines.append(line)
    cfg_path.write_text("".join(out_lines))


def process_archive(
    zip_path: Path,
    cfg_template: Path,
    tecs_script: Path,
    verbose: bool = False,
    cleanup: bool = False,
    out_dir_override: Path | None = None
) -> None:
    """Unzip *zip_path*, update config, and run tecs.

    *cfg_template* is the original configuration file; a temporary copy is
    made for each archive so multiple workers can operate in parallel
    without clobbering one another.  The temporary config is removed when
    processing finishes.

    When *cleanup* is True the extracted folder will be removed after
    ``tecs`` has finished processing it.
    """

    # create temporary config based on template
    tmp_cfg = None
    try:
        tmp_fd, tmp_pathstr = tempfile.mkstemp(suffix=".cfg")
        os.close(tmp_fd)
        tmp_cfg = Path(tmp_pathstr)
        shutil.copy2(cfg_template, tmp_cfg)

        # determine the absolute outDir from the template config so the
        # temporary config writes outputs to the project's out folder
        orig_cfg_text = cfg_template.read_text().splitlines()
        out_dir_path: Path | None = None
        if out_dir_override:
            out_dir_path = out_dir_override
            if not out_dir_path.is_absolute():
                out_dir_path = (Path.cwd() / out_dir_path).resolve()
        else:
            for l in orig_cfg_text:
                s = l.strip()
                if s.startswith("outDir"):
                    parts = s.split("=", 1)
                    if len(parts) > 1:
                        val = parts[1].strip().strip("'\"")
                        if val:
                            cand = Path(val)
                            if not cand.is_absolute():
                                cand = (cfg_template.parent / cand).resolve()
                            out_dir_path = cand
                    break
            if out_dir_path is None:
                out_dir_path = (cfg_template.parent / "out").resolve()

        dest_dir = zip_path.with_suffix("")
        if not dest_dir.exists():
            print(f"Unzipping {zip_path} -> {dest_dir}")
            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(dest_dir)
        else:
            print(f"Destination {dest_dir} already exists, skipping unzip")

        # change both obsDir and navDir to point to the unzipped directory
        print(f"Writing dirs to config: {dest_dir} (temp {tmp_cfg})")
        update_cfg(tmp_cfg, dest_dir, out_dir=out_dir_path)
        if verbose:
            print(f"Config file {tmp_cfg} updated with obsDir/navDir = {dest_dir} and outDir = {out_dir_path}")

        # run the tecs script
        start_ts = __import__('datetime').datetime.now()
        print(f"Running tecs for {dest_dir} (started {start_ts})")
        if verbose:
            print(f"Executing: {sys.executable} {tecs_script} -c {tmp_cfg}")
        status = "success"
        try:
            subprocess.run([sys.executable, str(tecs_script), "-c", str(tmp_cfg)], check=True)
        except subprocess.CalledProcessError:
            status = "failure"
            raise
        finally:
            end_ts = __import__('datetime').datetime.now()
            # append summary to our own log file in out_dir
            if out_dir_path is not None:
                log_file = out_dir_path / 'process_rinex.log'
                os.makedirs(out_dir_path, exist_ok=True)
                with open(log_file, 'a') as lf:
                    lf.write(f"{start_ts.isoformat()} - {status} {dest_dir.name} "
                             f"({dest_dir}) in {end_ts - start_ts}\n")

        if cleanup:
            # remove the directory tree
            if verbose:
                print(f"Cleaning up extracted directory {dest_dir}")
            for root, dirs, files in os.walk(dest_dir, topdown=False):
                for name in files:
                    os.remove(os.path.join(root, name))
                for name in dirs:
                    os.rmdir(os.path.join(root, name))
            os.rmdir(dest_dir)
            if verbose:
                print(f"Removed {dest_dir}")
    finally:
        if tmp_cfg and tmp_cfg.exists():
            try:
                tmp_cfg.unlink()
                if verbose:
                    print(f"Deleted temporary config {tmp_cfg}")
            except Exception:
                pass
    # end of process_archive


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Process zipped RINEX directories with tecs."
    )
    parser.add_argument(
        "--root", "-r", type=Path, required=True,
        help="Root directory containing day subfolders."
    )
    parser.add_argument(
        "--cfg", "-c", type=Path, required=True,
        help="Path to the tecs.cfg configuration file to update."
    )
    parser.add_argument(
        "--tecs", "-t", type=Path, default=Path("tecs.py"),
        help="Path to the tecs.py script to execute (default: tecs.py in current directory)."
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable verbose debugging output."
    )
    parser.add_argument(
        "--cleanup", "-k", action="store_true",
        help="Delete extracted folders after tecs has run."
    )
    parser.add_argument(
        "--out", "-o", type=Path,
        help="Optional output directory override (absolute or relative to container)."
    )
    parser.add_argument(
        "--jobs", "-j", type=int, default=1,
        help="Number of archives to process in parallel (default 1)."
    )
    args = parser.parse_args()

    root = args.root
    if not root.is_dir():
        parser.error(f"Root {root} is not a directory")

    if args.verbose:
        print(f"Scanning root directory: {root}")
    if args.jobs > 1 and args.verbose:
        print(f"Using up to {args.jobs} parallel jobs")

    # gather all archive paths first
    work_items = []  # tuples (archive,)
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            if args.verbose:
                print(f" skipping non-directory entry: {entry}")
            continue
        if not is_day_dir(entry.name):
            if args.verbose:
                print(f" ignoring non-day directory: {entry.name}")
            continue

        if args.verbose:
            print(f"\n=== processing day folder: {entry} ===")
            print(f" listing contents: {list(entry.iterdir())}")

        archives = sorted(entry.glob("*.zip"))
        if not archives:
            print(f" no zip archives found in {entry}")
            continue

        for archive in archives:
            work_items.append(archive)

    if not work_items:
        print("No archives to process.")
        return 0

    # process archives either sequentially or in parallel
    if args.jobs <= 1:
        for archive in work_items:
            print(f"--- archive: {archive.name} ---")
            try:
                process_archive(
                    archive,
                    args.cfg,
                    args.tecs,
                    verbose=args.verbose,
                    cleanup=args.cleanup,
                    out_dir_override=args.out
                )
            except subprocess.CalledProcessError as e:
                print(f" tecs failed for {archive}: {e}", file=sys.stderr)
            except Exception as exc:  # pylint: disable=broad-except
                print(f" error processing {archive}: {exc}", file=sys.stderr)
    else:
        # run up to args.jobs workers
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {
                pool.submit(
                    process_archive,
                    archive,
                    args.cfg,
                    args.tecs,
                    args.verbose,
                    args.cleanup,
                    args.out
                ): archive for archive in work_items
            }
            for fut in as_completed(futures):
                archive = futures[fut]
                try:
                    fut.result()
                except subprocess.CalledProcessError as e:
                    print(f" tecs failed for {archive}: {e}", file=sys.stderr)
                except Exception as exc:  # pylint: disable=broad-except
                    print(f" error processing {archive}: {exc}", file=sys.stderr)

    if args.verbose:
        print("Processing complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
