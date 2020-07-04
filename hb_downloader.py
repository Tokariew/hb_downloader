import argparse
import hashlib
import http.cookiejar
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from math import floor, log2
from pathlib import Path

import requests
from dateutil import parser as dt_parser
from termcolor import colored

from ruamel.yaml import YAML


yaml = YAML(typ='safe')
yaml.default_flow_style = False


def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller."""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)

    return os.path.join(os.path.abspath(''), relative_path)


def round10(x):
    return floor(x / 10) * 10


def human_size(x):
    if x == 0:
        return '0 B'
    suffixes = ['B', 'kiB', 'MiB', 'GiB', 'TiB']
    c = round10(log2(x))
    x = x / 2**c
    r = suffixes[c // 10]
    return f'{x:.02f} {r}'


def md5sum(filename, blocksize=65536):
    hash = hashlib.md5()
    with open(filename, 'rb') as f:
        for block in iter(lambda: f.read(blocksize), b''):
            hash.update(block)
    return hash.hexdigest()


def restring(string):
    s = re.sub('[\\/*?:"<>|\']', '_', string)
    return re.sub('( |[.])+([.]| |$)', '', s)


def download(url, file_name):
    with requests.get(url, stream=True) as r:
        total_length = int(r.headers.get('content-length'))
        print('Downloading ', colored(f'{file_name.name} ', 'blue', 'on_white'),
              colored(f'{human_size(total_length)} ', 'white', 'on_cyan'))
        with open(file_name, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024**2):
                f.write(chunk)
    time.sleep(.1)


def c_error(text):
    return colored(text, 'red', 'on_white')


def read_yaml():
    try:
        with open('config.yaml') as yamfile:
            cfg = yaml.load(yamfile)
        return cfg
    except FileNotFoundError:
        Path('config.yaml').write_text(Path(resource_path('example_config.yaml')).read_text())


def parse_config(parser):
    cfg = read_yaml()
    if cfg is None:
        return []
    platforms = parser.parse_args().platform
    download_limit = parser.parse_args().download_limit[0]
    download_limit = download_limit if download_limit else None
    download_limit = cfg[
        'download_limit'] if download_limit is None or download_limit < 0 else download_limit
    purchase_limit = parser.parse_args().purchase_limit[0]
    purchase_limit = cfg[
        'purchase_limit'] if purchase_limit is None or purchase_limit < 0 else purchase_limit
    smallest_first = parser.parse_args().smallest_first
    cfg = {
        'path': cfg['download_folder'],
        'download_limit': download_limit,
        'purchase_limit': purchase_limit,
        'smallest_first': smallest_first,
        'session_cookie': cfg['session_cookie']
    }
    return platforms, cfg


class HumbleApi:
    LOGIN_URL = 'https://www.humblebundle.com/processlogin'
    ORDER_LIST_URL = 'https://www.humblebundle.com/api/v1/user/order'
    ORDER_URL = 'https://www.humblebundle.com/api/v1/order/{order_id}'

    default_headers = {
        'Accept': 'application/json',
        'Accept-Charset': 'utf-8',
        'Keep-Alive': 'true',
        'X-Requested-By': 'hb_android_app',
        'User-Agent': 'Apache-HttpClient/UNAVAILABLE (java 1.4)'
    }
    default_params = {'ajax': 'true'}

    def __init__(self,
                 platform='audio',
                 download_limit=None,
                 purchase_limit=None,
                 smallest_first=True,
                 path='.',
                 session_cookie=''):
        self.platform = platform
        self.download_limit = download_limit
        self.purchase_limit = purchase_limit
        self.reverse = not smallest_first
        self.download_folder = Path(path)
        self.download_folder.mkdir(exist_ok=True)
        self.dir = self.download_folder / Path(platform)
        self.auth_sess_cookie = bytes(session_cookie, 'utf-8').decode()
        self.session = requests.Session()

        self.cookie = http.cookiejar.Cookie(0, '_simpleauth_sess', self.auth_sess_cookie, None,
                                            None, 'www.humblebundle.com', None, None, '/', None,
                                            True, None, False, None, None, None)

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

    def change_platform(self, platform):
        if platform != self.platform:
            self.platform = platform
            self.dir = self.dir.parent / Path(platform)
        self.dir.mkdir(exist_ok=True, parents=True)

    def get_orders(self):
        r = self.session.request('GET', self.ORDER_LIST_URL)
        r = r.json()
        self.order_list = [item['gamekey'] for item in r]

    def get_product(self, *args):
        args = args[0]
        i = args[0]
        order = args[1]
        info = 'Getting products:' + colored(f'{i+1}/{self.orders_num}', 'green')
        i = self.ORDER_URL.format(order_id=order)
        r = self.session.request('GET', i)
        r = r.json()
        time.sleep(.1)
        return info, Order(r['subproducts'], r['product']['human_name'], r['created'])

    def get_products(self, max_items=0):
        self.product_lists = []
        self.orders_num = len(self.order_list)
        with ThreadPoolExecutor(max_workers=self.download_limit) as executor:
            for info, order in executor.map(self.get_product, enumerate(self.order_list)):
                self.product_lists.append(order)
                print(info)
        self.product_lists.sort(key=lambda x: x.date, reverse=False)  # oldest first
        self.product_set = {item for order in self.product_lists for item in order.products}

    def check_download(self):
        if self.purchase_limit is None:
            self.purchase_limit = 0
        self.product_set2 = {
            item
            for order in self.product_lists[-self.purchase_limit:] for item in order.products
        }
        self.not_downloaded = {
            item
            for order in self.product_lists[:-self.purchase_limit] for item in order.products
        }
        self.product_set2 = self.product_set2.difference(self.not_downloaded)
        self.product_set = self.product_set.intersection(self.product_set2)  # will fail now
        self.download_list = [item for item in self.product_set if self.platform == item.platform]
        self.total_size = sum([item.size for item in self.download_list])
        self.human_size = human_size(self.total_size)
        self.downloaded_size = 0
        self.download_list.sort(key=lambda x: x.size, reverse=self.reverse)

    def download2(self, *args):
        args = args[0]
        i = args[0]
        item = args[1]
        name = restring(item.hb_name)
        name = item.date.strftime('%Y-%m-%d ') + name
        dir = self.dir / Path(name)
        dir.mkdir(exist_ok=True)
        dir = dir / Path(restring(item.name))
        dir.mkdir(exist_ok=True)

        filename = item.url[:item.url.find('?')]
        filename = filename[filename.rfind('/') + 1:]
        filename = dir / Path(filename)

        try:
            item_down = self.downloaded_list[self.downloaded_list.index(item)]
        except ValueError:
            if filename.exists():
                if md5sum(filename) == item.md5:
                    # this check is very dirty, just in case of having download files from old version…
                    item.checked = True
                    print('dirty skip')
                    return item
        else:
            item_down.checked = True
            info_skip = 'Skiping: ' + colored(f'{filename.name} ', 'yellow') + colored(
                f'{i+1}/{self.total_items}', 'magenta')
            if not item_down.checked:
                print('Checking md5')
                if md5sum(filename) == item.md5:
                    self.downloaded_size += item.size
                    print(info_skip)
                    return item_down
            else:
                self.downloaded_size += item.size
                print(info_skip)
                return item_down

        try:
            download(item.url, filename)
        except FileNotFoundError:
            err_msg = 'Probably 260 Character Path Limit on Windows.'
            info = c_error('Error: ') + "Can't Download " + c_error(
                f'{item.name} ') + 'from ' + c_error(f'{item.hb_name}\n{err_msg}')
            print(info)
        else:
            self.downloaded_size += item.size
            info = f'Downloaded {filename.name}, progress: ' + colored(
                f'{human_size(self.downloaded_size)}/{self.human_size} ', 'cyan') + colored(
                    f'{i+1}/{self.total_items}', 'magenta')
            print(info)
            item.checked = False
            return item

    def downloads(self):
        self.check_download()
        self.total_items = len(self.download_list)
        with ThreadPoolExecutor(max_workers=self.download_limit) as executor:
            for track in executor.map(self.download2, enumerate(self.download_list)):
                if track is not None:
                    self.downloading_list.append(track)


class Order:
    def __init__(self, dic, name, date):
        self.products = []
        self.hb_name = name
        self.date = dt_parser.parse(date)

        for product in dic:
            name = product['human_name']
            for items in product['downloads']:
                for struct in items['download_struct']:
                    url2 = size2 = md52 = ''
                    try:
                        url2 = (struct['url']['web'])
                        size2 = (struct['file_size'])
                        md52 = (struct['md5'])
                    except KeyError:
                        print(colored(f'Problem with: {name}', 'red'))
                    platform = items['platform']
                    test = {
                        'name': name,
                        'url': url2,
                        'size': size2,
                        'md5': md52,
                        'platform': platform,
                        'hb_name': self.hb_name,
                        'date': self.date
                    }
                    if url2 != '':
                        self.products.append(Product(**test))


class Product:
    def __init__(self, **kwargs):
        self.checked = False
        for k in kwargs.keys():
            setattr(self, k, kwargs[k])

    def __eq__(self, other):
        return self.md5 == other.md5

    def __hash__(self):
        return hash(self.md5)


if __name__ == '__main__':

    platform_list = [
        'android', 'audio', 'ebook', 'linux', 'mac', 'windows', 'video, other', 'nogames', 'all'
    ]
    parser = argparse.ArgumentParser(
        description='Download files from Humble Bundle, based on selected platform')
    parser.add_argument('platform',
                        metavar='platform',
                        type=str,
                        nargs='+',
                        help=f'platform to download, valid platforms are: {platform_list}')
    parser.add_argument('-l',
                        '--download-limit',
                        metavar='X',
                        type=int,
                        default=[None],
                        nargs=1,
                        help='Parallel download limit, optional.')
    parser.add_argument('-n',
                        '--purchase-limit',
                        metavar='Y',
                        type=int,
                        default=[None],
                        nargs=1,
                        help='number of newest purchases to download, default 0 for all')
    parser.add_argument('-s',
                        '--smallest_first',
                        action='store_true',
                        help='switch to download smallest files first')

    try:
        platforms, cfg = parse_config(parser)
        cookie = cfg['session_cookie']
        test = cookie[0]  # this catch empty string
    except (IndexError, ValueError):
        print('No valid session_cookie, please provide session_cookie in config.yaml')
    else:
        a = HumbleApi(platforms[0], **cfg)
        a.get_orders()
        a.get_products()
        if 'nogames' in platforms:
            platforms = {x.platform for x in a.product_set}
            platforms = list(platforms.difference({'linux', 'mac', 'windows', 'android'}))
        if 'all' in platforms:
            platforms = list({x.platform for x in a.product_set})
        for platform in platforms:
            a.change_platform(platform)
            a.downloads()

        with open('downloaded.yaml', 'w') as yamfile:
            to_dump = set(a.downloaded_list).union(a.downloading_list)  # hmm i like sets?
            to_dump = [item.__dict__ for item in to_dump]
            yaml.indent(mapping=4, sequence=6, offset=3)
            yaml.dump(to_dump, yamfile)
