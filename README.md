# hb_downloader

Simple script to download purchases from humble bundle account.

By default, script download multiple files at once, starting with the biggest files.
Script will only download files specified by platform parameter. Platform  parameter is based on platform selection in [HB Library](https://www.humblebundle.com/home/library).

Script only download files once even if it was purchased in multiple bundles. It will be saved in the oldest bundle/purchase folder.

To not download files multiple times, hash of file is computed and compared with one provided by HB, so if HB will update files it will overwrite existing one.

By default, file are saved in following folder structure:

platform/date of purchase bundle name/item name

This structure helps with importing ebooks into calibre, so each ebook will be in separate folder with multiple available formats.

## Requiments
Python in version 3.6 of higher, accesible in command line

Script require few additional non-standard packages. Install them with

```
pip install termcolor python-dateutil python-dateutil ruamel.yaml

```

For Windows systems script require to remove 260 path name limit, otherwise download of some files will fail or script will try to redownload files with long file names.

## Usage

Make config.yaml file based on example_config.yaml and provide _simpleauth_sess cookie from browser between apostrophes.

Providing switches for download_limit and purchase_limit ignore default values from config.yaml file.
```
python hb_downloader.py --help
usage: hb_downloader.py [-h] [-l X] [-n Y] [-s] platform [platform ...]

Download files from Humble Bundle, based on selected platform

positional arguments:
  platform              platform to download, valid platforms are: ['android',
                        'audio', 'ebook', 'linux', 'mac', 'windows', 'video,
                        other', 'nogames', 'all']

optional arguments:
  -h, --help            show this help message and exit
  -l X, --download-limit X
                        Parallel download limit, optional.
  -n Y, --purchase-limit Y
                        number of newest purchases to download, default 0 for
                        all
  -s, --smallest_first  switch to download smallest files first
```

You can download multiple platforms one after another for example to download all audio albums, and after that all ebook files:

```
python hb_downloader.py audio ebook
```

To download all files from HB account use:
```
python hb_downloader.py all
```

To download all files, but not games:
```
python hb_downloader.py nogames
```

By default, script download multiple files at once, starting with the biggest files.
You can limit maximum parallel downloads by specifying download-limit, to download 6 audio files at once use:
```
python hb_downloader.py -l 6 audio
```

To download only files from the latest purchase use purchase-limit option, for example to download only ebooks from last 5 bundles use:

```
python hb_downloader.py -n 5 ebook
```

Above example will skip files if they were in previous bundles purchased, so it recommended use is to update downloaded collection by newest purchased bundles not downloaded before. If purchase-limit option is omitted all previously downloaded files will have their hash recomputed which will slow down overall process of downloading.
