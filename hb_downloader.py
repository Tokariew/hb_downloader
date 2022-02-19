#!/bin/python
import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from json import JSONDecodeError
from math import floor, log2
from pathlib import Path
from sys import exit, stdout
from typing import Any, Dict, List, Optional, Tuple

import requests
from loguru import logger
from ruamel.yaml import YAML  # type: ignore
from slugify import slugify

logger.remove(0)
logger.add(
    stdout,
    level='WARNING',
    colorize=True,
    format="\x1b[A\r<level>{level}:</level> {message}\x1b[K\x1b[B\x1b[B"
)


def human_size(x: int) -> str:
    if x == 0:
        return '0 B'
    suffixes = ['B', 'kiB', 'MiB', 'GiB', 'TiB']
    exponent = floor(log2(x) / 10) * 10
    x = x / 2 ** exponent
    suffix = suffixes[exponent // 10]
    return f'{x:.2f} {suffix}'


def md5sum(filepath: Path, blocksize: int = 65536) -> str:
    filehash = hashlib.md5()
    with open(filepath, 'rb') as f:
        for block in iter(lambda: f.read(blocksize), b''):
            filehash.update(block)
    return filehash.hexdigest()


def download(urllink: str, filepath: Path, reported_size: int, chunk_size: int = 1048576) -> bool:
    with requests.get(urllink, stream=True) as r:
        total_length = int(r.headers.get('content-length'))  # type: ignore
        if total_length != reported_size:
            raise ValueError
        with open(filepath, 'wb') as f:
            for chunk in r.iter_content(chunk_size=chunk_size):
                f.write(chunk)
    return True


def progress_bar(count: int, total: int, bar_length: int = 60, suffix: str = '') -> None:
    filled_length = bar_length * count // total
    stdout.write(f'[{"="*filled_length:-<{bar_length}}] {count / total:.1%} … {suffix}\x1b[K\r')
    stdout.flush()


def extract_filename(product):
    filename = product.url[: product.url.find('?')]
    filename = filename[filename.rfind('/') + 1 :]
    return filename


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description='Download files from Humble Bundle, based on selected platform'
    )
    parser.add_argument(
        'platform',
        metavar='platform',
        type=str,
        choices=platform_list,
        nargs='+',
        help=f'platform to download, valid platforms are: {platform_list}'
    )
    parser.add_argument(
        '-l',
        '--download_limit',
        metavar='X',
        default=[None],
        type=int,
        nargs=1,
        help='Download X files in parallel'
    )
    parser.add_argument(
        '-n',
        '--purchase_limit',
        metavar='Y',
        type=int,
        default=[None],
        nargs=1,
        help='Download only from Y newest purchases, 0 for all --default'
    )
    parser.add_argument(
        '-s', '--smallest_first', action='store_true', help='Download smallest files first'
    )
    return parser


def parse_config(parser: argparse.ArgumentParser) -> Tuple[List[str], dict[str, Any]]:
    try:
        with open('config.yaml') as yamlfile:
            cfg = yaml.load(yamlfile)
            _ = cfg.keys()  # check if file is not empty
    except (FileNotFoundError, AttributeError):
        Path('config.yaml').write_text(Path('example_config.yaml').read_text())
        logger.warning(f"No valid config file, creating default.")
        exit()
    try:
        del cfg['trove']
    except KeyError:
        logger.debug("Already at new config")
    platforms = parser.parse_args().platform
    download_limit = parser.parse_args().download_limit[0]
    if download_limit:
        cfg['download_limit'] = download_limit
    purchase_limit = parser.parse_args().purchase_limit[0]
    if purchase_limit:
        cfg['purchase_limit'] = purchase_limit
    if parser.parse_args().smallest_first:
        cfg['smallest_first'] = True
    return platforms, cfg


def dump_data(to_dump: Any, filepath: Path) -> None:
    to_dump = [item.__dict__ for item in to_dump]
    to_dump = sorted(to_dump, key=lambda i: (i['date'], i['name']))
    yaml.indent(mapping=4, sequence=6, offset=3)
    with open(filepath, 'w') as file:
        yaml.dump(to_dump, file)


