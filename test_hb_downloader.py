"""Tests for hb_downloader.py.

Run with:  pytest test_hb_downloader.py -v
"""

import hashlib
import importlib
import sys
import types
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pytest

# ---------------------------------------------------------------------------
# The module uses `platform_list` at import-time inside create_parser(), which
# requires it to already be defined in the module's global scope.  We stub it
# before the real import so the module loads without error outside __main__.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Stub heavy third-party imports so tests run without the full environment.
# ruamel.yaml is a namespace package; both the parent and child must be
# registered before any import statement tries to resolve them.
# ---------------------------------------------------------------------------

_logger_stub = MagicMock()

# loguru
_loguru_mod = types.ModuleType("loguru")
_loguru_mod.logger = _logger_stub
sys.modules["loguru"] = _loguru_mod

# ruamel (namespace) + ruamel.yaml
_ruamel_mod = types.ModuleType("ruamel")
_ruamel_mod.__path__ = []  # marks it as a package
sys.modules["ruamel"] = _ruamel_mod
_ruamel_yaml_mod = types.ModuleType("ruamel.yaml")
_ruamel_yaml_mod.YAML = MagicMock()
sys.modules["ruamel.yaml"] = _ruamel_yaml_mod
_ruamel_mod.yaml = _ruamel_yaml_mod

# slugify
_slugify_mod = types.ModuleType("slugify")
_slugify_mod.slugify = lambda s, **kw: s.lower().replace(" ", "-")
sys.modules["slugify"] = _slugify_mod

# tqdm
_tqdm_mod = types.ModuleType("tqdm")
_tqdm_mod.tqdm = MagicMock()
sys.modules["tqdm"] = _tqdm_mod

# requests
_requests_mod = types.ModuleType("requests")
_requests_mod.Session = MagicMock()
_requests_mod.get = MagicMock()
sys.modules["requests"] = _requests_mod

# ---------------------------------------------------------------------------
# Now import the module under test (not via __main__).
# ---------------------------------------------------------------------------
import importlib.util, types

spec = importlib.util.spec_from_file_location(
    "hb_downloader", Path(__file__).parent / "hb_downloader.py"
)
hb = importlib.util.module_from_spec(spec)
# Inject the platform_list that create_parser() references at import time.
hb.platform_list = [
    "android", "audio", "ebook", "linux", "mac",
    "windows", "video", "other", "nogames", "all",
]
spec.loader.exec_module(hb)


# Convenience aliases
human_size = hb.human_size
md5sum = hb.md5sum
extract_filename = hb.extract_filename
download_func = hb.download
configure_logging = hb.configure_logging
Product = hb.Product
Order = hb.Order
HumbleApi = hb.HumbleApi


# ===========================================================================
# human_size
# ===========================================================================

class TestHumanSize:
    def test_zero(self):
        assert human_size(0) == "0 B"

    def test_bytes(self):
        assert human_size(512) == "512.00 B"

    def test_one_kibibyte(self):
        assert human_size(1024) == "1.00 kiB"

    def test_one_mebibyte(self):
        assert human_size(1024 ** 2) == "1.00 MiB"

    def test_one_gibibyte(self):
        assert human_size(1024 ** 3) == "1.00 GiB"

    def test_one_tebibyte(self):
        assert human_size(1024 ** 4) == "1.00 TiB"

    def test_fractional(self):
        assert human_size(1536) == "1.50 kiB"

    def test_large_odd(self):
        # 2.5 GiB
        assert human_size(int(2.5 * 1024 ** 3)) == "2.50 GiB"


# ===========================================================================
# extract_filename
# ===========================================================================

class TestExtractFilename:
    def _make_product(self, url):
        p = MagicMock()
        p.url = url
        return p

    def test_normal_url(self):
        p = self._make_product("https://example.com/path/file.zip?token=abc")
        assert extract_filename(p) == "file.zip"

    def test_url_without_query(self):
        p = self._make_product("https://example.com/path/game.tar.gz?")
        assert extract_filename(p) == "game.tar.gz"

    def test_url_deep_path(self):
        p = self._make_product("https://cdn.hb.com/a/b/c/d/ebook.epub?sig=xyz")
        assert extract_filename(p) == "ebook.epub"


