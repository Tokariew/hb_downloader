#!/bin/python
import argparse
import hashlib
import http.cookiejar
import time
from concurrent.futures import ThreadPoolExecutor
from json import JSONDecodeError
from math import floor, log2
from pathlib import Path

import requests
from dateutil import parser as dt_parser
from ruamel.yaml import YAML
from slugify import slugify
from termcolor import colored

yaml = YAML(typ='safe')
yaml.default_flow_style = False


def human_size(x):
    if x == 0:
        return '0 B'
    suffixes = ['B', 'kiB', 'MiB', 'GiB', 'TiB']
    c = floor(log2(x) / 10) * 10
    x = x / 2 ** c
    r = suffixes[c // 10]
    return f'{x:.02f} {r}'


def md5sum(filename, blocksize=65536):
    hash = hashlib.md5()
    with open(filename, 'rb') as f:
        for block in iter(lambda: f.read(blocksize), b''):
            hash.update(block)
    return hash.hexdigest()


def download(url, file_name, report_size, chunk_size=1048576):
    with requests.get(url, stream=True) as r:
        total_length = int(r.headers.get('content-length'))
        if total_length != report_size:
            print(
                'Ignoring file:', colored(f'{file_name.name}', 'red'),
                ' mismatch between download size and reported by API'
            )
            return
        print(
            'Downloading ', colored(f'{file_name.name} ', 'blue', 'on_white'),
            colored(f'{human_size(total_length)} ', 'white', 'on_cyan')
        )
        with open(file_name, 'wb') as f:
            for chunk in r.iter_content(chunk_size=chunk_size):
                f.write(chunk)
    time.sleep(.1)


def parse_config(parser):
    try:
        with open('config.yaml') as yamfile:
            cfg = yaml.load(yamfile)
            _ = cfg.keys()  # Check if file is not empty
    except (FileNotFoundError, AttributeError):
        Path('config.yaml').write_text(Path('example_config.yaml').read_text())
        return
    platforms = parser.parse_args().platform
    download_limit = parser.parse_args().download_limit[0]
    if download_limit:
        cfg['download_limit'] = download_limit
    purchase_limit = parser.parse_args().purchase_limit[0]
    if purchase_limit:
        cfg['purchase_limit'] = purchase_limit
    if parser.parse_args().smallest_first:
        cfg['smallest_first'] = True
    if parser.parse_args().trove:
        cfg['trove'] = True

    return platforms, cfg


def dump_data(to_dump, file_name):
    to_dump = [item.__dict__ for item in to_dump]
    to_dump = sorted(to_dump, key=lambda i: (i['date'], i['name']))
    yaml.indent(mapping=4, sequence=6, offset=3)
    with open(file_name, 'w') as file:
        yaml.dump(to_dump, file)


class HumbleApi:
    LOGIN_URL = 'https://www.humblebundle.com/processlogin'
    ORDER_LIST_URL = 'https://www.humblebundle.com/api/v1/user/order'
    ORDER_URL = 'https://www.humblebundle.com/api/v1/order/{order_id}'
    TROVE_URL = 'https://www.humblebundle.com/api/v1/trove/chunk?property=start&direction=desc&index={page}'

    default_headers = {
        'Accept': 'application/json',
        'Accept-Charset': 'utf-8',
        'X-Requested-By': 'hb_android_app',
        'User-Agent': 'Apache-HttpClient/UNAVAILABLE (java 1.4)'
    }
    default_params = {'ajax': 'true'}

    def __init__(
        self,
        download_limit=6,
        purchase_limit=0,
        smallest_first=False,
        download_folder='.',
        trove=False,
        platforms=[],
        session_cookie=''
    ):
        self.download_limit = download_limit
        self.purchase_limit = purchase_limit
        self.reverse_order = not smallest_first
        self.download_folder = Path(download_folder)
        self.download_folder.mkdir(exist_ok=True)
        self.trove = trove
        self.platforms = platforms

        self.auth_sess_cookie = bytes(session_cookie, 'utf-8').decode()
        self.session = requests.Session()

        self.cookie = http.cookiejar.Cookie(
            0,
            '_simpleauth_sess',
            self.auth_sess_cookie,
            None,
            None,
            'www.humblebundle.com',
            None,
            None,
            '/',
            None,
            True,
            None,
            False,
            None,
            None,
            None
        )

        self.session.cookies.set_cookie(self.cookie)
        self.session.headers.update(self.default_headers)
        self.session.params.update(self.default_params)

        try:
            with open('downloaded.yaml') as yamfile:
                self.downloaded_list = yaml.load(yamfile)
                self.downloaded_list = [Product(**item) for item in self.downloaded_list]
        except FileNotFoundError:
            self.downloaded_list = []

        self.downloading_list = []

    def get_order_list(self):
        r = self.session.get(self.ORDER_LIST_URL)
        r = r.json()
        self.order_key_list = [item['gamekey'] for item in r]

    def get_order_info(self, i, order):
        info = 'Getting products:' + colored(f'{i+1}/{self.orders_num}', 'green')
        url = self.ORDER_URL.format(order_id=order)
        r = self.session.get(url)
        try:
            r = r.json()
            time.sleep(.1)
        except JSONDecodeError:
            print(f'JSON error for: {url}')
            return info, []
        return info, Order(r['subproducts'], r['product']['human_name'], r['created'])

    def get_trove_info(self):
        page = 0
        trove_list = []
        while True:
            r = self.session.get(self.TROVE_URL.format(page=page))
            if r.status_code != 200:
                break
            if not r.json():  # check for empty list
                break
            trove_list += r.json()
            page += 1
        self.trove_set = set(Order(trove_list, 'trove', '2010-11-24').products)

    def check_trove_active(self):
        try:
            tmp = list(self.trove_set)[0]
        except IndexError:
            self.trove = False
            print(colored('Humble Trove is no more…', 'yellow'))
            return False
        try:
            url = self.get_trove_download_link(tmp)
        except KeyError:
            print(
                colored(
                    "Humble Choice subscription probably paused, can't download trove games",
                    'yellow'
                )
            )
            self.trove = False
            return False
        return True

    def get_trove_download_link(self, product):
        r = self.session.post(
            'https://www.humblebundle.com/api/v1/user/download/sign',
            data={'machine_name': product.machine_name}
        )
        return r.json()['signed_url']

    def get_product_list(self):
        self.order_list = []
        self.orders_num = len(self.order_key_list)
        with ThreadPoolExecutor(max_workers=self.download_limit) as executor:
            for info, order in executor.map(
                self.get_order_info, range(self.orders_num), self.order_key_list
            ):
                if order:
                    self.order_list.append(order)
                print(info)
        self.order_list.sort(key=lambda x: x.date, reverse=False)  # oldest first
        self.product_set = {item for order in self.order_list for item in order.products}

        if self.trove:
            self.get_trove_info()

    def check_platforms(self):
        if 'nogames' in self.platforms:
            platforms = {x.platform for x in a.product_set}
            self.platforms = list(platforms.difference({'linux', 'mac', 'windows', 'android'}))
        if 'all' in self.platforms:
            self.platforms = list({x.platform for x in a.product_set})

    def prepare_download_list(self):
        if self.purchase_limit is None:
            self.purchase_limit = 0
        self.to_download_set = {
            item
            for order in self.order_list[-self.purchase_limit:]
            for item in order.products
        }
        self.to_not_download_set = {
            item
            for order in self.order_list[:-self.purchase_limit]
            for item in order.products
        }
        # below line to remove duplicate items from earlier bundles
        self.to_download_set = self.to_download_set.difference(self.to_not_download_set)

        self.check_platforms()

        if self.trove:
            # difference for check to work… otherwise humble will return non trove link
            self.trove_set = self.trove_set.difference(self.to_download_set)
            if self.check_trove_active():
                self.to_download_set = self.to_download_set.union(self.trove_set)

        self.to_download_list = [
            item for item in self.to_download_set if item.platform in self.platforms
        ]
        self.total_size = sum([item.size for item in self.to_download_list])
        self.human_size = human_size(self.total_size)
        self.downloaded_size = 0
        self.to_download_list.sort(key=lambda x: x.size, reverse=self.reverse_order)

    def download_threads(self):
        self.prepare_download_list()
        self.total_items = len(self.to_download_list)
        with ThreadPoolExecutor(max_workers=self.download_limit) as executor:
            for track in executor.map(
                self.download, range(self.total_items), self.to_download_list
            ):
                if track is not None:
                    self.downloading_list.append(track)
        to_dump = set(self.downloading_list).union(self.downloaded_list)
        dump_data(to_dump, 'downloaded.yaml')
        to_dump = set(self.downloaded_list).difference(self.downloading_list)
        dump_data(to_dump, 'orphaned.yaml')
        to_dump = set(self.to_download_list).difference(self.downloading_list)
        dump_data(to_dump, 'not-downloaded.yaml')

    def download(self, i, item):
        name = slugify(item.hb_name)
        name = item.date.strftime('%Y-%m-%d ') + name
        dir = self.download_folder / Path(item.platform) / Path(name) / Path(slugify(item.name))
        dir.mkdir(parents=True, exist_ok=True)

        if 'trove' in name and self.trove:
            item.url = self.get_trove_download_link(item)

        filename = item.url[:item.url.find('?')]
        filename = filename[filename.rfind('/') + 1:]
        filename = dir / Path(filename)

        if self.check_file(item, filename):
            self.downloaded_size += item.size
            item.checked = True
            return item
        else:

            try:
                download(item.url, filename, item.size)
            except FileNotFoundError:
                print('Probably 260 Character Path Limit on Windows.')
            except ConnectionResetError:
                print('Internets problems')

            else:
                if filename.exists():
                    if item.size != filename.stat().st_size:
                        print(colored(f'Download failed, deleting file {filename.name}', 'red'))
                        filename.unlink()
                    else:
                        self.downloaded_size += item.size
                        item.checked = False
                        info = f'Downloaded {filename.name}, progress: ' + colored(
                            f'{human_size(self.downloaded_size)}/{self.human_size} ', 'cyan'
                        ) + colored(f'{i+1}/{self.total_items}', 'magenta')
                        print(info)
                    return item

    def check_file(self, item, filename):
        try:
            item_down = self.downloaded_list[self.downloaded_list.index(item)]
            if item_down.checked:
                return True
        except ValueError:
            pass

        if filename.exists():
            if md5sum(filename) == item.md5:
                print(colored(f'Skiping {filename.name}', 'green'))
                return True
        try:
            filename.unlink()
            print(colored(f'Not correct md5sum, deleting {filename.name}', red))
        except FileNotFoundError:
            pass
        return False


class Order:

    def __init__(self, json_list, name, date):
        self.products = []
        self.hb_name = name
        self.date = dt_parser.parse(date)
        self.name_exclusion = [
            'thespookening_android', 'worldofgoo_android_pc_soundtrack_audio', 'dustforce_asm'
        ]
        self.md5_exclusion = [
            'c0776421f3527a706cf1f3f3765cafb4',  # issue 1
            '2f8612361dde58c73525ea0d024c0460',  # issue 1
            'bcb063559d17364e9f7bfd3d4fd799ee',  # issue 1
            '428dd67152164f444e6fa21e87caa147',  # issue 1
            'b5796f487f5f647045bb5fb6eaf16edf'  # issue 2 -- SOMA mac version
        ]

        trove = False

        for product in json_list:
            try:
                name = product['human_name']
            except KeyError:
                name = product['human-name']  # trove being sneaky
                trove = True

            if not trove:
                for items in product['downloads']:
                    for struct in items['download_struct']:
                        machine_name = items['machine_name']
                        self.extract_data(struct, items['platform'], name, machine_name)
            else:
                for key in product['downloads'].keys():
                    machine_name = product['machine_name']
                    self.extract_data(product['downloads'][key], key, name, machine_name)

    def extract_data(self, struct, platform, name, machine_name):
        try:
            url = struct['url']['web']
            size = struct['file_size']
            md5 = struct['md5']
            machine_name2 = struct['machine_name'] if self.hb_name == 'trove' else machine_name
        except KeyError:
            if machine_name not in self.name_exclusion:  # Don't bother user with known problems
                print(colored(f'Problem with parsing information: {name}', 'red'))
        else:
            if md5 not in self.md5_exclusion:
                test = {
                    'name': name,
                    'url': url,
                    'size': size,
                    'md5': md5,
                    'platform': platform,
                    'hb_name': self.hb_name,
                    'date': self.date,
                    'machine_name': machine_name2
                }
                self.products.append(Product(**test))


class Product:

    def __init__(self, **kwargs):
        self.checked = False
        for k in kwargs.keys():
            setattr(self, k, kwargs[k])

    def __eq__(self, other):
        return self.md5 == other.md5

    def __hash__(self):
        # without hash, it not possible to put this into set. simplify logic
        return hash(self.md5)


if __name__ == '__main__':
    yaml = YAML(typ='safe')
    yaml.default_flow_style = False
    platform_list = [
        'android', 'audio', 'ebook', 'linux', 'mac', 'windows', 'video, other', 'nogames', 'all'
    ]
    parser = argparse.ArgumentParser(
        description='Download files from Humble Bundle, based on selected platform'
    )
    parser.add_argument(
        'platform',
        metavar='platform',
        type=str,
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
        help='Download Y newest purchases, 0 for all --default'
    )
    parser.add_argument(
        '-s', '--smallest_first', action='store_true', help='Download smallest files first'
    )
    parser.add_argument(
        '--trove', action='store_true', help='If selected trove games will be downloaded'
    )

    try:
        platforms, cfg = parse_config(parser)
        cookie = cfg['session_cookie']
        test = cookie[0]  # this catch empty string
    except (IndexError, ValueError):
        print('No valid session_cookie, please provide session_cookie in config.yaml')
    except TypeError:
        print(
            'No valid config file -- generated from default, please provide session_cookie in config file'
        )

    a = HumbleApi(**cfg, platforms=platforms)
    a.get_order_list()
    a.get_product_list()
    a.download_threads()
