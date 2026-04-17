#!/bin/python
from __future__ import annotations

import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from json import JSONDecodeError
from math import floor, log2
from pathlib import Path
from sys import exit, stdout
from typing import Any, List, Optional, Tuple

import requests
from loguru import logger
from ruamel.yaml import YAML  # type: ignore
from slugify import slugify
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Logging bootstrap: a minimal WARNING-level stdout sink is set up here so
# that any log calls before configure_logging() (e.g. inside parse_config)
# are still visible.  configure_logging() replaces this sink once the full
# CLI/config options are known.
# ---------------------------------------------------------------------------
logger.remove(0)
logger.add(
    stdout,
    level="WARNING",
    colorize=True,
    format="<level>{level}:</level> {message}\x1b[K",
)

# Custom level for items whose local file is simply absent (between WARNING=30
# and DEBUG=10, so it shows under WARNING by default).
logger.level("Missing", no=21, color="<yellow>")


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def human_size(x: int) -> str:
    """Convert a byte count to a human-readable string (e.g. 1.50 MiB)."""
    if x == 0:
        return "0 B"
    suffixes = ["B", "kiB", "MiB", "GiB", "TiB"]
    exponent = floor(log2(x) / 10) * 10
    x = x / 2**exponent
    suffix = suffixes[exponent // 10]
    return f"{x:.2f} {suffix}"


def md5sum(filepath: Path, pbar=None, blocksize: int = 65536) -> Optional[str]:
    """Return the MD5 hex digest of a file, optionally updating a tqdm progress bar.

    Returns None and logs a critical error if the file cannot be read.
    """
    filehash = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            for block in iter(lambda: f.read(blocksize), b""):
                filehash.update(block)
                if pbar:
                    pbar.update(len(block))
        return filehash.hexdigest()
    except OSError:
        logger.critical(f"Could not read file for MD5: {filepath}")
        return None


def download(
    urllink: str,
    filepath: Path,
    reported_size: int,
    pbar,
    chunk_size: int = 1048576,
    keep_wrong_size: bool = False,
) -> bool:
    """Stream a URL to disk, raising ValueError if the server-reported size
    disagrees with the catalogue size (unless keep_wrong_size is True).

    A missing Content-Length header is treated as a warning and the size
    check is skipped rather than crashing.
    """
    with requests.get(urllink, stream=True) as r:
        content_length = r.headers.get("content-length")
        if content_length is not None:
            total_length = int(content_length)
            if total_length != reported_size and not keep_wrong_size:
                raise ValueError
        else:
            logger.warning(f"No Content-Length header for {filepath.name}, skipping size check")
        with open(filepath, "wb") as f:
            for chunk in r.iter_content(chunk_size=chunk_size):
                f.write(chunk)
                pbar.update(len(chunk))
    return True


def extract_filename(product) -> str:
    """Strip the query-string from a product URL and return the bare filename."""
    filename = product.url[: product.url.find("?")]
    filename = filename[filename.rfind("/") + 1 :]
    return filename


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------

def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download files from Humble Bundle, based on selected platform",
    )
    parser.add_argument(
        "platform",
        metavar="platform",
        type=str,
        nargs="*",
        default=[],
        help=f"platform to download, valid platforms are: {platform_list}",
    )
    parser.add_argument(
        "-l",
        "--download_limit",
        metavar="X",
        default=[None],
        type=int,
        nargs=1,
        help="Download X files in parallel",
    )
    parser.add_argument(
        "-n",
        "--purchase_limit",
        metavar="Y",
        type=int,
        default=[None],
        nargs=1,
        help="Download only from Y newest purchases, 0 for all --default",
    )
    parser.add_argument(
        "-s",
        "--smallest_first",
        action="store_true",
        help="Download smallest files first",
    )
    parser.add_argument(
        "-w",
        "--keep_wrong_size",
        action="store_true",
        help="Download files with wrong size and keep them",
    )
    parser.add_argument(
        "-m",
        "--keep_wrong_md5sum",
        action="store_true",
        help="keep files with wrong md5sum them",
    )
    parser.add_argument(
        "-r",
        "--skip_recheck",
        action="store_true",
        help="Skip MD5 verification for files that already exist with the correct size",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Report each item as it is processed",
    )
    parser.add_argument(
        "--log-output",
        choices=["stdout", "file", "both"],
        default=None,
        help="Where to write log output: stdout (default), file, or both",
    )
    parser.add_argument(
        "--log-file",
        metavar="FILENAME",
        default=None,
        help="Log file name (default: hb_downloader_output_<datetime>.log)",
    )
    return parser


# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------

def parse_config(parser: argparse.ArgumentParser) -> Tuple[List[str], dict[str, Any]]:
    """Load config.yaml and merge CLI overrides on top.

    Config files are resolved relative to the script's own directory so the
    program works regardless of the working directory it is launched from.

    CLI flags always win over config-file values.  If config.yaml is absent
    or empty, example_config.yaml is copied in its place and the program
    exits so the user can fill it in.
    """
    script_dir = Path(__file__).parent
    config_path = script_dir / "config.yaml"
    example_path = script_dir / "example_config.yaml"

    try:
        with open(config_path) as yamlfile:
            cfg = yaml.load(yamlfile)
            _ = cfg.keys()  # raises AttributeError if file is empty
    except (FileNotFoundError, AttributeError):
        try:
            config_path.write_text(example_path.read_text())
            logger.warning(f"No config.yaml found — created a default at {config_path}")
            logger.warning("Edit it to add your session_cookie, then re-run.")
        except FileNotFoundError:
            logger.critical(f"Neither config.yaml nor example_config.yaml found in {script_dir}")
        exit()

    # Drop legacy key no longer used
    cfg.pop("trove", None)

    args = parser.parse_args()

    # CLI overrides for numeric/boolean flags (only applied when explicitly set)
    if args.download_limit[0]:
        cfg["download_limit"] = args.download_limit[0]
    if args.purchase_limit[0]:
        cfg["purchase_limit"] = args.purchase_limit[0]
    if args.smallest_first:
        cfg["smallest_first"] = True
    if args.keep_wrong_size:
        cfg["keep_wrong_size"] = True
    if args.keep_wrong_md5sum:
        cfg["keep_wrong_md5sum"] = True
    if args.skip_recheck:
        cfg["skip_recheck"] = True

    # Logging options: CLI overrides config; config overrides built-in defaults
    if args.verbose:
        cfg["verbose"] = True
    cfg.setdefault("verbose", False)

    if args.log_output is not None:
        cfg["log_output"] = args.log_output
    cfg.setdefault("log_output", "stdout")

    if args.log_file is not None:
        cfg["log_file"] = args.log_file
    # log_file intentionally has no default here; None triggers auto-naming

    # Platform: CLI wins over config; config list is the fallback.
    # A bare string in config (e.g. 'platforms: all') is normalised to a list.
    if args.platform:
        platforms = args.platform
    else:
        platforms = cfg.pop("platforms", [])
        if isinstance(platforms, str):
            platforms = [platforms]
    cfg.pop("platforms", None)  # ensure it never leaks into **cfg

    # Validate platform names now that CLI and config have been merged.
    valid = set(platform_list)
    bad = [p for p in platforms if p not in valid]
    if bad:
        parser.error(f"invalid platform(s): {', '.join(bad)} (choose from {platform_list})")

    return platforms, cfg


def configure_logging(verbose: bool, log_output: str, log_file: Optional[str]) -> None:
    """Replace the bootstrap logging sink with the user-configured one(s).

    verbose=True lowers the level to INFO, showing per-item progress messages.
    log_output controls whether logs go to stdout, a file, or both.
    When writing to a file-only sink the tqdm progress bar still renders on
    the terminal because tqdm writes directly to stderr/stdout.
    """
    level = "INFO" if verbose else "WARNING"
    stdout_format = "<level>{level}:</level> {message}\x1b[K"
    file_format = "{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {message}"

    logger.remove()  # clear the bootstrap sink

    if log_output in ("stdout", "both"):
        logger.add(stdout, level=level, colorize=True, format=stdout_format)

    if log_output in ("file", "both"):
        if log_file is None:
            log_file = f"hb_downloader_output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        logger.add(log_file, level=level, colorize=False, format=file_format)
        logger.info(f"Logging to file: {log_file}")


# ---------------------------------------------------------------------------
# YAML persistence helpers
# ---------------------------------------------------------------------------