class HumbleApi:

    default_headers = {
        'Accept': 'application/json',
        'Accept-Charset': 'utf-8',
        'X-Requested-By': 'hb_android_app',
        'User-Agent': 'Apache-HttpClient/UNAVAIABLE (java 1.4)'
    }
    ORDER_LIST_URL = 'https://www.humblebundle.com/api/v1/user/order'
    ORDER_URL = 'https://www.humblebundle.com/api/v1/order/{order_id}'

    def __init__(
        self,
        download_limit: int = 6,
        purchase_limit: int = 0,
        smallest_first: bool = False,
        download_folder: str = '.',
        platforms: list[str] = [],
        session_cookie: str = ''
    ):
        self.download_limit = download_limit
        self.purchase_limit = purchase_limit
        self.reverse_order = not smallest_first

        self.download_folder = Path(download_folder)
        self.download_folder.mkdir(exist_ok=True)

        self.platforms = platforms

        self.session = requests.Session()
        self.session.cookies.update({'_simpleauth_sess': session_cookie})
        self.session.headers.update(self.default_headers)
        self.session.params.update({'ajax': 'true'})  # type: ignore

        try:
            with open('downloaded.yaml') as yamlfile:
                tmp = yaml.load(yamlfile)
                self.downloaded_list = [Product(**item) for item in tmp]
                self.checked_list = [item for item in self.downloaded_list if item.checked]
        except FileNotFoundError:
            logger.debug('First time running')
            self.downloaded_list = []
            self.checked_list = []

        self.orders_num = 0
        self.total_size = 0
        self.human_size = ''
        self.downloaded_size = 0
        self.total_items = 0

        self.order_key_list: list[str] = []
        self.order_list: list[Order] = []

        self.downloading_list: list[Product] = []
        self.all_set: set[Product] = set()
        self.to_not_download_set: set[Product] = set()
        self.to_download_set: set[Product] = set()
        self.to_download_list: list[Product] = []
        self.orphaned_set: set[Product] = set()

    def check_platforms(self):
        if 'nogames' in self.platforms:
            platforms = {x.platform for x in self.all_set}
            self.platforms = list(platforms.difference({'linux', 'mac', 'windows', 'android'}))
        if 'all' in self.platforms:
            self.platforms = list(x.platform for x in self.all_set)

    def get_order_list(self):
        r = self.session.get(self.ORDER_LIST_URL)
        try:
            r = r.json()
            self.order_key_list = [item['gamekey'] for item in r]
            self.orders_num = len(self.order_key_list)
        except JSONDecodeError:
            logger.warning(f"Can't extract data from HB")
            exit()

    def get_order_info(self, i, order):
        r = self.session.get(self.ORDER_URL.format(order_id=order))
        try:
            r = r.json()
        except JSONDecodeError:
            logger.error(f'Problem with getting info about {self.ORDER_URL.format(order_id=order)}')
            return info, []
        return i, Order(r['subproducts'], r['product']['human_name'], r['created'])

    def get_product_list(self):
        with ThreadPoolExecutor(max_workers=self.download_limit) as executor:
            for info, order in executor.map(
                self.get_order_info, range(self.orders_num), self.order_key_list
            ):
                if order:
                    self.order_list.append(order)
                progress_bar(info + 1, self.orders_num, suffix='Getting Products')
        print('\n')
        self.order_list.sort(key=lambda x: x.date, reverse=False)  # oldest first
        self.all_set = {item for order in self.order_list for item in order.products}

    def get_download_list(self):
        self.to_download_set = {
            item
            for order in self.order_list[-self.purchase_limit :]
            for item in order.products
        }
        self.to_not_download_set = {
            item
            for order in self.order_list[:-self.purchase_limit]
            for item in order.products
        }

        self.to_download_set = self.to_download_set.difference(self.to_not_download_set)

        self.check_platforms()

        self.to_download_set = {
            item
            for item in self.to_download_set
            if item.platform in self.platforms
        }
        self.to_download_set = self.to_download_set.difference(self.checked_list)
        # above line to have only new items and items, which are not checked already
        self.to_download_list = list(self.to_download_set)
        self.total_size = sum(item.size for item in self.to_download_list)
        self.human_size = human_size(self.total_size)
        self.total_items = len(self.to_download_list)
        self.downloaded_size = 0
        self.to_download_list.sort(key=lambda x: x.size, reverse=self.reverse_order)
        self.orphaned_set = set(self.downloaded_list).difference(self.all_set)
        #self.orphaned_set = self.all_set.difference(self.downloaded_list)

    def download_helper(self):
        self.clean_orphan()
        with ThreadPoolExecutor(max_workers=self.download_limit) as executor:
            for product in executor.map(
                self.download, range(self.total_items), self.to_download_list
            ):
                if product is not None:
                    self.downloading_list.append(product)
                progress_bar(
                    self.downloaded_size,
                    self.total_size,
                    suffix=f'Downloading: {human_size(self.downloaded_size)}/{self.human_size}'
                )
        print('\n')
        self.save_data()

    def clean_orphan(self):
        root = self.download_folder

        for item in self.orphaned_set:
            bundle_name = f'{item.date.date()} {slugify(item.bundle_name)}'
            item_name = slugify(item.name)
            filename = Path(
                f'{root}/{item.platform}/{bundle_name}/{item_name}/{extract_filename(item)}'
            )

            if filename.exists():
                if item.md5 == md5sum(filename):
                    logger.warning(f"Moving orphaned file {filename.name}")
                    item2 = filename.parents[3].joinpath('orphaned', *filename.parts[-4 :])
                    i = 1
                    # loop here to avoid rewriting files, *nix problem
                    while True:
                        if not item2.exists():
                            break
                        item2 = item2.with_suffix(item2.suffix + f'.{i}')
                        i += 1
                    item2.parent.mkdir(parents=True, exist_ok=True)
                    filename.rename(item2)
            else:
                logger.debug(f"File already moved {filename.name}")

    def check_file(self, product, filename):
        if not filename.exists():
            logger.warning(f'File missing {filename}')
        else:
            md5 = md5sum(filename)
            if md5 == product.md5:
                logger.success(f'File checked {filename.name}')
                return True
            else:
                # file exist, but not correct md5 -> delete it
                logger.error(f'File mismatch md5sum, deleting {product.name}, {filename}')
                filename.unlink()
        return False

    def download(self, i, item):
        name = slugify(item.bundle_name)
        name = item.date.strftime('%Y-%m-%d ') + name
        directory = self.download_folder / Path(item.platform
                                                ) / Path(name) / Path(slugify(item.name))

        directory.mkdir(parents=True, exist_ok=True)

        filename = directory / Path(extract_filename(item))

        if self.check_file(item, filename):
            self.downloaded_size += item.size
            item.checked = True
            return item
        else:
            try:
                logger.debug(f"Download start for '{item.name}' {filename.name}")
                info = download(item.url, filename, item.size)
                if info:
                    logger.success(f"Downloaded {filename.name} {human_size(item.size)}")
            except ValueError:
                logger.warning(f"Mismatch in reported size {filename.name}")
            except FileNotFoundError:
                logger.error("Probable error with 260 character path limit")
            except ConnectionResetError:
                logger.warning(f"Connection problem when getting {filename}")
            else:
                if filename.exists():
                    if item.size != filename.stat().st_size:
                        logger.error(f"Deleting file with incorrect size {filename}")
                        filename.unlink()
                        return
                    else:
                        self.downloaded_size += item.size
                        item.checked = False
                    return item

    def save_data(self):
        to_dump = set(self.downloading_list).union(self.downloaded_list)
        dump_data(to_dump, 'downloaded.yaml')
        dump_data(self.orphaned_set, 'orphaned.yaml')
        to_dump = self.all_set.difference(to_dump)
        dump_data(to_dump, 'not-downloaded.yaml')