# ===========================================================================
# md5sum
# ===========================================================================

class TestMd5sum:
    def test_known_content(self, tmp_path):
        data = b"hello humble"
        expected = hashlib.md5(data).hexdigest()
        f = tmp_path / "test.bin"
        f.write_bytes(data)
        assert md5sum(f) == expected

    def test_updates_pbar(self, tmp_path):
        data = b"x" * 1024
        f = tmp_path / "data.bin"
        f.write_bytes(data)
        pbar = MagicMock()
        md5sum(f, pbar=pbar)
        pbar.update.assert_called()

    def test_missing_file_returns_none(self, tmp_path):
        result = md5sum(tmp_path / "nonexistent.bin")
        assert result is None


# ===========================================================================
# download() function
# ===========================================================================

class TestDownloadFunction:
    def _make_response(self, content: bytes, content_length=None):
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.headers = {}
        if content_length is not None:
            resp.headers["content-length"] = str(content_length)
        resp.iter_content = MagicMock(return_value=[content])
        return resp

    def test_successful_download(self, tmp_path):
        data = b"filedata"
        resp = self._make_response(data, content_length=len(data))
        pbar = MagicMock()
        with patch.object(_requests_mod, "get", return_value=resp):
            result = download_func("http://x/f.zip", tmp_path / "f.zip", len(data), pbar)
        assert result is True
        assert (tmp_path / "f.zip").read_bytes() == data

    def test_size_mismatch_raises(self, tmp_path):
        data = b"short"
        resp = self._make_response(data, content_length=99)
        pbar = MagicMock()
        with patch.object(_requests_mod, "get", return_value=resp):
            with pytest.raises(ValueError):
                download_func("http://x/f.zip", tmp_path / "f.zip", 10, pbar)

    def test_keep_wrong_size_does_not_raise(self, tmp_path):
        data = b"short"
        resp = self._make_response(data, content_length=99)
        pbar = MagicMock()
        with patch.object(_requests_mod, "get", return_value=resp):
            result = download_func(
                "http://x/f.zip", tmp_path / "f.zip", 10, pbar, keep_wrong_size=True
            )
        assert result is True

    def test_missing_content_length_proceeds(self, tmp_path):
        data = b"data"
        resp = self._make_response(data, content_length=None)
        pbar = MagicMock()
        with patch.object(_requests_mod, "get", return_value=resp):
            result = download_func("http://x/f.zip", tmp_path / "f.zip", len(data), pbar)
        assert result is True


# ===========================================================================
# Product
# ===========================================================================

class TestProduct:
    def _make(self, md5="abc", size=100, **kw):
        return Product(md5=md5, size=size, name="Game", url="http://x",
                       platform="linux", bundle_name="Bundle",
                       date=datetime(2023, 1, 1), machine_name="game_linux", **kw)

    def test_equality_same_md5_size(self):
        a = self._make(md5="aaa", size=100)
        b = self._make(md5="aaa", size=100)
        assert a == b

    def test_inequality_different_md5(self):
        a = self._make(md5="aaa", size=100)
        b = self._make(md5="bbb", size=100)
        assert a != b

    def test_inequality_different_size(self):
        a = self._make(md5="aaa", size=100)
        b = self._make(md5="aaa", size=200)
        assert a != b

    def test_hash_equal_products_same_hash(self):
        a = self._make(md5="aaa", size=100)
        b = self._make(md5="aaa", size=100)
        assert hash(a) == hash(b)

    def test_set_deduplication(self):
        a = self._make(md5="aaa", size=100)
        b = self._make(md5="aaa", size=100)
        c = self._make(md5="bbb", size=100)
        assert len({a, b, c}) == 2

    def test_checked_defaults_false(self):
        p = self._make()
        assert p.checked is False

    def test_hb_name_migration(self):
        p = Product(md5="x", size=1, name="G", url="u", platform="linux",
                    hb_name="Old Bundle", date=datetime(2020, 1, 1),
                    machine_name="g")
        assert p.bundle_name == "Old Bundle"
        assert not hasattr(p, "hb_name")

    def test_eq_non_product_returns_not_implemented(self):
        p = self._make()
        assert p.__eq__("string") == NotImplemented