def dump_data(to_dump: Any, filepath: Path) -> None:
    """Serialise a collection of Product objects to a YAML file, sorted by date then name."""
    to_dump = [item.__dict__ for item in to_dump]
    to_dump = sorted(to_dump, key=lambda i: (i["date"], i["name"]))
    yaml.indent(mapping=4, sequence=6, offset=3)
    with open(filepath, "w") as file:
        yaml.dump(to_dump, file)


# ---------------------------------------------------------------------------
# Core downloader
# ---------------------------------------------------------------------------

class HumbleApi:
    """Orchestrates fetching the order list, building the download queue,
    and downloading or verifying each product file."""

    default_headers = {
        "Accept": "application/json",
        "Accept-Charset": "utf-8",
        "X-Requested-By": "hb_android_app",
        "User-Agent": "Apache-HttpClient/UNAVAIABLE (java 1.4)",
    }
    ORDER_LIST_URL = "https://www.humblebundle.com/api/v1/user/order"
    ORDER_URL = "https://www.humblebundle.com/api/v1/order/{order_id}"

    def __init__(
        self,
        download_limit: int = 6,
        purchase_limit: int = 0,
        smallest_first: bool = False,
        download_folder: str = ".",
        platforms: list[str] = [],
        session_cookie: str = "",
        keep_wrong_size: bool = False,
        keep_wrong_md5sum: bool = False,
        skip_recheck: bool = False,
    ):
        self.download_limit = download_limit
        # purchase_limit=0 means "all purchases". Python's -0 == 0, so
        # list[-0:] == list[0:] (all orders) and list[:-0] == list[:0]
        # (empty), which gives the correct behaviour for both cases.
        self.purchase_limit = purchase_limit
        self.reverse_order = not smallest_first  # True = largest first (default)
        self.keep_wrong_size = keep_wrong_size
        self.keep_wrong_md5sum = keep_wrong_md5sum
        self.skip_recheck = skip_recheck

        self.download_folder = Path(download_folder)
        self.download_folder.mkdir(exist_ok=True)

        self.platforms = platforms

        self.session = requests.Session()
        self.session.cookies.update({"_simpleauth_sess": session_cookie})
        self.session.headers.update(self.default_headers)
        self.session.params.update({"ajax": "true"})  # type: ignore

        # Load the persisted list of already-processed products, if any.
        try:
            with open("downloaded.yaml") as yamlfile:
                tmp = yaml.load(yamlfile)
                self.downloaded_list = [Product(**item) for item in tmp]
                # Items marked checked=True are fully verified and skipped entirely.
                self.checked_list = [
                    item for item in self.downloaded_list if item.checked
                ]
        except FileNotFoundError:
            logger.debug("First time running")
            self.downloaded_list = []
            self.checked_list = []

        self.orders_num = 0
        self.total_size = 0
        self.human_size = ""
        self.downloaded_size = 0
        self.total_items = 0

        self.order_key_list: list[str] = []
        self.order_list: list[Order] = []

        self.downloading_list: list[Product] = []
        self.all_set: set[Product] = set()          # every product across all orders
        self.to_not_download_set: set[Product] = set()
        self.to_download_set: set[Product] = set()
        self.to_download_list: list[Product] = []
        self.orphaned_set: set[Product] = set()     # in downloaded.yaml but no longer in orders

    def check_platforms(self):
        """Expand the 'nogames' and 'all' platform aliases to concrete platform names."""
        if "nogames" in self.platforms:
            # Include every non-game platform present in the product catalogue.
            game_platforms = {"linux", "mac", "windows", "android"}
            all_platforms = {x.platform for x in self.all_set}
            self.platforms = list(all_platforms.difference(game_platforms))
        if "all" in self.platforms:
            self.platforms = list({x.platform for x in self.all_set})

    def get_order_list(self):
        """Fetch the list of order keys (gamekeyes) for the authenticated account."""
        r = self.session.get(self.ORDER_LIST_URL)
        try:
            r = r.json()
            self.order_key_list = [item["gamekey"] for item in r]
            self.orders_num = len(self.order_key_list)
        except JSONDecodeError:
            logger.warning("Can't extract data from HB")
            exit()

    def get_order_info(self, order):
        """Fetch detailed product info for a single order key."""
        r = self.session.get(self.ORDER_URL.format(order_id=order))
        try:
            r = r.json()
        except JSONDecodeError:
            logger.error(
                f"Problem with getting info about {self.ORDER_URL.format(order_id=order)}",
            )
            return None, []
        return Order(r["subproducts"], r["product"]["human_name"], r["created"])

    def get_product_list(self):
        """Fetch all order details in parallel and build self.all_set."""
        with tqdm(
            total=self.orders_num, desc="Getting products", dynamic_ncols=True,
        ) as progress, ThreadPoolExecutor(max_workers=self.download_limit) as executor:
            futures = [
                executor.submit(self.get_order_info, order_key)
                for order_key in self.order_key_list
            ]
            for future in as_completed(futures):
                if future:
                    progress.update(1)
                    self.order_list.append(future.result())
        print("\n")
        self.order_list.sort(key=lambda x: x.date, reverse=False)  # oldest first
        self.all_set = {item for order in self.order_list for item in order.products}

    def get_download_list(self):
        """Build the prioritised download queue from the order list.

        Applies purchase_limit to restrict to the N newest orders, filters by
        platform, and subtracts items already fully verified (checked=True).
        Also identifies products present in downloaded.yaml that no longer
        appear in any order (orphans).
        """
        # Slice to the N newest orders; purchase_limit=0 means all orders
        # (because -0 == 0 and list[0:] returns the full list).
        self.to_download_set = {
            item
            for order in self.order_list[-self.purchase_limit :]
            for item in order.products
        }
        # Products outside the purchase window must not be re-queued even if
        # they happen to appear again in a newer order (duplicate products).
        self.to_not_download_set = {
            item
            for order in self.order_list[: -self.purchase_limit]
            for item in order.products
        }
        self.to_download_set = self.to_download_set.difference(self.to_not_download_set)

        self.check_platforms()

        # Keep only products matching the requested platforms.
        self.to_download_set = {
            item for item in self.to_download_set if item.platform in self.platforms
        }
        # Skip items already verified on a previous run.
        self.to_download_set = self.to_download_set.difference(self.checked_list)

        self.to_download_list = list(self.to_download_set)
        self.total_size = sum(item.size for item in self.to_download_list)
        self.human_size = human_size(self.total_size)
        self.total_items = len(self.to_download_list)
        self.downloaded_size = 0
        self.to_download_list.sort(key=lambda x: x.size, reverse=self.reverse_order)

        # Products we downloaded previously but that no longer appear in any order.
        self.orphaned_set = set(self.downloaded_list).difference(self.all_set)

    def download_helper(self):
        """Move orphaned files then download/verify all queued products in parallel."""
        self.clean_orphan()

        with tqdm(
            unit="B",
            unit_divisor=1024,
            unit_scale=True,
            total=self.total_size,
            desc="Downloading",
            dynamic_ncols=True,
        ) as pbar, ThreadPoolExecutor(max_workers=self.download_limit) as executor:
            futures = [
                executor.submit(self.download, item, pbar)
                for item in self.to_download_list
            ]
            for future in as_completed(futures):
                if future.result() is not None:
                    self.downloading_list.append(future.result())
        print("\n")
        self.save_data()

    def clean_orphan(self):
        """Move files for orphaned products into an 'orphaned' subdirectory.

        An orphaned product is one recorded in downloaded.yaml that no longer
        exists in any Humble Bundle order (e.g. removed from catalogue).
        Files are only moved if the MD5 still matches, to avoid touching
        partial or already-moved files.
        """
        root = self.download_folder

        for item in self.orphaned_set:
            bundle_name = f"{item.date.date()} {slugify(item.bundle_name)}"
            item_name = slugify(item.name)
            filename = Path(
                f"{root}/{item.platform} / {item.date.date().strftime('%Y')} /"
                f"{bundle_name}/{item_name}/{extract_filename(item)}",
            )

            if filename.exists():
                if item.md5 == md5sum(filename):
                    logger.warning(f"Moving orphaned file {filename.name}")
                    dest = filename.parents[3].joinpath(
                        "orphaned", *filename.parts[-4:],
                    )
                    # Append a numeric suffix to avoid silently overwriting an
                    # existing file at the destination (common on case-insensitive
                    # filesystems).
                    i = 1
                    while dest.exists():
                        dest = dest.with_suffix(dest.suffix + f".{i}")
                        i += 1
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    filename.rename(dest)
            else:
                logger.debug(f"Orphaned file already moved or absent: {filename.name}")

    def check_file(self, product, filename: Path, pbar) -> bool:
        """Verify an existing file against the catalogue record.

        Order of checks (fast to slow):
        1. File existence
        2. File size vs catalogue size
        3. MD5 hash (skipped when skip_recheck=True and size matches)

        Returns True if the file is considered valid, False otherwise.
        Invalid files are deleted so a fresh download can be attempted.
        """
        if not filename.exists():
            logger.log(
                "Missing", f"File missing: {filename.name} {human_size(product.size)}",
            )
            return False

        actual_size = filename.stat().st_size
        if actual_size != product.size:
            logger.error(
                f"File size mismatch for {filename.name} "
                f"(expected {human_size(product.size)}, got {human_size(actual_size)}), deleting"
            )
            filename.unlink()
            return False

        # Size is correct; skip the expensive MD5 read if requested.
        if self.skip_recheck:
            logger.info(f"Skipping MD5 check for {filename.name} (--skip-recheck)")
            return True

        md5 = md5sum(filename, pbar)
        if md5 == product.md5:
            logger.success(f"File checked {filename.name}")
            return True

        if self.keep_wrong_md5sum:
            logger.info(f"Keeping {filename.name} even with wrong md5sum")
            # Return False so the item is not marked as fully verified.
            return False

        logger.error(f"File mismatch md5sum, deleting {product.name}, {filename}")
        filename.unlink()
        return False

    def download(self, item, pbar):
        """Download or verify a single product.

        Returns the Product on success (or if it already exists on disk),
        None if the file had to be deleted due to a size/integrity error.
        """
        logger.info(f"Processing: '{item.name}' [{item.platform}] {human_size(item.size)}")

        name = item.date.strftime("%Y-%m-%d ") + slugify(item.bundle_name)
        directory = (
            self.download_folder
            / Path(item.platform)
            / item.date.strftime("%Y")
            / Path(name)
            / Path(slugify(item.name))
        )
        directory.mkdir(parents=True, exist_ok=True)
        filename = directory / Path(extract_filename(item))

        # If the file already passes verification, mark it checked and return.
        if self.check_file(item, filename, pbar):
            self.downloaded_size += item.size
            item.checked = True
            return item

        try:
            # File exists but failed verification (e.g. wrong md5, keep_wrong_md5sum);
            # do not re-download, just report it as present.
            if filename.exists():
                return item
            logger.info(f"Download start for '{item.name}' {filename.name}")
            info = download(
                item.url,
                filename,
                item.size,
                pbar,
                keep_wrong_size=self.keep_wrong_size,
            )
            if info:
                logger.success(
                    f"Downloaded {filename.name} {human_size(item.size)}",
                )
        except ValueError:
            logger.warning(f"Mismatch in reported size {filename.name}")
        except FileNotFoundError:
            logger.error("Probable error with 260 character path limit")
        except ConnectionResetError:
            logger.warning(f"Connection problem when getting {filename}")
        else:
            if filename.exists():
                if (
                    item.size != filename.stat().st_size
                    and not self.keep_wrong_size
                ):
                    logger.error(f"Deleting file with incorrect size {filename}")
                    filename.unlink()
                    return None
                self.downloaded_size += item.size
                item.checked = False
                return item

    def save_data(self):
        """Persist download state to YAML files for future runs."""
        completed = set(self.downloading_list).union(self.downloaded_list)
        dump_data(completed, "downloaded.yaml")
        dump_data(self.orphaned_set, "orphaned.yaml")
        dump_data(self.all_set.difference(completed), "not-downloaded.yaml")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class Order:
    """Represents a single Humble Bundle purchase and the products it contains."""

    # Known-broken machine names that are missing required fields.
    name_exclusion = [
        "thespookening_android",
        "worldofgoo_android_pc_soundtrack_audio",
        "dustforce_asm",
    ]
    # Known-broken MD5 hashes (duplicates or catalogue errors).
    md5_exclusion = [
        "c0776421f3527a706cf1f3f3765cafb4",  # issue 1
        "2f8612361dde58c73525ea0d024c0460",  # issue 1
        "bcb063559d17364e9f7bfd3d4fd799ee",  # issue 1
        "428dd67152164f444e6fa21e87caa147",  # issue 1
        "748b36888d3c6e747dc00eea5d518bb9",
        "ef8a5895edce744719bc031ffb0173b0",
        "b5796f487f5f647045bb5fb6eaf16edf",  # issue 2 -- SOMA mac version
    ]

    def __init__(self, json_list: list[dict[str, Any]], name: str, date: str):
        self.products: list[Product] = []
        self.bundle_name = name
        self.date = datetime.strptime(date, "%Y-%m-%dT%H:%M:%S.%f")
        for product in json_list:
            try:
                product_name = product["human_name"]
            except KeyError:
                pass
            else:
                for items in product["downloads"]:
                    machine_name = items["machine_name"]
                    for struct in items["download_struct"]:
                        self.extract_data(struct, items["platform"], product_name, machine_name)

    def extract_data(
        self, struct: dict[str, Any], platform: str, name: str, machine_name: str,
    ) -> None:
        """Parse a single download struct and append a Product if it is valid."""
        try:
            url = struct["url"]["web"]
            size = struct["file_size"]
            md5 = struct["md5"]
        except KeyError:
            if machine_name not in self.name_exclusion:
                logger.error(f"Problem with parsing {machine_name}")
        else:
            if md5 not in self.md5_exclusion:
                self.products.append(Product(
                    name=name,
                    url=url,
                    size=size,
                    md5=md5,
                    platform=platform,
                    bundle_name=self.bundle_name,
                    date=self.date,
                    machine_name=machine_name,
                ))