class Order:

    def __init__(self, json_list: list[dict[str, Any]], name: str, date: str):
        self.products: list[Product] = []
        self.bundle_name = name
        self.date = datetime.strptime(date, '%Y-%m-%dT%H:%M:%S.%f')
        self.name_exclusion = [
            'thespookening_android', 'worldofgoo_android_pc_soundtrack_audio', 'dustforce_asm'
        ]
        self.md5_exclusion = [
            'c0776421f3527a706cf1f3f3765cafb4',  # issue 1
            '2f8612361dde58c73525ea0d024c0460',  # issue 1
            'bcb063559d17364e9f7bfd3d4fd799ee',  # issue 1
            '428dd67152164f444e6fa21e87caa147',  # issue 1
            '748b36888d3c6e747dc00eea5d518bb9',
            'ef8a5895edce744719bc031ffb0173b0',
            'b5796f487f5f647045bb5fb6eaf16edf'  # issue 2 -- SOMA mac version
        ]
        for product in json_list:
            try:
                name = product['human_name']
            except KeyError:
                pass
            else:
                for items in product['downloads']:
                    machine_name = items['machine_name']
                    for struct in items['download_struct']:
                        self.extract_data(struct, items['platform'], name, machine_name)

    def extract_data(
        self, struct: dict[str, Any], platform: str, name: str, machine_name: str
    ) -> None:
        try:
            url = struct['url']['web']
            size = struct['file_size']
            md5 = struct['md5']
            machine_name = machine_name
        except KeyError:
            if machine_name not in self.name_exclusion:
                logger.error(f'Problem with parsing {machine_name}')
                pass
        else:
            if md5 not in self.md5_exclusion:
                data = {
                    'name': name,
                    'url': url,
                    'size': size,
                    'md5': md5,
                    'platform': platform,
                    'bundle_name': self.bundle_name,
                    'date': self.date,
                    'machine_name': machine_name
                }
                self.products.append(Product(**data))


class Product:

    def __init__(self, **kwargs: Any):
        self.checked: bool = False
        self.md5: str
        self.size: int
        self.name: str
        for key in kwargs.keys():
            setattr(self, key, kwargs[key])
        if hasattr(self, 'hb_name'):  # this convert from old downloaded.yaml
            logger.debug(f'Converting old hb_name to new bundle_name for {self.name}')
            setattr(self, 'bundle_name', self.hb_name)
            delattr(self, 'hb_name')

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Product):
            return NotImplemented
        return self.md5 == other.md5 and self.size == other.size

    def __hash__(self) -> int:
        return hash(f'{self.md5}{self.size}')


if __name__ == '__main__':
    yaml = YAML(typ='safe')
    yaml.default_flow_style = False
    platform_list = [
        'android', 'audio', 'ebook', 'linux', 'mac', 'windows', 'video, other', 'nogames', 'all'
    ]
    parser = create_parser()
    platforms, cfg = parse_config(parser)

    try:
        cookie = cfg['session_cookie']
        _ = cookie[0]
    except (IndexError, ValueError):
        logger.critical('No valid cookie')
        exit()

    runner = HumbleApi(**cfg, platforms=platforms)
    runner.get_order_list()
    runner.get_product_list()
    runner.get_download_list()
    runner.download_helper()