# ===========================================================================
# Order
# ===========================================================================

class TestOrder:
    def _base_struct(self, md5="good_md5", machine_name="game_linux"):
        return {
            "machine_name": machine_name,
            "platform": "linux",
            "download_struct": [
                {
                    "url": {"web": "https://cdn.hb.com/game.zip?tok=1"},
                    "file_size": 1024,
                    "md5": md5,
                }
            ],
        }

    def _make_order(self, subproducts):
        return Order(subproducts, "Test Bundle", "2023-06-15T12:00:00.000000")

    def test_parses_product(self):
        products = [{"human_name": "My Game", "downloads": [self._base_struct()]}]
        order = self._make_order(products)
        assert len(order.products) == 1
        assert order.products[0].name == "My Game"
        assert order.products[0].platform == "linux"
        assert order.products[0].size == 1024

    def test_skips_md5_exclusion(self):
        excluded_md5 = Order.md5_exclusion[0]
        products = [{"human_name": "Bad", "downloads": [self._base_struct(md5=excluded_md5)]}]
        order = self._make_order(products)
        assert len(order.products) == 0

    def test_skips_name_exclusion(self):
        # name_exclusion is only checked when a struct is missing required fields.
        # It suppresses the error log for known-broken machine names.
        excl = Order.name_exclusion[0]
        # Provide an incomplete struct (missing md5/file_size) to trigger the KeyError path.
        incomplete_struct = {"url": {"web": "https://cdn.hb.com/f.zip?t=1"}}
        products = [{"human_name": "Bad", "downloads": [
            {"machine_name": excl, "platform": "linux", "download_struct": [incomplete_struct]}
        ]}]
        order = self._make_order(products)
        # No product should be added because the struct is incomplete.
        assert len(order.products) == 0

    def test_skips_asm_entries_silently(self):
        # ASM structs (Humble App Store Manager) have asm_config/asm_manifest
        # instead of a URL.  They should be skipped at DEBUG level with no WARNING.
        asm_struct = {"asm_config": "...", "asm_manifest": "...", "human_size": "12 MB"}
        products = [{"human_name": "My Game", "downloads": [
            {"machine_name": "mygame_asm", "platform": "windows", "download_struct": [asm_struct]}
        ]}]
        order = self._make_order(products)
        assert len(order.products) == 0
        # Must not have emitted a WARNING for the ASM entry
        warning_calls = [
            str(c) for c in _logger_stub.warning.call_args_list
            if "mygame_asm" in str(c)
        ]
        assert warning_calls == []

    def test_skips_missing_human_name(self):
        products = [{"downloads": [self._base_struct()]}]
        order = self._make_order(products)
        assert len(order.products) == 0

    def test_bundle_name_and_date(self):
        order = self._make_order([])
        assert order.bundle_name == "Test Bundle"
        assert order.date == datetime(2023, 6, 15, 12, 0, 0)

    def test_multiple_downloads(self):
        products = [
            {"human_name": "A", "downloads": [self._base_struct(md5="md1")]},
            {"human_name": "B", "downloads": [self._base_struct(md5="md2")]},
        ]
        order = self._make_order(products)
        assert len(order.products) == 2


# ===========================================================================
# HumbleApi.check_file
# ===========================================================================

