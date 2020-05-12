import argparse
import hashlib
import http.cookiejar
import re
import time
from concurrent.futures import ThreadPoolExecutor
from math import floor, log2
from pathlib import Path

import requests
from dateutil import parser as dt_parser
from termcolor import colored

# need your cookie _simpleauth_sess value from browser
session_cookie = ''


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
    with open(filename, "rb") as f:
        for block in iter(lambda: f.read(blocksize), b""):
            hash.update(block)
    return hash.hexdigest()


def restring(string):
    s = re.sub('[\\/*?:"<>|\']', '_', string)
    return re.sub('( |[.])+([.]| |$)', '', s)


def download(url, file_name):
    with requests.get(url, stream=True) as r:
        total_length = int(r.headers.get('content-length'))
        print('Downloading ', colored(f'{file_name.name} ', 'blue',
                                      'on_white'),
              colored(f'{human_size(total_length)} ', 'white', 'on_cyan'))
        with open(file_name, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024**2):
                f.write(chunk)
    time.sleep(.1)


class HumbleApi:
    LOGIN_URL = "https://www.humblebundle.com/processlogin"
    ORDER_LIST_URL = "https://www.humblebundle.com/api/v1/user/order"
    ORDER_URL = "https://www.humblebundle.com/api/v1/order/{order_id}"

    default_headers = {
        "Accept": "application/json",
        "Accept-Charset": "utf-8",
        "Keep-Alive": "true",
        "X-Requested-By": "hb_android_app",
        "User-Agent": "Apache-HttpClient/UNAVAILABLE (java 1.4)"
    }
    default_params = {"ajax": "true"}

    def __init__(self, platform='audio', d_limit=None, n_limit=None,
                 revs=True):
        self.platform = platform
        self.d_limit = d_limit
        self.n_limit = n_limit
        self.reverse = revs
        self.dir = Path('.') / Path(platform)
        self.dir.mkdir(exist_ok=True, parents=True)
        self.session = requests.Session()

        auth_sess_cookie = bytes(session_cookie, 'utf-8').decode()
        self.cookie = http.cookiejar.Cookie(0, "_simpleauth_sess",
                                            auth_sess_cookie, None, None,
                                            "www.humblebundle.com", None, None,
                                            "/", None, True, None, False, None,
                                            None, None)

        self.session = requests.Session()
        self.session.cookies.set_cookie(self.cookie)
        self.session.headers.update(self.default_headers)
        self.session.params.update(self.default_params)

    def change_platform(self, platform):
        if platform != self.platform:
            self.platform = platform
            self.dir = Path('.') / Path(platform)
            self.dir.mkdir(exist_ok=True, parents=True)

    def get_orders(self):
        r = self.session.request('GET', self.ORDER_LIST_URL)
        r = r.json()
        self.order_list = [item['gamekey'] for item in r]

    def get_product(self, *args):
        args = args[0]
        i = args[0]
        order = args[1]
        info = 'Getting products:' + colored(f'{i+1}/{self.orders_num}',
                                             'green')
        i = self.ORDER_URL.format(order_id=order)
        r = self.session.request('GET', i)
        r = r.json()
        time.sleep(.1)
        return info, Order(r['subproducts'], r['product']['human_name'],
                           r['created'])

    def get_products(self, max_items=0):
        self.product_lists = []
        self.orders_num = len(self.order_list)
        with ThreadPoolExecutor(max_workers=self.d_limit) as executor:
            for info, order in executor.map(self.get_product,
                                            enumerate(self.order_list)):
                self.product_lists.append(order)
                print(info)
        '''for j, order in enumerate(self.order_list):
            print('Getting products:', colored(f'{j+1}/{orders}', 'green'))
            i = self.ORDER_URL.format(order_id=order)
            r = self.session.request('GET', i)
            r = r.json()
            self.product_lists.append(Order(r['subproducts'], r['product']['human_name'], r['created']))
            if j + 1 >= max_items and max_items != 0:
                break'''
        self.product_lists.sort(key=lambda x: x.date,
                                reverse=False)  # oldest first
        self.product_set = {
            item
            for order in self.product_lists for item in order.products
        }

    def check_download(self):
        if self.n_limit is None:
            self.n_limit = 0
        self.product_set2 = {
            item
            for order in self.product_lists[-self.n_limit:]
            for item in order.products
        }
        self.not_downloaded = {
            item
            for order in self.product_lists[:-self.n_limit]
            for item in order.products
        }
        self.product_set2 = self.product_set2.difference(self.not_downloaded)
        self.product_set = self.product_set.intersection(
            self.product_set2)  # will fail now
        self.download_list = [
            item for item in self.product_set if self.platform == item.platform
        ]
        self.total_size = sum([item.size for item in self.download_list])
        self.human_size = human_size(self.total_size)
        self.downloaded_size = 0
        self.download_list.sort(key=lambda x: x.size, reverse=self.reverse)

    def download2(self, *args):
        args = args[0]
        i = args[0]
        item = args[1]
        name = restring(item.hb_name)
        name = item.date.strftime("%Y-%m-%d ") + name
        dir = self.dir / Path(name)
        dir.mkdir(exist_ok=True)
        dir = dir / Path(restring(item.name))
        dir.mkdir(exist_ok=True)
        filename = item.url[:item.url.find('?')]
        filename = filename[filename.rfind('/') + 1:]
        filename = dir / Path(filename)
        if filename.exists():
            if md5sum(filename) == item.md5:
                info = 'Skiping: ' + colored(
                    f'{filename.name} ', 'yellow') + colored(
                        f'{i+1}/{self.total_items}', 'magenta')
                self.downloaded_size += item.size
                print(info)
                return
        try:
            download(item.url, filename)
        except FileNotFoundError:
            print(item.url)
        self.downloaded_size += item.size
        info = f'Downloaded {filename.name}, progress: ' + colored(
            f'{human_size(self.downloaded_size)}/{self.human_size} ',
            'cyan') + colored(f'{i+1}/{self.total_items}', 'magenta')
        print(info)

    def downloads(self):
        self.check_download()
        self.total_items = len(self.download_list)
        with ThreadPoolExecutor(max_workers=self.d_limit) as executor:
            for track in executor.map(self.download2,
                                      enumerate(self.download_list)):
                pass


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
        for k in kwargs.keys():
            setattr(self, k, kwargs[k])

    def __eq__(self, other):
        return self.md5 == other.md5

    def __hash__(self):
        return hash(self.md5)


