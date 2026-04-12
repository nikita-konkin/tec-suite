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
import threading


DAY_RE = re.compile(r"^\d+$")
SHORT_NAME_LOCKS: dict[str, threading.Lock] = {}
SHORT_NAME_LOCKS_GUARD = threading.Lock()


def get_short_name_lock(short_name: str) -> threading.Lock:
    """Return a shared lock object for a short station name."""
    with SHORT_NAME_LOCKS_GUARD:
        lock = SHORT_NAME_LOCKS.get(short_name)
        if lock is None:
            lock = threading.Lock()
            SHORT_NAME_LOCKS[short_name] = lock
    return lock


def is_day_dir(name: str) -> bool:
    """Return True if *name* looks like a day-number directory."""
    return bool(DAY_RE.fullmatch(name))


def find_archives_in_folder(folder: Path, verbose: bool = False) -> list[Path]:
    """Find zip archives in *folder* and one level of numeric subfolders.

    This supports both layouts:
    1) root/day/*.zip
    2) root/month/day/*.zip
    """
    direct = sorted(folder.glob("*.zip"))
    if direct:
        return direct

    nested: list[Path] = []
    for child in sorted(folder.iterdir()):
        if not child.is_dir() or not is_day_dir(child.name):
            continue
        nested.extend(sorted(child.glob("*.zip")))

    if verbose and nested:
        print(
            f" found {len(nested)} zip archive(s) in nested day folders under {folder}"
        )
    return nested


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
    out_dir_override: Path | None = None,
    keep_ext_letters: set[str] | None = None,
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
    short_lock: threading.Lock | None = None
    short_lock_acquired = False
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

        output_base_dir = out_dir_path

        def append_process_log(message: str) -> None:
            if output_base_dir is None:
                return
            try:
                log_file = output_base_dir / 'process_rinex.log'
                os.makedirs(output_base_dir, exist_ok=True)
                now_ts = __import__('datetime').datetime.now().isoformat()
                with open(log_file, 'a') as lf:
                    lf.write(f"{now_ts} - {message}\n")
            except Exception:
                pass

        dest_dir = zip_path.with_suffix("")
        if not dest_dir.exists():
            print(f"Unzipping {zip_path}")
            # print(f"Unzipping {zip_path} -> {dest_dir}")
            with zipfile.ZipFile(zip_path, "r") as z:
                z.extractall(dest_dir)
        else:
            print(f"Destination {dest_dir} already exists, skipping unzip")

        if keep_ext_letters:
            removed_count = 0
            kept_count = 0
            for root, _, files in os.walk(dest_dir):
                for f in files:
                    # handle names like *.26g, *.26o, *.26n by checking
                    # the last letter of the final suffix
                    suffix = Path(f).suffix.lower()
                    ext_letter = suffix[-1] if suffix else ''
                    file_path = Path(root) / f
                    if ext_letter in keep_ext_letters:
                        kept_count += 1
                        continue
                    try:
                        file_path.unlink()
                        removed_count += 1
                    except Exception:
                        pass
            append_process_log(
                f"filtered extracted files for '{zip_path.name}': kept={kept_count}, removed={removed_count}, allowed={sorted(keep_ext_letters)}"
            )

        # change both obsDir and navDir to point to the unzipped directory
        # while applying a temporary short naming scheme.
        orig_dest_dir = dest_dir
        orig_name = orig_dest_dir.name
        short_name = orig_name[:7]
        # converter-compatible base name for files (classic RINEX expects
        # 8 chars before .YYo/.YYn etc). Example: armv001g33 -> armv0010
        short_file_name = f"{short_name}0"
        # short_file_name = short_name
        renamed = False
        short_lock = get_short_name_lock(short_name)
        short_lock.acquire()
        short_lock_acquired = True
        try:
            if orig_name != short_name:
                candidate = dest_dir.parent / short_name
                # if a stale directory from previous interrupted run exists,
                # keep original naming for this archive to avoid clobbering
                if candidate.exists():
                    print(f"Short directory already exists ({candidate}), using original name for this archive")
                    append_process_log(
                        f"rename skipped directory '{dest_dir}' -> '{candidate}' (target already exists)"
                    )
                else:
                    # 1) rename directory to first 7 chars
                    os.rename(dest_dir, candidate)
                    append_process_log(f"renamed directory '{dest_dir}' -> '{candidate}'")
                    dest_dir = candidate

                    # 2) rename extracted files from original full station
                    #    prefix to converter-compatible short file prefix
                    for root, _, files in os.walk(dest_dir):
                        for f in files:
                            if f.startswith(orig_name):
                                src = os.path.join(root, f)
                                dst = os.path.join(root, f.replace(orig_name, short_file_name, 1))
                                try:
                                    os.rename(src, dst)
                                    append_process_log(f"renamed input file '{src}' -> '{dst}'")
                                except Exception:
                                    append_process_log(f"failed to rename input file '{src}' -> '{dst}'")
                                    pass
                    renamed = True
        except Exception as exc:
            print(f"Rename workflow failed for {orig_name}: {exc}")
            append_process_log(f"rename workflow failed for '{orig_name}': {exc}")
            dest_dir = orig_dest_dir

        out_dir_for_tecs: Path | None = None
        if output_base_dir is not None:
            out_station_name = short_name if renamed else orig_name
            out_dir_for_tecs = output_base_dir / out_station_name

        print(f"Writing dirs to config: {dest_dir} (temp {tmp_cfg})")
        update_cfg(tmp_cfg, dest_dir, out_dir=out_dir_for_tecs)
        if verbose:
            print(f"Config file {tmp_cfg} updated with obsDir/navDir = {dest_dir} and outDir = {out_dir_for_tecs}")

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
            append_process_log(
                f"{status} {orig_name} ({orig_dest_dir}) in {end_ts - start_ts}"
            )

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
        # If we renamed the extracted input folder earlier, restore it now
        elif renamed:
            try:
                # dest_dir currently points to the short-name path
                short_path = dest_dir
                orig_path = orig_dest_dir
                # rename any files inside back to original names where possible
                for root, _, files in os.walk(short_path):
                    for f in files:
                        if f.startswith(short_file_name):
                            src = os.path.join(root, f)
                            dst = os.path.join(root, f.replace(short_file_name, orig_name, 1))
                            try:
                                os.rename(src, dst)
                                append_process_log(f"restored input file '{src}' -> '{dst}'")
                            except Exception:
                                append_process_log(f"failed to restore input file '{src}' -> '{dst}'")
                                pass
                os.rename(short_path, orig_path)
                append_process_log(f"restored directory '{short_path}' -> '{orig_path}'")
            except Exception:
                append_process_log(f"failed to restore directory '{short_path}' -> '{orig_path}'")
                pass
            finally:
                pass

        # Relocate outputs from:
        #   out/<station>/<year>/<yday>/<marker>
        # to:
        #   out/<year>/<yday>/<final_name>
        # where final_name is original station name for shortened runs,
        # otherwise marker (default tec-suite behavior).
        try:
            if output_base_dir is not None and out_dir_for_tecs is not None:
                station_out_dir = out_dir_for_tecs
                if station_out_dir.exists():
                    for year_dir in station_out_dir.iterdir():
                        if not year_dir.is_dir():
                            continue
                        for yday_dir in year_dir.iterdir():
                            if not yday_dir.is_dir():
                                continue
                            for marker_dir in yday_dir.iterdir():
                                if not marker_dir.is_dir():
                                    continue

                                final_leaf = orig_name if renamed else marker_dir.name
                                target_dir = output_base_dir / year_dir.name / yday_dir.name / final_leaf
                                target_dir.parent.mkdir(parents=True, exist_ok=True)

                                if target_dir.exists():
                                    # merge marker directory into target
                                    for item in marker_dir.iterdir():
                                        dst_item = target_dir / item.name
                                        if dst_item.exists():
                                            if item.is_dir() and dst_item.is_dir():
                                                shutil.copytree(item, dst_item, dirs_exist_ok=True)
                                                shutil.rmtree(item)
                                                append_process_log(
                                                    f"merged output subdirectory '{item}' -> '{dst_item}'"
                                                )
                                            else:
                                                stem = item.stem
                                                suffix = item.suffix
                                                idx = 1
                                                while True:
                                                    alt = target_dir / f"{stem}__dup{idx}{suffix}"
                                                    if not alt.exists():
                                                        break
                                                    idx += 1
                                                shutil.move(str(item), str(alt))
                                                append_process_log(
                                                    f"moved output item '{item}' -> '{alt}' (name collision)"
                                                )
                                        else:
                                            shutil.move(str(item), str(dst_item))
                                            append_process_log(
                                                f"moved output item '{item}' -> '{dst_item}'"
                                            )
                                    try:
                                        marker_dir.rmdir()
                                    except Exception:
                                        pass
                                else:
                                    os.rename(marker_dir, target_dir)
                                    append_process_log(
                                        f"relocated output directory '{marker_dir}' -> '{target_dir}'"
                                    )

                            try:
                                yday_dir.rmdir()
                            except Exception:
                                pass
                        try:
                            year_dir.rmdir()
                        except Exception:
                            pass
                    try:
                        station_out_dir.rmdir()
                    except Exception:
                        pass

                # tecs writes tecs.log in station_out_dir; move it to base log
                # namespace and then remove the now-empty station folder.
                if station_out_dir.exists():
                    station_log = station_out_dir / 'tecs.log'
                    if station_log.exists():
                        try:
                            merged_log = output_base_dir / 'tecs_per_station.log'
                            with open(station_log, 'r') as src, open(merged_log, 'a') as dst:
                                dst.write(f"\n===== {orig_name} =====\n")
                                dst.write(src.read())
                            os.remove(station_log)
                            append_process_log(
                                f"merged station tecs log '{station_log}' -> '{merged_log}'"
                            )
                        except Exception as exc:
                            append_process_log(
                                f"failed to merge station tecs log '{station_log}': {exc}"
                            )

                    try:
                        station_out_dir.rmdir()
                        append_process_log(
                            f"removed empty station output directory '{station_out_dir}'"
                        )
                    except Exception:
                        pass
                else:
                    append_process_log(
                        f"no station output directory found for '{orig_name}' (expected '{station_out_dir}')"
                    )
        except Exception as exc:
            append_process_log(
                f"failed to relocate output tree for '{orig_name}': {exc}"
            )
    finally:
        if short_lock is not None and short_lock_acquired:
            try:
                short_lock.release()
            except Exception:
                pass
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
        "--root", "-r", type=str, required=True,
        help=("Root directory containing day subfolders. If the environment "
              "variable RINEX_DATA_PATH_HOST is set you may pass a relative path "
              "or a server-relative path (for example '/2026_original/001') "
              "and it will be resolved against RINEX_DATA_PATH_HOST.")
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
    parser.add_argument(
        "--keep-exts", type=str, default="",
        help="Comma-separated extension letters to keep after unzip (example: g,o,n). Other extracted files are deleted."
    )
    args = parser.parse_args()

    keep_ext_letters: set[str] | None = None
    if args.keep_exts:
        keep_ext_letters = {
            item.strip().lstrip('.').lower()
            for item in args.keep_exts.split(',')
            if item.strip()
        }
        if not keep_ext_letters:
            parser.error("--keep-exts was provided but no valid extension letters were parsed")

    # Resolve --root possibly against RINEX_DATA_PATH_HOST env variable
    root_arg = args.root
    # Prefer resolving against the container-side mount point `RINEX_DATA_PATH`.
    # Fall back to `RINEX_DATA_PATH_HOST` only if the container path is not set.
    root_arg = args.root
    env_container_base = os.environ.get("RINEX_DATA_PATH")
    env_host_base = os.environ.get("RINEX_DATA_PATH_HOST")
    if (env_container_base or env_host_base) and (
        not Path(root_arg).is_absolute() or root_arg.startswith(('/', '\\'))
    ):
        rel = str(root_arg).lstrip('/\\')
        if env_container_base:
            # resolve against the container mount point so path checks succeed
            root = Path(env_container_base) / rel
            if args.verbose:
                print(f"Resolved --root against container base {env_container_base}: {root}")
        else:
            # container-side base not available; fall back to host base
            root = Path(env_host_base) / rel
            if args.verbose:
                print(f"Resolved --root against host base {env_host_base}: {root}")
    else:
        root = Path(root_arg)

    if not root.is_dir():
        parser.error(f"Root {root} is not a directory")

    if args.verbose:
        print(f"Scanning root directory: {root}")

    # Resolve --out against TECSUITE_OUT_DAT_DATA_PATH / TECSUITE_OUT_DAT_DATA_PATH_HOST if provided
    out_path_resolved: Path | None = None
    if args.out:
        # args.out is a Path; if absolute, use as-is. If relative or
        # server-relative (leading slash), resolve against TECSUITE_OUT_DAT_DATA_PATH
        # (container) or TECSUITE_OUT_DAT_DATA_PATH_HOST (host) env variables if set.
        if args.out.is_absolute():
            out_path_resolved = args.out
        else:
            # prefer host-side TECSUITE_OUT_DAT_DATA_PATH_HOST when available so that
            # resolved --out paths refer to the host filesystem location
            env_out_base = os.environ.get("TECSUITE_OUT_DAT_DATA_PATH_HOST") or os.environ.get("TECSUITE_OUT_DAT_DATA_PATH")
            if env_out_base:
                rel = str(args.out).lstrip('/\\')
                out_path_resolved = Path(env_out_base) / rel
            else:
                out_path_resolved = (Path.cwd() / args.out).resolve()
        if args.verbose:
            print(f"Resolved output directory: {out_path_resolved}")
    if args.jobs > 1 and args.verbose:
        print(f"Using up to {args.jobs} parallel jobs")

    # gather all archive paths first
    work_items: list[tuple[int, Path]] = []  # (index, archive)

    # 1) Add any zip files directly under root (treat root as a single day)
    direct_archives = sorted(root.glob("*.zip"))
    if direct_archives:
        if args.verbose:
            print(f"Found {len(direct_archives)} zip(s) directly in root: {root}")
        for archive in direct_archives:
            work_items.append((0, archive))

    # 2) Then scan day subdirectories as before
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

        archives = find_archives_in_folder(entry, verbose=args.verbose)
        if not archives:
            if args.verbose:
                print(f" no zip archives found in {entry}")
            continue

        for archive in archives:
            work_items.append((0, archive))

    if not work_items:
        print("No archives to process.")
        return 0

    total = len(work_items)
    # assign indices
    work_items = [(i+1, archive) for i, (_, archive) in enumerate(work_items)]

    # process archives either sequentially or in parallel
    if args.jobs <= 1:
        for idx, archive in work_items:
            print(f"Completed {idx}/{total}: {archive.name}")
            try:
                process_archive(
                    archive,
                    args.cfg,
                    args.tecs,
                    verbose=args.verbose,
                    cleanup=args.cleanup,
                    out_dir_override=out_path_resolved or args.out,
                    keep_ext_letters=keep_ext_letters,
                )
            except subprocess.CalledProcessError as e:
                print(f" tecs failed for {archive}: {e}", file=sys.stderr)
            except Exception as exc:  # pylint: disable=broad-except
                print(f" error processing {archive}: {exc}", file=sys.stderr)
    else:
        # run up to args.jobs workers
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futures = {}
            for idx, archive in work_items:
                print(f"Submitting job {idx}/{total}: {archive.name}")
                fut = pool.submit(
                    process_archive,
                    archive,
                    args.cfg,
                    args.tecs,
                    args.verbose,
                    args.cleanup,
                    out_path_resolved or args.out,
                    keep_ext_letters,
                )
                futures[fut] = (idx, archive)

            for fut in as_completed(futures):
                idx, archive = futures[fut]
                print(f"Completed {idx}/{total}: {archive.name}")
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