class TestCheckFile:
    def _make_api(self, skip_recheck=False, keep_wrong_md5sum=False, keep_wrong_size=False, dry_run=False):
        api = object.__new__(HumbleApi)
        api.skip_recheck = skip_recheck
        api.keep_wrong_md5sum = keep_wrong_md5sum
        api.keep_wrong_size = keep_wrong_size
        api.dry_run = dry_run
        return api

    def _make_product(self, md5="correcthash", size=100):
        p = MagicMock()
        p.md5 = md5
        p.size = size
        p.name = "Game"
        return p

    def test_missing_file_returns_false(self, tmp_path):
        api = self._make_api()
        p = self._make_product()
        assert api.check_file(p, tmp_path / "missing.bin", None) is False

    def test_size_mismatch_deletes_and_returns_false(self, tmp_path):
        f = tmp_path / "file.bin"
        f.write_bytes(b"x" * 50)
        api = self._make_api(keep_wrong_size=False)
        p = self._make_product(size=100)
        result = api.check_file(p, f, None)
        assert result is False
        assert not f.exists()

    def test_size_mismatch_kept_when_keep_wrong_size(self, tmp_path):
        data = b"x" * 50
        f = tmp_path / "file.bin"
        f.write_bytes(data)
        # Wrong size but wrong md5 too — keep_wrong_size keeps the file
        api = self._make_api(keep_wrong_size=True, keep_wrong_md5sum=True)
        p = self._make_product(size=100, md5="wronghash")
        result = api.check_file(p, f, None)
        assert f.exists()  # must NOT be deleted
        assert result is False  # not fully verified

    def test_skip_recheck_correct_size_returns_true(self, tmp_path):
        data = b"x" * 100
        f = tmp_path / "file.bin"
        f.write_bytes(data)
        api = self._make_api(skip_recheck=True)
        p = self._make_product(size=100)
        assert api.check_file(p, f, None) is True

    def test_correct_md5_returns_true(self, tmp_path):
        data = b"hello"
        f = tmp_path / "file.bin"
        f.write_bytes(data)
        correct_md5 = hashlib.md5(data).hexdigest()
        api = self._make_api()
        p = self._make_product(md5=correct_md5, size=len(data))
        assert api.check_file(p, f, None) is True

    def test_wrong_md5_deletes_and_returns_false(self, tmp_path):
        data = b"hello"
        f = tmp_path / "file.bin"
        f.write_bytes(data)
        api = self._make_api(keep_wrong_md5sum=False)
        p = self._make_product(md5="wronghash", size=len(data))
        result = api.check_file(p, f, None)
        assert result is False
        assert not f.exists()

    def test_keep_wrong_md5sum_does_not_delete(self, tmp_path):
        data = b"hello"
        f = tmp_path / "file.bin"
        f.write_bytes(data)
        api = self._make_api(keep_wrong_md5sum=True)
        p = self._make_product(md5="wronghash", size=len(data))
        result = api.check_file(p, f, None)
        assert result is False   # not verified, but not deleted either
        assert f.exists()

    def test_dry_run_size_mismatch_does_not_delete(self, tmp_path):
        f = tmp_path / "file.bin"
        f.write_bytes(b"x" * 50)
        api = self._make_api(dry_run=True)
        p = self._make_product(size=100)
        result = api.check_file(p, f, None)
        assert f.exists()  # must NOT be deleted in dry-run mode
        assert result is False

    def test_dry_run_md5_mismatch_does_not_delete(self, tmp_path):
        data = b"hello"
        f = tmp_path / "file.bin"
        f.write_bytes(data)
        api = self._make_api(dry_run=True)
        p = self._make_product(md5="wronghash", size=len(data))
        result = api.check_file(p, f, None)
        assert f.exists()  # must NOT be deleted in dry-run mode
        assert result is False

    def test_dry_run_size_mismatch_does_not_delete(self, tmp_path):
        f = tmp_path / "file.bin"
        f.write_bytes(b"x" * 50)
        api = self._make_api(dry_run=True)
        p = self._make_product(size=100)
        result = api.check_file(p, f, None)
        assert f.exists()  # must NOT be deleted in dry-run mode
        assert result is False

    def test_dry_run_md5_mismatch_does_not_delete(self, tmp_path):
        data = b"hello"
        f = tmp_path / "file.bin"
        f.write_bytes(data)
        api = self._make_api(dry_run=True)
        p = self._make_product(md5="wronghash", size=len(data))
        result = api.check_file(p, f, None)
        assert f.exists()  # must NOT be deleted in dry-run mode
        assert result is False


# ===========================================================================
# HumbleApi.get_download_list  (purchase_limit and platform filtering)
# ===========================================================================