if __name__ == '__main__':
    platform_list = [
        'android', 'audio', 'ebook', 'linux', 'mac', 'windows', 'video, other'
    ]
    parser = argparse.ArgumentParser(
        description=
        'Download files from Humble Bundle, based on selected platform')
    parser.add_argument(
        'platform',
        metavar='platform',
        type=str,
        nargs='+',
        help=f'platform to download, valid platforms are: {platform_list}')
    parser.add_argument('-l',
                        '--download-limit',
                        metavar='X',
                        type=int,
                        default=[0],
                        nargs=1,
                        help='Parallel download limit, optional.')
    parser.add_argument(
        '-n',
        '--purchase-limit',
        metavar='Y',
        type=int,
        default=[0],
        nargs=1,
        help='number of newest purchases to download, default 0 for all')
    parser.add_argument('-s',
                        '--smallest_first',
                        action='store_false',
                        help='switch to download smallest files first')
    platforms = parser.parse_args().platform
    print(n_limit)
    a = HumbleApi(platforms[0], d_limit, n_limit)
    d_limit = parser.parse_args().download_limit[0]
    d_limit = d_limit if d_limit else None
    n_limit = parser.parse_args().purchase_limit[0]
    revs = parser.parse_args().smallest_first
    a = HumbleApi(platforms[0], d_limit, n_limit, revs)
    a.get_orders()
    a.get_products()
    for platform in platforms:
        a.change_platform(platform)
        a.downloads()