class Product:
    """Represents a single downloadable file within an order.

    Two products are considered equal (and hash the same) when they share
    both md5 and size, allowing set operations to deduplicate across orders.
    """

    def __init__(self, **kwargs: Any):
        self.checked: bool = False
        self.md5: str
        self.size: int
        self.name: str
        for key, value in kwargs.items():
            setattr(self, key, value)
        # Backwards-compat: rename legacy field from older downloaded.yaml files.
        if hasattr(self, "hb_name"):
            logger.debug(f"Converting old hb_name to new bundle_name for {self.name}")
            self.bundle_name = self.hb_name
            delattr(self, "hb_name")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Product):
            return NotImplemented
        return self.md5 == other.md5 and self.size == other.size

    def __hash__(self) -> int:
        return hash(f"{self.md5}{self.size}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    platform_list = [
        "android",
        "audio",
        "ebook",
        "linux",
        "mac",
        "windows",
        "video",
        "other",
        "nogames",
        "all",
    ]
    parser = create_parser()
    platforms, cfg = parse_config(parser)

    if not platforms:
        parser.error(
            "no platforms specified — pass them on the CLI or add 'platforms' to config.yaml"
        )

    verbose = cfg.pop("verbose", False)
    log_output = cfg.pop("log_output", "stdout")
    log_file = cfg.pop("log_file", None)
    configure_logging(verbose, log_output, log_file)

    # Report the effective configuration so the user can verify what will run.
    logger.info("Active configuration:")
    logger.info(f"  download_folder   : {cfg.get('download_folder', '.')}")
    logger.info(f"  platforms         : {', '.join(platforms) if platforms else 'all'}")
    logger.info(f"  download_limit    : {cfg.get('download_limit', 6)}")
    _pl = cfg.get('purchase_limit', 0)
    logger.info(f"  purchase_limit    : {'all' if not _pl else _pl}")
    logger.info(f"  smallest_first    : {cfg.get('smallest_first', False)}")
    logger.info(f"  skip_recheck      : {cfg.get('skip_recheck', False)}")
    logger.info(f"  keep_wrong_size   : {cfg.get('keep_wrong_size', False)}")
    logger.info(f"  keep_wrong_md5sum : {cfg.get('keep_wrong_md5sum', False)}")
    logger.info(f"  verbose           : {verbose}")
    logger.info(f"  log_output        : {log_output}")
    if log_file:
        logger.info(f"  log_file          : {log_file}")

    try:
        cookie = cfg["session_cookie"]
        _ = cookie[0]
    except (IndexError, ValueError):
        logger.critical("No valid cookie")
        exit()

    runner = HumbleApi(**cfg, platforms=platforms)
    runner.get_order_list()
    runner.get_product_list()
    runner.get_download_list()
    runner.download_helper()