class TestGetDownloadList:
    def _make_product(self, md5, size=10, platform="linux", checked=False):
        p = Product(md5=md5, size=size, name=md5, url="http://x",
                    platform=platform, bundle_name="B",
                    date=datetime(2023, 1, 1), machine_name=md5)
        p.checked = checked
        return p

    def _make_order(self, products):
        o = MagicMock()
        o.products = products
        o.date = datetime(2023, 1, 1)
        return o

    def _make_api(self, platforms, purchase_limit=0, downloaded_list=None):
        api = object.__new__(HumbleApi)
        api.platforms = platforms
        api.purchase_limit = purchase_limit
        api.reverse_order = True
        api.downloaded_list = downloaded_list or []
        api.checked_list = [p for p in (downloaded_list or []) if p.checked]
        api.all_set = set()
        return api

    def test_filters_by_platform(self):
        p_linux = self._make_product("aaa", platform="linux")
        p_win = self._make_product("bbb", platform="windows")
        api = self._make_api(platforms=["linux"])
        api.order_list = [self._make_order([p_linux, p_win])]
        api.all_set = {p_linux, p_win}
        api.get_download_list()
        assert p_linux in api.to_download_list
        assert p_win not in api.to_download_list

    def test_excludes_checked_items(self):
        p_new = self._make_product("aaa", platform="linux")
        p_checked = self._make_product("bbb", platform="linux", checked=True)
        api = self._make_api(platforms=["linux"], downloaded_list=[p_checked])
        api.order_list = [self._make_order([p_new, p_checked])]
        api.all_set = {p_new, p_checked}
        api.get_download_list()
        assert p_new in api.to_download_list
        assert p_checked not in api.to_download_list

    def test_purchase_limit_restricts_to_newest(self):
        p_old = self._make_product("old", platform="linux")
        p_new = self._make_product("new", platform="linux")
        old_order = self._make_order([p_old])
        old_order.date = datetime(2020, 1, 1)
        new_order = self._make_order([p_new])
        new_order.date = datetime(2023, 1, 1)
        api = self._make_api(platforms=["linux"], purchase_limit=1)
        api.order_list = [old_order, new_order]
        api.all_set = {p_old, p_new}
        api.get_download_list()
        assert p_new in api.to_download_list
        assert p_old not in api.to_download_list

    def test_total_size_calculated(self):
        p1 = self._make_product("aaa", size=500, platform="linux")
        p2 = self._make_product("bbb", size=300, platform="linux")
        api = self._make_api(platforms=["linux"])
        api.order_list = [self._make_order([p1, p2])]
        api.all_set = {p1, p2}
        api.get_download_list()
        assert api.total_size == 800

    def test_orphaned_set_identified(self):
        p_active = self._make_product("active", platform="linux")
        p_gone = self._make_product("gone", platform="linux")
        api = self._make_api(platforms=["linux"], downloaded_list=[p_active, p_gone])
        api.order_list = [self._make_order([p_active])]
        api.all_set = {p_active}
        api.get_download_list()
        assert p_gone in api.orphaned_set
        assert p_active not in api.orphaned_set


# ===========================================================================
# configure_logging
# ===========================================================================

class TestConfigureLogging:
    def test_stdout_sink_added(self):
        with patch.object(_logger_stub, "remove"), \
             patch.object(_logger_stub, "add") as mock_add:
            configure_logging(verbose=False, log_output="stdout", log_file=None)
            # Should add exactly one sink (stdout)
            assert mock_add.call_count == 1

    def test_file_sink_added(self, tmp_path):
        log_path = str(tmp_path / "test.log")
        with patch.object(_logger_stub, "remove"), \
             patch.object(_logger_stub, "add") as mock_add:
            configure_logging(verbose=False, log_output="file", log_file=log_path)
            assert mock_add.call_count == 1
            args, _ = mock_add.call_args
            assert args[0] == log_path

    def test_both_sinks_added(self, tmp_path):
        log_path = str(tmp_path / "test.log")
        with patch.object(_logger_stub, "remove"), \
             patch.object(_logger_stub, "add") as mock_add:
            configure_logging(verbose=False, log_output="both", log_file=log_path)
            assert mock_add.call_count == 2

    def test_verbose_sets_info_level(self, tmp_path):
        log_path = str(tmp_path / "test.log")
        with patch.object(_logger_stub, "remove"), \
             patch.object(_logger_stub, "add") as mock_add:
            configure_logging(verbose=True, log_output="file", log_file=log_path)
            _, kwargs = mock_add.call_args
            assert kwargs["level"] == "INFO"

    def test_non_verbose_sets_warning_level(self, tmp_path):
        log_path = str(tmp_path / "test.log")
        with patch.object(_logger_stub, "remove"), \
             patch.object(_logger_stub, "add") as mock_add:
            configure_logging(verbose=False, log_output="file", log_file=log_path)
            _, kwargs = mock_add.call_args
            assert kwargs["level"] == "WARNING"

    def test_auto_generates_log_filename(self):
        with patch.object(_logger_stub, "remove"), \
             patch.object(_logger_stub, "add") as mock_add:
            configure_logging(verbose=False, log_output="file", log_file=None)
            args, _ = mock_add.call_args
            assert args[0].startswith("hb_downloader_output_")
            assert args[0].endswith(".log")


# ===========================================================================
# parse_config
# ===========================================================================

class TestParseConfig:
    """Tests for parse_config() – config-file reading and CLI-override logic."""

    def _run(self, cfg_data, cli_overrides=None):
        """Run parse_config with a mocked config file and given CLI args."""
        defaults = dict(
            platform=[], download_limit=[None], purchase_limit=[None],
            smallest_first=False, keep_wrong_size=False, keep_wrong_md5sum=False,
            skip_recheck=False, verbose=False, log_output=None, log_file=None,
        )
        if cli_overrides:
            defaults.update(cli_overrides)
        args_mock = MagicMock(**defaults)

        mock_yaml = MagicMock()
        mock_yaml.load.return_value = cfg_data

        parser = hb.create_parser()
        old_yaml = getattr(hb, "yaml", None)
        hb.yaml = mock_yaml
        try:
            with patch("builtins.open", mock_open(read_data="")), \
                 patch.object(parser, "parse_args", return_value=args_mock):
                return hb.parse_config(parser)
        finally:
            if old_yaml is None:
                del hb.yaml
            else:
                hb.yaml = old_yaml

    def test_platforms_list_read_from_config(self):
        platforms, _ = self._run({"session_cookie": "x", "platforms": ["linux", "ebook"]})
        assert platforms == ["linux", "ebook"]

    def test_platforms_string_normalised_to_list(self):
        """platforms: all in YAML loads as a str; must become ['all']."""
        platforms, _ = self._run({"session_cookie": "x", "platforms": "all"})
        assert platforms == ["all"]

    def test_cli_platform_overrides_config(self):
        platforms, _ = self._run(
            {"session_cookie": "x", "platforms": ["linux"]},
            cli_overrides={"platform": ["windows"]},
        )
        assert platforms == ["windows"]

    def test_platforms_not_leaked_into_cfg(self):
        _, cfg = self._run({"session_cookie": "x", "platforms": ["linux"]})
        assert "platforms" not in cfg

    def test_verbose_default_false_when_absent(self):
        _, cfg = self._run({"session_cookie": "x"})
        assert cfg["verbose"] is False

    def test_log_output_default_stdout_when_absent(self):
        _, cfg = self._run({"session_cookie": "x"})
        assert cfg["log_output"] == "stdout"

    def test_cli_verbose_overrides_config_false(self):
        _, cfg = self._run({"session_cookie": "x", "verbose": False},
                           cli_overrides={"verbose": True})
        assert cfg["verbose"] is True

    def test_cli_log_output_overrides_config(self):
        _, cfg = self._run({"session_cookie": "x"},
                           cli_overrides={"log_output": "both"})
        assert cfg["log_output"] == "both"

    def test_cli_log_file_stored(self):
        _, cfg = self._run({"session_cookie": "x"},
                           cli_overrides={"log_file": "mylog.log"})
        assert cfg["log_file"] == "mylog.log"

    def test_legacy_trove_key_removed(self):
        _, cfg = self._run({"session_cookie": "x", "trove": True})
        assert "trove" not in cfg

